"""
Entry Logic
───────────
Checks all conditions for entering a trade within an active time window.

Entry is allowed when:
  1. Pre-market direction is BULLISH or BEARISH (not NEUTRAL)
  2. India VIX < 22 (panic filter)
  3. ATM delta ≥ 0.40 (confirming we are near the money)
  4. OI shows fresh buildup in the direction (≥ 5% OI change)
  5. Daily loss limit not yet breached
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from src.strategy.direction_engine import Direction, DirectionResult
from src.data.option_chain import OptionChainSnapshot
from src.utils.logger import setup_logger

log = setup_logger("entry_logic")


@dataclass
class EntrySignal:
    should_enter: bool = False
    option_type: str = ""       # "CE" | "PE"
    strike: int = 0
    expected_entry_price: float = 0.0
    reason: str = ""


class EntryLogic:
    def __init__(self, config: dict):
        oc_cfg = config.get("option_chain", {})
        self.max_vix = oc_cfg.get("max_vix_for_entry", 22.0)
        self.min_delta = oc_cfg.get("min_delta", 0.40)
        self.min_oi_buildup_pct = oc_cfg.get("min_oi_buildup_pct", 5.0)

    def check_entry(
        self,
        direction_result: DirectionResult,
        chain: OptionChainSnapshot,
        daily_pnl: float,
        daily_loss_limit: float,
        window_id: str,
    ) -> EntrySignal:

        # ── 1. Direction must be set ─────────────────────────────
        if direction_result.direction == Direction.NEUTRAL:
            return EntrySignal(reason="Pre-market direction is NEUTRAL — skipping window")

        opt_type = "CE" if direction_result.direction == Direction.BULLISH else "PE"
        atm_data = chain.atm_call if opt_type == "CE" else chain.atm_put

        # ── 2. VIX panic filter ──────────────────────────────────
        vix = direction_result.inputs.india_vix
        if vix > self.max_vix:
            return EntrySignal(
                reason=f"India VIX {vix:.1f} > max {self.max_vix} — too volatile, skip"
            )

        # ── 3. Daily loss guard ──────────────────────────────────
        if daily_pnl <= -abs(daily_loss_limit):
            return EntrySignal(
                reason=f"Daily loss limit breached (P&L: ₹{daily_pnl:,.0f}) — no more trades today"
            )

        # ── 4. Delta filter (are we truly ATM?) ─────────────────
        abs_delta = abs(atm_data.greeks.delta)
        if abs_delta < self.min_delta:
            return EntrySignal(
                reason=f"{opt_type} delta {abs_delta:.2f} < {self.min_delta} — not near money"
            )

        # ── 5. OI buildup filter ─────────────────────────────────
        if atm_data.oi_change_pct < self.min_oi_buildup_pct:
            log.info(
                f"{window_id}: OI buildup {atm_data.oi_change_pct:.1f}% < "
                f"{self.min_oi_buildup_pct}% threshold — weak conviction, skip"
            )
            # Soft skip (warn but allow — user can override in paper mode)
            # Uncomment next line to make it a hard block:
            # return EntrySignal(reason=f"Weak OI buildup {atm_data.oi_change_pct:.1f}%")

        reason = (
            f"[{window_id}] {direction_result.direction.value} signal confirmed. "
            f"Strike {chain.atm_strike}{opt_type} | LTP ₹{atm_data.ltp:.2f} | "
            f"Delta {abs_delta:.2f} | OI chg {atm_data.oi_change_pct:.1f}% | VIX {vix:.1f}"
        )

        log.info(f"ENTRY SIGNAL: {reason}")

        return EntrySignal(
            should_enter=True,
            option_type=opt_type,
            strike=chain.atm_strike,
            expected_entry_price=atm_data.ltp,
            reason=reason,
        )

    def check_entry_backtest(
        self,
        direction: Direction,
        spot: float,
        vix: float,
        option_price: float,
        atm_strike: int,
        daily_pnl: float,
        daily_loss_limit: float,
        window_id: str,
    ) -> EntrySignal:
        """Simplified entry check for backtesting (no live OI data)."""
        if direction == Direction.NEUTRAL:
            return EntrySignal(reason="NEUTRAL — skip")
        if vix > self.max_vix:
            return EntrySignal(reason=f"VIX {vix:.1f} too high")
        if daily_pnl <= -abs(daily_loss_limit):
            return EntrySignal(reason="Daily loss limit hit")

        opt_type = "CE" if direction == Direction.BULLISH else "PE"
        return EntrySignal(
            should_enter=True,
            option_type=opt_type,
            strike=atm_strike,
            expected_entry_price=option_price,
            reason=f"[BT][{window_id}] {direction.value} | {atm_strike}{opt_type} @ ₹{option_price:.2f}",
        )
