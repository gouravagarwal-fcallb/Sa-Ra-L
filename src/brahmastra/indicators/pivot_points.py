"""
Pivot Points — Standard (Floor) and Camarilla methods.
Computed from previous session's high/low/close.
Used as magnet levels: price tends to react at these zones.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StandardPivots:
    pivot: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float

    def nearest_level(self, price: float) -> tuple[str, float]:
        """Return (label, value) of the closest pivot level to price."""
        levels = {
            "P": self.pivot, "R1": self.r1, "R2": self.r2, "R3": self.r3,
            "S1": self.s1,   "S2": self.s2, "S3": self.s3,
        }
        label, val = min(levels.items(), key=lambda kv: abs(kv[1] - price))
        return label, val

    def zone(self, price: float, tolerance_pct: float = 0.1) -> Optional[str]:
        """Return label of the pivot zone price is near, or None."""
        label, val = self.nearest_level(price)
        if abs(price - val) / val * 100 <= tolerance_pct:
            return label
        return None


@dataclass
class CamarillaPivots:
    pivot: float
    r1: float
    r2: float
    r3: float    # strong resistance — reversal expected here
    r4: float    # breakout zone
    s1: float
    s2: float
    s3: float    # strong support — reversal expected here
    s4: float    # breakdown zone


def calculate_standard_pivots(
    prev_high: float, prev_low: float, prev_close: float
) -> StandardPivots:
    """Floor pivot points from previous session OHLC."""
    p  = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * p - prev_low
    s1 = 2 * p - prev_high
    r2 = p + (prev_high - prev_low)
    s2 = p - (prev_high - prev_low)
    r3 = prev_high + 2 * (p - prev_low)
    s3 = prev_low  - 2 * (prev_high - p)
    return StandardPivots(
        pivot=round(p,2),  r1=round(r1,2), r2=round(r2,2), r3=round(r3,2),
        s1=round(s1,2), s2=round(s2,2), s3=round(s3,2),
    )


def calculate_camarilla_pivots(
    prev_high: float, prev_low: float, prev_close: float
) -> CamarillaPivots:
    """Camarilla pivot points — tighter levels, better for intraday scalping."""
    rng = prev_high - prev_low
    p   = (prev_high + prev_low + prev_close) / 3
    r1  = prev_close + rng * 1.1 / 12
    r2  = prev_close + rng * 1.1 / 6
    r3  = prev_close + rng * 1.1 / 4
    r4  = prev_close + rng * 1.1 / 2
    s1  = prev_close - rng * 1.1 / 12
    s2  = prev_close - rng * 1.1 / 6
    s3  = prev_close - rng * 1.1 / 4
    s4  = prev_close - rng * 1.1 / 2
    return CamarillaPivots(
        pivot=round(p,2),
        r1=round(r1,2), r2=round(r2,2), r3=round(r3,2), r4=round(r4,2),
        s1=round(s1,2), s2=round(s2,2), s3=round(s3,2), s4=round(s4,2),
    )


class PivotTracker:
    """
    Maintains today's pivot levels and updates them each new session.
    Call update_session() each morning with previous day's high/low/close.
    """

    def __init__(self):
        self._standard:   Optional[StandardPivots]   = None
        self._camarilla:  Optional[CamarillaPivots]  = None
        self._session_date = None

    def update_session(self, date, prev_high: float, prev_low: float,
                       prev_close: float) -> None:
        if date != self._session_date:
            self._standard  = calculate_standard_pivots(prev_high, prev_low, prev_close)
            self._camarilla = calculate_camarilla_pivots(prev_high, prev_low, prev_close)
            self._session_date = date

    @property
    def standard(self) -> Optional[StandardPivots]:
        return self._standard

    @property
    def camarilla(self) -> Optional[CamarillaPivots]:
        return self._camarilla

    @property
    def is_ready(self) -> bool:
        return self._standard is not None
