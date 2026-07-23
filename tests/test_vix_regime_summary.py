"""
VIX-regime breakdown in the backtest summary — regression test.

Guards the EXPIRY_SCALPER "worst days" analysis: export_summary_json must bucket
DAILY P&L by the day's VIX correctly (a stale-loop-variable bug once made every
band report the last bucket's P&L) and list the most-losing days with their VIX.
"""
import os
import tempfile

from src.backtest.engine import BacktestResult, BacktestTrade
from src.backtest.report import export_summary_json


def _mk():
    r = BacktestResult()
    days = {"2026-01-01": 5000.0, "2026-01-02": 4000.0,
            "2026-01-03": -3000.0, "2026-01-04": -8000.0}
    vix = {"2026-01-01": 12.0, "2026-01-02": 14.0,
           "2026-01-03": 19.0, "2026-01-04": 24.0}
    r.daily_pnl = dict(days)
    r.vix_by_date = dict(vix)
    r.total_pnl = sum(days.values())
    for d, p in days.items():
        r.trades.append(BacktestTrade(
            date=d, window_id="T1", instrument="NIFTY", direction="BULLISH",
            option_type="CE", strike=100, entry_price=10.0, exit_price=10 + p / 100,
            entry_time="09:22:00", exit_time="10:10:00", pnl_pct=1.0, gross_pnl=p,
            transaction_cost=0.0, pnl_rupees=p, quantity=1, lot_size=1,
            trade_budget=10000, exit_reason="TARGET_HIT" if p > 0 else "STOP_LOSS",
            holding_minutes=48, is_expiry=True, is_paper=False))
    return r


def test_vix_buckets_hold_their_own_pnl():
    r = _mk()
    s = export_summary_json(r, os.path.join(tempfile.gettempdir(), "vix_sm.json"),
                            strategy_name="EXPIRY_SCALPER_v1")
    bands = {b["vix_band"]: b for b in s["by_vix_bucket"]}
    # Each band must carry ITS OWN day's P&L (the stale-`b` bug reported -8000 for all).
    assert bands["11-13"]["pnl"] == 5000.0
    assert bands["13-15"]["pnl"] == 4000.0
    assert bands["18-22"]["pnl"] == -3000.0
    assert bands[">=22"]["pnl"] == -8000.0
    # Calm regime wins, hot regime bleeds — the shape a VIX filter would exploit.
    assert bands["11-13"]["win_day_pct"] == 100.0
    assert bands[">=22"]["win_day_pct"] == 0.0


def test_worst_days_sorted_with_vix():
    r = _mk()
    s = export_summary_json(r, os.path.join(tempfile.gettempdir(), "vix_sm2.json"),
                            strategy_name="EXPIRY_SCALPER_v1")
    wd = s["worst_days"]
    assert wd[0]["date"] == "2026-01-04" and wd[0]["pnl"] == -8000.0
    assert wd[0]["vix"] == 24.0
    assert all(d["pnl"] < 0 for d in wd)          # only losing days listed


def test_no_vix_data_no_blocks():
    # A strategy that never records VIX (e.g. GAP_FADE) must not grow the new blocks.
    r = BacktestResult()
    r.daily_pnl = {"2026-01-01": 100.0}
    s = export_summary_json(r, os.path.join(tempfile.gettempdir(), "vix_sm3.json"),
                            strategy_name="GAP_FADE_v1")
    assert s["by_vix_bucket"] is None
    assert s["worst_days"] is None
