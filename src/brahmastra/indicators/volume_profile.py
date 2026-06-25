"""
Volume Profile — price histogram weighted by volume.
Identifies High Volume Nodes (HVN) and Low Volume Nodes (LVN).
Point of Control (POC): price level with highest traded volume.
Value Area: 70% of volume traded (VA High and VA Low).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict
from src.brahmastra.data.bar_builder import Bar


@dataclass
class VolumeProfileResult:
    poc:        float              # Point of Control (peak volume price)
    va_high:    float              # Value Area High
    va_low:     float              # Value Area Low
    hvn:        list[float]        # High Volume Node levels (±1 bucket of POC)
    lvn:        list[float]        # Low Volume Node gaps
    total_vol:  float
    buckets:    dict[float, float] = field(default_factory=dict)  # price→volume

    def is_near_poc(self, price: float, tolerance_pct: float = 0.1) -> bool:
        return abs(price - self.poc) / self.poc * 100 <= tolerance_pct

    def is_in_value_area(self, price: float) -> bool:
        return self.va_low <= price <= self.va_high

    def zone(self, price: float) -> str:
        """'POC' | 'VALUE_AREA' | 'HVN' | 'LVN' | 'OUTSIDE'"""
        if self.is_near_poc(price):
            return "POC"
        if self.is_in_value_area(price):
            return "VALUE_AREA"
        for h in self.hvn:
            if abs(price - h) / price * 100 <= 0.15:
                return "HVN"
        for l in self.lvn:
            if abs(price - l) / price * 100 <= 0.15:
                return "LVN"
        return "OUTSIDE"


class VolumeProfile:
    """
    Session volume profile: resets each day at 9:15 AM.
    Buckets price into `bucket_size` increments and accumulates volume.
    """

    def __init__(self, bucket_size: float = 50.0):
        self._bucket_size = bucket_size
        self._buckets:     dict[float, float] = defaultdict(float)
        self._session_date = None
        self._result:      Optional[VolumeProfileResult] = None

    def _round_to_bucket(self, price: float) -> float:
        return round(round(price / self._bucket_size) * self._bucket_size, 2)

    def update(self, bar: Bar) -> Optional[VolumeProfileResult]:
        bar_date = bar.ts_open.date()
        if bar_date != self._session_date:
            self._buckets = defaultdict(float)
            self._session_date = bar_date

        # Distribute bar volume across its price range
        lo_bucket = self._round_to_bucket(bar.low)
        hi_bucket = self._round_to_bucket(bar.high)
        price = lo_bucket
        touched: list[float] = []
        while price <= hi_bucket + self._bucket_size * 0.1:
            touched.append(price)
            price = round(price + self._bucket_size, 2)

        if touched:
            vol_per = bar.volume / len(touched)
            for p in touched:
                self._buckets[p] += vol_per

        if not self._buckets:
            return None

        self._result = self._build_result(bar.close)
        return self._result

    def _build_result(self, current_price: float) -> VolumeProfileResult:
        buckets   = dict(self._buckets)
        total_vol = sum(buckets.values())
        if total_vol == 0:
            return None

        poc = max(buckets, key=buckets.get)
        sorted_prices = sorted(buckets.keys())

        # Value Area: 70% of total volume, expanding from POC outward
        target    = total_vol * 0.70
        va_vol    = buckets.get(poc, 0)
        lo_idx    = sorted_prices.index(poc)
        hi_idx    = lo_idx
        lo_ptr    = lo_idx - 1
        hi_ptr    = lo_idx + 1

        while va_vol < target:
            lo_add = buckets.get(sorted_prices[lo_ptr], 0) if lo_ptr >= 0 else 0
            hi_add = buckets.get(sorted_prices[hi_ptr], 0) if hi_ptr < len(sorted_prices) else 0
            if lo_add == 0 and hi_add == 0:
                break
            if lo_add >= hi_add and lo_ptr >= 0:
                va_vol += lo_add
                lo_idx  = lo_ptr
                lo_ptr -= 1
            elif hi_ptr < len(sorted_prices):
                va_vol += hi_add
                hi_idx  = hi_ptr
                hi_ptr += 1
            else:
                break

        va_low  = sorted_prices[lo_idx]
        va_high = sorted_prices[hi_idx]

        # HVN: top 20% volume buckets; LVN: bottom 20%
        vol_values  = sorted(buckets.values())
        hvn_thresh  = vol_values[int(len(vol_values) * 0.80)] if vol_values else 0
        lvn_thresh  = vol_values[int(len(vol_values) * 0.20)] if vol_values else 0
        hvn = sorted([p for p, v in buckets.items() if v >= hvn_thresh])
        lvn = sorted([p for p, v in buckets.items() if v <= lvn_thresh])

        return VolumeProfileResult(
            poc       = poc,
            va_high   = va_high,
            va_low    = va_low,
            hvn       = hvn,
            lvn       = lvn,
            total_vol = round(total_vol, 0),
            buckets   = {k: round(v, 1) for k, v in buckets.items()},
        )

    @property
    def value(self) -> Optional[VolumeProfileResult]:
        return self._result

    @property
    def is_ready(self) -> bool:
        return self._result is not None
