"""
API-Driven Portfolio Runner
───────────────────────────
Wraps the existing `PortfolioRunner` (src/live/portfolio_runner.py) so strategies
can be started/stopped individually from the web API instead of all-at-once from
the CLI. Reuses the base class for registry/config loading, the strategy_type →
engine ladder, and the CSV trade log. Adds:

  • per-strategy start/stop on demand (start_strategy / stop_strategy)
  • a status callback that ALSO writes into the per-strategy BrahmastraState
    (the bridge that powers the rich per-strategy detail view)
  • no Rich terminal UI and no blocking input("YES") prompt — the live arming
    guard lives in the API layer (see server.py / Phase 4)
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta

from src.live.portfolio_runner import PortfolioRunner, StrategyStatus
from src.api.state_registry import MultiStrategyState, get_multi_state

IST = timezone(timedelta(hours=5, minutes=30))


class ApiPortfolioRunner(PortfolioRunner):
    """PortfolioRunner controllable per-strategy via the API."""

    # Throttle for the "always leave an analysis trail" fallback (seconds).
    _ANALYSIS_MIN_GAP_S = 45.0

    def __init__(self, registry_path: str = "strategies/registry.yaml",
                 settings: dict = None, multi: MultiStrategyState = None):
        super().__init__(registry_path=registry_path, settings=settings)
        self.multi = multi or get_multi_state()
        self._init_csv()
        # Strategies the OPERATOR stopped — self-recovery must never restart these
        # (so auto-recovery can't fight a STOP ALL or a deliberate stop).
        self._operator_stopped: set[str] = set()
        # Last time we logged an ANALYSIS line per strategy (monotonic seconds), so
        # a running-but-quiet engine still leaves a throttled analysis trail and is
        # never falsely flagged "telemetry broken".
        self._last_analysis_log: dict[str, float] = {}
        # Crash self-heal: how many times we've auto-restarted each strategy this
        # session. Capped (see supervise()) so a persistently-crashing engine doesn't
        # thrash — after the cap we give up loudly instead of silently dying.
        self._restart_budget: dict[str, int] = {}
        self._restart_mode:   dict[str, str] = {}   # remember the mode it was started in

    # ── Bridge callback: base status update + per-strategy state writes ────────

    def _bridge_callback(self, name: str):
        base_cb = self._update_status(name)          # reuse base behaviour
        st      = self.multi.get(name)

        def _cb(**kwargs):
            base_cb(**kwargs)                         # update StrategyStatus + CSV
            try:
                self._mirror_to_state(st, name, kwargs)
            except Exception:
                pass                                  # never let mirroring break a trade
        return _cb

    def _mirror_to_state(self, st, name: str, kw: dict) -> None:
        """Translate StrategyStatus/trade_event kwargs into BrahmastraState writes."""
        # Session-level fields for the detail view.
        # NOTE: total_trades / wins / losses / win_rate / session_pnl are NOT mirrored
        # from the engine here — they are owned solely by state.record_trade_event(),
        # driven off the same EXIT events that build the closed-trades list, so the
        # stat cards can never disagree with the trade list. (Most engines never emit
        # trades_today/wins_today, so mirroring them left the cards stuck at 0.)
        sess = {}
        with self._lock:
            stt = self._statuses.get(name)
        if stt:
            sess.setdefault("mode", stt.mode)
            sess.setdefault("phase", stt.state)
        if sess:
            st.update_session(**sess)

        # Count a per-cycle analysis line from EITHER `signal` or `last_signal`
        # (adapters like INRUSD/PASHUPATASTRA report via `last_signal`). Log it when
        # notable, OR at least once every _ANALYSIS_MIN_GAP_S so a running-but-quiet
        # engine still leaves an analysis trail (was falsely read as "telemetry
        # broken") without flooding fast-ticking engines.
        sig = kw.get("signal") or kw.get("last_signal")
        if sig:
            import time as _time
            _now = _time.monotonic()
            _last = self._last_analysis_log.get(name, 0.0)
            if kw.get("notable") or (_now - _last) >= self._ANALYSIS_MIN_GAP_S:
                st.add_log("ANALYSIS", sig)
                self._last_analysis_log[name] = _now

        ev = kw.get("trade_event")
        if ev:
            etype = ev.get("event", "TRADE")
            # Bot 2 — push every trade call (with rationale) to the signals channel.
            try:
                from src.api.telegram_bots import get_signal_bot
                ev_out = dict(ev)
                ev_out.setdefault("signal", kw.get("signal"))
                get_signal_bot(self.settings).send_trade_call(name, ev_out)
            except Exception:
                pass
            st.add_log("TRADE",
                       f"{etype} {ev.get('instrument','')} {ev.get('option_type','')}"
                       f"{ev.get('strike','')} qty={ev.get('quantity','')} "
                       f"@{ev.get('price','')} pnl={ev.get('pnl','')}")
            # Proper position lifecycle: open positions show in the Trades tab, exits
            # move to closed with a computed P&L. (Replaces the old append that
            # dropped open trades and mislabelled the entry price.)
            try:
                st.record_trade_event(ev)
            except Exception:
                pass

    # ── Engine construction (reuses the base ladder, extended) ────────────────

    def _create_engine(self, name: str, strategy_config: dict, broker, mode: str,
                        cb, stop_event: threading.Event):
        stype = strategy_config.get("strategy_type", "5min_fixed_quantity")

        if stype == "expiry_scalper":
            from src.live.expiry_scalper_live import ExpiryScalperLive
            engine = ExpiryScalperLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "range_scalper":
            from src.live.range_scalper_live import RangeScalperLive
            engine = RangeScalperLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "black_swan":
            from src.live.black_swan_live import BlackSwanLive
            engine = BlackSwanLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "nifty_intraday":
            from src.live.nifty_intraday_live import NiftyIntradayLive
            engine = NiftyIntradayLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "atm_pulse_burst":
            from src.live.atm_pulse_burst_live import ATMPulseBurstLive
            engine = ATMPulseBurstLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "bb_expiry_scalper":
            from src.live.bb_expiry_scalper_live import BBExpiryScalperLive
            engine = BBExpiryScalperLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "brahmastra":
            from src.brahmastra.brahmastra_live import BrahmastraLive
            from src.brahmastra.api.state import get_state
            # Register brahmastra's global state as this strategy's coordinator slot
            self.multi.register(name, get_state())
            engine = BrahmastraLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "inrusd_futures":
            from src.live.inrusd_live import INRUSDLive
            engine = INRUSDLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "pashupatastra":
            from src.live.pashupatastra_runner import PashupatastraRunner
            engine = PashupatastraRunner(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "gap_fade":
            from src.live.gap_fade_live import GapFadeLive
            engine = GapFadeLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "trend_following":
            from src.live.trend_rider_live import TrendRiderLive
            engine = TrendRiderLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "volatility_mean_reversion":
            from src.live.vix_seller_live import VixSellerLive
            engine = VixSellerLive(strategy_config, broker, mode=mode, status_callback=cb)
        else:
            from src.live.live_engine import LiveEngine
            engine = LiveEngine(strategy_config, broker, mode=mode)
            engine._status_callback = cb

        # Cooperative stop hook — engines check getattr(self,'_stop_event',None)
        try:
            engine._stop_event = stop_event
        except Exception:
            pass
        return engine

    def _paper_quote_broker(self):
        """A single shared, read-only KiteBroker used by paper strategies to read
        real option quotes. Created once and reused. Returns None when no Kite
        session is available (sandbox/offline) so PaperBroker falls back to its
        model. This broker is used for quotes only — paper engines never call its
        place_order."""
        cached = getattr(self, "_quote_broker_cached", "unset")
        if cached != "unset":
            return cached
        broker = None
        try:
            from src.broker.kite_broker import create_kite_broker
            broker = create_kite_broker(self.settings)
        except Exception as e:
            from src.utils.logger import setup_logger
            setup_logger("api_runner").warning(
                f"No live Kite quote source for paper pricing ({str(e)[:80]}) — "
                f"paper will fall back to modelled prices.")
        self._quote_broker_cached = broker
        return broker

    # ── Per-strategy thread ───────────────────────────────────────────────────

    def _run_one(self, name: str, mode: str, stop_event: threading.Event) -> None:
        from src.utils.logger import setup_logger
        _log = setup_logger("api_runner")
        def _slot():
            try: return self.multi.get(name)
            except Exception: return None
        try:
            st_slot = _slot()
            with self._lock:
                self._statuses[name].state = "STARTING"
            if st_slot:
                st_slot.update_session(phase="STARTING", mode=mode)

            strategy_config = self._load_strategy_config(name)

            if mode == "live":
                from src.broker.kite_broker import create_kite_broker
                broker = create_kite_broker(self.settings)
            else:
                from src.broker.paper_broker import PaperBroker
                # Give paper strategies a live, READ-ONLY Kite quote source so fills
                # price at the real market instead of a Black-Scholes model. Orders
                # stay fully simulated. Degrades to the model when no Kite session is
                # available (sandbox/offline).
                broker = PaperBroker(
                    slippage_pct=strategy_config.get("backtest", {}).get("slippage_pct", 0.1),
                    quote_broker=self._paper_quote_broker())

            cb     = self._bridge_callback(name)
            engine = self._create_engine(name, strategy_config, broker, mode, cb, stop_event)

            with self._lock:
                self._statuses[name].state = "RUNNING"
            st_slot.update_session(phase="ACTIVE")
            _log.info(f"[{name}] engine running (mode={mode})")
            engine.run()

            with self._lock:
                self._statuses[name].state = "STOPPED"
            st_slot.update_session(phase="STOPPED")

        except NotImplementedError as e:
            with self._lock:
                self._statuses[name].state = "IDLE"
                self._statuses[name].error = str(e)[:120]
            s = _slot()
            if s: s.add_log("CTRL", str(e)[:160])
        except Exception as e:
            import traceback
            with self._lock:
                self._statuses[name].state = "ERROR"
                self._statuses[name].error = str(e)[:120]
            s = _slot()
            if s: s.add_log("ERROR", f"CRASHED: {str(e)[:160]}")
            _log.error(f"[{name}] CRASHED: {e}\n{traceback.format_exc()}")

    # ── Public control API ────────────────────────────────────────────────────

    def is_running(self, name: str) -> bool:
        t = self._threads.get(name)
        return bool(t and t.is_alive())

    def start_strategy(self, name: str, mode: str = "paper") -> dict:
        # Fast stop→restart: an old thread may still be winding down (is_alive lags).
        # If it's stopping, give it a moment to exit before refusing.
        old = self._threads.get(name)
        if old and old.is_alive():
            ev = self._stop_events.get(name)
            if ev and ev.is_set():
                old.join(timeout=3.0)            # let the stopping thread finish
            if old.is_alive():
                return {"name": name, "started": False, "reason": "already running"}

        registry = self._load_registry().get("strategies", {})
        if name not in registry:
            return {"name": name, "started": False, "reason": "unknown strategy"}

        # ── HARD STATUS GUARD (deepest line of defence) ──────────────────────
        # Archived / planned strategies must NEVER run (any mode). Only a
        # live-status strategy may run LIVE. This is the chokepoint for ALL start
        # paths (autostart, manual paper, confirm-live), so even a bypassed
        # arm/confirm cannot start a non-eligible strategy with real orders.
        st_status = (registry.get(name, {}).get("status") or "").lower()
        if st_status in ("archived", "planned"):
            return {"name": name, "started": False,
                    "reason": f"{name} is {st_status} — refusing to run it"}
        if mode == "live" and st_status != "live":
            return {"name": name, "started": False,
                    "reason": f"{name} status is '{st_status}', not live — refusing LIVE start"}

        # Defense-in-depth: never start LIVE (real orders) without positive capital,
        # even if the server-side arm/confirm guard were somehow bypassed.
        if mode == "live":
            from src.api.capital import apply_overrides
            cap = apply_overrides(dict(registry)).get(name, {}).get("capital_allocated_rs", 0)
            if cap is None or cap <= 0:
                return {"name": name, "started": False,
                        "reason": f"no capital allocated (Rs.{cap}) — cannot go live"}

        self._operator_stopped.discard(name)     # starting clears any operator-stop
        self._statuses[name]    = StrategyStatus(name=name, mode=mode)
        self._stop_events[name] = threading.Event()
        t = threading.Thread(target=self._run_one,
                             args=(name, mode, self._stop_events[name]),
                             name=f"strategy-{name}", daemon=True)
        self._threads[name] = t
        t.start()
        return {"name": name, "started": True, "mode": mode}

    def stop_strategy(self, name: str) -> dict:
        self._operator_stopped.add(name)         # mark operator intent — no auto-restart
        ev = self._stop_events.get(name)
        if ev:
            ev.set()
        with self._lock:
            if name in self._statuses:
                self._statuses[name].state = "STOPPING"
        if self.multi.has(name):
            self.multi.get(name).update_session(phase="STOPPED")
        return {"name": name, "stopping": True}

    def stop_all(self) -> dict:
        names = list(self._stop_events.keys())
        for n in names:
            self.stop_strategy(n)
        return {"stopping": names}

    def was_operator_stopped(self, name: str) -> bool:
        return name in self._operator_stopped

    # ── Crash self-heal supervisor ────────────────────────────────────────────
    _MAX_RESTARTS = 4     # per strategy per session before we give up (and alert)

    def supervise(self) -> list[str]:
        """Restart strategies that CRASHED mid-session so a transient data hiccup
        doesn't silently kill a strategy for the rest of the day (the RANGE_SCALPER
        "0 analysis cycles" symptom). Rules that keep this safe:
          • Never touch a strategy the operator stopped (respects STOP ALL / manual stop).
          • Only restart during market hours (no after-hours thrash).
          • ALWAYS restart in PAPER — never auto-arm live (real orders keep needing the
            per-session arm+confirm). If it crashed while live, it comes back paper +
            an alert to re-arm.
          • Cap at _MAX_RESTARTS; after that, give up LOUDLY (visible ERROR line) rather
            than restart-crash-loop forever.
        Returns the names it restarted this pass.
        """
        try:
            from src.api.operating_policy import _market_closed
            if _market_closed():
                return []
        except Exception:
            pass
        restarted: list[str] = []
        with self._lock:
            names = list(self._statuses.keys())
        for name in names:
            if self.is_running(name) or self.was_operator_stopped(name):
                continue
            st = self._statuses.get(name)
            # Only a crashed engine (or a dead thread that never cleanly stopped) —
            # not one that ended normally at close (STOPPED) or is IDLE by design.
            if not st or st.state not in ("ERROR", "RUNNING"):
                continue
            used = self._restart_budget.get(name, 0)
            slot = self.multi.get(name) if self.multi.has(name) else None
            if used >= self._MAX_RESTARTS:
                if used == self._MAX_RESTARTS and slot:      # alert once
                    slot.add_log("ERROR", f"Crashed {used}× and auto-restart gave up — "
                                          f"needs a look. Fix + restart manually.")
                    self._restart_budget[name] = used + 1    # bump so we don't repeat the alert
                continue
            self._restart_budget[name] = used + 1
            res = self.start_strategy(name, mode="paper")    # PAPER only — never auto-live
            if res.get("started"):
                restarted.append(name)
                if slot:
                    slot.add_log("CTRL", f"Auto-restarted after a crash "
                                         f"(attempt {used + 1}/{self._MAX_RESTARTS}, paper).")
        return restarted

    def autostart_from_registry(self) -> list[dict]:
        """Policy-driven auto-start so the operator never hand-pushes a strategy.

        Mode comes from operating_policy.decide(): every non-paused/archived strategy
        comes up ACTIVE. Real-money orders remain gated — with per-session arm/confirm
        kept, auto-start brings strategies up in PAPER (active analysis) and the
        operator arms eligible ones to live via the existing guard. If a future policy
        sets auto_live_orders, decide() may return mode='live' only for strategies that
        pass the risk gates (capital, telemetry, real engine); a blocked one is started
        PAPER + alerted, never silently. Every decision is audited.
        """
        from src.api.operating_policy import load_policy, decide
        from src.api.bot_core import audit
        policy = load_policy()
        out: list[dict] = []
        if not policy.get("auto_start", True):
            self.last_autostart = []
            return out
        registry = self._load_registry().get("strategies", {})
        for name, cfg in registry.items():
            d = decide(name, cfg, policy)
            if d["action"] != "start":
                audit("STRATEGY_LIFECYCLE", name, "autostart_skip", "ok", d["reason"])
                continue
            res = self.start_strategy(name, mode=d["mode"])
            res["decision"] = d
            out.append(res)
            audit("STRATEGY_LIFECYCLE", name, "autostart",
                  "ok" if res.get("started") else "warn", f"{d['mode']}: {d['reason']}")
            try:
                if res.get("started") and self.multi.has(name):
                    self.multi.get(name).add_log(
                        "LIVE" if d["mode"] == "live" else "CTRL",
                        f"Auto-started ({d['mode']}) — {d['reason']}")
                    if d.get("degraded"):
                        self.multi.get(name).add_log(
                            "CTRL", "Live blocked → running PAPER; arm/confirm to go live once gates pass")
            except Exception:
                pass
        self.last_autostart = out
        return out

    def runtime_status(self, name: str) -> dict:
        with self._lock:
            st = self._statuses.get(name)
            running = self.is_running(name)
            if not st:
                return {"running": running, "mode": None, "state": "IDLE",
                        "real_pnl": 0, "paper_pnl": 0, "open_positions": 0,
                        "trades_today": 0, "wins_today": 0, "last_tick": "—",
                        "last_signal": "—", "error": ""}
            return {
                "running": running, "mode": st.mode, "state": st.state,
                "direction": st.direction, "score": st.score, "budget": st.budget,
                "real_pnl": st.real_pnl, "paper_pnl": st.paper_pnl,
                "open_positions": st.open_positions, "trades_today": st.trades_today,
                "wins_today": st.wins_today, "last_tick": st.last_tick,
                "last_signal": st.last_signal, "error": st.error,
            }
