"""
Daily Analysis Aggregation
──────────────────────────
One endpoint that gathers the whole day's picture in one place:
  • Pre-market direction/bias (reuses DirectionEngine, like `main.py --mode premarket`)
  • Each strategy's latest signal + narrator headlines
  • Today's executed trades (from logs/trades_<date>.csv written by the runner)
"""
from __future__ import annotations

import os
import csv
from datetime import date, datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

_premarket_cache: dict = {}     # {date: result} — computed once per session


def _premarket(registry: dict) -> dict:
    today = date.today().isoformat()
    if _premarket_cache.get("date") == today:
        return _premarket_cache["data"]
    out = {"available": False}
    try:
        from main import load_configs
        from src.strategy.direction_engine import DirectionEngine
        from src.data.market_data import get_india_vix
        _, strat = load_configs(None)
        eng = DirectionEngine(strat)
        try:
            res = eng.evaluate_from_live_data()
        except Exception:
            from src.strategy.direction_engine import DirectionInputs
            res = eng.evaluate(DirectionInputs(india_vix=get_india_vix() or 15.0))
        out = {
            "available": True,
            "score": getattr(res, "score", None),
            "bias_label": getattr(res, "direction", None) or getattr(res, "bias", None),
            "breakdown": getattr(res, "breakdown", None) or getattr(res, "score_breakdown", None),
            "india_vix": getattr(res, "india_vix", None),
        }
    except Exception as e:
        out = {"available": False, "reason": str(e)[:140]}
    _premarket_cache.update({"date": today, "data": out})
    return out


def _todays_trades() -> list:
    path = f"logs/trades_{date.today().isoformat()}.csv"
    if not os.path.isfile(path):
        return []
    try:
        return list(csv.DictReader(open(path, encoding="utf-8")))
    except Exception:
        return []


def build_daily_analysis(registry: dict, multi, runner) -> dict:
    per_strategy = []
    for name, cfg in registry.items():
        if cfg.get("status") not in ("live", "paper"):
            continue
        rt = runner.runtime_status(name) if runner else {}
        narrator = {}
        if multi is not None and multi.has(name):
            snap = multi.get(name).snapshot()
            narrator = snap.get("narrator", {})
        per_strategy.append({
            "name": name,
            "status": cfg.get("status"),
            "last_signal": rt.get("last_signal", "—"),
            "real_pnl": rt.get("real_pnl", 0),
            "paper_pnl": rt.get("paper_pnl", 0),
            "trades_today": rt.get("trades_today", 0),
            "narrator": narrator,
        })
    return {
        "date": date.today().isoformat(),
        "generated_at": datetime.now(IST).isoformat(),
        "premarket": _premarket(registry),
        "per_strategy": per_strategy,
        "trades_today": _todays_trades(),
    }
