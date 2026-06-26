"""
GTI Signal — Ghost Trader Indicator equivalent.

Combines 4 layers into a single high-probability entry signal:
  Layer 1: Demand/Supply Zone (institutional footprint)
  Layer 2: Market Phase (Accumulation → Compression → Trap → Breakout)
  Layer 3: Elliott Wave direction confirmation
  Layer 4: Volume surge (gamma play / short covering trigger)

Signal hierarchy:
  STRONG_BUY  — all 4 layers aligned, breakout with volume at demand zone, W3/WC confirmed
  BUY         — 3/4 layers aligned
  WEAK_BUY    — 2/4, proceed with caution
  WAIT        — ambiguous
  WEAK_SELL   — 2/4 bearish
  SELL        — 3/4 bearish
  STRONG_SELL — all 4 aligned bearish

Expiry-day gate:
  Position size doubled on expiry day (Tue=Nifty, Thu=Sensex) when
  signal is BUY/SELL or stronger. On non-expiry days, max size = HALF.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.brahmastra.indicators.demand_supply_zones import (
    DemandSupplyZones, ZoneSnapshot, MarketPhase, ZoneType
)
from src.brahmastra.indicators.elliott_wave import (
    ElliottWaveAnalyzer, ElliottWaveResult, WaveLabel, WaveType, OptionsAction
)
from src.brahmastra.data.bar_builder import Bar

IST = timezone(timedelta(hours=5, minutes=30))

# Waves with strong directional conviction
BULL_WAVES = {WaveLabel.W1, WaveLabel.W2, WaveLabel.W3}
BEAR_WAVES = {WaveLabel.W5, WaveLabel.WA, WaveLabel.WB, WaveLabel.WC}
RANGE_WAVES = {WaveLabel.W4}

EXPIRY_DAYS = {"SENSEX": 3, "NIFTY": 1}   # Thu=3, Tue=1 (weekday indices)


@dataclass
class GTISignalResult:
    # Core signal
    signal:          str           # 'STRONG_BUY'|'BUY'|'WEAK_BUY'|'WAIT'|'WEAK_SELL'|'SELL'|'STRONG_SELL'
    strength:        float         # 0–100
    direction:       str           # 'BULL' | 'BEAR' | 'NEUTRAL'

    # Layer outputs
    zone_signal:     str           # 'BUY'|'SELL'|'WAIT'
    zone_strength:   float
    zone_phase:      str           # market phase name
    wave_label:      str           # current wave
    wave_type:       str
    wave_confidence: float
    vol_ratio:       float         # current volume / 20-bar avg
    atr_ratio:       float         # ATR compression ratio

    # Zone details
    demand_zone_mid: Optional[float] = None
    supply_zone_mid: Optional[float] = None
    at_demand:       bool = False
    at_supply:       bool = False
    indecision_candle: bool = False
    breakout_dir:    Optional[str] = None

    # Trade guidance
    action:          str = "WAIT"  # options action from EW module
    strike_guidance: str = ""
    position_size:   str = "NONE"  # FULL|HALF|QUARTER|NONE
    is_expiry_day:   bool = False
    reasoning:       list[str] = field(default_factory=list)

    def summary(self) -> str:
        exp = " [EXPIRY]" if self.is_expiry_day else ""
        return (
            f"{self.signal}({self.strength:.0f}){exp} | "
            f"Phase={self.zone_phase} | Wave={self.wave_label} "
            f"({self.wave_type}) conf={self.wave_confidence:.0f}% | "
            f"vol={self.vol_ratio:.1f}x | ATR={self.atr_ratio:.2f}"
        )

    def is_actionable(self, min_strength: float = 60.0) -> bool:
        return self.signal not in ("WAIT", "WEAK_BUY", "WEAK_SELL") and self.strength >= min_strength


class GTISignal:
    """
    Stateful GTI signal generator. Feed bars via update(); call analyze() for signal.

    Usage:
        gti = GTISignal(instrument='SENSEX', timeframe='15m')
        for bar in bars:
            gti.update(bar)
        result = gti.analyze()
        if result.is_actionable():
            print(result.summary())
    """

    def __init__(
        self,
        instrument:       str   = "SENSEX",
        timeframe:        str   = "15m",
        min_ew_confidence: float = 50.0,
        min_zone_strength: float = 40.0,
    ):
        self.instrument        = instrument
        self.timeframe         = timeframe
        self.min_ew_confidence = min_ew_confidence
        self.min_zone_strength = min_zone_strength

        strike_step = 100 if instrument == "SENSEX" else 50

        self.dsz = DemandSupplyZones(timeframe=timeframe, lookback=300)
        self.ew  = ElliottWaveAnalyzer(
            timeframe=timeframe, instrument=instrument,
            min_swing_pct=1.2 if timeframe in ("15m", "1h") else 2.0,
            lookback=300, strike_step=strike_step,
        )

    def update(self, bar: Bar) -> None:
        self.dsz.update(bar)
        self.ew.update(bar)

    def analyze(self, current_price: Optional[float] = None) -> GTISignalResult:
        zone_snap = self.dsz.snapshot()
        ew_result = self.ew.analyze()
        now       = datetime.now(IST)
        is_expiry = self._is_expiry_day(now)

        if zone_snap is None:
            return self._wait_result(ew_result, is_expiry, ["Insufficient zone data"])

        return self._combine(zone_snap, ew_result, is_expiry, current_price)

    # ── Core combination logic ────────────────────────────────────────────────

    def _combine(
        self,
        zs: ZoneSnapshot,
        ew: ElliottWaveResult,
        is_expiry: bool,
        current_price: Optional[float],
    ) -> GTISignalResult:
        reasons: list[str] = []
        bull_score = 0.0
        bear_score = 0.0

        # ── Layer 1: Zone signal ─────────────────────────────────────────────
        if zs.signal == "BUY":
            bull_score += zs.signal_strength * 0.35
            reasons.append(f"Zone BUY at demand ({zs.phase.value})")
        elif zs.signal == "SELL":
            bear_score += zs.signal_strength * 0.35
            reasons.append(f"Zone SELL at supply ({zs.phase.value})")
        else:
            reasons.append(f"Zone WAIT ({zs.phase.value})")

        # ── Layer 2: Market phase bonus ──────────────────────────────────────
        if zs.phase == MarketPhase.BREAKOUT:
            if zs.breakout_dir == "UP":
                bull_score += 25
                reasons.append("Breakout UP with volume")
            elif zs.breakout_dir == "DOWN":
                bear_score += 25
                reasons.append("Breakout DOWN with volume")
        elif zs.phase == MarketPhase.COMPRESSION:
            bonus = zs.phase_confidence * 15
            if zs.at_demand:
                bull_score += bonus
                reasons.append(f"Compression at demand (conf={zs.phase_confidence:.0%})")
            elif zs.at_supply:
                bear_score += bonus
                reasons.append(f"Compression at supply (conf={zs.phase_confidence:.0%})")
        elif zs.phase == MarketPhase.TRAP:
            if zs.at_demand:
                bull_score += 15
                reasons.append("Trap reversal at demand (SL hunt)")
            elif zs.at_supply:
                bear_score += 15
                reasons.append("Trap reversal at supply (SL hunt)")

        # Indecision candle at zone = compression confirmation
        if zs.indecision_at_zone:
            if zs.at_demand:
                bull_score += 10
                reasons.append("Indecision candle at demand zone")
            elif zs.at_supply:
                bear_score += 10
                reasons.append("Indecision candle at supply zone")

        # ── Layer 3: Elliott Wave direction ──────────────────────────────────
        ew_weight = min(ew.confidence / 100, 1.0) * 30  # max 30 pts from EW
        if ew.confidence >= self.min_ew_confidence:
            if ew.current_wave in BULL_WAVES and ew.wave_type == WaveType.IMPULSE:
                bull_score += ew_weight
                reasons.append(f"EW Wave {ew.current_wave.value} impulse (conf={ew.confidence:.0f}%)")
            elif ew.current_wave in BEAR_WAVES:
                bear_score += ew_weight
                reasons.append(f"EW Wave {ew.current_wave.value} bearish (conf={ew.confidence:.0f}%)")
            elif ew.current_wave == WaveLabel.W3 and ew.wave_type == WaveType.IMPULSE:
                bull_score += ew_weight * 1.3   # W3 gets extra weight
                reasons.append(f"EW Wave 3 — STRONGEST IMPULSE (conf={ew.confidence:.0f}%)")
            elif ew.current_wave == WaveLabel.WC:
                bear_score += ew_weight * 1.3
                reasons.append(f"EW Wave C — C-wave dump (conf={ew.confidence:.0f}%)")
        else:
            reasons.append(f"EW low confidence ({ew.confidence:.0f}%) — partial weight")
            if ew.current_wave in BULL_WAVES:
                bull_score += ew_weight * 0.4
            elif ew.current_wave in BEAR_WAVES:
                bear_score += ew_weight * 0.4

        # ── Layer 4: Volume surge (gamma play trigger) ───────────────────────
        if zs.vol_ratio >= 2.0:
            if bull_score > bear_score:
                bull_score += 15
                reasons.append(f"Volume surge {zs.vol_ratio:.1f}x — potential gamma play")
            else:
                bear_score += 15
                reasons.append(f"Volume surge {zs.vol_ratio:.1f}x — potential gamma play")
        elif zs.vol_ratio >= 1.5:
            if bull_score > bear_score:
                bull_score += 8
            else:
                bear_score += 8

        # ── Expiry day multiplier ────────────────────────────────────────────
        if is_expiry:
            bull_score *= 1.25
            bear_score *= 1.25
            reasons.append("EXPIRY DAY — position multiplier active")

        # ── Determine signal ─────────────────────────────────────────────────
        net = bull_score - bear_score
        total = bull_score + bear_score if (bull_score + bear_score) > 0 else 1

        if net > 55:
            signal = "STRONG_BUY"
            direction = "BULL"
        elif net > 30:
            signal = "BUY"
            direction = "BULL"
        elif net > 10:
            signal = "WEAK_BUY"
            direction = "BULL"
        elif net < -55:
            signal = "STRONG_SELL"
            direction = "BEAR"
        elif net < -30:
            signal = "SELL"
            direction = "BEAR"
        elif net < -10:
            signal = "WEAK_SELL"
            direction = "BEAR"
        else:
            signal = "WAIT"
            direction = "NEUTRAL"

        strength = min(100.0, abs(net))

        # Position size
        if signal in ("STRONG_BUY", "STRONG_SELL"):
            size = "FULL" if is_expiry else "HALF"
        elif signal in ("BUY", "SELL"):
            size = "HALF" if is_expiry else "QUARTER"
        elif signal in ("WEAK_BUY", "WEAK_SELL"):
            size = "QUARTER"
        else:
            size = "NONE"

        return GTISignalResult(
            signal=signal, strength=strength, direction=direction,
            zone_signal=zs.signal, zone_strength=zs.signal_strength,
            zone_phase=zs.phase.value,
            wave_label=ew.current_wave.value, wave_type=ew.wave_type.value,
            wave_confidence=ew.confidence,
            vol_ratio=round(zs.vol_ratio, 2),
            atr_ratio=round(zs.atr_ratio, 2),
            demand_zone_mid=zs.nearest_demand.mid if zs.nearest_demand else None,
            supply_zone_mid=zs.nearest_supply.mid if zs.nearest_supply else None,
            at_demand=zs.at_demand, at_supply=zs.at_supply,
            indecision_candle=zs.indecision_at_zone,
            breakout_dir=zs.breakout_dir,
            action=ew.action.value,
            strike_guidance=ew.strike_guidance,
            position_size=size,
            is_expiry_day=is_expiry,
            reasoning=reasons,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_expiry_day(self, now: datetime) -> bool:
        exp_weekday = EXPIRY_DAYS.get(self.instrument, 3)
        return now.weekday() == exp_weekday

    def _wait_result(
        self, ew: ElliottWaveResult, is_expiry: bool, reasons: list[str]
    ) -> GTISignalResult:
        return GTISignalResult(
            signal="WAIT", strength=0.0, direction="NEUTRAL",
            zone_signal="WAIT", zone_strength=0.0, zone_phase="UNKNOWN",
            wave_label=ew.current_wave.value if ew else "?",
            wave_type=ew.wave_type.value if ew else "unknown",
            wave_confidence=ew.confidence if ew else 0.0,
            vol_ratio=1.0, atr_ratio=1.0,
            is_expiry_day=is_expiry,
            reasoning=reasons,
        )
