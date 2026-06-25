"""
ADX — Average Directional Index (Wilder, 14-period).
Measures trend strength regardless of direction.
+DI/-DI lines show directional bias.
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass
from src.brahmastra.data.bar_builder import Bar


@dataclass
class ADXResult:
    adx:   float          # 0–100; >25 = trending, >40 = strong trend
    plus_di:  float       # +DI: bullish directional strength
    minus_di: float       # -DI: bearish directional strength
    trend:    str         # 'STRONG_BULL' | 'BULL' | 'STRONG_BEAR' | 'BEAR' | 'SIDEWAYS'


class ADX:
    """Wilder's ADX with +DI/-DI. Period default=14."""

    def __init__(self, period: int = 14):
        self.period    = period
        self._prev_high: Optional[float] = None
        self._prev_low:  Optional[float] = None
        self._prev_close: Optional[float] = None

        self._smooth_tr  = 0.0
        self._smooth_pdm = 0.0
        self._smooth_ndm = 0.0
        self._smooth_dx  = 0.0
        self._adx: Optional[float] = None
        self._step = 0
        self._result: Optional[ADXResult] = None

    def update(self, bar: Bar) -> Optional[ADXResult]:
        if self._prev_close is None:
            self._prev_high  = bar.high
            self._prev_low   = bar.low
            self._prev_close = bar.close
            return None

        tr  = max(bar.high - bar.low,
                  abs(bar.high - self._prev_close),
                  abs(bar.low  - self._prev_close))

        pdm = max(bar.high - self._prev_high, 0)
        ndm = max(self._prev_low - bar.low,   0)
        if pdm > ndm:
            ndm = 0.0
        elif ndm > pdm:
            pdm = 0.0
        else:
            pdm = ndm = 0.0

        self._step += 1

        if self._step < self.period:
            self._smooth_tr  += tr
            self._smooth_pdm += pdm
            self._smooth_ndm += ndm
            self._prev_high  = bar.high
            self._prev_low   = bar.low
            self._prev_close = bar.close
            return None

        if self._step == self.period:
            self._smooth_tr  += tr
            self._smooth_pdm += pdm
            self._smooth_ndm += ndm
        else:
            # Wilder smooth
            self._smooth_tr  = self._smooth_tr  - (self._smooth_tr  / self.period) + tr
            self._smooth_pdm = self._smooth_pdm - (self._smooth_pdm / self.period) + pdm
            self._smooth_ndm = self._smooth_ndm - (self._smooth_ndm / self.period) + ndm

        plus_di  = 100 * self._smooth_pdm / self._smooth_tr if self._smooth_tr else 0
        minus_di = 100 * self._smooth_ndm / self._smooth_tr if self._smooth_tr else 0
        dx_denom = plus_di + minus_di
        dx       = 100 * abs(plus_di - minus_di) / dx_denom if dx_denom else 0

        if self._adx is None and self._step == self.period:
            self._smooth_dx = dx
            self._adx       = dx
        else:
            self._adx = (self._adx * (self.period - 1) + dx) / self.period

        trend = "SIDEWAYS"
        if self._adx >= 40:
            trend = "STRONG_BULL" if plus_di > minus_di else "STRONG_BEAR"
        elif self._adx >= 25:
            trend = "BULL" if plus_di > minus_di else "BEAR"

        self._prev_high  = bar.high
        self._prev_low   = bar.low
        self._prev_close = bar.close

        self._result = ADXResult(
            adx      = round(self._adx, 2),
            plus_di  = round(plus_di, 2),
            minus_di = round(minus_di, 2),
            trend    = trend,
        )
        return self._result

    @property
    def value(self) -> Optional[ADXResult]:
        return self._result

    @property
    def is_ready(self) -> bool:
        return self._result is not None
