"""
Backtest Summary Aggregation
────────────────────────────
Reads each strategy's machine-readable `summary.json` (written by
report.export_summary_json), with a CSV fallback so the dashboard's Backtests
page works even before a strategy has been re-run under the new convention.
"""
from __future__ import annotations

import os
import csv
import json


def _results_dir(cfg: dict, name: str) -> str:
    return cfg.get("results_dir") or f"strategies/{name}/results/"


def _summarize_csv(path: str) -> dict:
    """Derive headline metrics from a backtest_trades.csv as a fallback."""
    try:
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
    except Exception:
        return {}
    if not rows:
        return {}
    def num(r, *keys):
        for k in keys:
            v = r.get(k)
            if v not in (None, ""):
                try: return float(v)
                except Exception: pass
        return None
    pnls = [num(r, "pnl_rupees", "gross_pnl", "pnl") for r in rows]
    pnls = [p for p in pnls if p is not None]
    wins = [p for p in pnls if p > 0]
    # Surface the actual date span + per-trade budget so a short-window / generic
    # run can't masquerade as a validated deep backtest in the UI.
    dates = sorted(str(r.get("date", "")) for r in rows if r.get("date"))
    budgets = [num(r, "trade_budget") for r in rows]
    budgets = [b for b in budgets if b is not None]
    out = {
        "total_trades": len(rows),
        "total_pnl": round(sum(pnls), 2) if pnls else None,
        "win_rate": round(100 * len(wins) / len(pnls), 1) if pnls else None,
        "summary_source": "csv_fallback",
    }
    if dates:
        out["period"] = {"start": dates[0], "end": dates[-1]}
        # span in days → flag runs that only cover a short (yfinance-sized) window
        try:
            from datetime import date as _d
            s = _d.fromisoformat(dates[0][:10]); e = _d.fromisoformat(dates[-1][:10])
            out["span_days"] = (e - s).days
            out["short_window"] = (e - s).days < 180
        except Exception:
            pass
    if budgets:
        out["per_trade_budget"] = round(sum(budgets) / len(budgets))
    return out


def load_summary(name: str, cfg: dict) -> dict:
    rdir = _results_dir(cfg, name)
    out = {"name": name, "full_name": cfg.get("full_name", name),
           "status": cfg.get("status"),
           "strategy_type": cfg.get("type") or cfg.get("strategy_type"),
           "has_summary_json": False, "has_csv": False, "gap": True}
    sj = os.path.join(rdir, "summary.json")
    if os.path.isfile(sj):
        try:
            with open(sj, encoding="utf-8") as f:
                data = json.load(f)
            out.update({"has_summary_json": True, "gap": False, "summary": data})
            # surface common headline fields if present
            for k in ("total_trades", "total_pnl", "win_rate", "sharpe",
                      "max_drawdown", "profit_factor", "generated_at", "period"):
                if k in data:
                    out[k] = data[k]
            return out
        except Exception as e:
            out["summary_error"] = str(e)[:120]
    # CSV fallback
    if os.path.isdir(rdir):
        csvs = [f for f in os.listdir(rdir)
                if f.endswith(".csv") and "trade" in f.lower()]
        if csvs:
            out["has_csv"] = True
            out.update(_summarize_csv(os.path.join(rdir, sorted(csvs)[0])))
            out["csv_files"] = csvs[:5]
    # equity curve image if present
    if os.path.isdir(rdir):
        pngs = [f for f in os.listdir(rdir) if f.endswith(".png")]
        if pngs:
            out["equity_curve"] = os.path.join(rdir, sorted(pngs)[0])
    return out


def aggregate_summaries(registry: dict) -> list:
    return [load_summary(name, cfg) for name, cfg in registry.items()]
