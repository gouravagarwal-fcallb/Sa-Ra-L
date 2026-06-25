"""
Bollinger Bands — 20-period SMA ± 2σ.
Squeeze detection and %B (price position within bands).
"""
from __future__ import annotations

import math
from typing import Optional
from dataclasses import dataclass
from collections import deque
from src.brahmastra.data.bar_builder import Bar


@dataclass
class BBResult:
    upper:    float
    middle:   float    # SMA
    lower:    float
    width:    float    # (upper - lower) / middle — normalised bandwidth
    pct_b:    float    # (price - lower) / (upper - lower); 0=lower, 1=upper
    squeeze:  bool     # bandwidth < 20-bar average bandwidth
    breakout: Optional[str] = None   # 'UP' | 'DOWN' | None (price outside bands)


class BollingerBands:
    """
    Standard Bollinger Bands: period=20, multiplier=2.0.
    Squeeze = bandwidth below its own 20-bar average (Keltner not needed).
    """

    def __init__(self, period: int = 20, multiplier: float = 2.0,
                 squeeze_lookback: int = 20):
        self.period    = period
        self.mult      = multiplier
        self._prices:  deque[float] = deque(maxlen=period)
        self._widths:  deque[float] = deque(maxlen=squeeze_lookback)
        self._result:  Optional[BBResult] = None

    def update(self, bar: Bar) -> Optional[BBResult]:
        self._prices.append(bar.close)
        if len(self._prices) < self.period:
            return None

        prices = list(self._prices)
        sma    = sum(prices) / self.period
        std    = math.sqrt(sum((p - sma) ** 2 for p in prices) / self.period)
        upper  = sma + self.mult * std
        lower  = sma - self.mult * std
        width  = (upper - lower) / sma if sma else 0

        self._widths.append(width)
        avg_width = sum(self._widths) / len(self._widths)
        squeeze   = width < avg_width

        pct_b = (bar.close - lower) / (upper - lower) if (upper - lower) else 0.5

        breakout = None
        if bar.close > upper:
            breakout = "UP"
        elif bar.close < lower:
            breakout = "DOWN"

        self._result = BBResult(
            upper    = round(upper, 2),
            middle   = round(sma, 2),
            lower    = round(lower, 2),
            width    = round(width, 4),
            pct_b    = round(pct_b, 4),
            squeeze  = squeeze,
            breakout = breakout,
        )
        return self._result

    @property
    def value(self) -> Optional[BBResult]:
        return self._result

    @property
    def is_ready(self) -> bool:
        return self._result is not None
