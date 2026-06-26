"""
Demand & Supply Zone Detector — GTI-equivalent implementation.

Identifies institutional demand/supply zones using:
  - High-volume impulse candles as zone anchors
  - Zone persistence until consumed by price
  - 4 market phases: ACCUMULATION, COMPRESSION, TRAP, BREAKOUT

Zone formation logic:
  A demand zone forms when price makes a strong upward impulse (body > 60%
  of range, volume > 1.5× avg) from a base. The zone boundaries are the
  body of the last consolidation candle before the impulse.

  A supply zone forms symmetrically on strong bearish impulses.

Zone states:
  FRESH    — never retested since forming
  TESTED   — price has touched zone once and bounced
  CONSUMED — price has closed beyond zone with volume
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.brahmastra.data.bar_builder import Bar


# ── Enums ─────────────────────────────────────────────────────────────────────

class ZoneType(Enum):
    DEMAND = "DEMAND"   # support / buy zone
    SUPPLY = "SUPPLY"   # resistance / sell zone


class ZoneState(Enum):
    FRESH    = "FRESH"
    TESTED   = "TESTED"
    CONSUMED = "CONSUMED"


class MarketPhase(Enum):
    ACCUMULATION = "ACCUMULATION"   # institutional loading at zone
    COMPRESSION  = "COMPRESSION"    # coiling, ATR contracting
    TRAP         = "TRAP"           # false break below/above zone
    BREAKOUT     = "BREAKOUT"       # valid break with volume
    TRENDING     = "TRENDING"       # away from any zone
    UNKNOWN      = "UNKNOWN"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Zone:
    zone_type:  ZoneType
    top:        float          # upper boundary
    bottom:     float          # lower boundary
    mid:        float          # midpoint
    formed_at:  object         # timestamp
    anchor_vol: float          # volume of the forming candle
    avg_vol_at_formation: float
    state:      ZoneState = ZoneState.FRESH
    touch_count: int = 0
    strength:   float = 0.0   # 0–1, based on impulse size and volume ratio

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def contains(self, price: float, tolerance: float = 0.003) -> bool:
        """True if price is within zone ± tolerance %."""
        margin = self.mid * tolerance
        return (self.bottom - margin) <= price <= (self.top + margin)

    def is_above(self, price: float) -> bool:
        return price > self.top

    def is_below(self, price: float) -> bool:
        return price < self.bottom


@dataclass
class ZoneSnapshot:
    nearest_demand:    Optional[Zone]
    nearest_supply:    Optional[Zone]
    at_demand:         bool
    at_supply:         bool
    phase:             MarketPhase
    phase_confidence:  float          # 0–1
    atr_ratio:         float          # current ATR / baseline ATR (compression if < 0.6)
    vol_ratio:         float          # current volume / 20-bar average
    indecision_at_zone: bool          # doji/spinning top inside zone
    breakout_dir:      Optional[str]  # 'UP' | 'DOWN' | None
    signal:            str            # 'BUY' | 'SELL' | 'WAIT'
    signal_strength:   float          # 0–100

    def summary(self) -> str:
        d = f"D@{self.nearest_demand.mid:.0f}" if self.nearest_demand else "D:none"
        s = f"S@{self.nearest_supply.mid:.0f}" if self.nearest_supply else "S:none"
        return (
            f"Phase={self.phase.value} | {d} | {s} | "
            f"vol={self.vol_ratio:.1f}x | ATR={self.atr_ratio:.2f} | "
            f"signal={self.signal}({self.signal_strength:.0f})"
        )


# ── Core detector ─────────────────────────────────────────────────────────────

class DemandSupplyZones:
    """
    Stateful zone detector. Feed completed bars via update().
    Call snapshot() to get the current zone state and market phase.

    Usage:
        dsz = DemandSupplyZones(timeframe='15m')
        for bar in history:
            dsz.update(bar)
        snap = dsz.snapshot()
        print(snap.summary())
    """

    # Impulse candle thresholds
    BODY_PCT_MIN      = 0.55   # body must be ≥ 55% of candle range
    VOL_MULT_ZONE     = 1.4    # volume must be ≥ 1.4× average to form zone
    IMPULSE_MOVE_ATR  = 1.5    # impulse must be ≥ 1.5× ATR

    # Breakout thresholds
    BREAKOUT_VOL_MULT = 1.5    # breakout volume ≥ 1.5× average
    BREAKOUT_CONFIRMS = 2      # consecutive closes beyond zone

    # Phase thresholds
    COMPRESSION_ATR_RATIO = 0.65   # ATR < 65% of baseline = compression
    TRAP_RETRACE_PCT      = 0.004  # price briefly exceeds zone by < 0.4% = trap

    def __init__(
        self,
        timeframe:      str = "15m",
        lookback:       int = 200,
        max_zones:      int = 8,     # keep only the nearest N zones
        vol_period:     int = 20,
        atr_period:     int = 14,
    ):
        self.timeframe   = timeframe
        self.max_zones   = max_zones
        self.vol_period  = vol_period
        self.atr_period  = atr_period

        self._bars:      deque[Bar]  = deque(maxlen=lookback)
        self._volumes:   deque[float] = deque(maxlen=vol_period)
        self._atrs:      deque[float] = deque(maxlen=atr_period * 3)
        self._baseline_atr: Optional[float] = None
        self._prev_close: Optional[float]   = None

        self.demand_zones: list[Zone] = []
        self.supply_zones: list[Zone] = []

        self._breakout_count: dict[str, int] = {}   # zone_id → consecutive confirms

    # ── Public API ────────────────────────────────────────────────────────────

    def update(self, bar: Bar) -> None:
        if not bar.complete or bar.timeframe != self.timeframe:
            return

        self._bars.append(bar)
        self._volumes.append(bar.volume)

        # ATR
        atr_val = self._calc_atr(bar)
        if atr_val:
            self._atrs.append(atr_val)
            if len(self._atrs) >= self.atr_period:
                self._baseline_atr = sum(list(self._atrs)[-self.atr_period * 2:]) / min(len(self._atrs), self.atr_period * 2)

        self._prev_close = bar.close

        if len(self._bars) < 5:
            return

        # Update existing zone states
        self._update_zone_states(bar)

        # Try to form new zones from recent bars
        self._detect_new_zones()

        # Prune consumed and distant zones
        self._prune_zones(bar.close)

    def snapshot(self) -> Optional[ZoneSnapshot]:
        if not self._bars:
            return None

        bar  = self._bars[-1]
        price = bar.close
        avg_vol = sum(self._volumes) / len(self._volumes) if self._volumes else 1
        vol_ratio = bar.volume / avg_vol if avg_vol > 0 else 1.0
        atr_ratio = self._current_atr_ratio()

        # Nearest live zones
        demand = self._nearest_zone(self.demand_zones, price, ZoneType.DEMAND)
        supply = self._nearest_zone(self.supply_zones, price, ZoneType.SUPPLY)

        at_demand = demand is not None and demand.contains(price)
        at_supply = supply is not None and supply.contains(price)

        indecision = self._is_indecision(bar)
        breakout_dir = self._detect_breakout(bar, demand, supply, vol_ratio)

        phase, phase_conf = self._classify_phase(
            bar, demand, supply, at_demand, at_supply,
            atr_ratio, vol_ratio, indecision, breakout_dir
        )

        signal, strength = self._generate_signal(
            phase, phase_conf, at_demand, at_supply,
            indecision, breakout_dir, vol_ratio
        )

        return ZoneSnapshot(
            nearest_demand=demand,
            nearest_supply=supply,
            at_demand=at_demand,
            at_supply=at_supply,
            phase=phase,
            phase_confidence=phase_conf,
            atr_ratio=atr_ratio,
            vol_ratio=vol_ratio,
            indecision_at_zone=indecision and (at_demand or at_supply),
            breakout_dir=breakout_dir,
            signal=signal,
            signal_strength=strength,
        )

    # ── Zone formation ─────────────────────────────────────────────────────────

    def _detect_new_zones(self) -> None:
        bars = list(self._bars)
        if len(bars) < 4:
            return

        avg_vol = sum(self._volumes) / len(self._volumes) if self._volumes else 1
        atr     = self._baseline_atr or 1

        # Check last 3 bars for impulse candles
        for i in range(max(1, len(bars) - 3), len(bars)):
            bar = bars[i]
            rng = bar.high - bar.low
            if rng <= 0:
                continue

            body     = abs(bar.close - bar.open)
            body_pct = body / rng
            vol_mult = bar.volume / avg_vol if avg_vol > 0 else 1

            # Must meet impulse criteria
            if body_pct < self.BODY_PCT_MIN or vol_mult < self.VOL_MULT_ZONE:
                continue
            if body < atr * self.IMPULSE_MOVE_ATR * 0.5:
                continue

            # The zone is the base candle (i-1) from which the impulse launched
            if i < 1:
                continue
            base = bars[i - 1]
            zone_top    = max(base.open, base.close)
            zone_bottom = min(base.open, base.close)

            if zone_top <= zone_bottom:
                zone_top    = base.high
                zone_bottom = base.low

            strength = min(1.0, (body_pct * 0.5) + (min(vol_mult, 3.0) / 3.0 * 0.5))

            if bar.close > bar.open:  # bullish impulse → demand zone
                z = Zone(
                    zone_type=ZoneType.DEMAND,
                    top=zone_top, bottom=zone_bottom,
                    mid=(zone_top + zone_bottom) / 2,
                    formed_at=bar.ts_open,
                    anchor_vol=bar.volume,
                    avg_vol_at_formation=avg_vol,
                    strength=strength,
                )
                if not self._zone_exists(self.demand_zones, z):
                    self.demand_zones.append(z)
            else:  # bearish impulse → supply zone
                z = Zone(
                    zone_type=ZoneType.SUPPLY,
                    top=zone_top, bottom=zone_bottom,
                    mid=(zone_top + zone_bottom) / 2,
                    formed_at=bar.ts_open,
                    anchor_vol=bar.volume,
                    avg_vol_at_formation=avg_vol,
                    strength=strength,
                )
                if not self._zone_exists(self.supply_zones, z):
                    self.supply_zones.append(z)

    def _zone_exists(self, zones: list[Zone], new: Zone, overlap_pct: float = 0.005) -> bool:
        margin = new.mid * overlap_pct
        return any(abs(z.mid - new.mid) < margin for z in zones if z.state != ZoneState.CONSUMED)

    # ── Zone state updates ────────────────────────────────────────────────────

    def _update_zone_states(self, bar: Bar) -> None:
        avg_vol = sum(self._volumes) / len(self._volumes) if self._volumes else 1
        vol_ratio = bar.volume / avg_vol if avg_vol > 0 else 1

        for zone in self.demand_zones + self.supply_zones:
            if zone.state == ZoneState.CONSUMED:
                continue

            if zone.contains(bar.close):
                if zone.state == ZoneState.FRESH:
                    zone.state = ZoneState.TESTED
                zone.touch_count += 1

            # Demand zone broken below with volume
            if zone.zone_type == ZoneType.DEMAND and bar.close < zone.bottom:
                if vol_ratio >= self.BREAKOUT_VOL_MULT:
                    key = f"D_{zone.mid:.0f}"
                    self._breakout_count[key] = self._breakout_count.get(key, 0) + 1
                    if self._breakout_count[key] >= self.BREAKOUT_CONFIRMS:
                        zone.state = ZoneState.CONSUMED
                else:
                    key = f"D_{zone.mid:.0f}"
                    self._breakout_count[key] = 0  # reset without volume

            # Supply zone broken above with volume
            elif zone.zone_type == ZoneType.SUPPLY and bar.close > zone.top:
                if vol_ratio >= self.BREAKOUT_VOL_MULT:
                    key = f"S_{zone.mid:.0f}"
                    self._breakout_count[key] = self._breakout_count.get(key, 0) + 1
                    if self._breakout_count[key] >= self.BREAKOUT_CONFIRMS:
                        zone.state = ZoneState.CONSUMED
                else:
                    key = f"S_{zone.mid:.0f}"
                    self._breakout_count[key] = 0

    def _prune_zones(self, price: float) -> None:
        # Remove consumed zones
        self.demand_zones = [z for z in self.demand_zones if z.state != ZoneState.CONSUMED]
        self.supply_zones = [z for z in self.supply_zones if z.state != ZoneState.CONSUMED]

        # Keep only N nearest to price
        if len(self.demand_zones) > self.max_zones:
            self.demand_zones.sort(key=lambda z: abs(price - z.mid))
            self.demand_zones = self.demand_zones[:self.max_zones]
        if len(self.supply_zones) > self.max_zones:
            self.supply_zones.sort(key=lambda z: abs(price - z.mid))
            self.supply_zones = self.supply_zones[:self.max_zones]

    # ── Phase classification ──────────────────────────────────────────────────

    def _classify_phase(
        self, bar: Bar,
        demand: Optional[Zone], supply: Optional[Zone],
        at_demand: bool, at_supply: bool,
        atr_ratio: float, vol_ratio: float,
        indecision: bool, breakout_dir: Optional[str],
    ) -> tuple[MarketPhase, float]:

        if breakout_dir == "UP":
            return MarketPhase.BREAKOUT, min(1.0, vol_ratio / 2.0)
        if breakout_dir == "DOWN":
            return MarketPhase.BREAKOUT, min(1.0, vol_ratio / 2.0)

        # Trap: price briefly poked beyond zone but volume is weak
        if at_demand or at_supply:
            if atr_ratio < self.COMPRESSION_ATR_RATIO:
                conf = min(1.0, (self.COMPRESSION_ATR_RATIO - atr_ratio) / 0.2 + 0.4)
                if indecision:
                    return MarketPhase.COMPRESSION, min(1.0, conf + 0.2)
                return MarketPhase.COMPRESSION, conf

            if vol_ratio < 0.7:
                return MarketPhase.ACCUMULATION, 0.6
            return MarketPhase.ACCUMULATION, 0.5

        # Trap pattern: price outside zone but recovering back
        if demand and not at_demand:
            dist_pct = abs(bar.close - demand.bottom) / demand.mid
            if dist_pct < self.TRAP_RETRACE_PCT and vol_ratio < self.BREAKOUT_VOL_MULT:
                return MarketPhase.TRAP, 0.65

        if supply and not at_supply:
            dist_pct = abs(bar.close - supply.top) / supply.mid
            if dist_pct < self.TRAP_RETRACE_PCT and vol_ratio < self.BREAKOUT_VOL_MULT:
                return MarketPhase.TRAP, 0.65

        if demand or supply:
            return MarketPhase.TRENDING, 0.5
        return MarketPhase.UNKNOWN, 0.2

    # ── Signal generation ─────────────────────────────────────────────────────

    def _generate_signal(
        self, phase: MarketPhase, phase_conf: float,
        at_demand: bool, at_supply: bool,
        indecision: bool, breakout_dir: Optional[str],
        vol_ratio: float,
    ) -> tuple[str, float]:

        if phase == MarketPhase.BREAKOUT:
            if breakout_dir == "UP":
                strength = min(100.0, 60 + vol_ratio * 15)
                return "BUY", strength
            elif breakout_dir == "DOWN":
                strength = min(100.0, 60 + vol_ratio * 15)
                return "SELL", strength

        if phase == MarketPhase.COMPRESSION:
            if at_demand and indecision:
                return "BUY", min(100.0, phase_conf * 80 + 10)
            if at_supply and indecision:
                return "SELL", min(100.0, phase_conf * 80 + 10)
            return "WAIT", 30.0

        if phase == MarketPhase.TRAP:
            if at_demand:
                return "BUY", 55.0   # trap reversal — buy the false breakdown
            if at_supply:
                return "SELL", 55.0
            return "WAIT", 20.0

        return "WAIT", 10.0

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _calc_atr(self, bar: Bar) -> Optional[float]:
        if self._prev_close is None:
            return bar.high - bar.low
        tr = max(
            bar.high - bar.low,
            abs(bar.high - self._prev_close),
            abs(bar.low  - self._prev_close),
        )
        return tr

    def _current_atr_ratio(self) -> float:
        if not self._bars or self._baseline_atr is None or self._baseline_atr == 0:
            return 1.0
        bars = list(self._bars)[-5:]
        recent_atrs = []
        for i, b in enumerate(bars):
            pc = bars[i - 1].close if i > 0 else b.open
            tr = max(b.high - b.low, abs(b.high - pc), abs(b.low - pc))
            recent_atrs.append(tr)
        if not recent_atrs:
            return 1.0
        return (sum(recent_atrs) / len(recent_atrs)) / self._baseline_atr

    @staticmethod
    def _nearest_zone(zones: list[Zone], price: float, ztype: ZoneType) -> Optional[Zone]:
        live = [z for z in zones if z.state != ZoneState.CONSUMED]
        if not live:
            return None
        if ztype == ZoneType.DEMAND:
            below = [z for z in live if z.mid <= price * 1.02]
            return max(below, key=lambda z: z.mid) if below else min(live, key=lambda z: abs(price - z.mid))
        else:
            above = [z for z in live if z.mid >= price * 0.98]
            return min(above, key=lambda z: z.mid) if above else min(live, key=lambda z: abs(price - z.mid))

    @staticmethod
    def _is_indecision(bar: Bar) -> bool:
        rng = bar.high - bar.low
        if rng <= 0:
            return False
        body = abs(bar.close - bar.open)
        return body / rng < 0.30   # body < 30% of range (doji/spinning top)

    @staticmethod
    def _detect_breakout(
        bar: Bar,
        demand: Optional[Zone],
        supply: Optional[Zone],
        vol_ratio: float,
    ) -> Optional[str]:
        if vol_ratio < 1.5:
            return None
        if supply and bar.close > supply.top and supply.state != ZoneState.CONSUMED:
            return "UP"
        if demand and bar.close < demand.bottom and demand.state != ZoneState.CONSUMED:
            return "DOWN"
        return None
