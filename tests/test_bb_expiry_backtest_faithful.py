"""
BB_EXPIRY faithful backtest — smoke/integration test.

Runs the REAL rebuilt `BacktestEngine.run_bb_expiry_scalper()` over one synthetic
NIFTY expiry day (a flat base then a sharp sustained breakout above the upper band),
with load_intraday + calendar helpers monkeypatched so no Kite is needed. Asserts:
  • the new per-bar loop + booking closure run end-to-end without error,
  • a Mode-A breakout trade fires and is booked with a MODE_A tag,
  • the engine reads the REAL config params (target 2.5× is reflected in the exit).
Also guards the key fidelity property: it is NOT NIFTY-hardcoded (a SENSEX expiry
day with data produces SENSEX trades).
"""
from datetime import date

import pandas as pd
import pytest

import src.backtest.engine as eng
from src.backtest.engine import BacktestEngine

D = date(2026, 6, 4)


def _breakout_day():
    # 22 flat 1-min bars (~24000, tiny noise → tight band), then a sustained ramp
    # far above the upper band so BREAKOUT confirms and the CE runs to its target.
    base = [24000 + (1 if i % 2 else -1) for i in range(22)]
    ramp = [24080, 24170, 24270, 24380, 24500, 24630, 24770, 24920]
    closes = base + ramp
    idx = pd.date_range("2026-06-04 09:15", periods=len(closes), freq="1min")
    return pd.DataFrame({"Close": closes, "Volume": [1000] * len(closes)}, index=idx)


def _cfg():
    return {
        "strategy_type": "bb_expiry_scalper",
        "bb_expiry_scalper": {
            "bb_period": 20, "bb_std_dev": 2.0, "bb_squeeze_threshold": 0.5,
            "bb_breakout_confirm_bars": 2, "trade_budget_rs": 10000,
            "daily_loss_limit_rs": 25000, "max_trades_per_day": 5,
            "enable_mode_a": True, "enable_mode_b": True,
            "mode_a_min_score": 65, "mode_a_otm_strikes": 1,
            "mode_a_target_mult": 2.5, "mode_a_stop_pct": 35,
            "mode_b_min_score": 70, "mode_b_otm_strikes": 0,
            "mode_b_target_mult": 3.0, "mode_b_stop_pct": 40,
            "min_premium_rs": 0.5, "max_premium_rs": 500.0, "avoid_first_min": 15,
            "cooldown_after_loss_min": 15, "cooldown_after_profit_min": 5, "max_vix": 28.0,
            "hard_close_time": "15:15",
        },
        "instruments": {"nifty": {"lot_size": 65, "strike_step": 50},
                        "sensex": {"lot_size": 20, "strike_step": 100}},
        "backtest": {"slippage_pct": 0.5, "risk_free_rate": 0.065, "initial_capital": 10000},
    }


def _run(monkeypatch, *, sensex=False):
    monkeypatch.setattr(eng, "load_intraday", lambda *a, **k: _breakout_day())
    if sensex:
        monkeypatch.setattr(eng, "get_nifty_weekly_expiry", lambda d: date(2000, 1, 1))
        monkeypatch.setattr(eng, "get_sensex_weekly_expiry", lambda d: D)
    else:
        monkeypatch.setattr(eng, "get_nifty_weekly_expiry", lambda d: D)
        monkeypatch.setattr(eng, "get_sensex_weekly_expiry", lambda d: date(2000, 1, 1))
    e = BacktestEngine({}, _cfg())
    e.start_date = D
    e.end_date = D
    e._intraday_start = lambda: D
    return e.run_bb_expiry_scalper()


def test_faithful_bb_fires_mode_a_breakout(monkeypatch):
    res = _run(monkeypatch)
    assert len(res.trades) >= 1, "a Mode-A breakout should fire on this day"
    t = res.trades[0]
    assert t.window_id == "MODE_A"
    assert t.instrument == "NIFTY"
    assert t.direction == "CE"                     # breakout is upward
    assert t.exit_reason in ("TARGET", "SL", "TRAIL_STOP", "FORCE_CLOSE")
    # Real config target is 2.5× — a TARGET exit must be ~2.5× entry (proves the
    # engine reads mode_a_target_mult, not the old hardcoded 1.5 default).
    if t.exit_reason == "TARGET":
        # Clamped: a 2.5x target LIMIT fills AT ~2.5x (minus slippage), NEVER at the
        # intrabar overshoot (booking flt once inflated Mode-A wins ~3x above target).
        assert 2.3 <= t.exit_price / t.entry_price <= 2.5


def test_faithful_bb_is_not_nifty_hardcoded(monkeypatch):
    """The old backtest hardcoded NIFTY. The rebuild must trade SENSEX expiry days too."""
    res = _run(monkeypatch, sensex=True)
    assert len(res.trades) >= 1
    assert res.trades[0].instrument == "SENSEX"


def test_phantom_sensex_weekly_before_launch_is_skipped(monkeypatch):
    """SENSEX weekly options launched 15-May-2023. A pre-launch SENSEX expiry day
    with full intraday data must produce ZERO trades (no phantom instruments)."""
    old = date(2022, 6, 2)                       # ~a year before SENSEX weekly existed
    monkeypatch.setattr(eng, "load_intraday", lambda *a, **k: _breakout_day())
    monkeypatch.setattr(eng, "get_nifty_weekly_expiry", lambda d: date(2000, 1, 1))
    monkeypatch.setattr(eng, "get_sensex_weekly_expiry", lambda d: old)
    e = BacktestEngine({}, _cfg())
    e.start_date = old; e.end_date = old
    e._intraday_start = lambda: old
    res = e.run_bb_expiry_scalper()
    assert len(res.trades) == 0, "SENSEX weekly before 15-May-2023 must not be traded"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
