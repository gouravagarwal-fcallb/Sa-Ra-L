"""Abstract broker interface — both PaperBroker and KiteBroker implement this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Order:
    symbol: str
    exchange: str
    option_type: str       # CE | PE
    strike: int
    expiry: str            # YYYYMMDD
    transaction: str       # BUY | SELL
    quantity: int
    order_type: str = "MARKET"
    price: float = 0.0
    order_id: str = ""
    status: str = "PENDING"
    avg_price: float = 0.0


class BaseBroker(ABC):

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """Place order; return order_id."""

    @abstractmethod
    def get_order_status(self, order_id: str) -> Order:
        """Return current order state."""

    @abstractmethod
    def get_ltp(self, symbol: str, exchange: str, strike: int, option_type: str, expiry: str) -> float:
        """Return last traded price for an option."""

    @abstractmethod
    def get_positions(self) -> list[Order]:
        """Return all open positions."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
