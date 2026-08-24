"""
Backtest insights — a max-period analysis report across all strategies.
──────────────────────────────────────────────────────────────────────
Reads every strategy's latest backtest summary.json and derives, for each:
  • a composite quality score + a fleet ranking (who's best),
  • the scenario it does BEST in and the scenario it does WORST in
    (from the by_window / by_instrument / by_exit_reason breakdowns),
  • strengths, weaknesses (where/why it fails),
  • rule-based GUARDRAILS to prevent loss in its weak scenarios,
  • "missing plugin" ideas — what to add to sharpen it.

Heuristics only — advisory, derived from the recorded metrics. Not a promise;
every backtest here carries modelled-cost / sample-size caveats.
"""
from __future__ import annotations

from typing import Optional


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _quality(row: dict) -> Optional[float]:
    """0–100 composite: profit factor + Sharpe + win-rate + a drawdown penalty.
    None when the strategy has no tradeable backtest."""
    s = row.get("summary") or {}
    trades = _f(row.get("total_trades") or s.get("total_trades")) or 0
    if trades < 1:
        return None
    pf   = _f(s.get("profit_factor")) or _f(row.get("profit_factor")) or 1.0
    shp  = _f(s.get("sharpe")) or _f(row.get("sharpe")) or 0.0
    wr   = _f(s.get("win_rate")) or _f(row.get("win_rate")) or 0.0
    pnl  = _f(s.get("total_pnl")) or _f(row.get("total_pnl")) or 0.0
    dd   = abs(_f(s.get("max_drawdown")) or _f(row.get("max_drawdown")) or 0.0)
    # Components, each softly capped.
    c_pf  = min(pf / 3.0, 1.0) * 35          # PF 3 → full marks
    c_shp = min(max(shp, 0) / 3.0, 1.0) * 25  # Sharpe 3 → full
    c_wr  = min(wr / 60.0, 1.0) * 15          # 60% WR → full
    c_pnl = 15 if pnl > 0 else -10
    # Drawdown penalty relative to profit (deep DD vs thin profit hurts).
    pen = 0.0
    if pnl > 0 and dd > 0:
        pen = min(dd / max(pnl, 1), 1.0) * 20
    elif pnl <= 0:
        pen = 15
    return round(max(0.0, min(100.0, c_pf + c_shp + c_wr + c_pnl - pen)), 1)


def _scenarios(s: dict) -> list:
    """Flatten by_window + by_instrument + by_exit_reason into comparable scenarios."""
    out = []
    for key, label in (("by_window", "window"), ("by_instrument", "instrument")):
        for r in (s.get(key) or []):
            name = r.get(key.split("_")[1]) or r.get("window") or r.get("instrument")
            out.append({"kind": label, "name": str(name),
                        "trades": r.get("trades") or r.get("count"),
                        "win_pct": _f(r.get("win_pct")),
                        "pnl": _f(r.get("pnl"))})
    return [x for x in out if x["pnl"] is not None and (x["trades"] or 0) > 0]


def _vix_regime_finding(summary: dict):
    """Read by_vix_bucket and decide, from data, whether a VIX regime bleeds.
    Returns None when there's no VIX data (older runs); else a dict with either a
    'bleeds' weakness+missing (a specific losing regime on ≥5 days) or a clean
    'strength' (no regime bleeds → a VIX filter would just cut winners)."""
    buckets = summary.get("by_vix_bucket")
    if not buckets:
        return None
    # A regime "bleeds" only if it loses money across a non-trivial number of days
    # (guard against calling 1-2 unlucky days a regime).
    losers = [b for b in buckets if _f(b.get("pnl")) is not None
              and b["pnl"] < 0 and (b.get("days") or 0) >= 5]
    if not losers:
        return {"bleeds": False,
                "strength": "No single VIX regime bleeds — losers are spread across "
                            "volatility bands, so the edge isn't regime-specific."}
    worst = min(losers, key=lambda b: b["pnl"])
    total_days = sum((b.get("days") or 0) for b in buckets)
    share = (worst["days"] / total_days * 100) if total_days else 0
    band = worst["vix_band"]
    return {
        "bleeds": True,
        "weakness": (f"Bleeds in the **VIX {band}** regime — {worst['days']} days "
                     f"({share:.0f}% of days), −₹{abs(worst['pnl']):,.0f}, "
                     f"only {worst.get('win_day_pct', 0):.0f}% winning days."),
        "missing": (f"A VIX filter that stands aside when India-VIX is in the {band} band "
                    f"(this is data-driven — that regime is the measured drag, verify with an A/B re-backtest)."),
    }


def _analyse_one(row: dict) -> dict:
    s = row.get("summary") or {}
    name = row.get("name")
    q = _quality(row)
    trades = int(_f(row.get("total_trades") or s.get("total_trades")) or 0)
    pf   = _f(s.get("profit_factor")) or _f(row.get("profit_factor"))
    shp  = _f(s.get("sharpe")) or _f(row.get("sharpe"))
    wr   = _f(s.get("win_rate")) or _f(row.get("win_rate"))
    pnl  = _f(s.get("total_pnl")) or _f(row.get("total_pnl")) or 0.0
    dd   = _f(s.get("max_drawdown")) or _f(row.get("max_drawdown"))
    aw   = _f(s.get("avg_win")); al = _f(s.get("avg_loss"))
    rr   = (abs(aw / al) if (aw and al) else None)

    scen = _scenarios(s)
    best = max(scen, key=lambda x: x["pnl"], default=None) if scen else None
    worst = min(scen, key=lambda x: x["pnl"], default=None) if scen else None

    strengths, weaknesses, guardrails, missing = [], [], [], []

    if trades == 0:
        return {"name": name, "tradeable": False,
                "note": row.get("class") == "zero_trades"
                        and "Fires 0 backtest trades — OI-dependent or a gate never triggers; forward-paper it instead."
                        or "No backtest trades on record — run a backtest first."}

    # Strengths
    if pf and pf >= 1.6: strengths.append(f"Solid edge — profit factor {pf:.2f}.")
    if shp and shp >= 2: strengths.append(f"Strong risk-adjusted return — Sharpe {shp:.2f}.")
    if best and best["pnl"] > 0:
        _bw = f", {best['win_pct']:.0f}% win" if best["win_pct"] is not None else ""
        strengths.append(f"Best in the **{best['name']}** {best['kind']} (+₹{best['pnl']:,.0f}{_bw}).")
    if rr and rr >= 2.5: strengths.append(f"High reward:risk — winners ≈ {rr:.1f}× losers.")

    # Weaknesses / where it fails
    if worst and worst["pnl"] < 0:
        weaknesses.append(f"Loses money in the **{worst['name']}** {worst['kind']} "
                          f"(−₹{abs(worst['pnl']):,.0f}).")
        guardrails.append(f"Disable / veto the **{worst['name']}** {worst['kind']} — it's a consistent drag.")
    if wr is not None and wr < 35 and (rr is None or rr < 2):
        weaknesses.append(f"Low win-rate ({wr:.0f}%) without the big R:R to pay for it.")
        missing.append("A confluence/quality filter (trend + volume + regime agreement) to skip the weakest setups.")
    if rr and rr < 1 and wr and wr >= 55:
        weaknesses.append("Wins often but losers are bigger than winners (poor R:R).")
        guardrails.append("Tighten stops / trail sooner, or widen the target so winners outrun losers.")
    if dd is not None and pnl > 0 and abs(dd) > 0.6 * pnl:
        weaknesses.append(f"Deep drawdown (−₹{abs(dd):,.0f}) vs profit (+₹{pnl:,.0f}) — bumpy ride.")
        guardrails.append("Add a tighter per-day loss cap and reduce size after 2 consecutive losers.")
    if pf and pf < 1.2:
        weaknesses.append("Thin edge (PF < 1.2) — costs could erase it.")
        missing.append("Model real costs (brokerage + STT + slippage) and add a stronger entry gate before sizing up.")

    # Data-driven VIX-regime finding (uses by_vix_bucket when the engine recorded it).
    # Replaces the generic "add a VIX filter" guess with what the data actually shows:
    # only flag a regime if it BLEEDS on a meaningful number of days.
    vix_finding = _vix_regime_finding(s)
    if vix_finding:
        if vix_finding["bleeds"]:
            weaknesses.append(vix_finding["weakness"])
            missing.append(vix_finding["missing"])
        else:
            strengths.append(vix_finding["strength"])

    # Generic 'missing plugin' nudges
    if not any("trail" in g.lower() for g in guardrails) and rr and rr < 2:
        missing.append("A trailing stop to capture more of the winning moves.")
    if not missing:
        if vix_finding is None:
            missing.append("Well-rounded here — a VIX/regime breakdown needs a re-backtest "
                           "(this run predates per-day VIX capture) to say if a regime filter would help.")
        else:
            missing.append("Well-rounded on this sample — no single VIX regime bleeds, so a "
                           "regime filter would likely cut winners too. Leave it off.")

    verdict = ("Strong" if (q or 0) >= 65 else "Promising" if (q or 0) >= 45
               else "Marginal" if (q or 0) >= 30 else "Weak")

    return {
        "name": name, "tradeable": True, "quality": q, "verdict": verdict,
        "trades": trades, "pnl": round(pnl, 0),
        "profit_factor": pf, "sharpe": shp, "win_rate": wr, "rr": round(rr, 2) if rr else None,
        "max_drawdown": dd, "period": row.get("period") or s.get("period"),
        "best_scenario": best, "worst_scenario": worst,
        "strengths": strengths, "weaknesses": weaknesses,
        "guardrails": guardrails, "missing": missing,
        "data_class": row.get("class"),
    }


def build_insights(registry: dict) -> dict:
    from src.api.backtests import aggregate_summaries
    rows = aggregate_summaries(registry)
    analysed = [_analyse_one(r) for r in rows]
    tradeable = [a for a in analysed if a.get("tradeable")]
    tradeable.sort(key=lambda a: (a.get("quality") or 0), reverse=True)
    ranking = [{"rank": i + 1, "name": a["name"], "quality": a["quality"],
                "verdict": a["verdict"], "pnl": a["pnl"], "profit_factor": a["profit_factor"],
                "sharpe": a["sharpe"], "win_rate": a["win_rate"]}
               for i, a in enumerate(tradeable)]

    best = tradeable[0] if tradeable else None
    # Fleet-level lessons.
    lessons = []
    if best:
        lessons.append(f"Best on backtest evidence: **{best['name']}** "
                       f"(quality {best['quality']}, PF {best['profit_factor']}, Sharpe {best['sharpe']}).")
    losers = [a for a in tradeable if (a.get("pnl") or 0) <= 0]
    if losers:
        lessons.append(f"Net-negative on this sample: {', '.join(a['name'] for a in losers)} — "
                       f"do NOT fund these until reworked.")
    thin = [a for a in tradeable if a.get("profit_factor") and a["profit_factor"] < 1.2]
    if thin:
        lessons.append(f"Thin-edge (PF<1.2), cost-fragile: {', '.join(a['name'] for a in thin)}.")
    lessons.append("Every number here is modelled-cost / limited-sample — treat as a ranking of "
                   "*evidence*, not a promise. Confirm survivors with forward paper before real capital.")

    return {
        "generated_note": "Advisory analysis derived from each strategy's latest backtest.",
        "count": len(tradeable),
        "best_overall": (best or {}).get("name"),
        "ranking": ranking,
        "strategies": analysed,
        "fleet_lessons": lessons,
    }
