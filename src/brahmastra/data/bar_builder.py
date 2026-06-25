"""
BRAHMASTRA Bar Builder
━━━━━━━━━━━━━━━━━━━━━━
Receives raw price ticks and builds OHLCV bars for all timeframes:
  1m, 5m, 15m, 1h, 1D, 1W

Every tick is processed in < 1ms.
Every completed bar is emitted to all registered listeners.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

IST = timezone(timedelta(hours=5, minutes=30))

TIMEFRAMES = ["1m", "5m", "15m", "1h", "1D", "1W"]

# Minutes per timeframe (1D and 1W handled separately)
_TF_MINUTES: dict[str, int] = {
    "1m":  1,
    "5m":  5,
    "15m": 15,
    "1h":  60,
}


@dataclass
class Bar:
    instrument: str
    timeframe:  str
    ts_open:    datetime      # bar open time (IST)
    ts_close:   datetime      # bar close time (IST)  [approximate]
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     float
    vwap:       float         # volume-weighted average price for this bar
    tick_count: int
    complete:   bool = False  # True once bar is finalised

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bull(self) -> bool:
        return self.close >= self.open

    @property
    def body_pct(self) -> float:
        """Body size as percentage of close price."""
        return (self.body / self.close * 100) if self.close else 0


@dataclass
class _InProgressBar:
    instrument: str
    timeframe:  str
    ts_open:    datetime
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     float
    _pv_sum:    float = 0.0   # price × volume sum for VWAP
    _v_sum:     float = 0.0   # volume sum for VWAP
    tick_count: int   = 0

    def update(self, price: float, volume: float = 0.0) -> None:
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low  = price
        self.close      = price
        self.volume    += volume
        self._pv_sum   += price * volume
        self._v_sum    += volume
        self.tick_count += 1

    def to_bar(self, ts_close: datetime, complete: bool = True) -> Bar:
        vwap = (self._pv_sum / self._v_sum) if self._v_sum > 0 else self.close
        return Bar(
            instrument = self.instrument,
            timeframe  = self.timeframe,
            ts_open    = self.ts_open,
            ts_close   = ts_close,
            open       = self.open,
            high       = self.high,
            low        = self.low,
            close      = self.close,
            volume     = self.volume,
            vwap       = round(vwap, 2),
            tick_count = self.tick_count,
            complete   = complete,
        )


def _bar_open_time(ts: datetime, timeframe: str) -> datetime:
    """Round ts DOWN to the nearest bar boundary for the given timeframe."""
    if timeframe in _TF_MINUTES:
        mins = _TF_MINUTES[timeframe]
        # Floor to nearest N-minute boundary
        floored_min = (ts.minute // mins) * mins
        return ts.replace(minute=floored_min, second=0, microsecond=0)
    if timeframe == "1D":
        # Day bar starts at 9:15 AM IST
        return ts.replace(hour=9, minute=15, second=0, microsecond=0)
    if timeframe == "1W":
        # Week bar starts Monday 9:15 AM IST
        days_since_monday = ts.weekday()  # 0=Monday
        monday = (ts - timedelta(days=days_since_monday))
        return monday.replace(hour=9, minute=15, second=0, microsecond=0)
    raise ValueError(f"Unknown timeframe: {timeframe}")


class BarBuilder:
    """
    Processes ticks for a single instrument across all timeframes.

    Usage:
        bb = BarBuilder("NIFTY", on_bar_complete=my_callback)
        bb.on_tick(price=24235.0, volume=100, ts=datetime.now(IST))

    The callback receives a completed Bar object.
    """

    def __init__(
        self,
        instrument: str,
        timeframes: list[str] = None,
        on_bar_complete: Optional[Callable[[Bar], None]] = None,
        on_bar_update: Optional[Callable[[Bar], None]] = None,
    ):
        self.instrument   = instrument
        self.timeframes   = timeframes or TIMEFRAMES
        self._on_complete = on_bar_complete
        self._on_update   = on_bar_update

        # In-progress bar per timeframe
        self._bars: dict[str, Optional[_InProgressBar]] = {tf: None for tf in self.timeframes}

        # History: last N completed bars per timeframe (for indicators)
        self._history: dict[str, list[Bar]] = defaultdict(list)
        self._history_max = 500   # keep last 500 completed bars per timeframe

    def on_tick(self, price: float, volume: float = 0.0,
                ts: Optional[datetime] = None) -> list[Bar]:
        """
        Process one tick. Returns list of any bars that just completed.

        ts should be IST-aware datetime. If None, uses now(IST).
        """
        if ts is None:
            ts = datetime.now(IST)

        completed: list[Bar] = []

        for tf in self.timeframes:
            bar_open = _bar_open_time(ts, tf)
            current  = self._bars[tf]

            if current is None:
                # First tick ever — start new bar
                self._bars[tf] = _InProgressBar(
                    instrument = self.instrument,
                    timeframe  = tf,
                    ts_open    = bar_open,
                    open=price, high=price, low=price, close=price,
                    volume=volume,
                )
                self._bars[tf]._pv_sum = price * volume
                self._bars[tf]._v_sum  = volume
                self._bars[tf].tick_count = 1

            elif bar_open > current.ts_open:
                # New bar period started — finalise previous bar
                finished = current.to_bar(ts_close=bar_open, complete=True)
                self._history[tf].append(finished)
                if len(self._history[tf]) > self._history_max:
                    self._history[tf].pop(0)
                completed.append(finished)
                if self._on_complete:
                    self._on_complete(finished)

                # Start new bar
                self._bars[tf] = _InProgressBar(
                    instrument = self.instrument,
                    timeframe  = tf,
                    ts_open    = bar_open,
                    open=price, high=price, low=price, close=price,
                    volume=volume,
                )
                self._bars[tf]._pv_sum = price * volume
                self._bars[tf]._v_sum  = volume
                self._bars[tf].tick_count = 1

            else:
                # Same bar — update
                current.update(price, volume)
                if self._on_update:
                    self._on_update(current.to_bar(ts_close=ts, complete=False))

        return completed

    def get_current_bar(self, timeframe: str) -> Optional[Bar]:
        """Return the in-progress (incomplete) bar for the given timeframe."""
        b = self._bars.get(timeframe)
        if b is None:
            return None
        return b.to_bar(ts_close=datetime.now(IST), complete=False)

    def get_history(self, timeframe: str, n: int = 200) -> list[Bar]:
        """Return last n completed bars for the given timeframe (oldest first)."""
        h = self._history.get(timeframe, [])
        return h[-n:] if len(h) > n else list(h)

    def load_history(self, timeframe: str, bars: list[Bar]) -> None:
        """Pre-load historical bars (from backfill). Replaces existing history."""
        self._history[timeframe] = list(bars[-self._history_max:])

    def bar_count(self, timeframe: str) -> int:
        """Number of completed bars available for this timeframe."""
        return len(self._history.get(timeframe, []))

    def is_warm(self, timeframe: str, min_bars: int = 200) -> bool:
        """True if enough history is available for indicator warmup."""
        return self.bar_count(timeframe) >= min_bars

    def warmup_status(self) -> dict[str, dict]:
        """Summary of warmup state for all timeframes."""
        needed = {"1m": 200, "5m": 200, "15m": 200, "1h": 200, "1D": 200, "1W": 200}
        return {
            tf: {
                "have":  self.bar_count(tf),
                "need":  needed.get(tf, 200),
                "warm":  self.is_warm(tf, needed.get(tf, 200)),
                "pct":   round(min(100, self.bar_count(tf) / needed.get(tf, 200) * 100), 1),
            }
            for tf in self.timeframes
        }


class MultiInstrumentBarBuilder:
    """
    Manages BarBuilder instances for multiple instruments simultaneously.

    Usage:
        mb = MultiInstrumentBarBuilder(["NIFTY", "SENSEX"], on_bar_complete=cb)
        mb.on_tick("NIFTY", price=24235.0, volume=100)
    """

    def __init__(
        self,
        instruments: list[str],
        timeframes: list[str] = None,
        on_bar_complete: Optional[Callable[[str, Bar], None]] = None,
    ):
        self._on_complete = on_bar_complete
        self._builders: dict[str, BarBuilder] = {}
        for inst in instruments:
            self._builders[inst] = BarBuilder(
                instrument=inst,
                timeframes=timeframes or TIMEFRAMES,
                on_bar_complete=lambda bar, i=inst: (
                    self._on_complete(i, bar) if self._on_complete else None
                ),
            )

    def on_tick(self, instrument: str, price: float,
                volume: float = 0.0, ts: Optional[datetime] = None) -> list[Bar]:
        if instrument not in self._builders:
            self._builders[instrument] = BarBuilder(instrument=instrument)
        return self._builders[instrument].on_tick(price, volume, ts)

    def get_builder(self, instrument: str) -> Optional[BarBuilder]:
        return self._builders.get(instrument)

    def warmup_status(self) -> dict[str, dict]:
        return {inst: b.warmup_status() for inst, b in self._builders.items()}
