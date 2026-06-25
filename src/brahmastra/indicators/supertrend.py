"""
Supertrend — ATR-based dynamic support/resistance trend filter.
Default: ATR(10) × 3.0 multiplier.
Flip direction signals are high-conviction trend change confirmations.
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass
from src.brahmastra.data.bar_builder import Bar
from src.brahmastra.indicators.atr import ATR


@dataclass
class SupertrendResult:
    value:     float         # the supertrend line value
    direction: str           # 'UP' (bullish) | 'DOWN' (bearish)
    flipped:   bool          # True if direction changed this bar
    distance:  float         # abs(close - supertrend) in points


class Supertrend:
    """
    Supertrend computed using Wilder ATR.
    direction=UP  → close above supertrend line (bullish)
    direction=DOWN → close below supertrend line (bearish)
    """

    def __init__(self, atr_period: int = 10, multiplier: float = 3.0):
        self._atr      = ATR(atr_period)
        self._mult     = multiplier

        self._prev_close:     Optional[float] = None
        self._prev_upper:     Optional[float] = None
        self._prev_lower:     Optional[float] = None
        self._prev_direction: Optional[str]   = None
        self._result:         Optional[SupertrendResult] = None

    def update(self, bar: Bar) -> Optional[SupertrendResult]:
        atr_val = self._atr.update(bar)
        if atr_val is None:
            self._prev_close = bar.close
            return None

        hl2    = (bar.high + bar.low) / 2
        upper  = hl2 + self._mult * atr_val
        lower  = hl2 - self._mult * atr_val

        # Adjust bands so they only tighten (Supertrend ratchet)
        if self._prev_upper is not None:
            upper = min(upper, self._prev_upper) if (
                self._prev_close is not None and self._prev_close < self._prev_upper
            ) else upper

        if self._prev_lower is not None:
            lower = max(lower, self._prev_lower) if (
                self._prev_close is not None and self._prev_close > self._prev_lower
            ) else lower

        # Determine direction
        if self._prev_direction is None:
            direction = "UP" if bar.close > upper else "DOWN"
        elif self._prev_direction == "UP":
            direction = "DOWN" if bar.close < lower else "UP"
        else:
            direction = "UP" if bar.close > upper else "DOWN"

        st_value  = lower if direction == "UP" else upper
        flipped   = self._prev_direction is not None and direction != self._prev_direction
        distance  = abs(bar.close - st_value)

        self._prev_close     = bar.close
        self._prev_upper     = upper
        self._prev_lower     = lower
        self._prev_direction = direction

        self._result = SupertrendResult(
            value     = round(st_value, 2),
            direction = direction,
            flipped   = flipped,
            distance  = round(distance, 2),
        )
        return self._result

    @property
    def value(self) -> Optional[SupertrendResult]:
        return self._result

    @property
    def is_ready(self) -> bool:
        return self._result is not None
