"""
Fibonacci Retracement and Extension levels.
Swing high/low automatically detected from bar history.
Key levels: 23.6%, 38.2%, 50%, 61.8%, 78.6% (retracement)
            127.2%, 161.8% (extension)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from src.brahmastra.data.bar_builder import Bar

RETRACE_LEVELS  = [0.0, 0.236, 0.382, 0.500, 0.618, 0.786, 1.0]
EXTENSION_LEVELS = [1.272, 1.618]
ALL_LEVELS       = RETRACE_LEVELS + EXTENSION_LEVELS


@dataclass
class FibLevels:
    swing_high:  float
    swing_low:   float
    direction:   str       # 'UP' (low→high move) | 'DOWN' (high→low move)
    levels:      dict[str, float] = field(default_factory=dict)

    def nearest(self, price: float) -> tuple[str, float, float]:
        """Return (label, value, distance_pct) of closest fib level."""
        best_label, best_val = min(
            self.levels.items(), key=lambda kv: abs(kv[1] - price)
        )
        dist_pct = abs(price - best_val) / price * 100
        return best_label, best_val, round(dist_pct, 2)

    def is_near_level(self, price: float, tolerance_pct: float = 0.15) -> Optional[str]:
        label, val, dist = self.nearest(price)
        return label if dist <= tolerance_pct else None


def _build_fib_levels(swing_high: float, swing_low: float, direction: str) -> FibLevels:
    rng    = swing_high - swing_low
    levels: dict[str, float] = {}

    if direction == "UP":
        # Retracement from high downward
        for r in RETRACE_LEVELS:
            pct = int(r * 1000) / 10
            levels[f"{pct:.1f}%"] = round(swing_high - rng * r, 2)
        # Extensions above swing high
        for e in EXTENSION_LEVELS:
            pct = int(e * 1000) / 10
            levels[f"{pct:.1f}%"] = round(swing_low + rng * e, 2)
    else:
        # DOWN: retracement from low upward
        for r in RETRACE_LEVELS:
            pct = int(r * 1000) / 10
            levels[f"{pct:.1f}%"] = round(swing_low + rng * r, 2)
        for e in EXTENSION_LEVELS:
            pct = int(e * 1000) / 10
            levels[f"{pct:.1f}%"] = round(swing_high - rng * e, 2)

    return FibLevels(swing_high=swing_high, swing_low=swing_low,
                     direction=direction, levels=levels)


class FibonacciTracker:
    """
    Auto-detects swing high/low from a rolling window of bars.
    Updates fib levels whenever a new swing is detected.

    swing_window: number of bars to look left/right for pivot detection
    """

    def __init__(self, swing_window: int = 10):
        self._window   = swing_window
        self._bar_buf:  list[Bar] = []
        self._fib:      Optional[FibLevels] = None

    def update(self, bar: Bar) -> Optional[FibLevels]:
        self._bar_buf.append(bar)
        if len(self._bar_buf) > self._window * 3:
            self._bar_buf.pop(0)

        if len(self._bar_buf) < self._window * 2 + 1:
            return self._fib

        mid = len(self._bar_buf) // 2
        mid_bar = self._bar_buf[mid]
        left    = self._bar_buf[:mid]
        right   = self._bar_buf[mid + 1:]

        is_swing_high = (
            all(b.high <= mid_bar.high for b in left[-self._window:]) and
            all(b.high <= mid_bar.high for b in right[:self._window])
        )
        is_swing_low = (
            all(b.low >= mid_bar.low for b in left[-self._window:]) and
            all(b.low >= mid_bar.low for b in right[:self._window])
        )

        if is_swing_high or is_swing_low:
            highs = [b.high for b in self._bar_buf]
            lows  = [b.low  for b in self._bar_buf]
            sh    = max(highs)
            sl    = min(lows)
            sh_idx = highs.index(sh)
            sl_idx = lows.index(sl)
            direction = "UP" if sh_idx > sl_idx else "DOWN"
            self._fib = _build_fib_levels(sh, sl, direction)

        return self._fib

    @property
    def value(self) -> Optional[FibLevels]:
        return self._fib

    @property
    def is_ready(self) -> bool:
        return self._fib is not None
