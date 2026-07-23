"""
GAP_FADE faithful backtest — smoke/integration test.

Runs the REAL rebuilt `BacktestEngine.run_gap_fade()` over a synthetic 2-day
scenario (Day-1 flat sets prev_close; Day-2 gaps UP then reverses down), with
load_intraday + calendar + daily-VIX monkeypatched (no Kite). Asserts the faithful
behaviour the old backtest lacked:
  • a gap-up + bearish reversal candle in the window fires a 1-OTM PE fade,
  • the VIX calm-tape filter blocks the trade on a high-VIX day.
"""
from datetime import date

import pandas as pd
import pytest

import src.backtest.engine as eng
import src.data.historical_loader as hl
from src.backtest.engine import BacktestEngine

D1 = date(2026, 6, 3)
D2 = date(2026, 6, 4)


def _flat_day():
    idx = pd.date_range("2026-06-03 09:15", periods=6, freq="5min")
    return pd.DataFrame({"Open": [24000] * 6, "High": [24010] * 6, "Low": [23990] * 6,
                         "Close": [24000] * 6, "Volume": [1000] * 6}, index=idx)


def _gap_reverse_day():
    # Gap UP to 24200 (+0.83% vs prev close 24000), bearish reversal candle, then a
    # sustained fall so the faded PE runs to its +15% target.
    times = ["09:15", "09:20", "09:25", "09:30", "09:35", "09:40",
             "09:45", "09:50", "09:55", "10:00", "10:05", "10:10", "10:15"]
    closes = [24180, 24100, 24000, 23900, 23820, 23760,
              23700, 23660, 23640, 23620, 23600, 23590, 23580]
    opens = [24200] + closes[:-1]
    idx = pd.DatetimeIndex([pd.Timestamp(f"2026-06-04 {t}") for t in times])
    return pd.DataFrame({"Open": opens, "High": [o + 20 for o in opens],
                         "Low": [c - 20 for c in closes], "Close": closes,
                         "Volume": [2000] * len(times)}, index=idx)


def _vix_df(level):
    return pd.DataFrame({"Close": [level, level]},
                        index=pd.DatetimeIndex([pd.Timestamp("2026-06-03"), pd.Timestamp("2026-06-04")]))


def _run(monkeypatch, vix_level, conv=None):
    monkeypatch.setattr(eng, "load_intraday",
                        lambda key, cur, interval="5m": _flat_day() if cur == D1 else _gap_reverse_day())
    monkeypatch.setattr(eng, "get_day_instrument", lambda d: "NIFTY")
    monkeypatch.setattr(eng, "get_nifty_weekly_expiry", lambda d: D2)
    monkeypatch.setattr(hl, "load_daily", lambda key, s, e: _vix_df(vix_level))
    gf = {"gap_min_pct": 0.5, "max_vix": 15.0, "otm_strikes": 1,
          "target_pct": 0.15, "stop_pct": 0.30, "window_start": "09:15",
          "window_end": "09:45", "hard_close_time": "10:15", "trade_budget_rs": 10000}
    gf.update(conv or {})
    e = BacktestEngine({}, {
        "strategy_type": "gap_fade",
        "gap_fade": gf,
        "instruments": {"nifty": {"lot_size": 65, "strike_step": 50}},
        "backtest": {"slippage_pct": 0.1, "risk_free_rate": 0.065, "initial_capital": 10000},
    })
    e.start_date = D1; e.end_date = D2
    e._intraday_start = lambda: D1
    return e.run_gap_fade()


def test_gap_fade_fires_pe_on_calm_day(monkeypatch):
    res = _run(monkeypatch, vix_level=12.0)          # calm → allowed
    assert len(res.trades) == 1, "gap-up + bearish reversal on a calm day should fade with a PE"
    t = res.trades[0]
    assert t.option_type == "PE"                      # fade a gap UP with a PE
    assert t.window_id == "GAP_FADE"
    assert t.strike < 24200                            # 1-OTM below the gap-up spot
    assert t.exit_reason in ("TARGET_HIT", "STOP_LOSS", "WINDOW_CLOSE")


def test_gap_fade_vix_filter_blocks_hot_day(monkeypatch):
    res = _run(monkeypatch, vix_level=20.0)          # VIX 20 ≥ 15 → too hot, must skip
    assert len(res.trades) == 0, "the calm-tape VIX filter must block the fade on a hot day"


def test_gap_fade_conviction_gap_strong_blocks_weak_gap(monkeypatch):
    # The synthetic day gaps +0.83%; a gap_strong_pct of 1.0% must veto it even though
    # it clears the 0.5% consider-threshold. Proves the stronger-gap conviction filter.
    res = _run(monkeypatch, vix_level=12.0, conv={"gap_strong_pct": 1.0})
    assert len(res.trades) == 0, "gap_strong_pct=1.0% must block a +0.83% gap"


def test_gap_fade_conviction_body_blocks_doji(monkeypatch):
    # Demand a huge reversal-candle body (2% of price) that the synthetic bars never
    # produce → the decisive-candle filter must veto every bar.
    res = _run(monkeypatch, vix_level=12.0, conv={"reversal_min_body_pct": 2.0})
    assert len(res.trades) == 0, "reversal_min_body_pct must reject indecisive candles"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
