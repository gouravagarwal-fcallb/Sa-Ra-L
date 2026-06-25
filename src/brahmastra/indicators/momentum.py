"""
Momentum indicators: OBV, ROC, Aroon, Linear Regression Slope.
Used for volume-price confirmation and trend velocity measurement.
"""
from __future__ import annotations

from typing import Optional
from collections import deque
from dataclasses import dataclass
from src.brahmastra.data.bar_builder import Bar


# ─── OBV (On-Balance Volume) ──────────────────────────────────────────────────

class OBV:
    """
    On-Balance Volume: cumulative volume momentum.
    Rising OBV + rising price = institutional accumulation.
    Falling OBV + rising price = distribution (bearish divergence).
    """

    def __init__(self):
        self._obv:        float = 0.0
        self._prev_close: Optional[float] = None

    def update(self, bar: Bar) -> float:
        if self._prev_close is None:
            self._prev_close = bar.close
            return self._obv

        if bar.close > self._prev_close:
            self._obv += bar.volume
        elif bar.close < self._prev_close:
            self._obv -= bar.volume
        # equal: no change

        self._prev_close = bar.close
        return self._obv

    @property
    def value(self) -> float:
        return self._obv


# ─── ROC (Rate of Change) ─────────────────────────────────────────────────────

class ROC:
    """
    Rate of Change: (close - close[n]) / close[n] × 100.
    Momentum oscillator; positive = accelerating up, negative = down.
    """

    def __init__(self, period: int = 10):
        self.period  = period
        self._prices: deque[float] = deque(maxlen=period + 1)

    def update(self, bar: Bar) -> Optional[float]:
        self._prices.append(bar.close)
        if len(self._prices) <= self.period:
            return None
        prev = self._prices[0]
        return round((bar.close - prev) / prev * 100, 4) if prev else None

    @property
    def is_ready(self) -> bool:
        return len(self._prices) > self.period


# ─── Aroon ────────────────────────────────────────────────────────────────────

@dataclass
class AroonResult:
    up:       float    # 0–100: how recently was the highest high?
    down:     float    # 0–100: how recently was the lowest low?
    osc:      float    # up - down: +100=strong bull, -100=strong bear
    signal:   str      # 'BULL' | 'BEAR' | 'NEUTRAL'


class Aroon:
    """
    Aroon(25): measures how many bars ago the period high/low occurred.
    Aroon Up = (period - bars since high) / period × 100
    """

    def __init__(self, period: int = 25):
        self.period  = period
        self._highs: deque[float] = deque(maxlen=period + 1)
        self._lows:  deque[float] = deque(maxlen=period + 1)

    def update(self, bar: Bar) -> Optional[AroonResult]:
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        if len(self._highs) <= self.period:
            return None

        highs = list(self._highs)
        lows  = list(self._lows)
        # Index of max/min (most recent = last index = 0 bars ago)
        max_idx = len(highs) - 1 - highs[::-1].index(max(highs))
        min_idx = len(lows)  - 1 - lows[::-1].index(min(lows))
        bars_since_high = len(highs) - 1 - max_idx
        bars_since_low  = len(lows)  - 1 - min_idx

        up   = (self.period - bars_since_high) / self.period * 100
        down = (self.period - bars_since_low)  / self.period * 100
        osc  = up - down

        signal = "NEUTRAL"
        if up >= 70 and down <= 30:
            signal = "BULL"
        elif down >= 70 and up <= 30:
            signal = "BEAR"

        return AroonResult(
            up=round(up, 1), down=round(down, 1),
            osc=round(osc, 1), signal=signal,
        )


# ─── Linear Regression Slope ─────────────────────────────────────────────────

class LinearRegressionSlope:
    """
    Slope of linear regression line over `period` bars.
    Positive → upward trend velocity. Normalised as % of price.
    """

    def __init__(self, period: int = 20):
        self.period  = period
        self._prices: deque[float] = deque(maxlen=period)

    def update(self, bar: Bar) -> Optional[float]:
        self._prices.append(bar.close)
        if len(self._prices) < self.period:
            return None
        return self._slope()

    def _slope(self) -> float:
        prices = list(self._prices)
        n      = len(prices)
        x_mean = (n - 1) / 2
        y_mean = sum(prices) / n
        num    = sum((i - x_mean) * (prices[i] - y_mean) for i in range(n))
        den    = sum((i - x_mean) ** 2 for i in range(n))
        slope  = num / den if den else 0
        # Normalise: slope per bar as % of average price
        return round(slope / y_mean * 100, 6) if y_mean else 0
