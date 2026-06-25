"""
Relative Strength Index (RSI) — Wilder's smoothed method.
Includes divergence detection (regular and hidden).
"""
from __future__ import annotations

from typing import Optional
from src.brahmastra.data.bar_builder import Bar


class RSI:
    """
    Wilder smoothed RSI with divergence detection.

    Divergence is checked over the last `div_lookback` bars once RSI is warm.
    """

    def __init__(self, period: int = 14, div_lookback: int = 5):
        self.period       = period
        self.div_lookback = div_lookback
        self._avg_gain:   Optional[float] = None
        self._avg_loss:   Optional[float] = None
        self._prev_close: Optional[float] = None
        self._rsi:        Optional[float] = None
        self._buf_gain:   list[float] = []
        self._buf_loss:   list[float] = []

        # Rolling history for divergence: (price, rsi)
        self._history: list[tuple[float, float]] = []

    def update(self, bar: Bar) -> Optional[float]:
        return self.update_price(bar.close)

    def update_price(self, price: float) -> Optional[float]:
        if self._prev_close is None:
            self._prev_close = price
            return None

        change = price - self._prev_close
        gain   = max(change, 0.0)
        loss   = max(-change, 0.0)
        self._prev_close = price

        if self._avg_gain is None:
            self._buf_gain.append(gain)
            self._buf_loss.append(loss)
            if len(self._buf_gain) >= self.period:
                self._avg_gain = sum(self._buf_gain) / self.period
                self._avg_loss = sum(self._buf_loss) / self.period
                self._buf_gain = []
                self._buf_loss = []
                self._rsi = self._compute_rsi()
        else:
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
            self._rsi = self._compute_rsi()

        if self._rsi is not None:
            self._history.append((price, self._rsi))
            if len(self._history) > max(50, self.div_lookback * 3):
                self._history.pop(0)

        return self._rsi

    def _compute_rsi(self) -> float:
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1 + rs))

    @property
    def value(self) -> Optional[float]:
        return self._rsi

    @property
    def is_ready(self) -> bool:
        return self._rsi is not None

    @property
    def is_overbought(self) -> bool:
        return self._rsi is not None and self._rsi >= 70

    @property
    def is_oversold(self) -> bool:
        return self._rsi is not None and self._rsi <= 30

    def divergence(self) -> Optional[str]:
        """
        Returns 'BULLISH', 'BEARISH', or None.

        Regular bull divergence  — price makes lower low, RSI makes higher low.
        Regular bear divergence  — price makes higher high, RSI makes lower high.
        """
        n = self.div_lookback
        if len(self._history) < n * 2:
            return None

        recent  = self._history[-n:]
        earlier = self._history[-n * 2:-n]

        recent_price_lo  = min(p for p, _ in recent)
        earlier_price_lo = min(p for p, _ in earlier)
        recent_rsi_lo    = min(r for _, r in recent)
        earlier_rsi_lo   = min(r for _, r in earlier)

        recent_price_hi  = max(p for p, _ in recent)
        earlier_price_hi = max(p for p, _ in earlier)
        recent_rsi_hi    = max(r for _, r in recent)
        earlier_rsi_hi   = max(r for _, r in earlier)

        if recent_price_lo < earlier_price_lo and recent_rsi_lo > earlier_rsi_lo:
            return "BULLISH"
        if recent_price_hi > earlier_price_hi and recent_rsi_hi < earlier_rsi_hi:
            return "BEARISH"
        return None


def rsi_from_bars(bars: list[Bar], period: int = 14) -> list[Optional[float]]:
    """Batch compute RSI series."""
    calc = RSI(period)
    return [calc.update(b) for b in bars]
