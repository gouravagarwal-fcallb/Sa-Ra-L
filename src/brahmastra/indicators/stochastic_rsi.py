"""
Stochastic RSI — RSI normalised within its own min/max range.
Faster and more sensitive to overbought/oversold extremes than plain RSI.
Standard: RSI(14) → Stoch window 14 → K smoothing 3 → D smoothing 3.
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass
from collections import deque
from src.brahmastra.indicators.rsi import RSI
from src.brahmastra.data.bar_builder import Bar


@dataclass
class StochRSIResult:
    k:         float    # %K smoothed
    d:         float    # %D = EMA of K
    raw:       float    # unsmoothed Stoch RSI value
    signal:    Optional[str] = None   # 'BULLISH_CROSS' | 'BEARISH_CROSS'


class StochasticRSI:
    """
    Stoch RSI: apply Stochastic formula to RSI values, then smooth K and D.
    """

    def __init__(self, rsi_period: int = 14, stoch_period: int = 14,
                 k_smooth: int = 3, d_smooth: int = 3):
        self._rsi        = RSI(rsi_period)
        self._stoch_win  = stoch_period
        self._k_smooth   = k_smooth
        self._d_smooth   = d_smooth

        self._rsi_buf:   deque[float] = deque(maxlen=stoch_period)
        self._k_buf:     deque[float] = deque(maxlen=k_smooth)
        self._d_buf:     deque[float] = deque(maxlen=d_smooth)

        self._prev_k:    Optional[float] = None
        self._prev_d:    Optional[float] = None
        self._result:    Optional[StochRSIResult] = None

    def update(self, bar: Bar) -> Optional[StochRSIResult]:
        rsi_val = self._rsi.update(bar)
        if rsi_val is None:
            return None

        self._rsi_buf.append(rsi_val)
        if len(self._rsi_buf) < self._stoch_win:
            return None

        rsi_min  = min(self._rsi_buf)
        rsi_max  = max(self._rsi_buf)
        rng      = rsi_max - rsi_min
        raw_stoch = (rsi_val - rsi_min) / rng if rng > 0 else 0.5

        self._k_buf.append(raw_stoch)
        k = sum(self._k_buf) / len(self._k_buf)

        self._d_buf.append(k)
        d = sum(self._d_buf) / len(self._d_buf)

        signal = None
        if self._prev_k is not None and self._prev_d is not None:
            if self._prev_k <= self._prev_d and k > d:
                signal = "BULLISH_CROSS"
            elif self._prev_k >= self._prev_d and k < d:
                signal = "BEARISH_CROSS"

        self._prev_k = k
        self._prev_d = d

        self._result = StochRSIResult(
            k      = round(k * 100, 2),
            d      = round(d * 100, 2),
            raw    = round(raw_stoch * 100, 2),
            signal = signal,
        )
        return self._result

    @property
    def value(self) -> Optional[StochRSIResult]:
        return self._result

    @property
    def is_ready(self) -> bool:
        return self._result is not None
