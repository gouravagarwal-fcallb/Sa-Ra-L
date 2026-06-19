"""
Position Manager
─────────────────
Tracks all open and closed trades for the day.
Enforces daily loss limit and windfall trailing stop.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List

from src.utils.helpers import format_inr
from src.utils.logger import setup_logger

log = setup_logger("position_manager")


class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class Trade:
    trade_id: str
    window_id: str           # T1 | T2 | T3 | T4
    symbol: str
    option_type: str         # CE | PE
    strike: int
    quantity: int
    lot_size: int
    entry_price: float
    entry_time: datetime
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    status: TradeStatus = TradeStatus.OPEN

    @property
    def lots(self) -> int:
        return self.quantity // self.lot_size

    @property
    def pnl(self) -> float:
        price = self.exit_price if self.status == TradeStatus.CLOSED else 0.0
        return (price - self.entry_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        price = self.exit_price if self.status == TradeStatus.CLOSED else 0.0
        return (price - self.entry_price) / self.entry_price * 100

    def close(self, exit_price: float, exit_time: datetime, reason: str) -> None:
        self.exit_price = exit_price
        self.exit_time = exit_time
        self.exit_reason = reason
        self.status = TradeStatus.CLOSED
        log.info(
            f"CLOSED {self.trade_id} | {self.symbol} {self.strike}{self.option_type} | "
            f"Entry ₹{self.entry_price:.2f} → Exit ₹{self.exit_price:.2f} | "
            f"P&L {format_inr(self.pnl)} ({self.pnl_pct:.1f}%) | {reason}"
        )


class PositionManager:
    def __init__(self, config: dict):
        risk = config.get("risk", {})
        self.daily_loss_limit = risk.get("daily_loss_limit", 1_000_000)
        self.expected_daily_profit = risk.get("expected_daily_profit", 200_000)
        self.windfall_multiplier = risk.get("windfall_multiplier", 2.5)

        self.trades: List[Trade] = []
        self._trade_counter = 0
        self.peak_day_pnl = 0.0
        self.day_stopped = False  # True when daily loss limit breached

    def new_trade_id(self) -> str:
        self._trade_counter += 1
        return f"TRD-{datetime.now().strftime('%Y%m%d')}-{self._trade_counter:02d}"

    def open_trade(
        self,
        window_id: str,
        symbol: str,
        option_type: str,
        strike: int,
        quantity: int,
        lot_size: int,
        entry_price: float,
    ) -> Trade:
        trade = Trade(
            trade_id=self.new_trade_id(),
            window_id=window_id,
            symbol=symbol,
            option_type=option_type,
            strike=strike,
            quantity=quantity,
            lot_size=lot_size,
            entry_price=entry_price,
            entry_time=datetime.now(),
        )
        self.trades.append(trade)
        log.info(
            f"OPENED {trade.trade_id} | {symbol} {strike}{option_type} | "
            f"Qty {quantity} ({trade.lots} lots) @ ₹{entry_price:.2f}"
        )
        return trade

    def close_trade(self, trade: Trade, exit_price: float, reason: str) -> None:
        trade.close(exit_price, datetime.now(), reason)
        new_pnl = self.realised_pnl
        if new_pnl > self.peak_day_pnl:
            self.peak_day_pnl = new_pnl

        if self.realised_pnl <= -self.daily_loss_limit:
            self.day_stopped = True
            log.warning(
                f"DAILY LOSS LIMIT HIT: {format_inr(self.realised_pnl)} — "
                "no more trades today"
            )

    @property
    def open_trades(self) -> List[Trade]:
        return [t for t in self.trades if t.status == TradeStatus.OPEN]

    @property
    def closed_trades(self) -> List[Trade]:
        return [t for t in self.trades if t.status == TradeStatus.CLOSED]

    @property
    def realised_pnl(self) -> float:
        return sum(t.pnl for t in self.closed_trades)

    @property
    def windfall_active(self) -> bool:
        return self.realised_pnl >= self.expected_daily_profit * self.windfall_multiplier

    def can_trade(self) -> bool:
        if self.day_stopped:
            return False
        if self.realised_pnl <= -self.daily_loss_limit:
            self.day_stopped = True
            return False
        return True

    def trade_count_for_window(self, window_id: str) -> int:
        return sum(1 for t in self.trades if t.window_id == window_id)

    def summary(self) -> str:
        lines = [
            f"  Trades today  : {len(self.trades)} ({len(self.closed_trades)} closed, {len(self.open_trades)} open)",
            f"  Realised P&L  : {format_inr(self.realised_pnl)}",
            f"  Peak day P&L  : {format_inr(self.peak_day_pnl)}",
            f"  Windfall mode : {'YES' if self.windfall_active else 'No'}",
            f"  Trading active: {'NO — STOPPED' if self.day_stopped else 'YES'}",
        ]
        return "\n".join(lines)
