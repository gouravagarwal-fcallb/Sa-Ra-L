"""
Forward-paper analysis — evidence from the days we ACTUALLY ran.
════════════════════════════════════════════════════════════════
For strategies that cannot be backtested (OI-dependent like RAMS / ATM_PULSE, or
no order path like TREND_RIDER), the only evidence is what they did live. This
aggregates, ACROSS EVERY session with logs:
  • how many analysis cycles they ran and how many trades they took (+ P&L),
  • their no-trade reason mix,
  • the NEAREST-MISS trajectory — the peak signal score they reached each day vs
    their entry threshold (so "0 trades" becomes "best 14/75 — never close" or
    "peak bearish 2/3 — came within 1"),
  • telemetry health from the trust log (how many days they were BLIND vs scoring).

Read-only over what the engines already log — reuses the closure-report parsers so
the scoring is identical to the daily view. Honest by construction: if a strategy
was blind or idle, that is exactly what it reports.
"""
from __future__ import annotations

import glob
import json
import os
import re
from collections import Counter


def _all_log_days() -> list:
    """Every date that has an analysis trail or a trades CSV."""
    days = set()
    for p in glob.glob("logs/dashboard/*_*.jsonl"):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.jsonl$", os.path.basename(p))
        if m:
            days.add(m.group(1))
    for p in glob.glob("logs/trades_*.csv"):
        m = re.search(r"trades_(\d{4}-\d{2}-\d{2})\.csv", p)
        if m:
            days.add(m.group(1))
    for p in glob.glob("logs/2*/"):                     # per-day dirs logs/<date>/
        b = os.path.basename(p.rstrip("/"))
        if re.match(r"\d{4}-\d{2}-\d{2}$", b):
            days.add(b)
    return sorted(days)


def _trust_history(name: str) -> list:
    path = f"logs/trust/{name}.jsonl"
    if not os.path.isfile(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    out.append(json.loads(ln))
    except Exception:
        pass
    return out


def analyze_forward(registry: dict, focus: list | None = None) -> dict:
    from src.api.closure_report import (_read_strategy_logs, _read_trades,
                                        _nearest_miss, classify_no_trade, _fnum)
    days = _all_log_days()
    names = focus or list(registry.keys())

    # Pre-read trades per day once (they carry a `strategy` column).
    trades_by_day = {d: _read_trades(d) for d in days}

    out = []
    for name in names:
        cycles = 0
        no_trade_reasons: Counter = Counter()
        peaks = []            # (day, peak_score, threshold, would_fire)
        active_days = 0
        # per-day analysis trail (dashboard jsonl) → cycles + nearest miss
        for d in days:
            logs = _read_strategy_logs(d).get(name) or []
            if not logs:
                continue
            active_days += 1
            for e in logs:
                cat = (e.get("category") or "").upper()
                if cat in ("ANALYSIS", "SIGNAL", "ANALYSE", "NO_TRADE"):
                    cycles += 1
                r = classify_no_trade(e.get("message", "") or "")
                if r:
                    no_trade_reasons[r] += 1
            nm = _nearest_miss(name, logs)
            if nm and nm.get("peak_score") is not None:
                peaks.append({"day": d, "peak": nm["peak_score"],
                              "threshold": nm.get("threshold"),
                              "reached_pct": nm.get("reached_pct"),
                              "would_have_fired": nm.get("would_have_fired"),
                              "direction": nm.get("peak_direction")})
        # trades actually taken (from the strategy-tagged CSVs)
        t_taken, wins, losses, pnl = 0, 0, 0, 0.0
        for d in days:
            for row in trades_by_day.get(d, []):
                if (row.get("strategy") or "").strip() != name:
                    continue
                ev = (row.get("event") or row.get("exit_reason") or "").upper()
                p = _fnum(row.get("pnl"))
                # count only exit/closed rows for W/L (entries have blank pnl)
                if row.get("pnl") not in (None, "",):
                    t_taken += 1
                    pnl += p
                    if p > 0:
                        wins += 1
                    elif p < 0:
                        losses += 1

        th = _trust_history(name)
        blind_days = sum(1 for r in th if not (r.get("metrics") or {}).get("telemetry_ok"))
        best = max(peaks, key=lambda x: (x["reached_pct"] or 0)) if peaks else None
        near = sum(1 for p in peaks if (p.get("reached_pct") or 0) >= 80)
        fired = sum(1 for p in peaks if p.get("would_have_fired"))

        # honest verdict
        if not active_days and not th:
            verdict = "No forward-paper record yet — never ran with logging, or logs not persisted."
        elif cycles == 0 and blind_days == len(th) and th:
            verdict = ("BLIND — ran but telemetry emitted no scored analysis on every recorded "
                       "day. Cannot be judged until telemetry is fixed (this is the blocker, "
                       "not the strategy).")
        elif t_taken == 0 and best is None:
            verdict = f"Idle — {cycles} cycles over {active_days} day(s), no trades, no score trail captured."
        elif t_taken == 0 and best is not None:
            bp = f"{best['peak']}/{best['threshold']}" if best.get("threshold") else str(best["peak"])
            verdict = (f"Never fired in {active_days} day(s): best score {bp} "
                       f"({best.get('reached_pct','?')}% of the bar). "
                       + ("Comes close — a forward-paper candidate." if (best.get('reached_pct') or 0) >= 70
                          else "Stays far from its threshold — its gate rarely engages."))
        else:
            wr = round(wins / t_taken * 100, 1) if t_taken else 0
            verdict = (f"{t_taken} trade(s) over {active_days} day(s): {wins}W/{losses}L "
                       f"({wr}% win), P&L ₹{round(pnl):,}. Thin — keep forward-papering.")

        out.append({
            "name": name,
            "days_with_logs": active_days,
            "analysis_cycles": cycles,
            "trades_taken": t_taken, "wins": wins, "losses": losses, "pnl": round(pnl, 0),
            "no_trade_reasons": dict(no_trade_reasons.most_common(6)),
            "peak_best": best,
            "days_came_close": near,               # ≥80% of threshold
            "days_would_have_fired": fired,
            "trust_days": len(th),
            "trust_blind_days": blind_days,
            "trust_now": (th[-1].get("trust_score") if th else None),
            "verdict": verdict,
        })

    return {
        "days_analysed": len(days),
        "day_range": (f"{days[0]} → {days[-1]}" if days else "—"),
        "strategies": out,
        "note": ("Forward-paper evidence only — real sessions, real (post-2026-07-07) quotes. "
                 "For OI/no-order-path strategies this is the ONLY evidence there is. "
                 "'Nearest miss' = peak signal score vs entry threshold; a strategy that never "
                 "comes near its bar has no live edge to show yet."),
    }
