"""
Regression guard for the closure-report exit-classification bug (2026-07-03).

The live engines log a close under its exit REASON — FORCE_CLOSE, STOP_LOSS,
TARGET_HIT, TRAILING_STOP, DAILY_LOSS_LIMIT, BE_STOP, EXIT_STOPLOSS, PARTIAL,
CLOSE — not a generic "EXIT" tag.  An earlier exact-match whitelist recognised
none of them, so every closed trade was dropped and the daily report showed
"0W / 0L / ₹0" on a day that actually lost ₹9,668.  These tests lock in that
each real exit reason is counted and that entry/scan rows never are.
"""
from src.api import closure_report as CR


# The 10 real closes from logs/trades_2026-07-03.csv (RAMS ×9 + BLACK_SWAN ×1).
_TODAY = [
    ("FORCE_CLOSE", -642.41), ("FORCE_CLOSE", 907.54), ("FORCE_CLOSE", -492.65),
    ("FORCE_CLOSE", 518.57),  ("STOP_LOSS", -8058.37), ("FORCE_CLOSE", -513.05),
    ("FORCE_CLOSE", -455.57), ("FORCE_CLOSE", -504.69), ("FORCE_CLOSE", -512.94),
    ("FORCE_CLOSE", 85.45),
]


def test_real_exit_reasons_are_recognised():
    for reason in ("FORCE_CLOSE", "STOP_LOSS", "TARGET_HIT", "TRAILING_STOP",
                   "DAILY_LOSS_LIMIT", "BE_STOP", "EXIT_STOPLOSS", "PARTIAL",
                   "CLOSE", "SQUAREOFF", "BOOKING", "SL"):
        assert CR._exit_event(reason), reason


def test_entry_and_scan_rows_are_not_exits():
    for ev in ("ENTRY", "ENTERED", "BUY", "SELL", "OPEN", "BAR_SCAN", "", None):
        assert not CR._exit_event(ev), ev
        assert not CR._is_exit_row({"event": ev, "pnl": ""}), ev


def test_pnl_backstop_counts_unknown_reason_with_pnl():
    # a reason string we don't explicitly tokenise, but it carries realised pnl
    assert CR._is_exit_row({"event": "WEIRD_NEW_REASON", "pnl": -12.5})
    # ...unless it's an entry row
    assert not CR._is_exit_row({"event": "ENTRY", "pnl": ""})


def test_today_tally_is_not_zero():
    trades = [{"strategy": "X", "event": e, "pnl": p, "exit_reason": e}
              for e, p in _TODAY]
    exits = [t for t in trades if CR._is_exit_row(t)]
    pnls = [t["pnl"] for t in exits]
    assert len(exits) == 10
    assert sum(1 for p in pnls if p > 0) == 3
    assert sum(1 for p in pnls if p < 0) == 7
    assert round(sum(pnls), 2) == -9668.12
