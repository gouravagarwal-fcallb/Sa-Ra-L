"""
Guard for the BRAHMASTRA order-path fix (2026-07-09).

Before the fix: entry orders were built with no price (paper filled at ₹0),
_poll_fill called a broker method that doesn't exist (fills never confirmed), and
monitoring/P&L ran off the INDEX spot instead of the option premium — all fantasy.

These tests lock in: (1) _poll_fill reads a real fill via get_order_status, and
(2) on_tick values unrealised P&L off the OPTION premium, not the index level.
"""
from datetime import datetime

from src.broker.paper_broker import PaperBroker
from src.broker.base import Order
from src.brahmastra.trading.trade_engine import TradeEngine, TradeRecord, TradeState


class _FakeQuoteBroker:
    def __init__(self, ltp): self._ltp = ltp
    def get_tradingsymbol(self, instrument, expiry, strike, opt):
        return f"{instrument}{strike}{opt}", "NFO"
    def get_ltp(self, *a, **k): return self._ltp


def test_poll_fill_reads_real_fill_via_get_order_status():
    pb = PaperBroker(quote_broker=_FakeQuoteBroker(120.0))
    order = Order(symbol="NIFTY24000CE", exchange="NFO", option_type="CE",
                  strike=24000, expiry="20260709", transaction="BUY",
                  quantity=65, price=120.0)
    oid = pb.place_order(order)                      # paper fill at 120 (+slippage)
    eng = TradeEngine.__new__(TradeEngine)
    eng._broker = pb
    fill = eng._poll_fill(oid, timeout=2)
    assert fill is not None and fill > 0             # old code returned None forever


def test_on_tick_pnl_uses_option_premium_not_index():
    eng = TradeEngine.__new__(TradeEngine)
    eng._trades = {}
    eng._book_t1_pct = 0.5
    eng._book_t2_pct = 0.25
    eng._session_pnl = 0.0
    eng._on_event = None
    eng._log_msg = lambda *a, **k: None
    eng.theta_per_minute = lambda *a, **k: 0.0
    # entry option premium 100; the option is now worth 130.
    eng._option_ltp = lambda trade: 130.0

    t = TradeRecord(
        trade_id="T1", scenario_id=1, instrument="NIFTY", hypothesis="BULL",
        option_type="CE", strike=24000, expiry="20260709", quantity=65, lots=1,
        lot_size=65, entry_price=100.0, current_price=100.0,
        sl_price=23800, target1=24500, target2=24700, target3=25000,
        high_since_entry=24000, low_since_entry=24000, state=TradeState.OPEN,
    )
    eng._trades["T1"] = t

    # index price ~24,050 — nowhere near the option premium; must NOT drive P&L.
    eng.on_tick(price=24050.0, atr_value=None, now=datetime(2026, 7, 9, 11, 0))

    # Unrealised P&L must be (130 − 100) × 65 = 1950 (option premium), NOT a number
    # derived from the ~24,050 index level.
    assert t.current_price == 130.0
    assert t.unrealised_pnl == (130.0 - 100.0) * 65
