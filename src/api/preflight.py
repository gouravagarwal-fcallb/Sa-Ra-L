"""
Pre-Open Preflight Self-Check
─────────────────────────────
One command to run before the market open. Verifies the things that must work
for a safe session and prints a clear GO / NO-GO:

  1. Trading day      — is today a trading day?
  2. Market data feed — can we fetch NIFTY spot + India VIX?
  3. Kite login       — is the access token valid? (required only if any LIVE
                        strategy is configured)
  4. Strategy readiness — per live/paper strategy: config audit + backtest summary
                        + backfill, via the shared readiness checks.

Exit code: 0 = GO, 1 = NO-GO  (so it can gate an automated start).
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[1m"; X = "\033[0m"
OK = f"{G}✓{X}"; BAD = f"{R}✗{X}"; WARN = f"{Y}⚠{X}"


def _load_registry() -> dict:
    import yaml
    return yaml.safe_load(open("strategies/registry.yaml", encoding="utf-8")).get("strategies", {})


def _check_trading_day() -> tuple[bool, str]:
    try:
        from src.utils.market_calendar import is_trading_day
        today = datetime.now(IST).date()
        if is_trading_day(today):
            return True, f"{today} is a trading day"
        return False, f"{today} is NOT a trading day (weekend/holiday)"
    except Exception as e:
        return True, f"calendar check skipped ({str(e)[:50]})"


def _check_data_feed() -> tuple[bool, dict]:
    detail = {}
    try:
        from src.data.market_data import get_spot_price, get_india_vix
        spot = get_spot_price("NIFTY")
        vix = get_india_vix()
        detail = {"nifty_spot": spot, "india_vix": vix}
        ok = bool(spot and spot > 0)
        if not ok:
            detail["reason"] = "NIFTY spot came back empty"
        return ok, detail
    except Exception as e:
        return False, {"reason": str(e)[:120]}


def _check_kite() -> tuple[bool, dict]:
    try:
        from main import load_configs
        settings, _ = load_configs(None)
        from src.broker.kite_broker import create_kite_broker
        broker = create_kite_broker(settings)        # constructor calls profile() to validate
        name = None
        try:
            name = broker._kite.profile().get("user_name")
        except Exception:
            pass
        return True, {"connected": True, "user": name}
    except Exception as e:
        return False, {"reason": str(e)[:160]}


def _check_session_timing() -> tuple[bool, dict]:
    """Are we before the 09:15 open? ORB / opening-range strategies need a pre-open
    start to build their morning range, so this is a caution (not a blocker)."""
    now = datetime.now(IST)
    weekday = now.weekday() < 5
    m = now.hour * 60 + now.minute
    before_open = weekday and m < (9 * 60 + 15)
    after_close = weekday and m > (15 * 60 + 30)
    return before_open, {"now": now.strftime("%H:%M"), "before_open": before_open,
                         "after_close": after_close, "weekday": weekday}


def build_preflight(settings: dict | None = None) -> dict:
    """Structured pre-open self-check (shared by the CLI and the dashboard).
    Returns a verdict (GO / GO_WITH_CAUTION / NO_GO), a list of named checks, the
    per-strategy readiness rows, and the blocker/caution lists."""
    reg = _load_registry()
    live_names  = [n for n, c in reg.items() if c.get("status") == "live"]
    paper_names = [n for n, c in reg.items() if c.get("status") == "paper"]
    active = live_names + paper_names
    blockers: list[str] = []
    cautions: list[str] = []
    checks: list[dict] = []

    day_ok, day_msg = _check_trading_day()
    checks.append({"key": "trading_day", "label": "Trading day", "ok": day_ok, "detail": day_msg})
    if not day_ok:
        cautions.append("Market is closed today (run the morning of a trading day).")

    before_open, sess = _check_session_timing()
    if not sess["weekday"] or sess["after_close"]:
        checks.append({"key": "timing", "label": "Session timing", "ok": None,
                       "detail": f"{sess['now']} — outside session"})
    elif before_open:
        checks.append({"key": "timing", "label": "Session timing", "ok": True,
                       "detail": f"{sess['now']} — before the 09:15 open (full session)"})
    else:
        checks.append({"key": "timing", "label": "Session timing", "ok": None,
                       "detail": f"{sess['now']} — after the open (partial: ORB strategies miss the morning)"})
        cautions.append("Started after the 09:15 open — ORB strategies (ATM_PULSE_BURST) "
                        "can't build their morning range today.")

    data_ok, data_d = _check_data_feed()
    checks.append({"key": "data_feed", "label": "Market data feed", "ok": data_ok,
                   "detail": (f"NIFTY {data_d.get('nifty_spot')}, VIX {data_d.get('india_vix')}"
                              if data_ok else data_d.get("reason", "unavailable"))})
    if not data_ok:
        blockers.append("Market data feed is unavailable.")

    if live_names:
        kite_ok, kite_d = _check_kite()
        checks.append({"key": "kite", "label": "Kite login", "ok": kite_ok,
                       "detail": (f"connected{(' as ' + kite_d['user']) if kite_d.get('user') else ''}"
                                  if kite_ok else kite_d.get("reason", "failed"))})
        if not kite_ok:
            blockers.append("Kite login failed but LIVE strategies are configured "
                            "(run: python main.py --mode login).")
    else:
        checks.append({"key": "kite", "label": "Kite login", "ok": None,
                       "detail": "skipped (no LIVE strategies; paper needs no broker)"})

    strategies: list[dict] = []
    if active:
        from src.api.readiness import check_readiness
        from src.api.state_registry import get_multi_state
        multi = get_multi_state()
        for name in active:
            cfg = reg[name]
            try:
                r = check_readiness(name, cfg, {"running": False}, multi)
            except Exception as e:
                strategies.append({"name": name, "status": cfg.get("status"), "error": str(e)[:60]})
                blockers.append(f"{name}: readiness check errored.")
                continue
            if r["config_audit_ok"] is False:
                blockers.append(f"{name}: config audit failed ({r['config_audit_detail'].get('issues')}).")
            if not r["backtest_ok"]:
                cautions.append(f"{name}: no backtest summary yet (run --mode backtest --strategy {name}).")
            strategies.append({"name": name, "status": cfg.get("status"),
                               "config_ok": r["config_audit_ok"], "backtest_ok": r["backtest_ok"],
                               "backfill_ok": r["backfill_ok"]})

    verdict = "NO_GO" if blockers else ("GO_WITH_CAUTION" if cautions else "GO")
    return {"generated_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "verdict": verdict, "checks": checks, "strategies": strategies,
            "blockers": blockers, "cautions": cautions,
            "before_open": before_open}


def run_preflight(settings: dict | None = None) -> int:
    print(f"\n{B}  ╔══════════════════════════════════════════════════════╗{X}")
    print(f"{B}  ║   Sa-Ra-L  ·  Pre-Open Preflight Self-Check          ║{X}")
    print(f"{B}  ╚══════════════════════════════════════════════════════╝{X}")
    print(f"  {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST\n")

    reg = _load_registry()
    live_names  = [n for n, c in reg.items() if c.get("status") == "live"]
    paper_names = [n for n, c in reg.items() if c.get("status") == "paper"]
    active = live_names + paper_names

    blockers: list[str] = []
    cautions: list[str] = []

    # 1 — trading day
    day_ok, day_msg = _check_trading_day()
    print(f"  {OK if day_ok else WARN} Trading day      — {day_msg}")
    if not day_ok:
        cautions.append("Market is closed today (run the morning of a trading day).")

    # 2 — data feed
    data_ok, data_d = _check_data_feed()
    if data_ok:
        print(f"  {OK} Market data feed — NIFTY {data_d.get('nifty_spot')}, VIX {data_d.get('india_vix')}")
    else:
        print(f"  {BAD} Market data feed — {data_d.get('reason','unavailable')}")
        blockers.append("Market data feed is unavailable.")

    # 3 — Kite login (required only if any LIVE strategy)
    if live_names:
        kite_ok, kite_d = _check_kite()
        if kite_ok:
            print(f"  {OK} Kite login       — connected{(' as ' + kite_d['user']) if kite_d.get('user') else ''}")
        else:
            print(f"  {BAD} Kite login       — {kite_d.get('reason','failed')}")
            blockers.append("Kite login failed but LIVE strategies are configured "
                            "(run: python main.py --mode login).")
    else:
        print(f"  {WARN} Kite login       — skipped (no LIVE strategies; paper needs no broker)")

    # 4 — per-strategy readiness
    print(f"\n  {B}Strategy readiness (live + paper):{X}")
    if not active:
        print("    (none in live/paper status)")
    else:
        from src.api.readiness import check_readiness
        from src.api.state_registry import get_multi_state
        multi = get_multi_state()
        for name in active:
            cfg = reg[name]
            try:
                r = check_readiness(name, cfg, {"running": False}, multi)
            except Exception as e:
                print(f"    {BAD} {name:<22} readiness error: {str(e)[:50]}")
                blockers.append(f"{name}: readiness check errored.")
                continue
            tags = []
            if r["config_audit_ok"] is False:
                blockers.append(f"{name}: config audit failed ({r['config_audit_detail'].get('issues')}).")
                tags.append(f"{BAD}config")
            else:
                tags.append(f"{OK}config")
            if r["backtest_ok"]:
                tags.append(f"{OK}backtest")
            else:
                cautions.append(f"{name}: no backtest summary yet (run --mode backtest --strategy {name}).")
                tags.append(f"{WARN}backtest")
            if r["backfill_ok"]:
                tags.append(f"{OK}backfill")
            elif r["backfill_ok"] is False:
                tags.append(f"{WARN}backfill")
            status = cfg.get("status")
            print(f"    {name:<22} [{status:<5}]  " + "  ".join(tags))

    # ── Verdict ───────────────────────────────────────────────────────────────
    print()
    if blockers:
        print(f"  {R}{B}  ✗  NO-GO — do not start until these are fixed:{X}")
        for b in blockers:
            print(f"  {R}     • {b}{X}")
        if cautions:
            print(f"\n  {Y}  Cautions:{X}")
            for c in cautions:
                print(f"  {Y}     • {c}{X}")
        print()
        return 1

    if cautions:
        print(f"  {Y}{B}  ⚠  GO — with cautions (review before arming live):{X}")
        for c in cautions:
            print(f"  {Y}     • {c}{X}")
        print(f"\n  {G}{B}  Core checks passed. Safe to start in PAPER; validate before live.{X}\n")
        return 0

    print(f"  {G}{B}  ✓  GO — all checks passed. Cleared for the open.{X}\n")
    return 0
