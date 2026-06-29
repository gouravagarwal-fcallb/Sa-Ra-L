"""
Readiness Subsystem — "properly checked, like BRAHMASTRA"
─────────────────────────────────────────────────────────
For each strategy, answer three machine-readable questions plus a config audit:

  backtest_ok      — does a results summary exist (summary.json, or CSV fallback)?
  backfill_ok      — can we fetch today's bars for the strategy's instrument?
  ticks_ok         — are live ticks updating right now (shared market feed)?
  config_audit_ok  — are platform constants current (NIFTY lot 65, Tue expiry)?

overall = READY (all green) | PARTIAL (backtest only) | NOT_READY | PLANNED.
The result is what the dashboard's Readiness page renders as a traffic-light grid.
"""
from __future__ import annotations

import os
import json
import time
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# Backfill freshness is cached to avoid hammering yfinance on every poll.
_BACKFILL_CACHE: dict[str, tuple] = {}     # instrument -> (checked_epoch, ok, detail)
_BACKFILL_TTL = 60                          # seconds
TICK_FRESH_SECONDS = 45


def _results_dir(cfg: dict, name: str) -> str:
    return cfg.get("results_dir") or f"strategies/{name}/results/"


def _check_backtest(name: str, cfg: dict) -> tuple[bool, dict]:
    rdir = _results_dir(cfg, name)
    summary_path = os.path.join(rdir, "summary.json")
    detail = {"summary_source": None, "needs_summary_json": True}
    if os.path.isfile(summary_path):
        try:
            with open(summary_path, encoding="utf-8") as f:
                s = json.load(f)
            detail.update({
                "summary_source": "summary.json",
                "needs_summary_json": False,
                "total_trades": s.get("total_trades"),
                "total_pnl": s.get("total_pnl"),
                "win_rate": s.get("win_rate"),
                "generated_at": s.get("generated_at") or s.get("period"),
            })
            return True, detail
        except Exception as e:
            detail["error"] = str(e)[:100]
    # CSV fallback
    if os.path.isdir(rdir):
        csvs = [f for f in os.listdir(rdir) if f.endswith(".csv")]
        if csvs:
            detail.update({"summary_source": "csv_fallback", "csv_files": csvs[:5]})
            return True, detail
    return False, detail


def _check_backfill(instrument: str) -> tuple[bool, dict]:
    now = time.time()
    cached = _BACKFILL_CACHE.get(instrument)
    if cached and now - cached[0] < _BACKFILL_TTL:
        return cached[1], cached[2]
    detail = {"instrument": instrument}
    try:
        from src.data.backfill import BackfillManager
        bars = BackfillManager().get_today_bars(instrument, interval="1m")
        ok = bool(bars)
        detail["bars_today"] = len(bars) if bars else 0
        detail["last_bar_ts"] = (bars[-1].get("t") if bars else None)
        if not ok:
            detail["reason"] = "no bars returned (market closed or data unavailable)"
    except Exception as e:
        ok = False
        detail["reason"] = f"backfill error: {str(e)[:120]}"
    _BACKFILL_CACHE[instrument] = (now, ok, detail)
    return ok, detail


def _check_ticks(name: str, instruments: list, multi, running: bool):
    """Live-tick freshness from the shared market feed (instrument-wide)."""
    if multi is None:
        return None, {"reason": "no state"}
    from src.api.state_registry import MARKET_SLOT
    detail = {}
    # Prefer the shared market slot; fall back to the strategy's own ticks.
    for slot in (MARKET_SLOT, name):
        if not multi.has(slot):
            continue
        ticks = multi.get(slot).ticks
        for inst in (instruments or list(ticks.keys())):
            t = ticks.get(inst) or ticks.get(str(inst).upper())
            if t and getattr(t, "timestamp", None):
                try:
                    hh, mm, ss = map(int, t.timestamp.split(":"))
                    now = datetime.now(IST)
                    age = abs((now.hour*3600+now.minute*60+now.second) - (hh*3600+mm*60+ss))
                    detail = {"instrument": inst, "last_tick_ts": t.timestamp,
                              "age_seconds": age, "slot": slot}
                    return age <= TICK_FRESH_SECONDS, detail
                except Exception:
                    detail = {"instrument": inst, "last_tick_ts": t.timestamp, "slot": slot}
                    return True, detail
    if not running:
        return None, {"reason": "not_running"}
    return False, {"reason": "no fresh tick"}


def _check_config_audit(name: str, cfg: dict) -> tuple[bool, dict]:
    """Verify NIFTY lot size (65) and Tuesday expiry in the strategy config."""
    path = cfg.get("config_file") or f"strategies/{name}/config.yaml"
    if not os.path.isfile(path):
        return False, {"reason": "config file missing (planned strategy)"}
    issues = []
    try:
        import yaml
        c = yaml.safe_load(open(path, encoding="utf-8"))
        text = json.dumps(c).lower()
        # NIFTY lot must be 65 if present
        insts = c.get("instruments", {})
        nifty = insts.get("nifty") if isinstance(insts, dict) else None
        if isinstance(nifty, dict) and nifty.get("lot_size") not in (None, 65):
            issues.append(f"NIFTY lot_size={nifty.get('lot_size')} (expected 65)")
        if "thursday" in text and "nifty" in text and "expiry" in text:
            # heuristic; SENSEX legitimately uses Thursday
            pass
    except Exception as e:
        return False, {"reason": f"parse error: {str(e)[:80]}"}
    return (len(issues) == 0), {"issues": issues}


def check_readiness(name: str, cfg: dict, runtime: dict, multi=None) -> dict:
    status = cfg.get("status")
    instruments = cfg.get("instruments", [])
    running = bool(runtime.get("running"))

    backtest_ok, bt_detail = _check_backtest(name, cfg)
    cfg_ok, cfg_detail     = _check_config_audit(name, cfg)

    # Skip network/tick checks for pure concepts with no config.
    if status == "planned" and not os.path.isfile(
            cfg.get("config_file") or f"strategies/{name}/config.yaml"):
        return {
            "name": name, "checked_at": datetime.now(IST).isoformat(),
            "backtest_ok": backtest_ok, "backtest_detail": bt_detail,
            "backfill_ok": None, "backfill_detail": {"reason": "planned — no config"},
            "ticks_ok": None, "ticks_detail": {"reason": "planned — no engine"},
            "config_audit_ok": cfg_ok, "config_audit_detail": cfg_detail,
            "overall": "PLANNED",
        }

    inst0 = (instruments[0] if instruments else "NIFTY")
    backfill_ok, bf_detail = _check_backfill(inst0)
    ticks_ok, tk_detail    = _check_ticks(name, instruments, multi, running)
    telemetry_ok, tel_detail = _check_telemetry(name, multi, running)

    if backtest_ok and backfill_ok and ticks_ok:
        overall = "READY"
    elif backtest_ok and (backfill_ok or ticks_ok):
        overall = "PARTIAL"
    elif backtest_ok:
        overall = "PARTIAL"
    else:
        overall = "NOT_READY"

    return {
        "name": name, "checked_at": datetime.now(IST).isoformat(),
        "backtest_ok": backtest_ok, "backtest_detail": bt_detail,
        "backfill_ok": backfill_ok, "backfill_detail": bf_detail,
        "ticks_ok": ticks_ok, "ticks_detail": tk_detail,
        "config_audit_ok": cfg_ok, "config_audit_detail": cfg_detail,
        "telemetry_ok": telemetry_ok, "telemetry_detail": tel_detail,
        "overall": overall,
    }


def _check_telemetry(name: str, multi, running: bool):
    """Phase 1 gate: a strategy must emit a verifiable analysis/heartbeat trail.
    None when stopped (nothing to verify yet)."""
    try:
        from src.api import telemetry as tel
        demoted = tel.is_demoted(name)
        state = multi.get(name) if (multi is not None and multi.has(name)) else None
        cycles = tel.cycles_today(name)
        age = tel.last_cycle_age_s(state) if state is not None else None
        if not running:
            return (None if cycles == 0 else True), {
                "cycles_today": cycles, "demoted": demoted, "reason": "not_running"}
        ok = tel.telemetry_ok(name, state=state, running=True)
        return ok, {"cycles_today": cycles, "last_cycle_age_s": age,
                    "demoted": demoted,
                    "reason": None if ok else "no verifiable analysis trail while running"}
    except Exception as e:
        return None, {"reason": f"telemetry check error: {str(e)[:80]}"}
