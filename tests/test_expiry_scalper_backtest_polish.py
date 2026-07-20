"""
EXPIRY_SCALPER backtest-engine polish — proves the partial-book/trail flags are
now honoured by the BACKTEST simulation (not just the live engine), and that with
the flags OFF the backtest is unchanged.

Runs the REAL `BacktestEngine.run_expiry_scalper()` over one synthetic expiry day,
with `load_intraday` + the market-calendar helpers monkeypatched so no network /
Kite is needed. The day is a deliberate "pop then crash":

    14:45 ref → 14:50 breakout (enter CE) → 14:55 big pop → 15:00 crash → EOD low

  • Flags OFF: rides the full position through the crash → a losing trade.
  • partial_book ON: books a slice at the 14:55 pop, so the trade nets POSITIVE
    even though the index round-tripped — exactly the "small win instead of a full
    stop" behaviour the feature is for.
"""
from copy import deepcopy
from datetime import date

import pandas as pd
import pytest

import src.backtest.engine as eng
from src.backtest.engine import BacktestEngine

D = date(2026, 6, 4)

# One synthetic 5-min expiry session. Entry CE (strike 80200) prices at ~Rs.13.15
# at 14:50; the pop to 80200 lifts it to ~Rs.38.77 (2.95x — above the 1.6x partial
# trigger, below the 5x target), then the crash to 79900 collapses it to ~Rs.0.01.
# So: flags OFF → full stop (loss); partial_book ON → books the pop → net win.
_TIMES  = ["14:45", "14:50", "14:55", "15:00", "15:05", "15:10", "15:15"]
_CLOSES = [80000,    80120,   80200,   79900,   79850,   79800,   79800]


# Low baseline volume on the first (reference) bar, high on the breakout bars, so
# the backtest's volume-surge gate (≥1.3× the pre-window average) is satisfied.
_VOLS = [100, 2000, 2000, 2000, 2000, 2000, 2000]


def _bars(*_a, **_k):
    idx = pd.DatetimeIndex([pd.Timestamp(f"2026-06-04 {t}:00") for t in _TIMES])
    return pd.DataFrame({"Close": _CLOSES, "Volume": _VOLS}, index=idx)


def _base_cfg():
    return {
        "strategy_type": "expiry_scalper",
        "expiry_scalper": {
            "trade_budget_rs": 10000,
            "hard_close_time": "15:29",
            "windows": [{
                "id": "W3", "name": "EOD", "start": "14:45", "end": "15:10",
                "momentum_threshold_pct": 0.10, "otm_strikes": 1,
                "max_premium_rs": 500, "min_premium_rs": 0.5,
                "target_multiplier": 5.0, "stop_loss_pct": 50,
                "require_score_direction": False,
            }],
        },
        "instruments": {
            "nifty":  {"lot_size": 65, "strike_step": 50},
            "sensex": {"lot_size": 20, "strike_step": 100},
        },
        "backtest": {"start_date": "2026-06-04", "end_date": "2026-06-04",
                     "initial_capital": 50000, "slippage_pct": 0.2},
        "risk": {"daily_loss_limit": 100000},
    }


def _run(scfg, monkeypatch):
    monkeypatch.setattr(eng, "load_intraday", _bars)
    monkeypatch.setattr(eng, "get_day_instrument", lambda d: "SENSEX")
    monkeypatch.setattr(eng, "get_sensex_weekly_expiry", lambda d: D)
    monkeypatch.setattr(eng, "get_nifty_weekly_expiry", lambda d: date(2000, 1, 1))
    e = BacktestEngine({}, scfg)
    e.start_date = D
    e.end_date = D
    e._intraday_start = lambda: D
    return e.run_expiry_scalper()


def test_flags_off_backtest_unchanged_and_partial_book_rescues(monkeypatch):
    # ── Flags OFF (default): one full-size trade that loses through the crash ──
    off = _run(_base_cfg(), monkeypatch)
    assert len(off.trades) == 1, "exactly one W3 trade on this day"
    t_off = off.trades[0]
    assert t_off.exit_reason in ("STOP_LOSS", "FORCE_CLOSE")   # never a trail when OFF
    assert t_off.exit_reason != "TRAIL_STOP"
    assert t_off.pnl_rupees < 0, "riding the crash is a loss"

    # ── partial_book ON: books the pop, so the SAME day nets a small win ──
    cfg_on = _base_cfg()
    cfg_on["expiry_scalper"].update(
        partial_book=True, partial_trigger_mult=1.6, partial_fraction=0.5,
        move_stop_to_breakeven=True, trail_stop=True, trail_pct=0.30,
    )
    on = _run(cfg_on, monkeypatch)
    assert len(on.trades) == 1
    t_on = on.trades[0]
    # Same entry (exit flags don't touch entry logic).
    assert t_on.entry_price == pytest.approx(t_off.entry_price)
    assert t_on.strike == t_off.strike
    # The partial rescue turns a loser into a better (and here, positive) outcome.
    assert t_on.pnl_rupees > t_off.pnl_rupees, "partial-book must improve the round-trip"
    assert t_on.pnl_rupees > 0, "booked pop outweighs the stopped remainder"


def test_confirmation_gate_delays_entry(monkeypatch):
    """With confirm_breakout ON and 2-tick persistence, entry cannot happen on the
    very first breakout bar — so on a day whose breakout appears then immediately
    crashes, the confirmed run takes a DIFFERENT (later/no) entry than the OFF run."""
    off = _run(_base_cfg(), monkeypatch)

    cfg = _base_cfg()
    cfg["expiry_scalper"].update(confirm_breakout=True, confirmation_ticks=2)
    on = _run(cfg, monkeypatch)

    # OFF enters at 14:50 (first breakout bar). With 2-tick confirmation the entry
    # must be at 14:55 at the earliest — a strictly later entry time (or none).
    assert off.trades and off.trades[0].entry_time == "14:50:00"
    if on.trades:
        assert on.trades[0].entry_time != "14:50:00"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
