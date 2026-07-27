"""
Standalone-engine backtest audit — regression tests for two fidelity fixes found
during the 2026-07-27 audit of the non-shared backtests:

  1. BRAHMASTRA: a daily bar that touches BOTH the stop and the target must book the
     STOP (conservative), not the target. Previously both-hit booked the winner.
  2. INRUSD: the technical indicators must be fed today's bar only AFTER the entry
     decision (which is taken at today's open) — no look-ahead.
"""
import inspect

from src.brahmastra.backtest.backtest_engine import _simulate_intraday_trade
import src.inrusd.inrusd_backtest as inrusd_bt


def test_brahmastra_both_hit_books_the_stop():
    # BULL: entry=100, atr=10 → sl=85, t1=120. A wide day (high 125, low 80) touches
    # BOTH. Conservative convention must book the STOP (SL_HIT), a loss.
    t = _simulate_intraday_trade(spot_open=100.0, spot_high=125.0, spot_low=80.0,
                                 spot_close=100.0, atr_approx=10.0, hypothesis="BULL")
    assert t is not None
    assert t['exit_reason'] == "SL_HIT", "both-hit daily bar must assume the stop filled first"


def test_brahmastra_clean_target_still_books_target():
    # A day that reaches the target but never the stop still books T1.
    t = _simulate_intraday_trade(spot_open=100.0, spot_high=125.0, spot_low=99.0,
                                 spot_close=124.0, atr_approx=10.0, hypothesis="BULL")
    assert t is not None
    assert t['exit_reason'] == "T1_HIT"


def test_inrusd_indicators_updated_after_decision_not_before():
    # Guard the look-ahead fix structurally: the run loop must NOT feed today's close
    # into the indicators before the entry-decision block. The update lines must sit
    # after the decision (near close_history.append), with the documented rationale.
    src = inspect.getsource(inrusd_bt)
    assert "LOOK-AHEAD FIX" in src
    # the rsi/atr/macd update must appear AFTER 'traded = False' (start of decision),
    # i.e. the update is no longer the first thing in the loop body.
    body = src.split("traded = False", 1)[1]
    assert "rsi14.update(cl)" in body and "atr14.update(hi, lo, cl)" in body, \
        "indicators must be updated after the entry decision, not before it"
