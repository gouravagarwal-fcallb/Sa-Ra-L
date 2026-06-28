"""
Special-engine backtests (BRAHMASTRA + PASHUPATASTRA)
────────────────────────────────────────────────────
These two strategies have their OWN dedicated backtest engines that do NOT fit the
generic 5-min `BacktestEngine` (BRAHMASTRA = 16-yr structural daily; PASHUPATASTRA =
multi-seed synthetic Monte-Carlo of a rare-release option-buy). Historically the
dispatcher fell back to `engine.run()` (the generic engine) for them, which silently
produced misleading numbers (e.g. a 45-day, Rs.8L/trade generic run shown as the
strategy's result).

This module runs each strategy's REAL engine and writes a normalized `summary.json`
(the same shape report.export_summary_json emits) so the dashboard shows honest,
correctly-labelled numbers. Each summary carries `data_basis` and `caveat` so the UI
can show what the result rests on (real daily history vs synthetic model).
"""
from __future__ import annotations

import os
import csv
import json
import importlib.util
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def _write_summary(path: str, summary: dict) -> dict:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    return summary


def _results_dir(name: str, scfg: dict) -> str:
    return scfg.get("results_dir") or f"strategies/{name}/results/"


# ══════════════════════════════════════════════════════════════════════════════
#  BRAHMASTRA — 16-year structural backtest (daily bars + synthetic option pricing)
# ══════════════════════════════════════════════════════════════════════════════
def run_brahmastra_summary(name: str, scfg: dict) -> dict:
    from src.brahmastra.backtest.backtest_engine import BrahmastraBacktest

    cfg        = scfg.get("backtest", {})
    instrument = cfg.get("instrument", "NIFTY")
    start_year = int(cfg.get("start_year", 2008))
    end_year   = int(cfg.get("end_year", 2024))
    capital    = (scfg.get("capital", {}) or {}).get("starting_capital", 10000)
    lot_size   = ((scfg.get("instruments", {}) or {})
                  .get(instrument.lower(), {}) or {}).get("lot_size", 65)

    engine = BrahmastraBacktest(instrument=instrument, start_year=start_year,
                                end_year=end_year, starting_capital=capital,
                                lot_size=lot_size)
    result = engine.run(verbose=False)

    out = _results_dir(name, scfg)
    os.makedirs(out, exist_ok=True)
    if result.total_trades > 0:
        try:
            engine.export_csv(result, os.path.join(out, "backtest_trades.csv"))
        except Exception:
            pass
        try:
            engine.plot_equity_curve(result, os.path.join(out, "equity_curve.png"))
        except Exception:
            pass

    yearly = getattr(result, "yearly_pnl", {}) or {}
    period_start = f"{start_year}-01-01"
    period_end   = f"{end_year}-12-31"
    summary = {
        "strategy_name": name,
        "run_kind": "structural_16yr",
        "generated_at": datetime.now(IST).isoformat(),
        "period": {"start": period_start, "end": period_end},
        "initial_capital": capital,
        "total_trades": result.total_trades,
        "total_pnl": round(result.total_pnl, 2),
        "win_rate": round(result.win_rate, 1),
        "profit_factor": round(getattr(result, "profit_factor", 0) or 0, 2),
        "sharpe": round(getattr(result, "sharpe", 0) or 0, 2),
        # Brahmastra reports drawdown as a % of equity (not rupees); keep both clear.
        "max_drawdown": round(getattr(result, "max_drawdown", 0) or 0, 2),
        "max_drawdown_unit": "percent",
        "cagr": round(getattr(result, "cagr", 0) or 0, 2),
        "by_year": {str(k): round(v, 2) for k, v in yearly.items()},
        "data_basis": "Daily bars (yfinance, synthetic fallback) + synthetic option pricing",
        "caveat": "16-year STRUCTURAL backtest on daily data with modelled option premiums — "
                  "not intraday, not real option-chain history. Indicative regime edge, not fills.",
        "engine": "BrahmastraBacktest",
        "by_instrument": [], "by_exit_reason": [], "by_window": [], "wfv": None,
    }
    return _write_summary(os.path.join(out, "summary.json"), summary)


# ══════════════════════════════════════════════════════════════════════════════
#  PASHUPATASTRA — multi-seed synthetic Monte-Carlo of the rare-release option buy
# ══════════════════════════════════════════════════════════════════════════════
def _load_pashupatastra_module():
    """Load the self-contained CLI backtest as an importable module (it lives under
    strategies/, not a package)."""
    import sys
    path = os.path.join("strategies", "PASHUPATASTRA_v1", "backtest_pashupatastra.py")
    spec = importlib.util.spec_from_file_location("backtest_pashupatastra", path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass (which looks up sys.modules[cls.__module__])
    # resolves correctly for this dynamically-loaded module.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_pashupatastra_summary(name: str, scfg: dict, *, seeds: int = 4) -> dict:
    """Run a representative multi-seed pass of the real PASHUPATASTRA engine on its
    synthetic price model and normalize to summary.json. `seeds` kept small for a
    responsive dashboard run; the full research sweep is the standalone CLI."""
    mod = _load_pashupatastra_module()

    bt   = scfg.get("backtest", {}) if isinstance(scfg.get("backtest"), dict) else {}
    start = str(bt.get("start_date", "2020-01-01"))
    end   = str(bt.get("end_date", "2026-06-30"))
    sd = datetime.fromisoformat(start).date()
    ed = datetime.fromisoformat(end).date()
    n_years = max((ed - sd).days / 365.25, 0.01)

    base = mod.Cfg(filter_skill=0.50)

    seed_metrics, artifact = [], None
    for s in range(seeds):
        daily = mod.gen_daily(sd, ed, 1000 + s)
        taken = mod.run_once(base, daily, 1000 + s)
        seed_metrics.append(mod.metrics(base, taken, n_years))
        if s == 0:
            artifact = taken

    def agg(key):
        xs = [m[key] for m in seed_metrics if m.get("n_bullets", 0) > 0 and key in m]
        return sum(xs) / len(xs) if xs else 0.0

    out = _results_dir(name, scfg)
    os.makedirs(out, exist_ok=True)

    # trades.csv (seed-0). Add a `pnl` column so the CSV fallback can read it too.
    if artifact:
        from dataclasses import asdict
        rows = [asdict(b) for b in artifact]
        for r in rows:
            r["pnl"] = r.get("net_pnl")
        with open(os.path.join(out, "backtest_trades.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)

    total_pnl = round(agg("total_net_pnl"), 2)
    n_bullets = round(agg("n_bullets"))
    win_rate  = round(agg("win_rate_pct"), 1)

    summary = {
        "strategy_name": name,
        "run_kind": "synthetic_montecarlo",
        "generated_at": datetime.now(IST).isoformat(),
        "period": {"start": start, "end": end},
        "seeds": seeds,
        "initial_capital": getattr(base, "slot_rs", 500000),
        "total_trades": n_bullets,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "profit_factor": round(agg("profit_factor"), 2),
        "sharpe": None,
        "max_drawdown": round(agg("max_drawdown_pct_slot"), 2),
        "max_drawdown_unit": "percent_of_slot",
        "return_on_slot_pct": round(agg("return_on_slot_pct"), 1),
        "expectancy_per_bullet_R": round(agg("expectancy_per_bullet_R"), 3),
        "bullets_per_month": round(agg("bullets_per_month"), 2),
        "data_basis": "SYNTHETIC calibrated NIFTY model + PROXY OI signal (filter_skill=0.50)",
        "caveat": "Model-based, NOT real market/option-chain data. Profitability hinges on an "
                  "ASSUMED signal skill the live OI recorder must still confirm. Treat as a "
                  "feasibility model, not a track record.",
        "engine": "backtest_pashupatastra",
        "by_instrument": [], "by_exit_reason": [], "by_window": [], "wfv": None,
    }
    return _write_summary(os.path.join(out, "summary.json"), summary)


# ── Single dispatch helper used by main.py + the API Run button ───────────────
SPECIAL = {
    "brahmastra":    run_brahmastra_summary,
    "pashupatastra": run_pashupatastra_summary,
}


def is_special(stype: str) -> bool:
    return stype in SPECIAL


def run_special(stype: str, name: str, scfg: dict) -> dict:
    return SPECIAL[stype](name, scfg)
