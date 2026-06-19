"""
Exit Logic
───────────
Manages when to exit an open options position.

Normal exit  : Premium up 25–30% from entry price
Windfall exit: When profit exceeds expected × windfall_multiplier,
               activate a trailing stop to protect original target.
Force exit   : At 15:20 IST (hard close before market end).
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.utils.logger import setup_logger

log = setup_logger("exit_logic")


class ExitReason(Enum):
    TARGET_HIT = "TARGET_HIT"
    TRAILING_STOP = "TRAILING_STOP"
    FORCE_CLOSE = "FORCE_CLOSE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    HOLDING = "HOLDING"


@dataclass
class ExitSignal:
    should_exit: bool = False
    reason: ExitReason = ExitReason.HOLDING
    exit_price: float = 0.0
    pnl_per_lot: float = 0.0
    description: str = ""


class ExitLogic:
    def __init__(self, config: dict):
        exit_cfg = config.get("exit", {})
        risk_cfg = config.get("risk", {})

        self.target_pct = exit_cfg.get("profit_target_pct", 27.5) / 100
        self.daily_loss_limit = risk_cfg.get("daily_loss_limit", 1_000_000)
        self.expected_daily_profit = risk_cfg.get("expected_daily_profit", 200_000)
        self.windfall_multiplier = risk_cfg.get("windfall_multiplier", 2.5)
        self.trail_drawdown_pct = risk_cfg.get("windfall_trail_drawdown_pct", 40) / 100

    def check_exit(
        self,
        entry_price: float,
        current_price: float,
        quantity: int,
        lot_size: int,
        cumulative_day_pnl: float,
        peak_trade_pnl: float,
        force_close: bool = False,
    ) -> ExitSignal:
        """
        Check whether to exit based on current option price.

        cumulative_day_pnl : sum of P&L from all closed trades today
        peak_trade_pnl     : highest unrealised P&L seen in this trade
        """
        lots = quantity // lot_size
        unrealised_pnl = (current_price - entry_price) * quantity

        # ── Force close (time-based or daily loss) ───────────────
        if force_close:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.FORCE_CLOSE,
                exit_price=current_price,
                pnl_per_lot=(current_price - entry_price) * lot_size,
                description=f"Force close | exit @ ₹{current_price:.2f}",
            )

        # ── Daily loss limit ─────────────────────────────────────
        total_pnl = cumulative_day_pnl + unrealised_pnl
        if total_pnl <= -self.daily_loss_limit:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.DAILY_LOSS_LIMIT,
                exit_price=current_price,
                pnl_per_lot=(current_price - entry_price) * lot_size,
                description=f"Daily loss limit ₹{self.daily_loss_limit:,.0f} hit",
            )

        # ── Normal profit target ─────────────────────────────────
        price_gain_pct = (current_price - entry_price) / entry_price
        if price_gain_pct >= self.target_pct:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.TARGET_HIT,
                exit_price=current_price,
                pnl_per_lot=(current_price - entry_price) * lot_size,
                description=(
                    f"Target hit: {price_gain_pct*100:.1f}% gain | "
                    f"Entry ₹{entry_price:.2f} → Exit ₹{current_price:.2f}"
                ),
            )

        # ── Windfall trailing stop ───────────────────────────────
        # Activated when day P&L exceeds expected_profit × multiplier
        windfall_threshold = self.expected_daily_profit * self.windfall_multiplier
        if (cumulative_day_pnl + unrealised_pnl) > windfall_threshold:
            # Protect: don't allow total day P&L to fall below expected_daily_profit
            trail_floor = self.expected_daily_profit
            if total_pnl < trail_floor:
                return ExitSignal(
                    should_exit=True,
                    reason=ExitReason.TRAILING_STOP,
                    exit_price=current_price,
                    pnl_per_lot=(current_price - entry_price) * lot_size,
                    description=(
                        f"Windfall trailing stop | Day P&L ₹{total_pnl:,.0f} < "
                        f"floor ₹{trail_floor:,.0f}"
                    ),
                )

        return ExitSignal(reason=ExitReason.HOLDING)

    def target_price(self, entry_price: float) -> float:
        """Calculate the target exit price given entry price."""
        return round(entry_price * (1 + self.target_pct), 2)
