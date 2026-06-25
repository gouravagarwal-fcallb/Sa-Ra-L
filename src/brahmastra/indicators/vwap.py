"""
VWAP — Volume Weighted Average Price with σ bands.
Resets daily at 9:15 AM IST. Includes ±1σ, ±2σ bands.
"""
from __future__ import annotations

from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from src.brahmastra.data.bar_builder import Bar

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class VWAPResult:
    vwap:    float
    upper1:  float   # vwap + 1σ
    lower1:  float   # vwap - 1σ
    upper2:  float   # vwap + 2σ
    lower2:  float   # vwap - 2σ
    position: str    # 'ABOVE' | 'BELOW' | 'AT'  (±0.05% of vwap)


class VWAP:
    """
    Session VWAP that resets at 9:15 AM each trading day.
    Fed with completed bars; uses bar's vwap × volume for cumulation.
    """

    def __init__(self):
        self._cum_pv:   float = 0.0   # cumulative price × volume
        self._cum_v:    float = 0.0   # cumulative volume
        self._cum_pv2:  float = 0.0   # cumulative price² × volume (for σ)
        self._session_date = None
        self._result:   Optional[VWAPResult] = None

    def update(self, bar: Bar) -> Optional[VWAPResult]:
        # Reset on new session (9:15 AM IST)
        bar_date = bar.ts_open.date()
        if bar_date != self._session_date:
            self._cum_pv   = 0.0
            self._cum_v    = 0.0
            self._cum_pv2  = 0.0
            self._session_date = bar_date

        price = bar.vwap if bar.vwap else bar.close
        vol   = bar.volume or 1.0   # avoid zero division

        self._cum_pv  += price * vol
        self._cum_v   += vol
        self._cum_pv2 += price * price * vol

        vwap = self._cum_pv / self._cum_v
        variance = (self._cum_pv2 / self._cum_v) - (vwap ** 2)
        sigma    = max(variance, 0.0) ** 0.5

        position = "AT"
        if bar.close > vwap * 1.0005:
            position = "ABOVE"
        elif bar.close < vwap * 0.9995:
            position = "BELOW"

        self._result = VWAPResult(
            vwap    = round(vwap, 2),
            upper1  = round(vwap + sigma, 2),
            lower1  = round(vwap - sigma, 2),
            upper2  = round(vwap + 2 * sigma, 2),
            lower2  = round(vwap - 2 * sigma, 2),
            position = position,
        )
        return self._result

    @property
    def value(self) -> Optional[VWAPResult]:
        return self._result

    @property
    def is_ready(self) -> bool:
        return self._result is not None
