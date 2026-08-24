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


def _read_trades_csv(name: str, cfg: dict) -> list:
    """Chronological (date, return_fraction) per trade from the strategy's trades CSV.
    return_fraction = net pnl / trade budget — the per-trade % on its native size,
    which we re-scale to the capital-aware position in the multi-year sim."""
    import os, csv
    rdir = cfg.get("results_dir") or f"strategies/{name}/results/"
    path = os.path.join(rdir, "backtest_trades.csv")
    if not os.path.isfile(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = (row.get("date") or row.get("entry_date") or "")[:10]
                if not d or len(d) < 7:
                    continue
                budget = _num(row.get("trade_budget"))
                pnl    = _num(row.get("pnl_rupees") if row.get("pnl_rupees") not in (None, "")
                              else row.get("pnl") or row.get("pnl_inr"))
                if budget > 0:
                    out.append((d, pnl / budget, budget))
    except Exception:
        return []
    out.sort(key=lambda t: t[0])
    return out


def simulate_multiyear(registry: dict, initial: float = 100000.0,
                       max_years: int = 20) -> dict:
    """Multi-year, capital-aware simulation of the operator's rules:
      • start each strategy with `initial` capital (no fresh money ever added),
      • size each trade as a FIXED FRACTION of *current* capital (auto-scales up as
        it compounds within a year, down as it shrinks) — the fraction is the
        strategy's own native per-trade budget / initial, so its risk-per-trade is
        preserved; if capital hits 0 the strategy DIES (no refill),
      • at each calendar year-end WITHDRAW the year's profit (booked out, base reset
        to the year's start); a LOSING year is retained (base permanently shrinks).
    Runs over each strategy's MAX available trade history (labelled) — note intraday
    strategies only have ~2-3 years of data, so '20 years' applies only to the
    daily/synthetic engines."""
    from src.api.backtests import aggregate_summaries
    trust_by = {r.get("name"): _trust(r) for r in aggregate_summaries(registry)}

    out_rows = []
    for name, cfg in registry.items():
        trust, note = trust_by.get(name, ("unverified", ""))
        trades = _read_trades_csv(name, cfg)
        if not trades:
            out_rows.append({"name": name, "trust": trust, "trust_note": note,
                             "has_trades": False,
                             "note": "no trades CSV — run its backtest first (or it can't backtest)."})
            continue
        budgets = [b for _, _, b in trades if b > 0]
        native  = (sum(budgets) / len(budgets)) if budgets else initial
        # Risk fraction per trade = native budget / starting capital, clamped to a
        # sane 2%–100% band so a mis-recorded budget can't produce absurd sizing.
        frac = min(max(native / initial, 0.02), 1.0)

        by_year: dict = {}
        for d, ret, _b in trades:
            by_year.setdefault(d[:4], []).append(ret)
        years_sorted = sorted(by_year)[:max_years]

        base = initial
        total_withdrawn = 0.0
        annual = []
        dead = False
        death_year = None
        for y in years_sorted:
            if dead:
                break
            year_start = base
            cap = base
            for ret in by_year[y]:
                cap += (frac * cap) * ret       # capital-aware position, compounding
                if cap <= 0:
                    cap = 0.0
                    dead = True
                    death_year = y
                    break
            profit = cap - year_start
            if profit > 0:                       # winning year → withdraw, base preserved
                total_withdrawn += profit
                base = year_start
                withdrawn = profit
            else:                                # losing year → retained, base shrinks
                base = cap
                withdrawn = 0.0
            annual.append({"year": y, "trades": len(by_year[y]),
                           "year_pnl": round(cap - year_start, 0),
                           "withdrawn": round(withdrawn, 0), "base_after": round(base, 0)})

        # Self-flag when annual compounding of MODELLED premiums has produced an
        # unrealistic magnitude (a strategy cannot really pay out 20× its capital/yr).
        # This is the honest tripwire: compounding fiction is still fiction.
        unrealistic = total_withdrawn > 20 * initial * max(len(years_sorted), 1)
        out_rows.append({
            "name": name, "trust": trust, "trust_note": note, "has_trades": True,
            "years_covered": len(years_sorted),
            "span": f"{years_sorted[0]}–{years_sorted[-1]}" if years_sorted else "—",
            "total_trades": len(trades),
            "per_trade_fraction_pct": round(frac * 100, 1),
            "native_budget": round(native, 0),
            "total_withdrawn": round(total_withdrawn, 0),
            "final_base": round(base, 0),
            "survived": not dead,
            "death_year": death_year,
            "net_outcome": round(total_withdrawn - (initial - base), 0),  # income − base eroded
            "unrealistic": bool(unrealistic),
            "annual": annual,
        })

    active = [r for r in out_rows if r.get("has_trades")]
    active.sort(key=lambda r: r.get("net_outcome", 0), reverse=True)
    non = [r for r in out_rows if not r.get("has_trades")]

    return {
        "initial_capital": round(initial, 0),
        "rules": ("Each strategy starts with the initial capital, no fresh money ever "
                  "added. Per-trade size = fixed fraction of CURRENT capital (auto-scales; "
                  "dies at 0, no refill). Each year-end: profit withdrawn, losses retained "
                  "(base permanently shrinks)."),
        "strategies": active + non,
        "verified_names": sorted(VERIFIED_FAITHFUL),
        "caveats": [
            "DATA SPAN — intraday option strategies only have ~2-3 years of history "
            "(Kite intraday from ~2015 / 1-min from ~2023); a true 20-year run is only "
            "possible for the daily/synthetic engines (BRAHMASTRA, INRUSD, PASHUPATASTRA). "
            "Each row shows the ACTUAL span covered.",
            "MIXED TRUST — only 'verified' rows have a backtest confirmed to match the live "
            "engine. Others are indicative/synthetic. Modelled premiums → rupees are a "
            "ceiling; prove with forward paper.",
            "Fixed-fractional sizing is ruin-resistant (size shrinks with capital), so "
            "'death' is rare and mostly flags a catastrophic single year. Annual withdrawal "
            "means the base never compounds above the start — it only shrinks on losing years.",
        ],
    }


def to_markdown_multiyear(sim: dict) -> str:
    L = [f"# Multi-year portfolio simulation — start ₹{int(sim['initial_capital']):,} each",
         "", f"_{sim['rules']}_", "",
         "| Strategy | Trust | Span | Yrs | Trades | Size/trade | Total withdrawn | Final base | Survived |",
         "|---|---|---|---:|---:|---:|---:|---:|:--:|"]
    for s in sim["strategies"]:
        if not s.get("has_trades"):
            L.append(f"| {s['name']} | {s['trust']} | — | — | — | — | — | — | {s.get('note','no data')} |")
            continue
        L.append(f"| {s['name']} | {s['trust']} | {s['span']} | {s['years_covered']} | "
                 f"{s['total_trades']} | {s['per_trade_fraction_pct']}% | "
                 f"₹{int(s['total_withdrawn']):,} | ₹{int(s['final_base']):,} | "
                 f"{'yes' if s['survived'] else 'DIED '+str(s['death_year'])} |")
    L += ["", "## Caveats"] + [f"- {c}" for c in sim["caveats"]]
    return "\n".join(L)


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
