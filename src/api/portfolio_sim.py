"""
Portfolio simulator — how the whole book performs on a FIXED shared pot.
════════════════════════════════════════════════════════════════════════
Operator's scenario: a fixed total capital (default Rs.1,00,000), no fresh money
introduced. Each strategy trades over the chosen backtest window; P&L accrues within
the window (markup/drawdown kept). At period end: profits are WITHDRAWN, losses are
NOT refilled — so a losing strategy permanently shrinks the base while a winner only
hands you withdrawable profit. That asymmetry is the point of the exercise.

HONESTY FIRST — this is the load-bearing part:
  • Only VERIFIED_FAITHFUL strategies have a backtest confirmed to reproduce their
    live engine (EXPIRY + BB after this session's rebuild). Every other row is an
    UNVERIFIED proxy, a NO-DATA (OI-blocked / never-fired) strategy, a SYNTHETIC
    model, or a SEPARATE engine. The table flags each, and the portfolio total is
    reported TWICE — "all" and "verified-only" — so the trustworthy subset is never
    hidden inside a pile of unvalidated numbers.
  • Every rupee is a MODELLED-premium ceiling — real fills are worse. Forward paper
    is the real proof.
  • P&L is on each strategy's fixed per-trade budget (no intra-year compounding).
"""
from __future__ import annotations

# Strategies whose backtest has been verified this session to faithfully reproduce
# the live engine. ONLY these carry backtest evidence you can lean on.
VERIFIED_FAITHFUL = {"EXPIRY_SCALPER_v1", "BB_EXPIRY_SCALPER_v1"}


def _num(v) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _trust(row: dict) -> tuple[str, str]:
    """Classify how much the strategy's backtest number can be trusted."""
    name = row.get("name")
    if name in VERIFIED_FAITHFUL:
        return "verified", "Backtest verified this session to match the live engine."
    status = (row.get("status") or "").lower()
    if status == "archived":
        return "archived", "Archived — intentionally not traded."
    rk = (row.get("run_kind") or "")
    stype = row.get("strategy_type") or ""
    trades = int(_num(row.get("total_trades")))
    if trades == 0:
        return "no_data", ("0 backtest trades — OI-dependent or a gate never fired; "
                           "cannot be judged from backtest (forward-paper it).")
    if rk in ("structural_16yr", "synthetic_montecarlo") or "synthetic" in rk:
        return "modelled", "Synthetic / structural model — not real intraday fills."
    if stype == "inrusd_futures" or rk == "inrusd_usdinr":
        return "separate", "Currency-futures engine (USD/INR) — separate model."
    return "unverified", ("Backtest NOT yet verified to match the live engine — "
                          "may over- or under-state (the BB lesson). Treat as indicative.")


def simulate_portfolio(registry: dict, total_capital: float = 100000.0,
                       period_label: str | None = None) -> dict:
    from src.api.backtests import aggregate_summaries
    rows = aggregate_summaries(registry)

    strat = []
    for r in rows:
        trust, note = _trust(r)
        pnl    = _num(r.get("total_pnl") or (r.get("summary") or {}).get("total_pnl"))
        trades = int(_num(r.get("total_trades") or (r.get("summary") or {}).get("total_trades")))
        strat.append({
            "name": r.get("name"),
            "trust": trust, "trust_note": note,
            "trades": trades,
            "pnl": round(pnl, 0),
            "win_rate": _num(r.get("win_rate") or (r.get("summary") or {}).get("win_rate")),
            "profit_factor": _num(r.get("profit_factor") or (r.get("summary") or {}).get("profit_factor")),
            "max_drawdown": round(_num(r.get("max_drawdown") or (r.get("summary") or {}).get("max_drawdown")), 0),
            "period": r.get("period") or (r.get("summary") or {}).get("period"),
            "run_kind": r.get("run_kind"),
            "tradeable": trades > 0 and trust not in ("archived",),
        })
    strat.sort(key=lambda s: (s["tradeable"], s["pnl"]), reverse=True)

    def rollup(subset: list) -> dict:
        pnl    = sum(s["pnl"] for s in subset)
        gains  = sum(s["pnl"] for s in subset if s["pnl"] > 0)
        losses = sum(s["pnl"] for s in subset if s["pnl"] < 0)
        return {
            "count": len(subset),
            "start_capital": round(total_capital, 0),
            "total_pnl": round(pnl, 0),
            "profit_withdrawn": round(gains, 0),       # winners: booked out
            "loss_retained": round(losses, 0),          # losers: permanently eaten
            "capital_after_withdrawal": round(total_capital + losses, 0),  # base for year 2
            "end_of_year_value": round(total_capital + pnl, 0),
            "return_pct": round(pnl / total_capital * 100, 1) if total_capital else 0.0,
        }

    active   = [s for s in strat if s["tradeable"]]
    verified = [s for s in active if s["trust"] == "verified"]

    return {
        "total_capital": round(total_capital, 0),
        "period_label": period_label or "each strategy's latest backtest window (see per-row Period)",
        "rules": ("Fixed shared pot, no fresh capital. P&L accrues within the window. "
                  "At period end profits are withdrawn; losses are NOT refilled, so a loss "
                  "permanently shrinks the base."),
        "strategies": strat,
        "portfolio_all": rollup(active),
        "portfolio_verified": rollup(verified),
        "verified_names": sorted(VERIFIED_FAITHFUL),
        "caveats": [
            "MIXED TRUST — only 'verified' rows have a backtest confirmed to match the live "
            "engine. 'unverified' may over/understate; 'no_data' can't be judged; 'modelled' "
            "are synthetic. The 'verified-only' rollup is the trustworthy number.",
            "Modelled option premiums → every rupee is an optimistic ceiling; real fills are "
            "worse. Forward paper is the real proof.",
            "P&L is on each strategy's fixed per-trade budget — no intra-year compounding "
            "(true compounding would amplify both gains and losses).",
            "For a true SAME-YEAR, SAME-CAPITAL comparison, first run one net backtest over a "
            "single 1-year range so every row shares the window; otherwise rows may span "
            "different periods (see each row's Period).",
        ],
    }


def to_markdown(sim: dict) -> str:
    """Render the sim as a shareable Markdown table."""
    L = [f"# Portfolio simulation — fixed pot ₹{int(sim['total_capital']):,}",
         "", f"_{sim['rules']}_", "",
         "| Strategy | Trust | Trades | Win% | PF | Net P&L | Max DD | Period |",
         "|---|---|---:|---:|---:|---:|---:|---|"]
    for s in sim["strategies"]:
        p = s.get("period") or {}
        per = f"{p.get('start','')}→{p.get('end','')}" if isinstance(p, dict) else ""
        L.append(f"| {s['name']} | {s['trust']} | {s['trades']} | "
                 f"{s['win_rate']:.0f} | {s['profit_factor']:.2f} | "
                 f"₹{int(s['pnl']):,} | ₹{int(s['max_drawdown']):,} | {per} |")
    for label, key in (("ALL tradeable", "portfolio_all"),
                       ("VERIFIED-ONLY (trustworthy)", "portfolio_verified")):
        r = sim[key]
        L += ["", f"## Portfolio — {label} ({r['count']} strategies)",
              f"- Start: ₹{int(r['start_capital']):,} · Net P&L: ₹{int(r['total_pnl']):,} "
              f"({r['return_pct']:+.1f}%)",
              f"- Profit withdrawn: ₹{int(r['profit_withdrawn']):,} · "
              f"Loss retained (base eaten): ₹{int(r['loss_retained']):,}",
              f"- Capital base for next year (losses not refilled): "
              f"₹{int(r['capital_after_withdrawal']):,}"]
    L += ["", "## Caveats"] + [f"- {c}" for c in sim["caveats"]]
    return "\n".join(L)
