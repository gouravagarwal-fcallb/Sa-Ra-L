"""
Sa-Ra-L Unified Control API
───────────────────────────
One FastAPI app for ALL strategies: list/run/stop, per-strategy live snapshot
(WebSocket), backtest summaries, daily analysis, readiness ("properly checked"),
multi-timeframe charts with Bollinger Bands, and a guarded real-order arming flow.

Reuses the BRAHMASTRA server's proven structure (ConnectionManager, broadcast
loop, static React serving) but drives the multi-strategy runner instead of a
single engine.

Run:
  python main.py --mode unified
  uvicorn src.api.server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import sys
import json
import asyncio
import secrets
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Query
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from fastapi.middleware.cors import CORSMiddleware
    _FASTAPI = True
except ImportError:
    _FASTAPI = False

from src.api.state_registry import get_multi_state, MARKET_SLOT
from src.api.runner import ApiPortfolioRunner

def _resource_root() -> str:
    """Repo root when running from source; the PyInstaller bundle dir when frozen
    into the desktop .exe (built with --add-data of the frontend build)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    return os.path.join(os.path.dirname(__file__), "..", "..")

STATIC_DIR = os.path.join(_resource_root(), "frontend", "brahmastra", "build")
REGISTRY_PATH = "strategies/registry.yaml"

# Client-REVIEW / demo build: when SARAL_REVIEW_MODE is set the live-order path is
# hard-disabled (arm/confirm return 403) so a build shared with sample clients can
# NEVER place a real order, no matter what they click.
REVIEW_MODE = os.environ.get("SARAL_REVIEW_MODE", "").lower() in ("1", "true", "yes")

# Live-order arming: token TTL and the exact phrase the user must type.
ARM_TTL_SECONDS = 60
def _live_phrase(name: str) -> str:
    return f"GO LIVE {name}"


def _load_settings() -> dict:
    try:
        from main import load_configs
        settings, _ = load_configs(None)
        return settings
    except Exception:
        return {}


_last_good_registry: dict = {}


def _load_registry() -> dict:
    """Load the strategy registry, resiliently. A corrupt/locked/half-written
    registry.yaml must NOT 500 every strategy + control endpoint mid-market — fall
    back to the last good copy (or empty) and keep the dashboard alive."""
    global _last_good_registry
    import yaml
    from src.api.capital import apply_overrides
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            reg = yaml.safe_load(f).get("strategies", {}) or {}
        reg = apply_overrides(reg)
        _last_good_registry = reg
        return reg
    except Exception as e:
        try:
            from src.utils.logger import setup_logger
            setup_logger("api.server").error(f"registry load failed: {e}; using last-good copy")
        except Exception:
            pass
        return dict(_last_good_registry)


def _audit_live(name: str, cap) -> None:
    """Append a durable, on-disk record of every real-money LIVE activation, so the
    arm/confirm trail survives a restart (in-memory logs are a 500-deep deque)."""
    try:
        import os, json
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        os.makedirs("logs", exist_ok=True)
        with open(os.path.join("logs", "live_activations.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now(ist).isoformat(),
                                "strategy": name, "capital_rs": cap,
                                "event": "CONFIRM_LIVE"}) + "\n")
    except Exception:
        pass


def create_app():
    if not _FASTAPI:
        raise ImportError("FastAPI not installed. Run: pip install fastapi uvicorn[standard]")

    # Prefer IPv4 for all outbound connections so real orders egress over the
    # steadier whitelisted IPv4 (Windows rotates its temporary IPv6, which would
    # otherwise break Kite's IP-whitelist mid-week). Disable with PREFER_IPV4=0.
    try:
        from src.utils.net import prefer_ipv4_from_env
        if prefer_ipv4_from_env(default=True):
            print("  ✓ Outbound network set to prefer IPv4 (stable Kite IP-whitelist match).")
    except Exception:
        pass

    app    = FastAPI(title="Sa-Ra-L Unified Control", version="1.0.0")
    # Restrict CORS to the local dashboard only — control/stop/arm endpoints place
    # REAL orders and must not be reachable cross-origin from a malicious page.
    _port = os.environ.get("SARAL_PORT", "8000")
    _allowed = [f"http://localhost:{_port}", f"http://127.0.0.1:{_port}"]
    for extra in (os.environ.get("SARAL_ALLOWED_ORIGINS", "").split(",")):
        if extra.strip():
            _allowed.append(extra.strip())
    app.add_middleware(CORSMiddleware, allow_origins=_allowed,
                       allow_methods=["*"], allow_headers=["*"])

    app.state.started_ist = datetime.now(IST)
    # Durable, greppable proof of when the platform actually booted (independent of
    # the in-memory started_ist) — so "did I start before 09:15?" is always answerable
    # from logs/startup.log, not memory.
    try:
        os.makedirs("logs", exist_ok=True)
        with open("logs/startup.log", "a", encoding="utf-8") as _bf:
            _bf.write(f"{app.state.started_ist.strftime('%Y-%m-%d %H:%M:%S')} IST  "
                      f"Sa-Ra-L server boot (pid {os.getpid()})\n")
    except Exception:
        pass
    # Note if the platform is started after the open — ORB / opening-range strategies
    # (e.g. ATM_PULSE_BURST) now RECONSTRUCT the morning opening range from today's
    # already-elapsed market bars on startup, so they still trade a late-start session.
    _st = app.state.started_ist
    if _st.weekday() < 5 and (_st.hour, _st.minute) > (9, 15) and (_st.hour < 15 or (_st.hour == 15 and _st.minute <= 30)):
        print(f"  ℹ Started {_st.strftime('%H:%M')} — after the 09:15 open. Opening-range "
              f"strategies (ATM_PULSE_BURST) will reconstruct the morning range from today's "
              f"market bars (needs a data feed); start before 09:15 to build it live.")

    multi  = get_multi_state()
    runner = ApiPortfolioRunner(registry_path=REGISTRY_PATH, settings=_load_settings(),
                                multi=multi)
    app.state.runner = runner
    app.state.arm_tokens: dict[str, dict] = {}      # name -> {token, expires}

    # ── WebSocket manager + broadcast loop (reused pattern) ───────────────────
    class ConnectionManager:
        def __init__(self): self.active: list[WebSocket] = []
        async def connect(self, ws):
            await ws.accept(); self.active.append(ws)
        def disconnect(self, ws):
            if ws in self.active: self.active.remove(ws)
        async def broadcast(self, data: dict):
            payload = json.dumps(data, default=str); dead = []
            for ws in self.active:
                try: await ws.send_text(payload)
                except Exception: dead.append(ws)
            for ws in dead: self.disconnect(ws)
    manager = ConnectionManager()

    async def _broadcast_loop():
        while True:
            for event in multi.pop_ws_events(200):
                await manager.broadcast(event)
            await asyncio.sleep(0.05)

    def _try_enable_kite():
        """Best-effort: give the dashboard a Kite connection so charts/VIX use the
        real (reliable) feed instead of flaky yfinance intraday. Non-fatal — on any
        failure charts simply fall back to yfinance."""
        try:
            from src.data import kite_historical
            if not kite_historical.is_enabled():
                if kite_historical.enable(runner.settings):
                    print("  ✓ Dashboard connected to Kite — live charts + VIX enabled.")
                else:
                    print("  [i] Kite not available to the dashboard — charts use yfinance "
                          "(intraday may be sparse). Run 'python main.py --mode autologin' "
                          "for a fresh token, then restart.")
        except Exception as e:
            print(f"  [i] Kite chart feed unavailable: {str(e)[:100]}")

    # Always-on market feed: pushes live ticks + charts into every running strategy.
    from src.api.market_feed import MarketFeed
    app.state.market_feed = MarketFeed(multi, runner)

    # Bots: News Desk (inbound) + Trade Signals (outbound publishing gateway).
    from src.api.telegram_bots import get_news_desk, get_signal_bot
    app.state.news_desk = get_news_desk(runner.settings)
    app.state.signal_bot = get_signal_bot(runner.settings)

    async def _supervisor_loop():
        """Every 30s, restart any strategy that crashed mid-session (paper-only, capped)
        so a transient error doesn't silently kill it for the rest of the day."""
        while True:
            await asyncio.sleep(30)
            try:
                restarted = await asyncio.to_thread(runner.supervise)
                if restarted:
                    print(f"  [supervisor] auto-restarted after crash: {', '.join(restarted)}")
            except Exception as e:
                print(f"  [supervisor] pass failed: {str(e)[:100]}")

    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(_broadcast_loop())
        asyncio.create_task(_supervisor_loop())
        # Connect to Kite off the event loop so a slow probe can't delay startup,
        # THEN start the market feed (so it has the Kite connection if available).
        async def _boot_feed():
            await asyncio.to_thread(_try_enable_kite)
            app.state.market_feed.start()
            # Auto-start all eligible strategies (policy-driven; PAPER-active by
            # default — real orders still need per-session arm/confirm). No manual push.
            try:
                started = await asyncio.to_thread(runner.autostart_from_registry)
                live = [s["name"] for s in started if s.get("decision", {}).get("mode") == "live"]
                print(f"  ✓ Auto-started {len(started)} strateg(ies)"
                      + (f"; LIVE: {', '.join(live)}" if live else " (all paper-active)"))
            except Exception as e:
                print(f"  [i] autostart skipped: {str(e)[:100]}")
        asyncio.create_task(_boot_feed())
        try:
            app.state.news_desk.start()    # no-op if not configured/enabled
        except Exception as e:
            print(f"  [i] News Desk not started: {str(e)[:100]}")
        try:
            app.state.signal_bot.start()   # spin up the delivery queue/worker
        except Exception as e:
            print(f"  [i] Signal bot not started: {str(e)[:100]}")

    @app.on_event("shutdown")
    async def _shutdown():
        for svc in ("market_feed", "news_desk", "signal_bot"):
            try:
                getattr(app.state, svc).stop()
            except Exception:
                pass

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _strategy_list_item(name: str, cfg: dict) -> dict:
        from src.api.readiness import check_readiness
        runtime = runner.runtime_status(name)
        try:
            readiness = check_readiness(name, cfg, runtime, multi)
            r_summary = {"overall": readiness["overall"],
                         "backtest_ok": readiness["backtest_ok"],
                         "backfill_ok": readiness["backfill_ok"],
                         "ticks_ok": readiness["ticks_ok"],
                         "config_audit_ok": readiness.get("config_audit_ok")}
        except Exception as e:
            r_summary = {"overall": "UNKNOWN", "error": str(e)[:100]}
        # Precise runtime class (default-live policy: never a bare "Blind").
        status_class = status_label = live_eligible = None
        try:
            from src.api.operating_policy import load_policy, classify_status, _live_gate
            from src.api import telemetry as _tel
            cyc = _tel.cycles_today(name)
            status_class, status_label = classify_status(name, cfg, runtime, cyc, load_policy(), "")
            if cfg.get("status") == "live":
                live_eligible = (_live_gate(name, cfg) is None)
        except Exception:
            pass
        return {
            "name": name,
            "full_name": cfg.get("full_name", name),
            "strategy_type": cfg.get("type") or cfg.get("strategy_type"),
            "status": cfg.get("status"),
            "instruments": cfg.get("instruments", []),
            "risk_profile": cfg.get("risk_profile"),
            "capital_allocated_rs": cfg.get("capital_allocated_rs", 0),
            "capital_target_rs": cfg.get("capital_target_rs", 0),
            "capital_overridden": cfg.get("capital_overridden", False),
            "runtime": runtime,
            "readiness": r_summary,
            "status_class": status_class,
            "status_label": status_label,
            "live_eligible": live_eligible,
            "live_blocked": bool(cfg.get("live_blocked")),
        }

    # ── REST: strategies ──────────────────────────────────────────────────────
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "ts": datetime.now(IST).isoformat()}

    @app.get("/api/version")
    async def version():
        """The JS bundle this server is serving. The frontend compares it to the
        bundle it actually loaded and warns the operator if they differ (the
        running server is older than the page) — the recurring 'restart' footgun."""
        bundle = None
        try:
            mf = os.path.join(STATIC_DIR, "asset-manifest.json")
            if os.path.isfile(mf):
                with open(mf, encoding="utf-8") as f:
                    bundle = (json.load(f).get("files", {}) or {}).get("main.js")
        except Exception:
            pass
        return {"bundle": bundle, "ts": datetime.now(IST).isoformat()}

    @app.get("/api/strategies")
    async def strategies():
        reg = _load_registry()
        def _build():
            return [_strategy_list_item(n, c) for n, c in reg.items()]
        try:
            # Offload the (possibly network-bound) readiness checks off the event loop
            # with a hard timeout so /api/strategies (polled every few sec) can never
            # freeze the whole dashboard during market hours.
            return await asyncio.wait_for(asyncio.to_thread(_build), timeout=25)
        except Exception:
            # Degrade gracefully — return basics without readiness rather than hang.
            return [{
                "name": n, "full_name": c.get("full_name", n),
                "strategy_type": c.get("type") or c.get("strategy_type"),
                "status": c.get("status"), "instruments": c.get("instruments", []),
                "risk_profile": c.get("risk_profile"),
                "capital_allocated_rs": c.get("capital_allocated_rs", 0),
                "capital_target_rs": c.get("capital_target_rs", 0),
                "capital_overridden": c.get("capital_overridden", False),
                "runtime": runner.runtime_status(n),
                "readiness": {"overall": "UNKNOWN", "note": "readiness check slow — retrying"},
            } for n, c in reg.items()]

    @app.get("/api/strategy/{name}/snapshot")
    async def strategy_snapshot(name: str):
        snap = multi.snapshot(name)
        if snap is None:
            # not yet running — return an empty-but-valid shell
            return {"session": {}, "ticks": {}, "scenarios": {}, "open_trades": [],
                    "closed_trades": [], "indicators": {}, "narrator": {},
                    "pending_signals": {}, "log_lines": [], "running": False}
        snap["running"] = runner.is_running(name)
        return snap

    @app.get("/api/strategy/{name}/readiness")
    async def strategy_readiness(name: str):
        from src.api.readiness import check_readiness
        reg = _load_registry()
        if name not in reg:
            raise HTTPException(404, f"Unknown strategy {name}")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(check_readiness, name, reg[name],
                                  runner.runtime_status(name), multi),
                timeout=25)
        except Exception:
            return {"name": name, "overall": "UNKNOWN",
                    "note": "readiness check timed out — data source slow"}

    @app.get("/api/strategy/{name}/backtest-summary")
    async def strategy_backtest_summary(name: str):
        from src.api.backtests import load_summary
        reg = _load_registry()
        if name not in reg:
            raise HTTPException(404, f"Unknown strategy {name}")
        return load_summary(name, reg[name])

    @app.get("/api/backtests")
    async def backtests():
        from src.api.backtests import aggregate_summaries
        return aggregate_summaries(_load_registry())

    @app.get("/api/strategy-audit")
    async def strategy_audit():
        from src.api.strategy_audit import audit_all
        return audit_all(_load_registry())

    @app.get("/api/equities/watchlist")
    async def equities_watchlist(limit: int = 15):
        """Intraday equity scanner — ranked NSE watchlist by ORB/VWAP/volume/RSI.
        Runs the (network) scan off the event loop; returns an honest empty-state
        with status='no_data' when no live intraday data is available."""
        from src.api.equity_scanner import scan_equities, kite_then_yf_fetch
        # Prefer the live Kite feed (real-time) over delayed yfinance — reuse the
        # runner's shared read-only quote broker if a session exists.
        broker = None
        try:
            broker = app.state.runner._paper_quote_broker()
        except Exception:
            broker = None
        if broker is not None and hasattr(broker, "get_equity_intraday_bars"):
            fetch = kite_then_yf_fetch(broker)
            label = "Kite intraday (live, real-time) · yfinance fallback"
        else:
            fetch = None
            label = "yfinance intraday (SYMBOL.NS, ~15-min delayed)"
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(scan_equities, None, limit, fetch, label), timeout=45)
        except Exception as e:
            return {"market_basis": "NSE", "status": "error", "watchlist": [],
                    "note": f"scan failed: {str(e)[:140]}", "returned": 0}

    @app.get("/api/equities/snapshots")
    async def equities_snapshots():
        """Dates that have a saved scan snapshot (newest first)."""
        from src.api.equity_scanner import list_snapshots
        return {"dates": list_snapshots()}

    @app.get("/api/equities/followup")
    async def equities_followup(date: str | None = None):
        """Grade a saved day's intraday picks against the next trading day's real
        close (momentum carry-over). Defaults to the most recent snapshot. Runs
        the (network) daily fetch off the event loop."""
        from src.api.equity_scanner import followup_analysis
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(followup_analysis, date), timeout=45)
        except Exception as e:
            return {"status": "error", "picks": [],
                    "note": f"follow-up failed: {str(e)[:140]}"}

    @app.get("/api/net-backtest")
    async def net_backtest():
        from src.api.net_backtest import build_net_backtest
        return build_net_backtest(_load_registry())

    def _do_net_backtest(frm, to, source):
        from datetime import datetime
        from types import SimpleNamespace
        period = f"{frm} → {to}" if (frm and to) else "configured / full history"
        app.state.net_bt_status = {"state": "running", "period": period,
                                   "source": source, "started_at": datetime.now(IST).isoformat()}
        try:
            from main import run_backtest_all
            args = SimpleNamespace(source=source or "kite", date_from=frm, date_to=to,
                                   futures_volume=True)
            run_backtest_all(args)   # runs every strategy over the period, writes the net report
            app.state.net_bt_status = {"state": "done", "period": period, "source": source,
                                       "finished_at": datetime.now(IST).isoformat()}
        except Exception as e:
            app.state.net_bt_status = {"state": "error", "period": period, "error": str(e)[:200]}

    @app.post("/api/net-backtest/run")
    async def net_backtest_run(request: Request):
        import threading, re as _re
        body = await request.json() if await _has_body(request) else {}
        frm = (body.get("from") or "").strip() or None
        to  = (body.get("to") or "").strip() or None
        source = (body.get("source") or "kite").strip()
        _iso = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm or to):
            if not (frm and to and _iso.match(frm) and _iso.match(to)):
                raise HTTPException(400, "Provide both from and to as YYYY-MM-DD (or neither for full history).")
            if frm > to:
                raise HTTPException(400, "'from' must be on or before 'to'.")
        cur = getattr(app.state, "net_bt_status", {})
        if cur.get("state") == "running":
            return {"started": False, "reason": "a net backtest is already running"}
        t = threading.Thread(target=_do_net_backtest, args=(frm, to, source),
                             name="net-bt", daemon=True)
        t.start()
        return {"started": True, "period": (f"{frm} → {to}" if frm else "full history"), "source": source}

    @app.get("/api/net-backtest/status")
    async def net_backtest_status():
        return getattr(app.state, "net_bt_status", {"state": "idle"})

    @app.get("/api/backtest-insights")
    async def backtest_insights():
        """Max-period analysis across all strategies: ranking, per-strategy
        strengths/weaknesses/best-worst scenario, guardrails, and fleet lessons."""
        from src.api.backtest_insights import build_insights
        return await asyncio.to_thread(build_insights, _load_registry())

    @app.get("/api/portfolio-sim")
    async def portfolio_sim(capital: float = Query(100000)):
        """Fixed-pot portfolio simulation: how every strategy performs on a shared
        capital (default Rs.1L), with per-strategy TRUST flags and a verified-only
        rollup so mixed-quality backtests aren't blended into one misleading number."""
        from src.api.portfolio_sim import simulate_portfolio
        return await asyncio.to_thread(simulate_portfolio, _load_registry(), capital)

    @app.get("/api/portfolio-sim/export")
    async def portfolio_sim_export(capital: float = Query(100000)):
        from src.api.portfolio_sim import simulate_portfolio, to_markdown
        from fastapi.responses import PlainTextResponse
        sim = await asyncio.to_thread(simulate_portfolio, _load_registry(), capital)
        return PlainTextResponse(to_markdown(sim))

    @app.get("/api/portfolio-sim/multiyear")
    async def portfolio_sim_multiyear(capital: float = Query(100000), years: int = Query(20)):
        """Multi-year, capital-aware sim: fixed start capital, per-trade size = fraction
        of CURRENT capital (dies at 0, no refill), annual profit withdrawal. Self-flags
        rows where compounding MODELLED premiums has produced an unrealistic magnitude."""
        from src.api.portfolio_sim import simulate_multiyear
        return await asyncio.to_thread(simulate_multiyear, _load_registry(), capital, years)

    @app.get("/api/portfolio-sim/multiyear/export")
    async def portfolio_sim_multiyear_export(capital: float = Query(100000), years: int = Query(20)):
        from src.api.portfolio_sim import simulate_multiyear, to_markdown_multiyear
        from fastapi.responses import PlainTextResponse
        sim = await asyncio.to_thread(simulate_multiyear, _load_registry(), capital, years)
        return PlainTextResponse(to_markdown_multiyear(sim))

    @app.get("/api/forward-analysis")
    async def forward_analysis(focus: str = Query(None)):
        """Forward-paper evidence from the days we actually ran — the ONLY evidence for
        OI/no-order-path strategies (RAMS, ATM_PULSE, TREND_RIDER). Aggregates cycles,
        trades, no-trade reasons, and the nearest-miss (peak score vs threshold) across
        every session with logs. Pass ?focus=A,B,C to restrict to specific strategies."""
        from src.api.forward_analysis import analyze_forward
        names = [s.strip() for s in focus.split(",")] if focus else None
        return await asyncio.to_thread(analyze_forward, _load_registry(), names)

    @app.get("/api/activity")
    async def activity(limit: int = Query(300), category: str = Query(None)):
        """One consolidated live feed of EVERY strategy's logs/signals/trade-calls,
        tagged by strategy and sorted by time — so the operator has a single window
        for all activity instead of digging into each strategy."""
        lines = []
        running = 0
        for name in multi.names():            # strategies that have a state slot
            try:
                st = multi.get(name)
                if runner.is_running(name):
                    running += 1
                for ln in list(st.log_lines):
                    lines.append({"ts": ln.get("ts", ""), "category": ln.get("category", ""),
                                  "message": ln.get("message", ""), "strategy": name})
            except Exception:
                continue
        if category and category.upper() != "ALL":
            cu = category.upper()
            lines = [l for l in lines if (l["category"] or "").upper() == cu]
        lines.sort(key=lambda x: x["ts"])
        # trade-calls = TRADE/SIGNAL/ANALYSIS lines, for the "any calls today?" view
        calls = [l for l in lines if (l["category"] or "").upper() in ("TRADE", "SIGNAL", "ANALYSIS")]
        return {"lines": lines[-limit:], "trade_calls": calls[-limit:],
                "running": running, "total_lines": len(lines)}

    @app.post("/api/strategy/{name}/capital")
    async def set_capital(name: str, request: Request):
        """Adjust the capital a strategy may trade with (persisted locally). Does
        NOT start/stop anything; live trading still requires the arm+confirm guard."""
        from src.api.capital import set_override
        if name not in _load_registry():
            raise HTTPException(404, f"Unknown strategy {name}")
        body = await request.json() if await _has_body(request) else {}
        try:
            result = set_override(name, body.get("capital_allocated_rs"))
        except ValueError as e:
            raise HTTPException(400, str(e))
        multi.get(name).add_log("CAPITAL", f"Capital set to Rs.{result['capital_allocated_rs']:,.0f}")
        return result

    # ── One-click backtest (runs in a background thread) ──────────────────────
    app.state.bt_status = {}     # name -> {state, started_at, finished_at, error}

    def _do_backtest(name: str, frm: str = None, to: str = None, source: str = None):
        from datetime import datetime
        period = f"{frm} → {to}" if (frm and to) else "configured period"
        app.state.bt_status[name] = {"state": "running", "period": period,
                                     "source": source or "default",
                                     "started_at": datetime.now(IST).isoformat()}
        try:
            from main import load_configs, run_backtest, run_backtest_1min
            settings, scfg = load_configs(name)
            # Custom period from the UI: override the strategy's configured window.
            if frm and to:
                scfg.setdefault("backtest", {})
                scfg["backtest"]["start_date"] = frm
                scfg["backtest"]["end_date"] = to
            # Old periods need Kite deep history (yfinance only serves ~60 days of
            # intraday). Enabling it also flips the engine's 60-day clamp off.
            if (source or "").lower() == "kite":
                try:
                    from src.data import kite_historical
                    kite_historical.enable(settings)
                except Exception as e:
                    multi.get(name).add_log("BACKTEST", f"Kite deep-history enable failed: {str(e)[:80]}")
            stype = scfg.get("strategy_type", "")
            multi.get(name).add_log("BACKTEST", f"Backtest started — {period} ({source or 'default'} source).")
            if stype == "trap_cmcd":
                # Dedicated high-fidelity trap backtester (real spot 3m + BS-priced
                # options + full costs). The generic engine has no trap_cmcd variant
                # and would silently run a DEFAULT strategy (mislabelled) — see
                # docs/TRAP_CMCD_ARCHITECTURE.md. Instrument (Nifty/Sensex) and the
                # results dir follow the strategy's own config.
                from src.backtest.trap_backtest import run_trap_backtest, NIFTY, SENSEX
                from src.live.trap_cmcd_live import TrapParams
                bt = scfg.get("backtest", {})
                tp = scfg.get("trap_cmcd", {})
                params = TrapParams(**{k: tp[k] for k in tp if k in TrapParams.__dataclass_fields__})
                inst = SENSEX if str(scfg.get("underlying", "NIFTY")).upper() == "SENSEX" else NIFTY
                run_trap_backtest(bt.get("start_date", "2025-01-01"),
                                  bt.get("end_date", "2026-08-10"),
                                  inst=inst, params=params, expiry_only=params.expiry_only,
                                  results_dir=scfg.get("results_dir", f"strategies/{name}/results"),
                                  write=True, verbose=False)
            elif stype == "1min_confluence":
                run_backtest_1min(scfg, name)
            else:
                run_backtest(scfg, name)
            app.state.bt_status[name] = {"state": "done", "period": period,
                                         "source": source or "default",
                                         "finished_at": datetime.now(IST).isoformat()}
            multi.get(name).add_log("BACKTEST", f"Backtest complete ({period}) — summary updated.")
        except Exception as e:
            app.state.bt_status[name] = {"state": "error", "period": period, "error": str(e)[:200]}
            multi.get(name).add_log("BACKTEST", f"Backtest failed: {str(e)[:120]}")

    @app.post("/api/strategy/{name}/run-backtest")
    async def run_backtest_endpoint(name: str, request: Request):
        import threading
        import re as _re
        if name not in _load_registry():
            raise HTTPException(404, f"Unknown strategy {name}")
        body = await request.json() if await _has_body(request) else {}
        frm = (body.get("from") or "").strip() or None
        to  = (body.get("to") or "").strip() or None
        source = (body.get("source") or "").strip() or None
        # Validate the optional custom period.
        _iso = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm or to):
            if not (frm and to and _iso.match(frm) and _iso.match(to)):
                raise HTTPException(400, "Provide both from and to as YYYY-MM-DD.")
            if frm > to:
                raise HTTPException(400, "'from' must be on or before 'to'.")
            # Deep-history intraday backtests are only meaningful from Kite.
            if not source:
                source = "kite"
        cur = app.state.bt_status.get(name, {})
        if cur.get("state") == "running":
            return {"name": name, "started": False, "reason": "already running"}
        t = threading.Thread(target=_do_backtest, args=(name, frm, to, source),
                             name=f"bt-{name}", daemon=True)
        t.start()
        return {"name": name, "started": True,
                "period": (f"{frm} → {to}" if frm else "configured"), "source": source or "default"}

    @app.get("/api/strategy/{name}/backtest-status")
    async def backtest_status(name: str):
        return app.state.bt_status.get(name, {"state": "idle"})

    @app.get("/api/strategy/{name}/equity-curve")
    async def equity_curve(name: str):
        reg = _load_registry()
        if name not in reg:
            raise HTTPException(404, f"Unknown strategy {name}")
        rdir = reg[name].get("results_dir") or f"strategies/{name}/results/"
        if os.path.isdir(rdir):
            pngs = sorted([f for f in os.listdir(rdir)
                           if f.endswith(".png") and "equity" in f.lower()])
            if pngs:
                return FileResponse(os.path.join(rdir, pngs[0]))
        raise HTTPException(404, "No equity curve yet — run the backtest first.")

    @app.get("/api/daily-analysis")
    async def daily_analysis():
        from src.api.daily_analysis import build_daily_analysis
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(build_daily_analysis, _load_registry(), multi, runner),
                timeout=25)
        except Exception:
            return {"premarket": {}, "strategies": [], "trades": [],
                    "note": "daily analysis timed out — data source slow"}

    @app.get("/api/premarket")
    async def premarket(force: bool = Query(False)):
        from src.api.premarket import build_premarket
        # run the (potentially slow, network-bound) fetch off the event loop
        return await asyncio.to_thread(build_premarket, force)

    @app.get("/api/market/summary")
    async def market_summary():
        from src.api.market import get_market_summary
        try:
            return await asyncio.wait_for(asyncio.to_thread(get_market_summary), timeout=15)
        except Exception:
            return {"source": "none"}

    @app.get("/api/market/internals")
    async def market_internals():
        """Live option-chain internals (PCR / Max Pain) for the Pre-Market page's
        'Live Market' section — separate from the frozen pre-market briefing."""
        from src.api.market import get_live_internals
        try:
            return await asyncio.wait_for(asyncio.to_thread(get_live_internals), timeout=15)
        except Exception:
            return {"pcr": None, "max_pain": None, "source": "none"}

    @app.get("/api/daily-closure")
    async def daily_closure(day: str = Query(None)):
        """End-of-day closure report + no-trade audit (UI_FE_Pg2 PART X).
        Pass ?day=YYYY-MM-DD to view a past session (defaults to today)."""
        from src.api.closure_report import build_closure_report
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(build_closure_report, _load_registry(), multi, runner, day),
                timeout=25)
        except Exception as e:
            return {"error": f"closure report failed: {str(e)[:160]}"}

    @app.get("/api/daily-closure/dates")
    async def daily_closure_dates():
        """Dates that have a trade log or analysis trail (for the date picker)."""
        import glob, os, re
        days = set()
        for p in glob.glob("logs/trades_*.csv"):
            m = re.search(r"trades_(\d{4}-\d{2}-\d{2})\.csv", p)
            if m:
                days.add(m.group(1))
        for p in glob.glob("logs/dashboard/*_*.jsonl"):
            m = re.search(r"_(\d{4}-\d{2}-\d{2})\.jsonl$", os.path.basename(p))
            if m:
                days.add(m.group(1))
        return {"dates": sorted(days, reverse=True)}

    @app.get("/api/daily-closure/export")
    async def daily_closure_export(format: str = Query("markdown"), day: str = Query(None)):
        from src.api.closure_report import build_closure_report, to_markdown, to_csv
        from fastapi.responses import PlainTextResponse
        rep = await asyncio.to_thread(build_closure_report, _load_registry(), multi, runner, day)
        if format == "csv":
            return PlainTextResponse(to_csv(rep), media_type="text/csv",
                                     headers={"Content-Disposition": f"attachment; filename=closure_{rep['date']}.csv"})
        if format == "json":
            import json as _json
            return PlainTextResponse(
                _json.dumps(rep, indent=2, default=str), media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename=closure_{rep['date']}.json"})
        return PlainTextResponse(to_markdown(rep), media_type="text/markdown",
                                 headers={"Content-Disposition": f"attachment; filename=closure_{rep['date']}.md"})

    @app.get("/api/autostart-report")
    async def autostart_report():
        """Why each strategy did/didn't auto-start, plus current run state — the
        one-shot answer to 'why is X not running?'."""
        reg = _load_registry()
        decisions = {d["name"]: d for d in getattr(runner, "last_autostart", []) if d.get("name")}
        rows = []
        for name in reg:
            rt = runner.runtime_status(name)
            dec = decisions.get(name, {}).get("decision", {})
            rows.append({
                "name": name, "status": reg[name].get("status"),
                "running": rt.get("running"), "state": rt.get("state"),
                "error": rt.get("error") or "",
                "autostart_mode": (decisions.get(name, {}) or {}).get("mode"),
                "autostart_reason": dec.get("reason") or
                    ("skipped (paused/archived)" if name not in decisions else ""),
            })
        return {"rows": rows}

    @app.get("/api/regime/current")
    async def regime_current():
        """Live market regime per index (Phase 2, observe-only)."""
        from src.api import regime
        try:
            return await asyncio.wait_for(asyncio.to_thread(regime.current, multi), timeout=10)
        except Exception as e:
            return {"error": str(e)[:120], "indices": {}}

    @app.get("/api/weekly-review")
    async def weekly_review(sessions: int = Query(20)):
        from src.api.weekly_review import build_weekly_review
        try:
            return await asyncio.wait_for(asyncio.to_thread(build_weekly_review, sessions), timeout=15)
        except Exception as e:
            return {"per_strategy": [], "note": str(e)[:120]}

    @app.get("/api/scoreboard")
    async def scoreboard(window: int = Query(20)):
        from src.api.scoreboard import build_scoreboard
        try:
            return await asyncio.wait_for(asyncio.to_thread(build_scoreboard, window), timeout=15)
        except Exception as e:
            return {"strategies": [], "book": [], "note": str(e)[:120]}

    @app.get("/api/preflight")
    async def preflight():
        """Pre-open self-check for the dashboard one-glance (GO / NO-GO)."""
        from src.api.preflight import build_preflight
        started = getattr(app.state, "started_ist", None)   # real app boot time, not view time
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(build_preflight, runner.settings, started), timeout=20)
        except Exception as e:
            return {"verdict": "UNKNOWN", "checks": [], "strategies": [],
                    "blockers": [f"preflight failed: {str(e)[:140]}"], "cautions": []}

    @app.get("/api/session-status")
    async def session_status():
        """Session timing health — lets the dashboard warn about a late (partial)
        start that starves opening-range strategies of their morning data."""
        st = getattr(app.state, "started_ist", None) or datetime.now(IST)
        now = datetime.now(IST)
        weekday = now.weekday() < 5
        open_min = 9 * 60 + 15
        start_min = st.hour * 60 + st.minute
        in_session = weekday and open_min <= (now.hour * 60 + now.minute) <= (15 * 60 + 30)
        # Late = started on a weekday after the open but before close.
        late = bool(weekday and start_min > open_min and start_min <= (15 * 60 + 30))
        return {
            "started_at": st.strftime("%Y-%m-%d %H:%M:%S"),
            "now": now.strftime("%H:%M:%S"),
            "started_after_open": late,
            "minutes_after_open": max(0, start_min - open_min) if late else 0,
            "in_session": in_session,
            "note": ("Started after the 09:15 open — opening-range strategies "
                     "(ATM_PULSE_BURST) reconstruct the morning range from today's "
                     "market bars on startup, so they still trade; start before 09:15 "
                     "to build it live." if late else ""),
        }

    @app.get("/api/portfolio/risk")
    async def portfolio_risk():
        """Observe-only book risk: net Greeks, exposure, premium-at-risk, stress, VaR."""
        from src.api.portfolio_risk import compute_portfolio_risk
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(compute_portfolio_risk, multi, runner), timeout=15)
        except Exception as e:
            return {"positions": 0, "legs": [], "note": str(e)[:160]}

    @app.post("/api/eod/run")
    async def eod_run(day: str = Query(None)):
        """Commit trust + snapshot the graded session (Phase 2 EOD job)."""
        from src.api.eod_jobs import run_eod
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(run_eod, _load_registry(), multi, runner, day), timeout=40)
        except Exception as e:
            return {"error": f"eod commit failed: {str(e)[:160]}"}

    @app.get("/api/trust/{name}")
    async def trust_history(name: str, n: int = Query(20)):
        from src.api.trust import history
        return {"strategy": name, "history": history(name, n)}

    @app.post("/api/telemetry/{name}/demote")
    async def telemetry_demote(name: str, request: Request):
        from src.api import telemetry
        body = await request.json() if await _has_body(request) else {}
        telemetry.demote(name, body.get("reason", "operator demotion"))
        return {"name": name, "demoted": True}

    @app.post("/api/telemetry/{name}/clear")
    async def telemetry_clear(name: str):
        from src.api import telemetry
        telemetry.clear_demoted(name)
        return {"name": name, "demoted": False}

    @app.get("/api/bots/status")
    async def bots_status():
        """Health + delivery/inbound summary for both bots (bot spec Part 9)."""
        sb = getattr(app.state, "signal_bot", None)
        nd = getattr(app.state, "news_desk", None)
        out = {
            "signals": {"enabled": bool(sb and sb.enabled),
                        "config": (sb.config_view() if sb else {}),
                        "delivery": (sb.store.summary() if (sb and sb.store) else {})},
            "news_desk": {"enabled": bool(nd and nd.enabled),
                          "config": (nd.config_view() if nd else {}),
                          "inbound_today": len(getattr(nd, "impacts", []) or [])},
        }
        # Loud cross-wiring warning so a swapped/duplicate token is obvious.
        if sb and getattr(sb, "_conflict", ""):
            out["warning"] = sb._conflict
        try:
            from src.api.bot_core import daily_bot_summary
            if sb and sb.store:
                out["daily"] = daily_bot_summary(sb.store)
        except Exception:
            pass
        return out

    @app.get("/api/bots/outbound")
    async def bots_outbound():
        sb = getattr(app.state, "signal_bot", None)
        return {"records": (sb.store.all() if (sb and sb.store) else [])}

    @app.get("/api/bots/inbound")
    async def bots_inbound():
        nd = getattr(app.state, "news_desk", None)
        return {"impacts": list(reversed(getattr(nd, "impacts", []) or []))}

    @app.get("/api/bots/audit")
    async def bots_audit(category: str = Query(None), limit: int = Query(200)):
        from src.api.bot_core import read_audit
        return {"entries": list(reversed(read_audit(category=category, limit=limit)))}

    @app.get("/api/context")
    async def context_effective():
        """Current advisory strategy-context adjustments (News Desk → engine, gated)."""
        from src.api.strategy_context import get_context
        ctx = get_context()
        return {"effective": ctx.effective(), "all": ctx.all()}

    @app.post("/api/context/{adj_id}/clear")
    async def context_clear(adj_id: str):
        from src.api.strategy_context import get_context
        return {"cleared": get_context().clear(adj_id)}

    @app.get("/api/news")
    async def news(limit: int = Query(20)):
        """News-desk (Bot 1) impact analyses — also surfaced in the Market-News panel."""
        try:
            return {"items": app.state.news_desk.store.recent(limit),
                    "enabled": app.state.news_desk.enabled}
        except Exception:
            return {"items": [], "enabled": False}

    @app.get("/api/market/{instrument}/chart")
    async def market_chart(instrument: str, tf: str = Query("5m")):
        from src.api.charts import get_chart
        return get_chart(multi, instrument.upper(), tf)

    @app.get("/api/market/{instrument}/zones")
    async def market_zones(instrument: str, tf: str = Query("15m")):
        """Observe-only GTI demand/supply zones for the chart overlay (read-only,
        no order logic). Defaults to 15m — the only timeframe whose zone edge
        validated (NIFTY + SENSEX, cost-viable) in the research backtests."""
        from src.api.gti_zones_live import compute_zones
        return compute_zones(instrument.upper(), tf)

    # ── Control: run / stop (paper is free; live requires arming) ─────────────
    @app.post("/api/strategy/{name}/run")
    async def run_strategy(name: str, request: Request):
        body = await request.json() if await _has_body(request) else {}
        mode = (body.get("mode") or "paper").lower()
        if mode == "live":
            raise HTTPException(400, "Live mode requires /arm-live then /confirm-live")
        return runner.start_strategy(name, mode="paper")

    @app.post("/api/strategy/{name}/stop")
    async def stop_strategy(name: str):
        return runner.stop_strategy(name)

    @app.post("/api/control")
    async def control(request: Request):
        body = await request.json() if await _has_body(request) else {}
        cmd  = (body.get("command") or "").lower()
        if cmd == "stop":
            return runner.stop_all()
        raise HTTPException(400, f"Unknown command {cmd!r}")

    # ── Live-order safety guards (Phase 4) ────────────────────────────────────
    @app.post("/api/strategy/{name}/arm-live")
    async def arm_live(name: str):
        if REVIEW_MODE:
            raise HTTPException(403, "Review/demo build — live trading is permanently disabled here.")
        reg = _load_registry()
        if name not in reg:
            raise HTTPException(404, f"Unknown strategy {name}")
        # STATUS GATE (first line of defence): only a strategy the operator has
        # marked `live` may ever be armed. archived / paused / stopped / paper /
        # testing / planned can NEVER go live, regardless of capital.
        st_status = (reg[name].get("status") or "").lower()
        if st_status != "live":
            raise HTTPException(409,
                f"{name} is '{st_status}', not live — only live-status strategies can arm live. "
                f"Set its status to live in the registry first if that's intended.")
        cap = reg[name].get("capital_allocated_rs", 0)
        if not cap or cap <= 0:
            raise HTTPException(400,
                f"{name} has no allocated capital (paper/planned only) — cannot arm live")
        # Operator/structural live exclusions (default-live policy): blocked bugs
        # and shadow-monitor-only strategies can never arm live.
        from src.api.operating_policy import _live_gate
        gate = _live_gate(name, reg[name])
        if gate and "capital" not in gate and "telemetry-demoted" not in gate:
            raise HTTPException(409, f"{name} cannot arm live: {gate}")
        # Telemetry gate (Phase 1): a blind strategy must not go live. Block if it's
        # been demoted for telemetry failure, or is running now with no verifiable
        # analysis trail. (If idle, the heartbeat guarantees a trail once it runs.)
        try:
            from src.api import telemetry as _tel
            if _tel.is_demoted(name):
                raise HTTPException(409,
                    f"{name} is telemetry-demoted (no analysis trail last session) — "
                    f"fix per-cycle analysis emission and clear the demotion before arming live")
            if runner.is_running(name):
                st = multi.get(name) if multi.has(name) else None
                if st is not None and not _tel.telemetry_ok(name, state=st, running=True):
                    raise HTTPException(409,
                        f"{name} is running but emitting NO verifiable analysis trail — "
                        f"refusing to arm live a blind strategy")
        except HTTPException:
            raise
        except Exception:
            pass
        token = secrets.token_urlsafe(8)
        app.state.arm_tokens[name] = {
            "token": token,
            "expires": datetime.now(IST) + timedelta(seconds=ARM_TTL_SECONDS),
        }
        multi.get(name).add_log("LIVE", f"ARM requested (token issued, {ARM_TTL_SECONDS}s TTL)")
        return {
            "name": name, "armed": True, "confirm_token": token,
            "ttl_seconds": ARM_TTL_SECONDS,
            "capital_cap_rs": cap,
            "required_phrase": _live_phrase(name),
            "warning": f"This will place REAL orders for {name} (cap Rs.{cap:,}). "
                       f"Type exactly '{_live_phrase(name)}' to confirm.",
        }

    @app.post("/api/strategy/{name}/confirm-live")
    async def confirm_live(name: str, request: Request):
        if REVIEW_MODE:
            raise HTTPException(403, "Review/demo build — live trading is permanently disabled here.")
        body  = await request.json() if await _has_body(request) else {}
        token = body.get("token", "")
        typed = body.get("typed_confirmation", "")
        rec   = app.state.arm_tokens.get(name)
        if not rec:
            raise HTTPException(400, "Not armed — call /arm-live first")
        if datetime.now(IST) > rec["expires"]:
            app.state.arm_tokens.pop(name, None)
            raise HTTPException(400, "Arm token expired — re-arm")
        if not secrets.compare_digest(token, rec["token"]):
            raise HTTPException(403, "Invalid arm token")
        if typed.strip() != _live_phrase(name):
            raise HTTPException(403, "Confirmation phrase mismatch")
        # Re-validate capital at confirm time (TOCTOU): capital could have been set
        # to 0 between arm and confirm. Re-load and block on <= 0 so a zeroed/removed
        # allocation can never go live on a stale token.
        reg = _load_registry()
        if name not in reg:
            raise HTTPException(404, f"Unknown strategy {name}")
        cap = reg[name].get("capital_allocated_rs", 0)
        if cap is None or cap <= 0:
            app.state.arm_tokens.pop(name, None)
            raise HTTPException(400, f"{name} capital is now Rs.{cap} — re-arm with positive capital")
        app.state.arm_tokens.pop(name, None)
        multi.get(name).add_log("LIVE", f"CONFIRMED — starting LIVE (real orders, cap Rs.{cap:,})")
        _audit_live(name, cap)
        return runner.start_strategy(name, mode="live")

    # ── Live-test: single equity order (e.g. buy 1 Tata Power) ────────────────
    # A minimal, operator-only real-order path to verify broker connectivity end
    # to end WITHOUT running a strategy. Same arm-then-typed-confirm guard as
    # live strategies. Defaults: NSE TATAPOWER · CNC (delivery) · MARKET · qty 1.
    def _equity_phrase(sym: str, qty: int, txn: str) -> str:
        return f"{txn.upper()} {qty} {sym.upper()}"

    @app.post("/api/livetest/equity/arm")
    async def livetest_equity_arm(request: Request):
        body = await request.json() if await _has_body(request) else {}
        params = {
            "symbol":     str(body.get("symbol", "TATAPOWER")).upper(),
            "exchange":   str(body.get("exchange", "NSE")).upper(),
            "transaction": str(body.get("transaction", "BUY")).upper(),
            "quantity":   int(body.get("quantity", 1) or 1),
            "product":    str(body.get("product", "CNC")).upper(),
            "order_type": str(body.get("order_type", "MARKET")).upper(),
            "price":      float(body.get("price", 0) or 0),
        }
        # Sanity caps so a fat-fingered payload can't arm a large order.
        if params["quantity"] <= 0 or params["quantity"] > 5:
            raise HTTPException(400, "Live-test quantity must be 1–5 shares")
        if params["transaction"] != "BUY":
            raise HTTPException(400, "Live-test supports BUY only")
        # Broker must be reachable before we even arm.
        try:
            from src.broker.kite_broker import create_kite_broker
            create_kite_broker(runner.settings)
        except Exception as e:
            raise HTTPException(409, f"Kite not connected — cannot arm live test: {str(e)[:160]}")
        token = secrets.token_urlsafe(8)
        phrase = _equity_phrase(params["symbol"], params["quantity"], params["transaction"])
        app.state.equity_test_token = {
            "token": token, "params": params, "phrase": phrase,
            "expires": datetime.now(IST) + timedelta(seconds=ARM_TTL_SECONDS),
        }
        if multi.has("_market"):
            multi.market().add_log("LIVE", f"EQUITY LIVE-TEST armed: {phrase} {params['product']}/{params['order_type']}")
        return {
            "armed": True, "confirm_token": token, "ttl_seconds": ARM_TTL_SECONDS,
            "params": params, "required_phrase": phrase,
            "warning": f"This places a REAL {params['product']} order: {phrase} at "
                       f"{params['order_type']}"
                       f"{' (as a marketable limit ~1% through LTP — Zerodha blocks naked API market orders)' if params['order_type'] == 'MARKET' else ''}"
                       f". Type exactly '{phrase}' to confirm.",
        }

    @app.post("/api/livetest/equity/confirm")
    async def livetest_equity_confirm(request: Request):
        body  = await request.json() if await _has_body(request) else {}
        token = body.get("token", "")
        typed = body.get("typed_confirmation", "")
        rec   = getattr(app.state, "equity_test_token", None)
        if not rec:
            raise HTTPException(400, "Not armed — call /api/livetest/equity/arm first")
        if datetime.now(IST) > rec["expires"]:
            app.state.equity_test_token = None
            raise HTTPException(400, "Arm token expired — re-arm")
        if not secrets.compare_digest(token, rec["token"]):
            raise HTTPException(403, "Invalid arm token")
        if typed.strip() != rec["phrase"]:
            raise HTTPException(403, "Confirmation phrase mismatch")
        app.state.equity_test_token = None       # single-use
        p = rec["params"]
        try:
            from src.broker.kite_broker import create_kite_broker
            broker = create_kite_broker(runner.settings)
            order_id = broker.place_equity_order(
                tradingsymbol=p["symbol"], exchange=p["exchange"],
                transaction=p["transaction"], quantity=p["quantity"],
                product=p["product"], order_type=p["order_type"], price=p["price"])
        except Exception as e:
            raise HTTPException(502, f"Order placement failed: {str(e)[:180]}")
        if not order_id:
            raise HTTPException(502, "Order rejected by broker (empty order_id) — check funds/margin and market hours")
        if multi.has("_market"):
            multi.market().add_log("LIVE", f"EQUITY LIVE-TEST placed: {rec['phrase']} → order_id {order_id}")
        try:
            from src.api.bot_core import audit
            audit("LIVE_EQUITY_TEST", p["symbol"], "order_placed", "warn",
                  f"{rec['phrase']} {p['product']}/{p['order_type']} order_id={order_id}")
        except Exception:
            pass
        return {"placed": True, "order_id": order_id, "params": p,
                "note": "Real order sent to Zerodha. Verify it in your Kite orderbook."}

    # ── WebSocket ─────────────────────────────────────────────────────────────
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await manager.connect(ws)
        try:
            await ws.send_text(json.dumps({"type": "snapshot_all",
                                           "data": multi.snapshot_all()}, default=str))
            while True:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30)
                if msg == "ping":
                    await ws.send_text('{"type":"pong"}')
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except Exception:
            pass
        finally:
            manager.disconnect(ws)

    # ── Serve React frontend ──────────────────────────────────────────────────
    if os.path.isdir(STATIC_DIR):
        _assets = os.path.join(STATIC_DIR, "static")
        if os.path.isdir(_assets):
            app.mount("/static", StaticFiles(directory=_assets), name="static")

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str):
            fp = os.path.join(STATIC_DIR, full_path)
            if os.path.isfile(fp):
                return FileResponse(fp)
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))
    else:
        @app.get("/")
        async def root():
            return {"message": "Sa-Ra-L Unified API running",
                    "note": "Frontend not built. cd frontend/brahmastra && npm run build",
                    "docs": "/docs"}

    return app


async def _has_body(request) -> bool:
    try:
        body = await request.body()
        return len(body) > 0
    except Exception:
        return False


app = create_app() if _FASTAPI else None


def _port_in_use(host: str, port: int) -> bool:
    import socket
    probe = host if host not in ("0.0.0.0", "") else "127.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            return s.connect_ex((probe, port)) == 0
        except OSError:
            return False


def run_server(host: str = "127.0.0.1", port: int = 8000, autostart: bool = False):
    import uvicorn
    # Friendly pre-flight: a port collision here almost always means the dashboard
    # is already running (or another process holds the port). Explain it clearly
    # instead of letting uvicorn dump a raw WinError 10048 / EADDRINUSE.
    if _port_in_use(host, port):
        print("\n  ╔══════════════════════════════════════════════════════════════╗")
        print(f"  ║  Port {port} is already in use.                               ")
        print("  ╚══════════════════════════════════════════════════════════════╝")
        print(f"      The Sa-Ra-L dashboard is most likely ALREADY running —")
        print(f"      just open  http://localhost:{port}  in your browser.")
        print(f"      Every strategy (incl. BRAHMASTRA) runs INSIDE this one")
        print(f"      dashboard — start them from the Strategies tab, you do NOT")
        print(f"      launch a separate server per strategy.")
        print(f"      To run a SECOND instance on another port instead:")
        print(f"        Windows:  set SARAL_PORT=8001 && python main.py --mode unified")
        print(f"        Linux/Mac: SARAL_PORT=8001 python main.py --mode unified\n")
        return
    if autostart and app is not None:
        app.state.runner.autostart_from_registry()
    print(f"\n  Sa-Ra-L Unified Dashboard → http://localhost:{port}  (API docs: /docs)\n")
    uvicorn.run("src.api.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
