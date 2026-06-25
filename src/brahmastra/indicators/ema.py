"""
Exponential Moving Average (EMA).
Used directly and as a building block for MACD, Supertrend, ADX.
"""
from __future__ import annotations

from typing import Optional
from src.brahmastra.data.bar_builder import Bar


class EMA:
    """Single EMA that updates one bar at a time."""

    def __init__(self, period: int):
        self.period     = period
        self._k         = 2.0 / (period + 1)
        self._ema:      Optional[float] = None
        self._buf:      list[float] = []

    def update(self, bar: Bar) -> Optional[float]:
        return self.update_price(bar.close)

    def update_price(self, price: float) -> Optional[float]:
        if self._ema is None:
            self._buf.append(price)
            if len(self._buf) >= self.period:
                self._ema = sum(self._buf) / len(self._buf)
                self._buf = []
        else:
            self._ema = price * self._k + self._ema * (1 - self._k)
        return self._ema

    @property
    def value(self) -> Optional[float]:
        return self._ema

    @property
    def is_ready(self) -> bool:
        return self._ema is not None


class EMAStack:
    """
    Multiple EMAs computed together for MTF analysis.
    Common config: 9, 21, 50, 200 for trend structure.
    """

    def __init__(self, periods: list[int]):
        self._emas = {p: EMA(p) for p in periods}

    def update(self, bar: Bar) -> dict[int, Optional[float]]:
        return {p: e.update(bar) for p, e in self._emas.items()}

    def values(self) -> dict[int, Optional[float]]:
        return {p: e.value for p, e in self._emas.items()}

    def is_ready(self, period: int) -> bool:
        return self._emas[period].is_ready if period in self._emas else False

    def trend_structure(self) -> Optional[str]:
        """
        Returns 'BULL', 'BEAR', or 'MIXED' based on EMA ordering.
        Requires periods [9, 21, 50, 200] to be configured.
        """
        vals = self.values()
        required = [9, 21, 50, 200]
        if not all(vals.get(p) for p in required):
            return None
        e9, e21, e50, e200 = (vals[p] for p in required)
        if e9 > e21 > e50 > e200:
            return "BULL"
        if e9 < e21 < e50 < e200:
            return "BEAR"
        return "MIXED"


def ema_from_bars(bars: list[Bar], period: int) -> list[Optional[float]]:
    """Batch compute EMA series."""
    calc = EMA(period)
    return [calc.update(b) for b in bars]
