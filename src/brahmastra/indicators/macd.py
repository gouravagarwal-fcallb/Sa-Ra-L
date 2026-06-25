"""
MACD — Moving Average Convergence/Divergence.
Standard 12/26/9 configuration.
Detects crossovers, zero-line crosses, histogram momentum shifts.
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass
from src.brahmastra.data.bar_builder import Bar
from src.brahmastra.indicators.ema import EMA


@dataclass
class MACDResult:
    macd:      float   # fast EMA - slow EMA
    signal:    float   # EMA of MACD
    histogram: float   # MACD - signal
    crossover: Optional[str] = None   # 'BULLISH' | 'BEARISH' | None
    zero_cross: Optional[str] = None  # 'UP' | 'DOWN' | None


class MACD:
    """
    Standard MACD: fast=12, slow=26, signal=9.
    Detects bullish/bearish crossovers and zero-line crosses.
    """

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self._fast   = EMA(fast)
        self._slow   = EMA(slow)
        self._signal = EMA(signal)

        self._prev_macd:   Optional[float] = None
        self._prev_signal: Optional[float] = None
        self._result:      Optional[MACDResult] = None

    def update(self, bar: Bar) -> Optional[MACDResult]:
        fast_val = self._fast.update(bar)
        slow_val = self._slow.update(bar)

        if fast_val is None or slow_val is None:
            return None

        macd_line = fast_val - slow_val
        sig_val   = self._signal.update_price(macd_line)

        if sig_val is None:
            self._prev_macd = macd_line
            return None

        histogram = macd_line - sig_val

        # Crossover detection
        crossover = None
        if self._prev_macd is not None and self._prev_signal is not None:
            if self._prev_macd <= self._prev_signal and macd_line > sig_val:
                crossover = "BULLISH"
            elif self._prev_macd >= self._prev_signal and macd_line < sig_val:
                crossover = "BEARISH"

        # Zero-line cross
        zero_cross = None
        if self._prev_macd is not None:
            if self._prev_macd <= 0 and macd_line > 0:
                zero_cross = "UP"
            elif self._prev_macd >= 0 and macd_line < 0:
                zero_cross = "DOWN"

        self._prev_macd   = macd_line
        self._prev_signal = sig_val

        self._result = MACDResult(
            macd      = round(macd_line, 4),
            signal    = round(sig_val, 4),
            histogram = round(histogram, 4),
            crossover = crossover,
            zero_cross = zero_cross,
        )
        return self._result

    @property
    def value(self) -> Optional[MACDResult]:
        return self._result

    @property
    def is_ready(self) -> bool:
        return self._result is not None

    def histogram_expanding(self) -> Optional[bool]:
        """True if histogram is growing in magnitude (momentum strengthening)."""
        if self._result is None:
            return None
        if self._prev_macd is None or self._prev_signal is None:
            return None
        prev_hist = self._prev_macd - self._prev_signal
        return abs(self._result.histogram) > abs(prev_hist)
