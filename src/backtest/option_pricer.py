"""
Black-Scholes Option Pricer for backtesting.

Converts historical Nifty spot + India VIX → theoretical option prices.
VIX is used as a proxy for at-the-money implied volatility.

Key assumptions:
  - VIX ≈ 30-day forward implied vol for ATM Nifty options (close but not exact)
  - Risk-free rate = 6.5% per annum
  - No dividends on Nifty (uses total return index in practice, close enough for options)
"""

from __future__ import annotations
import math
import numpy as np
from scipy.stats import norm
from dataclasses import dataclass


@dataclass
class PricedOption:
    strike: int
    option_type: str        # CE | PE
    price: float
    delta: float
    gamma: float
    theta: float            # per calendar day
    vega: float             # per 1% IV change
    iv: float               # annualised IV used
    T_hours: float          # time to expiry in hours
    spot: float


class OptionPricer:
    def __init__(self, risk_free_rate: float = 0.065):
        self.r = risk_free_rate

    def _T_years(self, hours: float) -> float:
        return max(hours / (365 * 24), 1e-8)

    def price(
        self,
        spot: float,
        strike: int,
        vix: float,
        T_hours: float,
        option_type: str = "CE",
    ) -> PricedOption:
        sigma = vix / 100
        T = self._T_years(T_hours)
        S, K = spot, float(strike)
        r = self.r

        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        nd1 = norm.pdf(d1)
        sqrt_T = math.sqrt(T)

        if option_type == "CE":
            price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
            delta = norm.cdf(d1)
        else:
            price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            delta = norm.cdf(d1) - 1

        gamma = nd1 / (S * sigma * sqrt_T)
        theta_annual = (
            -(S * nd1 * sigma) / (2 * sqrt_T)
            - r * K * math.exp(-r * T) * (norm.cdf(d2) if option_type == "CE" else norm.cdf(-d2))
        )
        theta_daily = theta_annual / 365
        vega = S * nd1 * sqrt_T / 100

        return PricedOption(
            strike=strike,
            option_type=option_type,
            price=max(0.0, round(price, 2)),
            delta=round(delta, 4),
            gamma=round(gamma, 6),
            theta=round(theta_daily, 4),
            vega=round(vega, 4),
            iv=round(vix, 2),
            T_hours=T_hours,
            spot=spot,
        )

    def atm_strike(self, spot: float, step: int = 50) -> int:
        return int(round(spot / step) * step)

    def hours_to_expiry(self, current_hour: float, current_minute: float) -> float:
        """
        Hours remaining until 15:30 on expiry day.
        current_hour / current_minute: e.g., 9, 22 → 9:22 AM
        """
        market_close_hour = 15.5  # 15:30
        current_decimal = current_hour + current_minute / 60
        return max(0.0, (market_close_hour - current_decimal))

    def simulate_trade(
        self,
        spot_at_entry: float,
        spot_path: list[float],
        vix: float,
        option_type: str,
        entry_hour: float,
        entry_minute: float,
        target_pct: float = 0.275,
        stop_loss_pct: float = 0.30,
        force_exit_hour: float = 15.33,
    ) -> dict:
        """
        Simulate a single trade along a spot price path.
        spot_path: list of subsequent spot prices (at 5-min intervals)
        Returns dict with entry_price, exit_price, pnl_pct, exit_reason, holding_minutes
        """
        strike = self.atm_strike(spot_at_entry)
        T0 = self.hours_to_expiry(entry_hour, entry_minute)
        entry_opt = self.price(spot_at_entry, strike, vix, T0, option_type)
        entry_price = entry_opt.price

        if entry_price < 0.5:
            return {"valid": False, "reason": "Entry price too low (< 0.5)"}

        target_price = entry_price * (1 + target_pct)
        stop_price = entry_price * (1 - stop_loss_pct)
        interval_minutes = 5
        current_minute_offset = 0

        for i, spot in enumerate(spot_path):
            current_minute_offset += interval_minutes
            elapsed_hours = current_minute_offset / 60
            T_remaining = T0 - elapsed_hours
            current_hour = entry_hour + entry_minute / 60 + elapsed_hours

            if current_hour >= force_exit_hour:
                current_price = self.price(spot, strike, vix, max(T_remaining, 0), option_type).price
                return {
                    "valid": True,
                    "entry_price": entry_price,
                    "exit_price": current_price,
                    "pnl_pct": (current_price - entry_price) / entry_price * 100,
                    "exit_reason": "FORCE_CLOSE",
                    "holding_minutes": current_minute_offset,
                    "strike": strike,
                }

            current_price = self.price(spot, strike, vix, max(T_remaining, 0), option_type).price

            if current_price >= target_price:
                return {
                    "valid": True,
                    "entry_price": entry_price,
                    "exit_price": current_price,
                    "pnl_pct": (current_price - entry_price) / entry_price * 100,
                    "exit_reason": "TARGET_HIT",
                    "holding_minutes": current_minute_offset,
                    "strike": strike,
                }

            if current_price <= stop_price:
                return {
                    "valid": True,
                    "entry_price": entry_price,
                    "exit_price": current_price,
                    "pnl_pct": (current_price - entry_price) / entry_price * 100,
                    "exit_reason": "STOP_LOSS",
                    "holding_minutes": current_minute_offset,
                    "strike": strike,
                }

        # End of path without target
        last_price = self.price(spot_path[-1], strike, vix, 0.01, option_type).price
        return {
            "valid": True,
            "entry_price": entry_price,
            "exit_price": last_price,
            "pnl_pct": (last_price - entry_price) / entry_price * 100,
            "exit_reason": "END_OF_DATA",
            "holding_minutes": len(spot_path) * interval_minutes,
            "strike": strike,
        }
