"""
Elliott Wave Analyzer for SENSEX / NIFTY options trading.

Identifies the current wave position (1-5 impulse, A-B-C correction) from
a deque of completed bars, then recommends the appropriate options strategy
with specific strike guidance using Fibonacci confluence.

Wave identification approach:
  - Pivot detection: ZigZag algorithm on close prices (min swing %)
  - Wave classification: rule-based validation of Elliott Wave constraints
  - Confidence scoring: Fibonacci ratio adherence + volume confirmation

Options strategy map (per wave):
  Wave 1 start   → Long ATM CE (trend initiation, small position)
  Wave 2 bottom  → Add CE (confirmation of Wave 1, cheap re-entry)
  Wave 3         → Long OTM CE (highest conviction, biggest position)
  Wave 4         → Short straddle / Iron Condor (range consolidation)
  Wave 5 top     → Long OTM PE or Bear Put Spread (divergence warning)
  Wave C         → Long OTM PE (strongest reversal move)
  Wave B peak    → Short CE (trap rally rejection)
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.brahmastra.data.bar_builder import Bar

# ── Fibonacci ratios used for wave validation ────────────────────────────────
PHI = 1.618033988749895
FIBO_RETRACE = [0.236, 0.382, 0.500, 0.618, 0.786]
FIBO_EXT     = [1.000, 1.272, 1.618, 2.000, 2.618]
FIBO_TOL     = 0.08   # ±8% tolerance around each Fib level


# ── Enums ────────────────────────────────────────────────────────────────────

class WaveType(Enum):
    IMPULSE    = "impulse"
    CORRECTIVE = "corrective"
    UNKNOWN    = "unknown"


class WaveLabel(Enum):
    W1 = "1"
    W2 = "2"
    W3 = "3"
    W4 = "4"
    W5 = "5"
    WA = "A"
    WB = "B"
    WC = "C"
    UNKNOWN = "?"


class OptionsAction(Enum):
    LONG_ATM_CE        = "LONG ATM CE"
    LONG_OTM_CE        = "LONG OTM CE (1-strike OTM)"
    LONG_OTM_PE        = "LONG OTM PE (1-strike OTM)"
    LONG_ATM_PE        = "LONG ATM PE"
    BEAR_PUT_SPREAD    = "BEAR PUT SPREAD"
    SHORT_STRADDLE     = "SHORT STRADDLE"
    IRON_CONDOR        = "IRON CONDOR"
    SHORT_ATM_CE       = "SHORT ATM CE"
    WAIT               = "WAIT — wave ambiguous"


# ── Core data classes ────────────────────────────────────────────────────────

@dataclass
class Pivot:
    """A detected swing high or swing low."""
    index:     int          # bar index in the history deque
    price:     float
    direction: str          # 'HIGH' | 'LOW'
    ts:        object       # bar timestamp


@dataclass
class WaveSegment:
    """A single Elliott wave segment between two consecutive pivots."""
    label:      WaveLabel
    start:      Pivot
    end:        Pivot
    move_pct:   float        # % price move
    retrace_of: Optional[float] = None   # retracement of previous wave (0–1)
    fib_valid:  bool = False             # adheres to expected Fib ratio?


@dataclass
class ElliottWaveResult:
    wave_type:       WaveType
    current_wave:    WaveLabel
    confidence:      float           # 0–100
    completed_waves: list[WaveSegment] = field(default_factory=list)
    pivots:          list[Pivot]     = field(default_factory=list)

    # Options guidance
    action:          OptionsAction   = OptionsAction.WAIT
    strike_guidance: str             = ""
    position_size:   str             = ""   # 'FULL' | 'HALF' | 'QUARTER' | 'NONE'
    reasoning:       str             = ""

    def summary(self) -> str:
        return (
            f"Wave {self.current_wave.value} ({self.wave_type.value.upper()}) | "
            f"conf={self.confidence:.0f}% | "
            f"action={self.action.value} | "
            f"size={self.position_size}"
        )


# ── ZigZag pivot detector ────────────────────────────────────────────────────

def _detect_pivots(closes: list[float], min_swing_pct: float = 1.5) -> list[Pivot]:
    """
    Detect swing highs and lows using a ZigZag filter.
    min_swing_pct: minimum % move to qualify as a new swing.
    Returns alternating HIGH/LOW pivots.
    """
    if len(closes) < 5:
        return []

    pivots: list[Pivot] = []
    direction = None   # 'UP' | 'DOWN'
    last_extreme_idx = 0
    last_extreme_val = closes[0]

    for i in range(1, len(closes)):
        c = closes[i]
        move_pct = (c - last_extreme_val) / last_extreme_val * 100

        if direction is None:
            if abs(move_pct) >= min_swing_pct:
                direction = "UP" if move_pct > 0 else "DOWN"
                last_extreme_idx = i
                last_extreme_val = c

        elif direction == "UP":
            if c > last_extreme_val:
                last_extreme_val = c
                last_extreme_idx = i
            elif (last_extreme_val - c) / last_extreme_val * 100 >= min_swing_pct:
                pivots.append(Pivot(
                    index=last_extreme_idx, price=last_extreme_val,
                    direction="HIGH", ts=None
                ))
                direction = "DOWN"
                last_extreme_val = c
                last_extreme_idx = i

        else:  # DOWN
            if c < last_extreme_val:
                last_extreme_val = c
                last_extreme_idx = i
            elif (c - last_extreme_val) / last_extreme_val * 100 >= min_swing_pct:
                pivots.append(Pivot(
                    index=last_extreme_idx, price=last_extreme_val,
                    direction="LOW", ts=None
                ))
                direction = "UP"
                last_extreme_val = c
                last_extreme_idx = i

    # Add the last in-progress extreme
    if direction == "UP":
        pivots.append(Pivot(index=last_extreme_idx, price=last_extreme_val,
                            direction="HIGH", ts=None))
    elif direction == "DOWN":
        pivots.append(Pivot(index=last_extreme_idx, price=last_extreme_val,
                            direction="LOW", ts=None))

    return pivots


def _attach_timestamps(pivots: list[Pivot], bars: list[Bar]) -> None:
    for p in pivots:
        if 0 <= p.index < len(bars):
            p.ts = bars[p.index].ts_open


# ── Fibonacci helpers ─────────────────────────────────────────────────────────

def _fib_near(ratio: float, targets: list[float], tol: float = FIBO_TOL) -> bool:
    return any(abs(ratio - t) <= tol for t in targets)


def _retrace_ratio(wave_move: float, prior_move: float) -> Optional[float]:
    if prior_move == 0:
        return None
    return abs(wave_move) / abs(prior_move)


# ── Elliott Wave Rule Validator ───────────────────────────────────────────────

class _ImpulseValidator:
    """
    Validates 5-wave impulse structure against classical Elliott Wave rules:
      Rule 1: Wave 2 cannot retrace more than 100% of Wave 1.
      Rule 2: Wave 3 is never the shortest among 1, 3, 5.
      Rule 3: Wave 4 does not enter Wave 1's price territory (non-overlap).
    Guidelines:
      Wave 2 typically retraces 50–61.8% of Wave 1.
      Wave 3 extends 1.618× or 2.618× Wave 1.
      Wave 4 retraces 38.2–50% of Wave 3.
      Wave 5 ≈ Wave 1 or 61.8% of Wave 1–3 net.
    """

    @staticmethod
    def validate(waves: list[float]) -> tuple[bool, float, list[str]]:
        """
        waves: list of signed moves [w1, w2, w3, w4, w5]
        Returns (valid, confidence_pct, reasons).
        """
        if len(waves) < 3:
            return False, 0.0, ["Not enough waves"]

        w1, w2, w3 = waves[0], waves[1], waves[2]
        w4 = waves[3] if len(waves) > 3 else None
        w5 = waves[4] if len(waves) > 4 else None

        issues = []
        bonuses = []
        confidence = 50.0

        # Rule 1: W2 cannot retrace > 100% of W1
        if abs(w2) >= abs(w1):
            issues.append("W2 retraces > 100% of W1 (invalid)")
            return False, 10.0, issues

        # Rule 2: W3 not shortest
        abs_w1, abs_w3 = abs(w1), abs(w3)
        abs_w5 = abs(w5) if w5 is not None else abs_w1  # assume ~W1 if unknown
        if abs_w3 < abs_w1 and abs_w3 < abs_w5:
            issues.append("W3 is shortest — violates Elliott Rule 2")
            return False, 15.0, issues

        # Guideline: W2 retraces 50–61.8% of W1
        r2 = _retrace_ratio(w2, w1)
        if r2 and _fib_near(r2, [0.500, 0.618], tol=0.10):
            bonuses.append(f"W2 retraces {r2:.1%} ≈ golden zone")
            confidence += 10

        # Guideline: W3 extension
        ext3 = _retrace_ratio(w3, w1)
        if ext3 and _fib_near(ext3, [1.618, 2.618], tol=0.12):
            bonuses.append(f"W3 extends {ext3:.2f}× W1 ≈ Fibonacci")
            confidence += 15

        # W4 checks
        if w4 is not None:
            r4 = _retrace_ratio(w4, w3)
            if r4 and _fib_near(r4, [0.382, 0.500], tol=0.08):
                bonuses.append(f"W4 retraces {r4:.1%} of W3 ≈ golden zone")
                confidence += 10
            if abs(w4) >= abs(w3):
                issues.append("W4 deeper than W3 — overlap violation")
                confidence -= 20

        # W5 checks
        if w5 is not None:
            ext5 = _retrace_ratio(w5, w1)
            if ext5 and _fib_near(ext5, [0.618, 1.000], tol=0.10):
                bonuses.append(f"W5 ≈ {ext5:.2f}× W1 ≈ Fibonacci")
                confidence += 8

        all_notes = issues + bonuses
        confidence = max(10.0, min(95.0, confidence))
        return len(issues) == 0, confidence, all_notes


# ── Options Strategy Selector ─────────────────────────────────────────────────

def _options_strategy(
    label: WaveLabel,
    wave_type: WaveType,
    confidence: float,
    current_price: float,
    strike_step: int = 100,     # SENSEX step=100, NIFTY step=50
) -> tuple[OptionsAction, str, str, str]:
    """
    Returns (action, strike_guidance, position_size, reasoning).
    """
    atm = round(current_price / strike_step) * strike_step
    otm_ce = atm + strike_step
    otm_pe = atm - strike_step
    far_otm_pe = atm - 2 * strike_step

    if wave_type == WaveType.IMPULSE:
        if label == WaveLabel.W1:
            return (
                OptionsAction.LONG_ATM_CE,
                f"Buy {atm} CE (ATM) — 2–3 week expiry",
                "QUARTER",
                "Wave 1 start: low conviction, pilot entry. SL below W1 origin."
            )
        if label == WaveLabel.W2:
            return (
                OptionsAction.LONG_ATM_CE,
                f"Add {atm} CE or {atm - strike_step} CE (slightly ITM) — 2 week expiry",
                "HALF",
                "W2 pullback to golden zone — higher-conviction re-entry before W3."
            )
        if label == WaveLabel.W3:
            return (
                OptionsAction.LONG_OTM_CE,
                (
                    f"Buy {otm_ce} CE (1-strike OTM) — 1–2 week expiry. "
                    f"Target: {atm + int(current_price * 0.016 * 1.618):.0f}"
                ),
                "FULL",
                "Wave 3 — longest, fastest wave. Maximum delta exposure. "
                "Trail SL to break-even at 1:1 R:R."
            )
        if label == WaveLabel.W4:
            return (
                OptionsAction.SHORT_STRADDLE if confidence >= 70 else OptionsAction.IRON_CONDOR,
                (
                    f"Sell {atm} CE + {atm} PE (Straddle) or "
                    f"Iron Condor {atm - strike_step}/{atm}/{atm + strike_step}/{atm + 2*strike_step}"
                ),
                "HALF",
                "Wave 4 consolidation — elevated IV, mean-reverting range. "
                "Theta capture strategy. Exit if W4 overlaps W1 territory."
            )
        if label == WaveLabel.W5:
            return (
                OptionsAction.LONG_OTM_PE,
                (
                    f"Buy {otm_pe} PE or Bear Put Spread "
                    f"{atm}/{otm_pe} — weekly expiry. "
                    "Watch for RSI/MACD divergence as W5 confirmation."
                ),
                "HALF",
                "Wave 5 terminal: divergence warning. Reduce CE exposure, "
                "initiate PE hedge. W5 often fails at 1.618× W1–3 distance."
            )

    else:  # CORRECTIVE
        if label == WaveLabel.WA:
            return (
                OptionsAction.LONG_ATM_PE,
                f"Buy {atm} PE — 1–2 week expiry",
                "QUARTER",
                "Wave A start of correction: pilot short. "
                "B-wave rally will follow — keep size small."
            )
        if label == WaveLabel.WB:
            return (
                OptionsAction.SHORT_ATM_CE,
                f"Sell {otm_ce} CE or buy {otm_pe} PE on B-wave peak rejection",
                "HALF",
                "Wave B trap rally — fades 38.2–61.8% of A-wave. "
                "Enter short on reversal signal at B top."
            )
        if label == WaveLabel.WC:
            return (
                OptionsAction.LONG_OTM_PE,
                (
                    f"Buy {otm_pe} PE or {far_otm_pe} PE (2-strikes OTM) — weekly expiry. "
                    f"Target: W3 length projected down from B-wave peak."
                ),
                "FULL",
                "Wave C — as powerful as Wave 3 but down. "
                "Accelerates into expiry. Maximum short delta."
            )

    return (
        OptionsAction.WAIT,
        "Monitor — wave ambiguous. Wait for next pivot confirmation.",
        "NONE",
        "Cannot clearly identify wave position. No trade until pivot confirmed."
    )


# ── Main Analyzer ────────────────────────────────────────────────────────────

class ElliottWaveAnalyzer:
    """
    Stateful Elliott Wave analyzer. Feed completed bars via update().
    Call analyze() to get the current wave reading and options strategy.

    Usage:
        analyzer = ElliottWaveAnalyzer(timeframe="15m", instrument="SENSEX")
        for bar in history:
            analyzer.update(bar)
        result = analyzer.analyze()
        print(result.summary())
    """

    def __init__(
        self,
        timeframe:      str  = "15m",
        instrument:     str  = "SENSEX",
        min_swing_pct:  float = 1.5,    # ZigZag sensitivity (% move to qualify as pivot)
        lookback:       int   = 200,    # bars to keep in memory
        strike_step:    int   = 100,    # SENSEX=100, NIFTY=50
    ):
        self.timeframe     = timeframe
        self.instrument    = instrument
        self.min_swing_pct = min_swing_pct
        self.lookback      = lookback
        self.strike_step   = strike_step
        self._bars: deque[Bar] = deque(maxlen=lookback)

    def update(self, bar: Bar) -> None:
        if bar.complete and bar.timeframe == self.timeframe:
            self._bars.append(bar)

    def analyze(self) -> ElliottWaveResult:
        bars = list(self._bars)
        if len(bars) < 20:
            return ElliottWaveResult(
                wave_type=WaveType.UNKNOWN,
                current_wave=WaveLabel.UNKNOWN,
                confidence=0.0,
                reasoning="Insufficient history (need ≥20 bars)."
            )

        closes = [b.close for b in bars]
        pivots = _detect_pivots(closes, self.min_swing_pct)
        _attach_timestamps(pivots, bars)

        if len(pivots) < 3:
            return ElliottWaveResult(
                wave_type=WaveType.UNKNOWN,
                current_wave=WaveLabel.UNKNOWN,
                confidence=0.0,
                pivots=pivots,
                reasoning="Too few pivots detected — market may be choppy."
            )

        return self._classify(pivots, bars[-1].close)

    def _classify(self, pivots: list[Pivot], current_price: float) -> ElliottWaveResult:
        """Attempt 5-wave impulse fit on the last 6 pivots, then 3-wave corrective."""
        n = len(pivots)

        # Try 5-wave impulse on last 6 pivots (origin + 5 pivots)
        if n >= 6:
            seg = pivots[-6:]
            result = self._try_impulse(seg, current_price)
            if result and result.confidence >= 40:
                return result

        # Try partial 5-wave (3 waves visible) on last 4 pivots
        if n >= 4:
            seg = pivots[-4:]
            result = self._try_impulse_partial(seg, current_price)
            if result and result.confidence >= 40:
                return result

        # Try 3-wave corrective on last 4 pivots
        if n >= 4:
            seg = pivots[-4:]
            result = self._try_corrective(seg, current_price)
            if result and result.confidence >= 35:
                return result

        # Fallback: report latest swing direction with low confidence
        return self._fallback(pivots, current_price)

    # ── 5-wave impulse fit ────────────────────────────────────────────────────

    def _try_impulse(self, pivots: list[Pivot], current_price: float) -> Optional[ElliottWaveResult]:
        """
        Expects 6 alternating pivots: p0(origin) p1 p2 p3 p4 p5.
        Bullish impulse: p0=LOW, p1=HIGH, p2=LOW, p3=HIGH, p4=LOW, p5=HIGH
        """
        if len(pivots) < 6:
            return None

        p = pivots
        # Determine direction
        if p[0].direction == "LOW" and p[1].direction == "HIGH":
            direction = "UP"
        elif p[0].direction == "HIGH" and p[1].direction == "LOW":
            direction = "DOWN"
        else:
            return None

        moves = [p[i+1].price - p[i].price for i in range(5)]
        if direction == "UP":
            expected_signs = [+1, -1, +1, -1, +1]
        else:
            expected_signs = [-1, +1, -1, +1, -1]

        if not all(math.copysign(1, m) == s for m, s in zip(moves, expected_signs)):
            return None

        valid, confidence, notes = _ImpulseValidator.validate(moves)

        # Determine which wave is current (is W5 complete or in progress?)
        last_close_vs_p5 = current_price - p[5].price
        if direction == "UP" and last_close_vs_p5 > 0:
            # Price above W5 high — extended W5 or new cycle
            current_wave = WaveLabel.W5
        elif direction == "UP" and last_close_vs_p5 <= 0:
            current_wave = WaveLabel.W5  # pulling back from W5 peak

        else:
            current_wave = WaveLabel.W5

        segments = self._build_segments(
            [WaveLabel.W1, WaveLabel.W2, WaveLabel.W3, WaveLabel.W4, WaveLabel.W5],
            p[:-1], moves
        )

        action, strike, size, reason = _options_strategy(
            current_wave, WaveType.IMPULSE, confidence,
            current_price, self.strike_step
        )
        reason = reason + " | Notes: " + "; ".join(notes)

        return ElliottWaveResult(
            wave_type=WaveType.IMPULSE,
            current_wave=current_wave,
            confidence=confidence,
            completed_waves=segments,
            pivots=p,
            action=action,
            strike_guidance=strike,
            position_size=size,
            reasoning=reason,
        )

    def _try_impulse_partial(self, pivots: list[Pivot], current_price: float) -> Optional[ElliottWaveResult]:
        """Fit 3 visible waves (W1–W3 in progress) from 4 pivots."""
        if len(pivots) < 4:
            return None
        p = pivots

        if p[0].direction == "LOW" and p[1].direction == "HIGH":
            direction = "UP"
        elif p[0].direction == "HIGH" and p[1].direction == "LOW":
            direction = "DOWN"
        else:
            return None

        moves = [p[i+1].price - p[i].price for i in range(3)]
        if direction == "UP":
            expected_signs = [+1, -1, +1]
        else:
            expected_signs = [-1, +1, -1]

        if not all(math.copysign(1, m) == s for m, s in zip(moves, expected_signs)):
            return None

        valid, confidence, notes = _ImpulseValidator.validate(moves)
        confidence *= 0.85   # Partial — lower confidence

        # Current price above p3 → W3 in progress
        if direction == "UP":
            current_wave = WaveLabel.W3 if current_price > p[3].price else WaveLabel.W2
        else:
            current_wave = WaveLabel.W3 if current_price < p[3].price else WaveLabel.W2

        segments = self._build_segments(
            [WaveLabel.W1, WaveLabel.W2, WaveLabel.W3],
            p[:-1], moves
        )
        action, strike, size, reason = _options_strategy(
            current_wave, WaveType.IMPULSE, confidence,
            current_price, self.strike_step
        )
        reason = reason + " | Partial 3-wave fit. | Notes: " + "; ".join(notes)

        return ElliottWaveResult(
            wave_type=WaveType.IMPULSE,
            current_wave=current_wave,
            confidence=confidence,
            completed_waves=segments,
            pivots=p,
            action=action,
            strike_guidance=strike,
            position_size=size,
            reasoning=reason,
        )

    # ── 3-wave corrective fit ─────────────────────────────────────────────────

    def _try_corrective(self, pivots: list[Pivot], current_price: float) -> Optional[ElliottWaveResult]:
        """
        A-B-C corrective from 4 pivots: p0(prior high/low) p1(A end) p2(B end) p3(C end/in-prog)
        """
        if len(pivots) < 4:
            return None
        p = pivots

        # Downward correction: p0=HIGH p1=LOW p2=HIGH p3=LOW
        if p[0].direction == "HIGH" and p[1].direction == "LOW":
            direction = "DOWN"
        elif p[0].direction == "LOW" and p[1].direction == "HIGH":
            direction = "UP"
        else:
            return None

        moves = [p[i+1].price - p[i].price for i in range(3)]
        if direction == "DOWN":
            expected_signs = [-1, +1, -1]
        else:
            expected_signs = [+1, -1, +1]

        if not all(math.copysign(1, m) == s for m, s in zip(moves, expected_signs)):
            return None

        wA, wB, wC = moves
        confidence = 40.0
        notes = []

        # B-wave should retrace 38.2–61.8% of A
        r_b = _retrace_ratio(wB, wA)
        if r_b and _fib_near(r_b, [0.382, 0.500, 0.618], tol=0.08):
            confidence += 15
            notes.append(f"B retraces {r_b:.1%} of A ≈ Fibonacci")

        # C should be ≈ equal to A or extend 1.618× A
        r_c = _retrace_ratio(wC, wA)
        if r_c and _fib_near(r_c, [1.000, 1.618], tol=0.10):
            confidence += 15
            notes.append(f"C = {r_c:.2f}× A ≈ Fibonacci extension")

        confidence = min(90.0, confidence)

        # Current wave
        if abs(current_price - p[3].price) / p[3].price < 0.005:
            current_wave = WaveLabel.WC
        elif abs(current_price - p[2].price) / p[2].price < 0.005:
            current_wave = WaveLabel.WB
        else:
            current_wave = WaveLabel.WC   # default: C in progress

        segments = self._build_segments(
            [WaveLabel.WA, WaveLabel.WB, WaveLabel.WC],
            p[:-1], moves
        )
        action, strike, size, reason = _options_strategy(
            current_wave, WaveType.CORRECTIVE, confidence,
            current_price, self.strike_step
        )
        reason = reason + " | Notes: " + "; ".join(notes)

        return ElliottWaveResult(
            wave_type=WaveType.CORRECTIVE,
            current_wave=current_wave,
            confidence=confidence,
            completed_waves=segments,
            pivots=p,
            action=action,
            strike_guidance=strike,
            position_size=size,
            reasoning=reason,
        )

    # ── Fallback ──────────────────────────────────────────────────────────────

    def _fallback(self, pivots: list[Pivot], current_price: float) -> ElliottWaveResult:
        last = pivots[-1] if pivots else None
        direction = "up" if last and last.direction == "HIGH" else "down"
        return ElliottWaveResult(
            wave_type=WaveType.UNKNOWN,
            current_wave=WaveLabel.UNKNOWN,
            confidence=15.0,
            pivots=pivots,
            action=OptionsAction.WAIT,
            strike_guidance="",
            position_size="NONE",
            reasoning=(
                f"Wave count ambiguous. Last pivot: {direction} at {last.price if last else '?'}. "
                "Need clearer swing structure. Monitor."
            )
        )

    @staticmethod
    def _build_segments(
        labels: list[WaveLabel],
        starts: list[Pivot],
        moves: list[float],
    ) -> list[WaveSegment]:
        segments = []
        for i, (label, start, move) in enumerate(zip(labels, starts, moves)):
            move_pct = move / start.price * 100
            seg = WaveSegment(
                label=label, start=start,
                end=Pivot(index=start.index, price=start.price + move,
                          direction="HIGH" if move > 0 else "LOW", ts=None),
                move_pct=round(move_pct, 2)
            )
            if i > 0:
                seg.retrace_of = _retrace_ratio(move, moves[i - 1])
                seg.fib_valid = _fib_near(
                    abs(seg.retrace_of) if seg.retrace_of else 0,
                    FIBO_RETRACE + FIBO_EXT, tol=FIBO_TOL
                )
            segments.append(seg)
        return segments


# ── Standalone OHLCV analysis (for backtest / Kite historical data) ──────────

def analyze_ohlcv(
    ohlcv: list[dict],
    instrument: str = "SENSEX",
    min_swing_pct: float = 1.5,
    strike_step: int = 100,
) -> ElliottWaveResult:
    """
    Convenience wrapper for offline / backtest use.

    ohlcv: list of dicts with keys: open, high, low, close, volume, date/datetime.
    Returns ElliottWaveResult without requiring live Bar objects.
    """
    from datetime import datetime, timezone, timedelta

    IST = timezone(timedelta(hours=5, minutes=30))

    fake_bars: list[Bar] = []
    for i, row in enumerate(ohlcv):
        ts = row.get("date") or row.get("datetime") or datetime.now(IST)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts).replace(tzinfo=IST)
        bar = Bar(
            instrument=instrument,
            timeframe="1D",
            ts_open=ts,
            ts_close=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0)),
            vwap=float(row.get("close", 0)),
            tick_count=1,
            complete=True,
        )
        fake_bars.append(bar)

    analyzer = ElliottWaveAnalyzer(
        timeframe="1D",
        instrument=instrument,
        min_swing_pct=min_swing_pct,
        lookback=500,
        strike_step=strike_step,
    )
    for bar in fake_bars:
        analyzer.update(bar)

    return analyzer.analyze()
