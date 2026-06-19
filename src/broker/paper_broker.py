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
    def __init__(self, slippage_pct: float = 0.1):
        self.slippage_pct = slippage_pct / 100
        self._orders: dict[str, Order] = {}

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
        """
        In paper mode we use Black-Scholes to estimate LTP.
        This requires current spot and VIX.
        """
        from src.data.market_data import get_india_vix
        from src.data.option_chain import synthetic_option_data
        from src.utils.market_calendar import days_to_expiry
        from datetime import date

        spot = get_spot_price(symbol)
        vix = get_india_vix()
        # Hours to expiry: days × 6.25 trading hours
        dte = days_to_expiry()
        hours = dte * 6.25
        opt_data = synthetic_option_data(spot, strike, option_type, hours, vix)
        return opt_data.ltp

    def get_positions(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status == "COMPLETE"]

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = "CANCELLED"
            return True
        return False
