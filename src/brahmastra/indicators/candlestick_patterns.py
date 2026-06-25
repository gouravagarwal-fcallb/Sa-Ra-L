"""
Candlestick Pattern Recognition — 20 patterns.

Single-bar:   Doji, Hammer, Shooting Star, Inverted Hammer, Hanging Man,
              Marubozu (Bull/Bear), Spinning Top
Double-bar:   Bullish/Bearish Engulfing, Bullish/Bearish Harami,
              Piercing Line, Dark Cloud Cover
Triple-bar:   Morning Star, Evening Star, Three White Soldiers,
              Three Black Crows, Morning Doji Star, Evening Doji Star,
              Abandoned Baby (Bull/Bear)

Each pattern returns a PatternSignal with name, direction, and confidence 0-1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from src.brahmastra.data.bar_builder import Bar


@dataclass
class PatternSignal:
    name:       str
    direction:  str    # 'BULLISH' | 'BEARISH' | 'NEUTRAL'
    confidence: float  # 0.0–1.0
    bar_index:  int    # index of last bar in pattern (0 = current)


def _body_pct(bar: Bar) -> float:
    return abs(bar.close - bar.open) / bar.close * 100 if bar.close else 0


def _is_doji(bar: Bar, threshold: float = 0.1) -> bool:
    rng = bar.high - bar.low
    return rng > 0 and _body_pct(bar) < threshold


def _upper_wick_ratio(bar: Bar) -> float:
    body_top = max(bar.open, bar.close)
    rng = bar.high - bar.low
    return (bar.high - body_top) / rng if rng > 0 else 0


def _lower_wick_ratio(bar: Bar) -> float:
    body_bot = min(bar.open, bar.close)
    rng = bar.high - bar.low
    return (body_bot - bar.low) / rng if rng > 0 else 0


# ─── Single-bar patterns ─────────────────────────────────────────────────────

def _detect_doji(bar: Bar) -> Optional[PatternSignal]:
    if _is_doji(bar):
        return PatternSignal("Doji", "NEUTRAL", 0.5, 0)
    return None


def _detect_hammer(bar: Bar) -> Optional[PatternSignal]:
    """Long lower wick ≥ 2× body, upper wick tiny, bullish reversal."""
    body = abs(bar.close - bar.open)
    lower_wick = min(bar.open, bar.close) - bar.low
    upper_wick = bar.high - max(bar.open, bar.close)
    if body > 0 and lower_wick >= 2 * body and upper_wick <= body * 0.3:
        conf = min(1.0, lower_wick / (body * 3))
        return PatternSignal("Hammer", "BULLISH", round(conf, 2), 0)
    return None


def _detect_inverted_hammer(bar: Bar) -> Optional[PatternSignal]:
    """Long upper wick ≥ 2× body, lower wick tiny. Bullish reversal in downtrend."""
    body = abs(bar.close - bar.open)
    upper_wick = bar.high - max(bar.open, bar.close)
    lower_wick = min(bar.open, bar.close) - bar.low
    if body > 0 and upper_wick >= 2 * body and lower_wick <= body * 0.3:
        conf = min(1.0, upper_wick / (body * 3))
        return PatternSignal("Inverted Hammer", "BULLISH", round(conf, 2), 0)
    return None


def _detect_shooting_star(bar: Bar) -> Optional[PatternSignal]:
    """Long upper wick ≥ 2× body, lower wick tiny, bearish reversal in uptrend."""
    body = abs(bar.close - bar.open)
    upper_wick = bar.high - max(bar.open, bar.close)
    lower_wick = min(bar.open, bar.close) - bar.low
    if body > 0 and upper_wick >= 2 * body and lower_wick <= body * 0.3:
        conf = min(1.0, upper_wick / (body * 3))
        return PatternSignal("Shooting Star", "BEARISH", round(conf, 2), 0)
    return None


def _detect_hanging_man(bar: Bar) -> Optional[PatternSignal]:
    """Identical shape to Hammer but bearish when in an uptrend."""
    body = abs(bar.close - bar.open)
    lower_wick = min(bar.open, bar.close) - bar.low
    upper_wick = bar.high - max(bar.open, bar.close)
    if body > 0 and lower_wick >= 2 * body and upper_wick <= body * 0.3:
        conf = min(1.0, lower_wick / (body * 3))
        return PatternSignal("Hanging Man", "BEARISH", round(conf, 2), 0)
    return None


def _detect_marubozu(bar: Bar) -> Optional[PatternSignal]:
    """Almost no wicks — full momentum candle."""
    rng = bar.high - bar.low
    if rng == 0:
        return None
    body = abs(bar.close - bar.open)
    if body / rng >= 0.95:
        direction = "BULLISH" if bar.close > bar.open else "BEARISH"
        return PatternSignal("Marubozu", direction, round(body / rng, 2), 0)
    return None


def _detect_spinning_top(bar: Bar) -> Optional[PatternSignal]:
    """Small body, wicks on both sides — indecision."""
    rng = bar.high - bar.low
    if rng == 0:
        return None
    body_pct = _body_pct(bar)
    uw = _upper_wick_ratio(bar)
    lw = _lower_wick_ratio(bar)
    if body_pct < 1.0 and uw > 0.15 and lw > 0.15:
        return PatternSignal("Spinning Top", "NEUTRAL", 0.4, 0)
    return None


# ─── Double-bar patterns ──────────────────────────────────────────────────────

def _detect_engulfing(prev: Bar, curr: Bar) -> Optional[PatternSignal]:
    """Current bar body fully engulfs previous bar body."""
    prev_bull = prev.close > prev.open
    curr_bull = curr.close > curr.open
    if prev_bull and not curr_bull:
        if curr.open >= prev.close and curr.close <= prev.open:
            return PatternSignal("Bearish Engulfing", "BEARISH", 0.8, 0)
    if not prev_bull and curr_bull:
        if curr.open <= prev.close and curr.close >= prev.open:
            return PatternSignal("Bullish Engulfing", "BULLISH", 0.8, 0)
    return None


def _detect_harami(prev: Bar, curr: Bar) -> Optional[PatternSignal]:
    """Current bar body entirely inside previous bar body."""
    prev_body_top = max(prev.open, prev.close)
    prev_body_bot = min(prev.open, prev.close)
    curr_body_top = max(curr.open, curr.close)
    curr_body_bot = min(curr.open, curr.close)
    if curr_body_top < prev_body_top and curr_body_bot > prev_body_bot:
        prev_bull = prev.close > prev.open
        direction = "BULLISH" if not prev_bull else "BEARISH"
        name = "Bullish Harami" if direction == "BULLISH" else "Bearish Harami"
        return PatternSignal(name, direction, 0.6, 0)
    return None


def _detect_piercing_line(prev: Bar, curr: Bar) -> Optional[PatternSignal]:
    """Bullish: prev bearish, curr opens below prev low, closes above midpoint of prev body."""
    if prev.close < prev.open and curr.close > curr.open:
        mid_prev = (prev.open + prev.close) / 2
        if curr.open < prev.close and curr.close > mid_prev and curr.close < prev.open:
            return PatternSignal("Piercing Line", "BULLISH", 0.7, 0)
    return None


def _detect_dark_cloud(prev: Bar, curr: Bar) -> Optional[PatternSignal]:
    """Bearish: prev bullish, curr opens above prev high, closes below midpoint of prev body."""
    if prev.close > prev.open and curr.close < curr.open:
        mid_prev = (prev.open + prev.close) / 2
        if curr.open > prev.close and curr.close < mid_prev and curr.close > prev.open:
            return PatternSignal("Dark Cloud Cover", "BEARISH", 0.7, 0)
    return None


# ─── Triple-bar patterns ──────────────────────────────────────────────────────

def _detect_morning_star(b1: Bar, b2: Bar, b3: Bar) -> Optional[PatternSignal]:
    """b1 bearish large, b2 small body (gap down), b3 bullish closes above b1 midpoint."""
    b1_bearish = b1.close < b1.open and _body_pct(b1) > 1.0
    b2_small   = _body_pct(b2) < 0.5
    b3_bullish = b3.close > b3.open and _body_pct(b3) > 1.0
    mid_b1     = (b1.open + b1.close) / 2
    if b1_bearish and b2_small and b3_bullish and b3.close > mid_b1:
        return PatternSignal("Morning Star", "BULLISH", 0.85, 0)
    return None


def _detect_evening_star(b1: Bar, b2: Bar, b3: Bar) -> Optional[PatternSignal]:
    """b1 bullish large, b2 small body (gap up), b3 bearish closes below b1 midpoint."""
    b1_bullish = b1.close > b1.open and _body_pct(b1) > 1.0
    b2_small   = _body_pct(b2) < 0.5
    b3_bearish = b3.close < b3.open and _body_pct(b3) > 1.0
    mid_b1     = (b1.open + b1.close) / 2
    if b1_bullish and b2_small and b3_bearish and b3.close < mid_b1:
        return PatternSignal("Evening Star", "BEARISH", 0.85, 0)
    return None


def _detect_morning_doji_star(b1: Bar, b2: Bar, b3: Bar) -> Optional[PatternSignal]:
    if b1.close < b1.open and _is_doji(b2) and b3.close > b3.open:
        mid_b1 = (b1.open + b1.close) / 2
        if b3.close > mid_b1:
            return PatternSignal("Morning Doji Star", "BULLISH", 0.9, 0)
    return None


def _detect_evening_doji_star(b1: Bar, b2: Bar, b3: Bar) -> Optional[PatternSignal]:
    if b1.close > b1.open and _is_doji(b2) and b3.close < b3.open:
        mid_b1 = (b1.open + b1.close) / 2
        if b3.close < mid_b1:
            return PatternSignal("Evening Doji Star", "BEARISH", 0.9, 0)
    return None


def _detect_three_white_soldiers(b1: Bar, b2: Bar, b3: Bar) -> Optional[PatternSignal]:
    """Three consecutive bullish bars, each opening within prior body, closing higher."""
    if (b1.close > b1.open and b2.close > b2.open and b3.close > b3.open and
            b2.open > b1.open and b2.close > b1.close and
            b3.open > b2.open and b3.close > b2.close):
        return PatternSignal("Three White Soldiers", "BULLISH", 0.9, 0)
    return None


def _detect_three_black_crows(b1: Bar, b2: Bar, b3: Bar) -> Optional[PatternSignal]:
    """Three consecutive bearish bars, each opening within prior body, closing lower."""
    if (b1.close < b1.open and b2.close < b2.open and b3.close < b3.open and
            b2.open < b1.open and b2.close < b1.close and
            b3.open < b2.open and b3.close < b2.close):
        return PatternSignal("Three Black Crows", "BEARISH", 0.9, 0)
    return None


def _detect_abandoned_baby(b1: Bar, b2: Bar, b3: Bar) -> Optional[PatternSignal]:
    """Doji gaps away from both neighbours — strongest reversal."""
    if not _is_doji(b2):
        return None
    # Bullish: b1 bearish, gap down to b2, gap up to b3 bullish
    if (b1.close < b1.open and b3.close > b3.open and
            b2.high < b1.low and b2.low < b3.open):
        return PatternSignal("Abandoned Baby Bull", "BULLISH", 0.95, 0)
    # Bearish: b1 bullish, gap up to b2, gap down to b3 bearish
    if (b1.close > b1.open and b3.close < b3.open and
            b2.low > b1.high and b2.high > b3.open):
        return PatternSignal("Abandoned Baby Bear", "BEARISH", 0.95, 0)
    return None


def _detect_inside_bar(b1: Bar, b2: Bar) -> Optional[PatternSignal]:
    """b2 high < b1 high AND b2 low > b1 low — compression before breakout."""
    if b2.high < b1.high and b2.low > b1.low:
        return PatternSignal("Inside Bar", "NEUTRAL", 0.5, 0)
    return None


# ─── Master scanner ───────────────────────────────────────────────────────────

class CandlestickScanner:
    """
    Maintains a rolling 3-bar buffer and scans all 20 patterns on each update.
    Returns a list of PatternSignals for the most recent bar.
    """

    def __init__(self):
        self._buf: list[Bar] = []
        self._last_patterns: list[PatternSignal] = []

    def update(self, bar: Bar) -> list[PatternSignal]:
        self._buf.append(bar)
        if len(self._buf) > 3:
            self._buf.pop(0)

        signals: list[PatternSignal] = []
        n = len(self._buf)

        if n >= 1:
            b = self._buf[-1]
            for fn in (_detect_doji, _detect_hammer, _detect_inverted_hammer,
                       _detect_shooting_star, _detect_hanging_man,
                       _detect_marubozu, _detect_spinning_top):
                s = fn(b)
                if s:
                    signals.append(s)

        if n >= 2:
            b1, b2 = self._buf[-2], self._buf[-1]
            for fn in (_detect_engulfing, _detect_harami,
                       _detect_piercing_line, _detect_dark_cloud,
                       _detect_inside_bar):
                s = fn(b1, b2)
                if s:
                    signals.append(s)

        if n >= 3:
            b1, b2, b3 = self._buf[-3], self._buf[-2], self._buf[-1]
            for fn in (_detect_morning_star, _detect_evening_star,
                       _detect_morning_doji_star, _detect_evening_doji_star,
                       _detect_three_white_soldiers, _detect_three_black_crows,
                       _detect_abandoned_baby):
                s = fn(b1, b2, b3)
                if s:
                    signals.append(s)

        self._last_patterns = signals
        return signals

    @property
    def last_patterns(self) -> list[PatternSignal]:
        return self._last_patterns

    def bullish_patterns(self) -> list[PatternSignal]:
        return [s for s in self._last_patterns if s.direction == "BULLISH"]

    def bearish_patterns(self) -> list[PatternSignal]:
        return [s for s in self._last_patterns if s.direction == "BEARISH"]

    def strongest(self) -> Optional[PatternSignal]:
        if not self._last_patterns:
            return None
        return max(self._last_patterns, key=lambda s: s.confidence)
