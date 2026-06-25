"""
Price action indicators: ORB, Gap Analysis, Round Number Levels, Heikin Ashi.
These are session-context indicators — they encode where we are in the day's story.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from src.brahmastra.data.bar_builder import Bar


# ─── ORB (Opening Range Breakout) ────────────────────────────────────────────

@dataclass
class ORBResult:
    high:              float
    low:               float
    range:             float
    status:            str    # 'BUILDING' | 'LOCKED' | 'BULL_BREAK' | 'BEAR_BREAK' | 'RANGE_DAY'
    break_confirmed:   bool
    bars_above_orb_high: int
    bars_below_orb_low:  int


class ORBCalculator:
    """
    Opening Range = max/min of 1-minute bars from 09:15 to 09:29.
    Range locks at 09:30.
    Break is confirmed when price holds on same side for 2 consecutive bars.
    RANGE_DAY is declared after 09:45 if price never left the range.
    """

    _ORB_START      = (9, 15)
    _ORB_END        = (9, 30)
    _RANGE_DAY_TIME = (9, 45)

    def __init__(self):
        self._orb_high:     float = 0.0
        self._orb_low:      float = float("inf")
        self._locked:       bool  = False
        self._bars_above:   int   = 0
        self._bars_below:   int   = 0
        self._consec_above: int   = 0
        self._consec_below: int   = 0
        self._break_confirmed: bool = False
        self._current_status:  str  = "BUILDING"

    def update(self, bar: Bar, time_str: str) -> ORBResult:
        hour, minute = map(int, time_str.split(":"))
        now_min = hour * 60 + minute

        orb_start_min = self._ORB_START[0] * 60 + self._ORB_START[1]
        orb_end_min   = self._ORB_END[0]   * 60 + self._ORB_END[1]
        range_day_min = self._RANGE_DAY_TIME[0] * 60 + self._RANGE_DAY_TIME[1]

        if not self._locked:
            if orb_start_min <= now_min < orb_end_min:
                if bar.high > self._orb_high:
                    self._orb_high = bar.high
                if bar.low < self._orb_low:
                    self._orb_low = bar.low
                self._current_status = "BUILDING"
            elif now_min >= orb_end_min:
                if self._orb_high == 0.0 or self._orb_low == float("inf"):
                    self._orb_high = bar.close
                    self._orb_low  = bar.close
                self._locked = True
                self._current_status = "LOCKED"
        else:
            close = bar.close

            if close > self._orb_high:
                self._consec_above += 1
                self._bars_above   += 1
                self._consec_below  = 0
            elif close < self._orb_low:
                self._consec_below += 1
                self._bars_below   += 1
                self._consec_above  = 0
            else:
                self._consec_above = 0
                self._consec_below = 0

            if self._consec_above >= 2:
                self._break_confirmed = True
                self._current_status  = "BULL_BREAK"
            elif self._consec_below >= 2:
                self._break_confirmed = True
                self._current_status  = "BEAR_BREAK"
            elif self._current_status not in ("BULL_BREAK", "BEAR_BREAK"):
                if now_min >= range_day_min and not self._break_confirmed:
                    self._current_status = "RANGE_DAY"
                else:
                    self._current_status = "LOCKED"

        orb_range = self._orb_high - self._orb_low if self._orb_low != float("inf") else 0.0

        return ORBResult(
            high                = self._orb_high if self._orb_high else 0.0,
            low                 = self._orb_low  if self._orb_low != float("inf") else 0.0,
            range               = round(orb_range, 2),
            status              = self._current_status,
            break_confirmed     = self._break_confirmed,
            bars_above_orb_high = self._bars_above,
            bars_below_orb_low  = self._bars_below,
        )


# ─── Gap Analysis ─────────────────────────────────────────────────────────────

@dataclass
class GapResult:
    gap_pct:    float
    gap_type:   str    # 'FLAT'|'GAP_UP_SMALL'|'GAP_UP_LARGE'|'GAP_DOWN_SMALL'|'GAP_DOWN_LARGE'|'GAP_AND_GO_UP'|'GAP_AND_GO_DOWN'|'GAP_FILL'
    direction:  str    # 'BULL' | 'BEAR' | 'NEUTRAL'
    prev_close: float
    today_open: float
    gap_filled: bool


def _classify_raw_gap(gap_pct: float) -> tuple[str, str]:
    """Returns (gap_type_base, direction) before intraday evolution."""
    if gap_pct >= 1.5:
        return "GAP_UP_LARGE", "BULL"
    elif gap_pct >= 0.2:
        return "GAP_UP_SMALL", "BULL"
    elif gap_pct <= -1.5:
        return "GAP_DOWN_LARGE", "BEAR"
    elif gap_pct <= -0.2:
        return "GAP_DOWN_SMALL", "BEAR"
    else:
        return "FLAT", "NEUTRAL"


class GapAnalyzer:
    """
    Tracks the daily gap between yesterday's close and today's open.
    GAP_AND_GO: small gap that sustains direction past 09:45.
    GAP_FILL: price returned to prev_close intraday.
    """

    _SESSION_OPEN_MIN = 9 * 60 + 15
    _GAP_AND_GO_MIN   = 9 * 60 + 45

    def __init__(self):
        self._prev_close: Optional[float] = None
        self._today_open: Optional[float] = None
        self._gap_pct:    float = 0.0
        self._base_type:  str   = "FLAT"
        self._direction:  str   = "NEUTRAL"
        self._gap_filled: bool  = False

    def set_prev_close(self, prev_close: float) -> None:
        self._prev_close = prev_close

    def update(self, bar: Bar, current_time_min: int) -> Optional[GapResult]:
        if self._prev_close is None:
            return None

        if self._today_open is None and current_time_min >= self._SESSION_OPEN_MIN:
            self._today_open = bar.open
            self._gap_pct    = (self._today_open - self._prev_close) / self._prev_close * 100
            self._base_type, self._direction = _classify_raw_gap(self._gap_pct)

        if self._today_open is None:
            return None

        if not self._gap_filled:
            if self._direction == "BULL" and bar.low <= self._prev_close:
                self._gap_filled = True
            elif self._direction == "BEAR" and bar.high >= self._prev_close:
                self._gap_filled = True

        gap_type = self._base_type

        if self._gap_filled:
            gap_type = "GAP_FILL"
        elif current_time_min >= self._GAP_AND_GO_MIN and not self._gap_filled:
            if self._base_type == "GAP_UP_SMALL" and bar.close > self._today_open:
                gap_type = "GAP_AND_GO_UP"
            elif self._base_type == "GAP_DOWN_SMALL" and bar.close < self._today_open:
                gap_type = "GAP_AND_GO_DOWN"

        return GapResult(
            gap_pct    = round(self._gap_pct, 4),
            gap_type   = gap_type,
            direction  = self._direction,
            prev_close = self._prev_close,
            today_open = self._today_open,
            gap_filled = self._gap_filled,
        )


# ─── Round Number Levels ──────────────────────────────────────────────────────

@dataclass
class RoundLevelResult:
    nearest_above:      float
    nearest_below:      float
    dist_to_above_pct:  float
    dist_to_below_pct:  float
    at_round_level:     bool
    round_level:        Optional[float]


_ROUND_STEP = {
    "NIFTY":  50,
    "SENSEX": 100,
}


def get_round_levels(price: float, instrument: str = "NIFTY") -> RoundLevelResult:
    step  = _ROUND_STEP.get(instrument.upper(), 50)
    below = math.floor(price / step) * step
    above = below + step

    threshold = price * 0.001
    at_level  = False
    level: Optional[float] = None

    if price - below <= threshold:
        at_level = True
        level    = float(below)
    elif above - price <= threshold:
        at_level = True
        level    = float(above)

    dist_above = (above - price) / price * 100 if price else 0.0
    dist_below = (price - below) / price * 100 if price else 0.0

    return RoundLevelResult(
        nearest_above     = float(above),
        nearest_below     = float(below),
        dist_to_above_pct = round(dist_above, 4),
        dist_to_below_pct = round(dist_below, 4),
        at_round_level    = at_level,
        round_level       = level,
    )


# ─── Heikin Ashi ──────────────────────────────────────────────────────────────

@dataclass
class HeikinAshiBar:
    ha_open:       float
    ha_high:       float
    ha_low:        float
    ha_close:      float
    direction:     str    # 'BULL' | 'BEAR'
    no_lower_wick: bool   # strong bull: HA_close == HA_low
    no_upper_wick: bool   # strong bear: HA_close == HA_high
    is_doji:       bool   # |HA_close - HA_open| < 0.05% of price


class HeikinAshi:
    """
    Heikin Ashi smoothed candles.
    HA_close = (O+H+L+C)/4
    HA_open  = (prev_HA_open + prev_HA_close) / 2  [first bar: (O+C)/2]
    HA_high  = max(H, HA_open, HA_close)
    HA_low   = min(L, HA_open, HA_close)
    """

    def __init__(self):
        self._prev_ha_open:  Optional[float] = None
        self._prev_ha_close: Optional[float] = None
        self._last:          Optional[HeikinAshiBar] = None

    def update(self, bar: Bar) -> HeikinAshiBar:
        ha_close = (bar.open + bar.high + bar.low + bar.close) / 4

        if self._prev_ha_open is None:
            ha_open = (bar.open + bar.close) / 2
        else:
            ha_open = (self._prev_ha_open + self._prev_ha_close) / 2

        ha_high = max(bar.high, ha_open, ha_close)
        ha_low  = min(bar.low,  ha_open, ha_close)

        direction      = "BULL" if ha_close >= ha_open else "BEAR"
        doji_threshold = bar.close * 0.0005
        is_doji        = abs(ha_close - ha_open) < doji_threshold

        result = HeikinAshiBar(
            ha_open       = round(ha_open,  2),
            ha_high       = round(ha_high,  2),
            ha_low        = round(ha_low,   2),
            ha_close      = round(ha_close, 2),
            direction     = direction,
            no_lower_wick = ha_close == ha_low,
            no_upper_wick = ha_close == ha_high,
            is_doji       = is_doji,
        )

        self._prev_ha_open  = ha_open
        self._prev_ha_close = ha_close
        self._last = result
        return result

    @property
    def value(self) -> Optional[HeikinAshiBar]:
        return self._last

    @property
    def is_ready(self) -> bool:
        return self._last is not None
