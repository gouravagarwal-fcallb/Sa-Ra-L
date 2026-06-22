"""
Candle data structures and pure indicator helpers.
Used by both live engine (1-min bars) and backtest (OHLC simulation).
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import List


@dataclass
class Candle:
    """Single OHLCV candle — any timeframe."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range_(self) -> float:
        return self.high - self.low

    @property
    def body_ratio(self) -> float:
        return self.body / self.range_ if self.range_ > 0 else 0.0

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


# ── Indicator helpers ─────────────────────────────────────────────────────────

def ema(values: List[float], period: int) -> float:
    """Exponential Moving Average of the full values list."""
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2.0 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1.0 - k)
    return result


def rsi(values: List[float], period: int = 14) -> float:
    """Wilder's RSI. Returns 50.0 when insufficient data."""
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        chg = values[i] - values[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0.0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def atr(bars: List[Candle], period: int = 14) -> float:
    """Average True Range."""
    if len(bars) < 2:
        return bars[0].range_ if bars else 0.0
    trs = []
    for i in range(1, len(bars)):
        pc = bars[i - 1].close
        trs.append(max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - pc),
            abs(bars[i].low  - pc),
        ))
    return sum(trs[-period:]) / min(len(trs), period)


def compute_vwap(bars: List[Candle]) -> float:
    """Session VWAP from OHLCV candles. Falls back to last close if no volume."""
    tv  = sum((c.high + c.low + c.close) / 3.0 * c.volume for c in bars)
    vol = sum(c.volume for c in bars)
    if vol > 0:
        return tv / vol
    # No volume data (indices) — use simple price average
    return sum((c.high + c.low + c.close) / 3.0 for c in bars) / len(bars) if bars else 0.0


def build_5min_from_1min(bars_1m: List[Candle]) -> List[Candle]:
    """
    Aggregate 1-min candles into 5-min candles.
    Groups by 5-min window boundary (09:15, 09:20, 09:25, ...).
    """
    if not bars_1m:
        return []

    groups: dict[datetime, List[Candle]] = {}
    for bar in bars_1m:
        key = bar.timestamp.replace(
            minute=(bar.timestamp.minute // 5) * 5,
            second=0, microsecond=0,
        )
        groups.setdefault(key, []).append(bar)

    result = []
    for key in sorted(groups):
        batch = groups[key]
        result.append(Candle(
            timestamp=key,
            open=batch[0].open,
            high=max(b.high for b in batch),
            low=min(b.low  for b in batch),
            close=batch[-1].close,
            volume=sum(b.volume for b in batch),
        ))
    return result
