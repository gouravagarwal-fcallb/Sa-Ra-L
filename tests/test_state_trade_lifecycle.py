"""
Regression guard for the dashboard state trade lifecycle (2026-07-06).

The live engines log a close under its exit REASON (FORCE_CLOSE, STOP_LOSS,
TARGET_HIT, TRAILING_STOP, DAILY_LOSS_LIMIT, BE_STOP, EXIT_STOPLOSS). An exact
`etype in (...)` set in record_trade_event() matched none of them, so a close
event fell through to the mark-update branch and the position was never moved
from open→closed — the "closed in the log, still OPEN in the Trades tab" bug.
These tests lock the open→closed transition for the real exit reasons.
"""
import pytest

from src.brahmastra.api.state import BrahmastraState


def _enter(st, strike, price=100.0):
    st.record_trade_event({"event": "ENTRY", "instrument": "NIFTY", "strike": strike,
                           "option_type": "CE", "quantity": 65, "price": price})


@pytest.mark.parametrize("reason", [
    "FORCE_CLOSE", "STOP_LOSS", "TARGET_HIT", "TRAILING_STOP",
    "DAILY_LOSS_LIMIT", "BE_STOP", "EXIT_STOPLOSS", "CLOSE", "SL",
])
def test_exit_reason_moves_open_to_closed(reason):
    st = BrahmastraState()
    _enter(st, 24300, 136.69)
    assert len(st.open_trades) == 1
    st.record_trade_event({"event": reason, "instrument": "NIFTY", "strike": 24300,
                           "option_type": "CE", "quantity": 65, "price": 151.08,
                           "pnl": 907.54, "reason": reason})
    assert len(st.open_trades) == 0, f"{reason} left the position OPEN"
    assert len(st.closed_trades) == 1
    assert st.closed_trades[-1]["net_pnl"] == 907.54


def test_entry_stays_open():
    st = BrahmastraState()
    _enter(st, 24300)
    assert len(st.open_trades) == 1
    assert len(st.closed_trades) == 0


def test_pnl_computed_when_engine_omits_it():
    st = BrahmastraState()
    _enter(st, 24300, 100.0)
    # no pnl on the exit → state computes (exit-entry)*qty for a long option
    st.record_trade_event({"event": "STOP_LOSS", "instrument": "NIFTY", "strike": 24300,
                           "option_type": "CE", "quantity": 65, "price": 90.0})
    assert len(st.open_trades) == 0
    assert st.closed_trades[-1]["net_pnl"] == pytest.approx((90.0 - 100.0) * 65)
