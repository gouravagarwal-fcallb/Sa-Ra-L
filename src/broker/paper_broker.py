"""
Paper Broker
─────────────
Simulates order execution without placing real orders.
Prints trade signals to terminal for manual execution on Zerodha.

All "fills" happen at the current LTP with a configurable slippage.
"""

from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional

from src.broker.base import BaseBroker, Order
from src.data.market_data import get_spot_price
from src.utils.logger import setup_logger

log = setup_logger("paper_broker")


class PaperBroker(BaseBroker):
    """Simulates fills; never places a real order.

    `quote_broker` (a live, read-only KiteBroker) is the important addition: when
    present, `get_ltp`/`get_tradingsymbol` return the REAL market quote/symbol so
    paper P&L reflects the actual option price, not a Black-Scholes model. The
    synthetic model is used ONLY as an offline fallback (sandbox / no Kite session
    / a quote that momentarily fails). Order placement stays 100% simulated."""

    def __init__(self, slippage_pct: float = 0.1, quote_broker=None):
        self.slippage_pct = slippage_pct / 100
        self._orders: dict[str, Order] = {}
        self._quote = quote_broker           # live KiteBroker for read-only quotes, or None
        try:
            from src.research.gti.kite_data import _Throttle
            self._throttle = _Throttle(0.2)  # keep well under Kite's ~3 req/s
        except Exception:
            self._throttle = None

    def get_tradingsymbol(self, instrument, expiry, strike, option_type):
        """Real NFO/BFO tradingsymbol when a live quote source exists, else a
        bare-index fallback (used only by the synthetic model path)."""
        if self._quote is not None:
            try:
                return self._quote.get_tradingsymbol(instrument, expiry, strike, option_type)
            except Exception:
                pass
        return instrument, ("NFO" if instrument == "NIFTY" else "BFO")

    def place_order(self, order: Order) -> str:
        order_id = str(uuid.uuid4())[:8].upper()
        fill_price = order.price * (1 + self.slippage_pct) if order.transaction == "BUY" \
            else order.price * (1 - self.slippage_pct)
        order.order_id = order_id
        order.status = "COMPLETE"
        order.avg_price = round(fill_price, 2)
        self._orders[order_id] = order

        banner = "=" * 60
        print(f"\n{banner}")
        print(f"  PAPER TRADE SIGNAL — {datetime.now().strftime('%H:%M:%S')}")
        print(f"  Action   : {order.transaction} {order.option_type}")
        print(f"  Instrument: {order.symbol} {order.strike} {order.option_type} {order.expiry}")
        print(f"  Exchange : {order.exchange}")
        print(f"  Qty      : {order.quantity:,}")
        print(f"  LTP      : ₹{order.price:.2f}")
        print(f"  Sim Fill : ₹{order.avg_price:.2f}  (slippage {self.slippage_pct*100:.1f}%)")
        print(f"  Order ID : {order_id}")
        print(f"{banner}\n")

        log.info(
            f"[PAPER] {order.transaction} {order.quantity} × "
            f"{order.symbol}{order.strike}{order.option_type} @ ₹{order.avg_price:.2f}"
        )
        return order_id

    def get_order_status(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def get_ltp(
        self,
        symbol: str,
        exchange: str,
        strike: int,
        option_type: str,
        expiry: str,
    ) -> float:
        """Real option LTP when a live quote source is wired (paper trades then
        price at the actual market, not a model). Returns 0.0 on a failed/absent
        quote so the caller can fall back to its own model. Only when NO live
        quote source exists at all (sandbox/offline) do we synthesise via
        Black-Scholes on the underlying index."""
        if self._quote is not None:
            try:
                if self._throttle is not None:
                    self._throttle.wait()
                px = self._quote.get_ltp(symbol, exchange, strike, option_type, expiry)
                return float(px) if px and float(px) > 0 else 0.0
            except Exception:
                return 0.0  # caller falls back to its strategy model

        # Offline fallback: Black-Scholes on the index (symbol is a bare index here).
        from src.data.market_data import get_india_vix
        from src.data.option_chain import synthetic_option_data
        from src.utils.market_calendar import days_to_expiry

        spot = get_spot_price(symbol)
        vix = get_india_vix()
        hours = days_to_expiry() * 6.25       # days × 6.25 trading hours
        opt_data = synthetic_option_data(spot, strike, option_type, hours, vix)
        return opt_data.ltp

    def get_positions(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status == "COMPLETE"]

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = "CANCELLED"
            return True
        return False
