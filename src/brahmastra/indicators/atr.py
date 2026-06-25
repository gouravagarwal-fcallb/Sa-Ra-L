"""
Average True Range (ATR) — Wilder's method.
Foundation for Supertrend, position sizing, and dynamic SL calculation.
"""
from __future__ import annotations

from typing import Optional
from src.brahmastra.data.bar_builder import Bar


class ATR:
    """
    Wilder's smoothed ATR (not simple moving average).
    Accepts Bar objects one at a time; maintains rolling state.
    """

    def __init__(self, period: int = 14):
        self.period   = period
        self._atr:    Optional[float] = None
        self._prev_close: Optional[float] = None
        self._buf:    list[float] = []   # warm-up buffer

    def update(self, bar: Bar) -> Optional[float]:
        """Feed one completed bar. Returns current ATR or None during warmup."""
        tr = self._true_range(bar.high, bar.low, self._prev_close)
        self._prev_close = bar.close

        if self._atr is None:
            self._buf.append(tr)
            if len(self._buf) >= self.period:
                self._atr = sum(self._buf) / len(self._buf)
                self._buf = []
        else:
            self._atr = (self._atr * (self.period - 1) + tr) / self.period

        return self._atr

    @staticmethod
    def _true_range(high: float, low: float, prev_close: Optional[float]) -> float:
        if prev_close is None:
            return high - low
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    @property
    def value(self) -> Optional[float]:
        return self._atr

    @property
    def is_ready(self) -> bool:
        return self._atr is not None


def atr_from_bars(bars: list[Bar], period: int = 14) -> list[Optional[float]]:
    """Batch compute ATR series from a list of completed bars."""
    calc = ATR(period)
    return [calc.update(b) for b in bars]
