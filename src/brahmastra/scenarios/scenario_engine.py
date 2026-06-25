"""
BRAHMASTRA Scenario Engine — Phase 2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs 3 parallel trade scenarios simultaneously.
Each scenario has a hypothesis (BULL/BEAR/NEUTRAL) and evolving confidence.

Lifecycle per scenario:
  WATCHING  → confidence 50-74%
  ARMED     → confidence 75-84%  (alert sent)
  READY     → confidence ≥ 85% for 1 bar   (pre-entry gate)
  CONFIRMED → confidence ≥ 85% for 2 consecutive bars  (ENTRY eligible)
  ACTIVE    → position is open
  PROFIT    → target hit, position exited
  STOPPED   → SL hit, position exited
  ABORTED   → confidence fell below 55%
  DEAD      → confidence fell below 30% (skip this scenario for the session)
  EXPIRED   → time-based exit (close approach)

Confidence update rules per tick:
  Base: current confluence score mapped 0→100
  Trend alignment bonus: +5 if Ichimoku + EMA agree with hypothesis
  Volume confirmation: +3 if OBV rising (bull) / falling (bear)
  Pattern boost: +confidence × pattern_conf if candle pattern matches
  Contradiction penalty: −5 if Supertrend flips against hypothesis
  VIX penalty: −3 per point above 20 (max −15)
  Time decay: −1 per 15-min bar after 2:30 PM IST (urgency)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


class ScenarioState(str, Enum):
    WATCHING   = "WATCHING"
    ARMED      = "ARMED"
    READY      = "READY"
    CONFIRMED  = "CONFIRMED"
    ACTIVE     = "ACTIVE"
    PROFIT     = "PROFIT"
    STOPPED    = "STOPPED"
    ABORTED    = "ABORTED"
    DEAD       = "DEAD"
    EXPIRED    = "EXPIRED"


TERMINAL_STATES = {
    ScenarioState.PROFIT, ScenarioState.STOPPED,
    ScenarioState.ABORTED, ScenarioState.DEAD, ScenarioState.EXPIRED,
}


@dataclass
class ScenarioSignal:
    """Emitted when a scenario crosses a state threshold."""
    scenario_id:  int
    hypothesis:   str         # 'BULL' | 'BEAR'
    state:        ScenarioState
    confidence:   float
    entry_price:  Optional[float]
    sl_price:     Optional[float]
    target1:      Optional[float]
    target2:      Optional[float]
    target3:      Optional[float]
    instrument:   str
    timeframe:    str
    timestamp:    datetime
    reason:       str


@dataclass
class ScenarioStatus:
    scenario_id:  int
    hypothesis:   str
    state:        ScenarioState
    confidence:   float
    peak_conf:    float
    entry_price:  Optional[float]
    sl_price:     Optional[float]
    target1:      Optional[float]
    target2:      Optional[float]
    target3:      Optional[float]
    instrument:   str
    timeframe:    str
    created_at:   datetime
    updated_at:   datetime
    bars_at_state: int        # how long in current state
    bars_above_85: int        # consecutive bars ≥ 85% (triggers CONFIRMED)
    reason:       str = ""

    def eta_to_entry(self) -> Optional[str]:
        if self.state in (ScenarioState.ARMED, ScenarioState.READY):
            gap = 85.0 - self.confidence
            if gap <= 0:
                return "NOW"
            return f"~{max(1, int(gap / 2))} bars"
        return None

    def display_line(self) -> str:
        conf_bar = "█" * int(self.confidence / 10) + "░" * (10 - int(self.confidence / 10))
        eta = self.eta_to_entry()
        eta_str = f"  ETA={eta}" if eta else ""
        sl_str  = f"  SL={self.sl_price:.0f}" if self.sl_price else ""
        t1_str  = f"  T1={self.target1:.0f}" if self.target1 else ""
        return (f"  SCN{self.scenario_id} [{self.hypothesis:<4}] "
                f"{self.state.value:<10} {conf_bar} {self.confidence:.1f}%"
                f"{eta_str}{sl_str}{t1_str}  [{self.reason[:40]}]")


class Scenario:
    """
    Single scenario tracking one hypothesis across one timeframe.
    Receives a ConfluenceResult each bar and updates state + confidence.
    """

    # State thresholds
    ARMED_AT     = 75.0
    ENTRY_AT     = 85.0
    ABORT_BELOW  = 55.0
    DEAD_BELOW   = 30.0

    def __init__(
        self,
        scenario_id:   int,
        hypothesis:    str,    # 'BULL' | 'BEAR'
        instrument:    str,
        timeframe:     str,
        entry_price:   Optional[float] = None,
        atr_value:     Optional[float] = None,
        atr_multiplier_sl: float = 2.0,
        risk_reward:   tuple[float, float, float] = (1.5, 2.5, 4.0),
    ):
        self.id            = scenario_id
        self.hypothesis    = hypothesis
        self.instrument    = instrument
        self.timeframe     = timeframe
        self._entry_price  = entry_price
        self._atr          = atr_value
        self._sl_mult      = atr_multiplier_sl
        self._rr           = risk_reward

        self._confidence    = 50.0
        self._peak_conf     = 50.0
        self._state         = ScenarioState.WATCHING
        self._bars_at_state = 0
        self._bars_above_85 = 0

        self._sl:    Optional[float] = None
        self._t1:    Optional[float] = None
        self._t2:    Optional[float] = None
        self._t3:    Optional[float] = None

        self._created_at  = datetime.now(IST)
        self._updated_at  = self._created_at
        self._reason      = "initialised"

        self._last_signal: Optional[ScenarioSignal] = None

        if entry_price and atr_value:
            self._compute_levels(entry_price, atr_value)

    def _compute_levels(self, price: float, atr: float) -> None:
        sl_dist = atr * self._sl_mult
        if self.hypothesis == "BULL":
            self._sl = round(price - sl_dist, 2)
            self._t1 = round(price + atr * self._rr[0], 2)
            self._t2 = round(price + atr * self._rr[1], 2)
            self._t3 = round(price + atr * self._rr[2], 2)
        else:
            self._sl = round(price + sl_dist, 2)
            self._t1 = round(price - atr * self._rr[0], 2)
            self._t2 = round(price - atr * self._rr[1], 2)
            self._t3 = round(price - atr * self._rr[2], 2)

    def update_levels(self, price: float, atr: float) -> None:
        self._entry_price = price
        self._atr         = atr
        self._compute_levels(price, atr)

    def update(
        self,
        confluence_score:  float,     # −100 to +100
        bar_close:         float,
        atr_value:         Optional[float] = None,
        india_vix:         Optional[float] = None,
        pattern_signal:    Optional[str]   = None,   # 'BULLISH'|'BEARISH'|None
        pattern_conf:      float = 0.0,
        st_flipped:        bool  = False,
        st_direction:      Optional[str] = None,     # 'UP'|'DOWN'
        obv_rising:        Optional[bool] = None,
        ema_structure:     Optional[str]  = None,    # 'BULL'|'BEAR'|'MIXED'
        ichi_bias:         Optional[str]  = None,
        now:               Optional[datetime] = None,
    ) -> Optional[ScenarioSignal]:
        """
        Feed one bar's data. Returns a ScenarioSignal if state changes.
        Returns None for quiet updates.
        """
        if self._state in TERMINAL_STATES:
            return None

        if now is None:
            now = datetime.now(IST)

        if atr_value and not self._atr:
            self._atr = atr_value
        if atr_value and bar_close:
            self._entry_price = bar_close
            self._compute_levels(bar_close, atr_value)

        # ── Raw confidence from confluence score ──────────────────────────────
        # Map confluence −100→+100 to a directional alignment 0→100
        if self.hypothesis == "BULL":
            raw = 50 + confluence_score / 2        # +100 score → 100% conf
        else:
            raw = 50 - confluence_score / 2        # −100 score → 100% conf

        raw = max(0.0, min(100.0, raw))

        # ── Adjustments ───────────────────────────────────────────────────────
        adj = 0.0
        reason_parts = []

        # EMA + Ichimoku alignment bonus
        ema_aligned  = (ema_structure == "BULL" and self.hypothesis == "BULL" or
                        ema_structure == "BEAR" and self.hypothesis == "BEAR")
        ichi_aligned = (ichi_bias and self.hypothesis in ichi_bias)
        if ema_aligned and ichi_aligned:
            adj += 7
            reason_parts.append("EMA+ICH align")
        elif ema_aligned or ichi_aligned:
            adj += 3
            reason_parts.append("partial align")

        # OBV confirmation
        if obv_rising is not None:
            if (obv_rising and self.hypothesis == "BULL") or \
               (not obv_rising and self.hypothesis == "BEAR"):
                adj += 3
                reason_parts.append("OBV confirm")

        # Candlestick pattern boost
        if pattern_signal and pattern_conf > 0:
            pattern_aligned = (pattern_signal == "BULLISH" and self.hypothesis == "BULL" or
                                pattern_signal == "BEARISH" and self.hypothesis == "BEAR")
            if pattern_aligned:
                adj += pattern_conf * 8
                reason_parts.append(f"candle+{pattern_conf:.0%}")
            elif pattern_signal != "NEUTRAL":
                adj -= pattern_conf * 5
                reason_parts.append(f"candle conflict")

        # Supertrend flip penalty
        if st_flipped and st_direction:
            st_aligned = (st_direction == "UP" and self.hypothesis == "BULL" or
                          st_direction == "DOWN" and self.hypothesis == "BEAR")
            if not st_aligned:
                adj -= 12
                reason_parts.append(f"ST flip {st_direction}")
            else:
                adj += 5
                reason_parts.append(f"ST flip favor")

        # VIX penalty
        if india_vix and india_vix > 20:
            vix_pen = min(15, (india_vix - 20) * 3)
            adj -= vix_pen
            reason_parts.append(f"VIX={india_vix:.0f}")

        # Time decay after 2:30 PM
        if now.hour == 14 and now.minute >= 30:
            bars_late = (now.minute - 30) // 15
            adj -= bars_late
            if bars_late:
                reason_parts.append(f"time-{bars_late}")
        elif now.hour >= 15:
            adj -= 10
            reason_parts.append("late session")

        # Smooth toward target with momentum
        new_conf = raw + adj
        new_conf = max(0.0, min(100.0, new_conf))

        # Exponential smoothing: 70% new, 30% prior (responsive but not jittery)
        self._confidence = round(0.70 * new_conf + 0.30 * self._confidence, 2)
        self._peak_conf  = max(self._peak_conf, self._confidence)
        self._updated_at = now
        self._reason     = ", ".join(reason_parts) if reason_parts else "score update"

        # ── State transitions ─────────────────────────────────────────────────
        prev_state = self._state
        signal     = None

        if self._confidence >= 85:
            self._bars_above_85 += 1
        else:
            self._bars_above_85 = 0

        if self._confidence < self.DEAD_BELOW:
            self._state = ScenarioState.DEAD
            self._reason = f"confidence dead at {self._confidence:.1f}%"

        elif self._confidence < self.ABORT_BELOW and self._state not in (
            ScenarioState.WATCHING,
        ):
            self._state = ScenarioState.ABORTED
            self._reason = f"confidence aborted at {self._confidence:.1f}%"

        elif self._bars_above_85 >= 2 and self._state in (
            ScenarioState.ARMED, ScenarioState.READY, ScenarioState.WATCHING
        ):
            self._state = ScenarioState.CONFIRMED
            self._reason = f"2 bars ≥85% → CONFIRMED for ENTRY"

        elif self._confidence >= 85 and self._bars_above_85 == 1 and self._state in (
            ScenarioState.ARMED, ScenarioState.WATCHING
        ):
            self._state = ScenarioState.READY
            self._reason = f"1st bar ≥85% → READY, need 1 more"

        elif self._confidence >= self.ARMED_AT and self._state == ScenarioState.WATCHING:
            self._state = ScenarioState.ARMED
            self._reason = f"confidence reached {self._confidence:.1f}% → ARMED"

        elif self._confidence < self.ARMED_AT and self._state in (
            ScenarioState.READY, ScenarioState.ARMED
        ):
            self._state = ScenarioState.WATCHING
            self._bars_above_85 = 0
            self._reason = f"confidence fell to {self._confidence:.1f}% → back to WATCHING"

        if self._state != prev_state:
            self._bars_at_state = 0
            signal = ScenarioSignal(
                scenario_id  = self.id,
                hypothesis   = self.hypothesis,
                state        = self._state,
                confidence   = self._confidence,
                entry_price  = self._entry_price,
                sl_price     = self._sl,
                target1      = self._t1,
                target2      = self._t2,
                target3      = self._t3,
                instrument   = self.instrument,
                timeframe    = self.timeframe,
                timestamp    = now,
                reason       = self._reason,
            )
            self._last_signal = signal
        else:
            self._bars_at_state += 1

        return signal

    def mark_active(self, fill_price: float, atr: float) -> None:
        self._state       = ScenarioState.ACTIVE
        self._entry_price = fill_price
        self._compute_levels(fill_price, atr)
        self._bars_at_state = 0

    def mark_profit(self, exit_price: float) -> None:
        self._state = ScenarioState.PROFIT
        self._reason = f"target hit @ {exit_price:.2f}"

    def mark_stopped(self, exit_price: float) -> None:
        self._state = ScenarioState.STOPPED
        self._reason = f"SL hit @ {exit_price:.2f}"

    def mark_expired(self) -> None:
        self._state = ScenarioState.EXPIRED
        self._reason = "session close — time exit"

    @property
    def status(self) -> ScenarioStatus:
        return ScenarioStatus(
            scenario_id   = self.id,
            hypothesis    = self.hypothesis,
            state         = self._state,
            confidence    = self._confidence,
            peak_conf     = self._peak_conf,
            entry_price   = self._entry_price,
            sl_price      = self._sl,
            target1       = self._t1,
            target2       = self._t2,
            target3       = self._t3,
            instrument    = self.instrument,
            timeframe     = self.timeframe,
            created_at    = self._created_at,
            updated_at    = self._updated_at,
            bars_at_state = self._bars_at_state,
            bars_above_85 = self._bars_above_85,
            reason        = self._reason,
        )

    @property
    def state(self) -> ScenarioState:
        return self._state

    @property
    def confidence(self) -> float:
        return self._confidence

    @property
    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    @property
    def is_entry_ready(self) -> bool:
        return self._state == ScenarioState.CONFIRMED

    @property
    def sl(self) -> Optional[float]:
        return self._sl

    @property
    def target1(self) -> Optional[float]:
        return self._t1

    @property
    def target2(self) -> Optional[float]:
        return self._t2

    @property
    def target3(self) -> Optional[float]:
        return self._t3


class ScenarioEngine:
    """
    Manages 3 parallel scenarios simultaneously.
    On each bar: updates all living scenarios, recycles dead/terminal ones,
    and emits signals when state transitions occur.

    The engine always maintains:
      Scenario 1: BULL hypothesis on primary timeframe (5m)
      Scenario 2: BEAR hypothesis on primary timeframe (5m)
      Scenario 3: Best opportunity on confirmation timeframe (15m)

    When a scenario becomes CONFIRMED, it remains until explicitly closed
    (mark_profit / mark_stopped / mark_expired) so the trade engine can act.
    """

    MAX_SCENARIOS = 3

    def __init__(
        self,
        instrument:      str,
        primary_tf:      str = "5m",
        confirm_tf:      str = "15m",
        on_signal=None,   # callable(ScenarioSignal)
    ):
        self.instrument  = instrument
        self.primary_tf  = primary_tf
        self.confirm_tf  = confirm_tf
        self._on_signal  = on_signal
        self._scenarios: dict[int, Scenario] = {}
        self._next_id    = 1

        # Seed the 3 permanent hypothesis slots
        self._seed_scenarios()

    def _seed_scenarios(self) -> None:
        self._scenarios[1] = Scenario(1, "BULL", self.instrument, self.primary_tf)
        self._scenarios[2] = Scenario(2, "BEAR", self.instrument, self.primary_tf)
        self._scenarios[3] = Scenario(3, "BULL", self.instrument, self.confirm_tf)
        self._next_id = 4

    def update(
        self,
        confluence_score:   float,
        bar_close:          float,
        atr_value:          Optional[float] = None,
        india_vix:          Optional[float] = None,
        pattern_signal:     Optional[str]   = None,
        pattern_conf:       float = 0.0,
        st_flipped:         bool  = False,
        st_direction:       Optional[str]   = None,
        obv_rising:         Optional[bool]  = None,
        ema_structure:      Optional[str]   = None,
        ichi_bias:          Optional[str]   = None,
        now:                Optional[datetime] = None,
    ) -> list[ScenarioSignal]:
        """
        Feed one bar to all active scenarios.
        Returns list of state-change signals (may be empty).
        """
        signals: list[ScenarioSignal] = []

        for sid, scenario in list(self._scenarios.items()):
            if scenario.is_terminal:
                continue

            sig = scenario.update(
                confluence_score = confluence_score,
                bar_close        = bar_close,
                atr_value        = atr_value,
                india_vix        = india_vix,
                pattern_signal   = pattern_signal,
                pattern_conf     = pattern_conf,
                st_flipped       = st_flipped,
                st_direction     = st_direction,
                obv_rising       = obv_rising,
                ema_structure    = ema_structure,
                ichi_bias        = ichi_bias,
                now              = now,
            )
            if sig:
                signals.append(sig)
                if self._on_signal:
                    try:
                        self._on_signal(sig)
                    except Exception:
                        pass

        # Recycle terminal scenarios (except ACTIVE — trade may still be open)
        for sid, scenario in list(self._scenarios.items()):
            if scenario.state in (
                ScenarioState.DEAD, ScenarioState.ABORTED, ScenarioState.EXPIRED
            ):
                # Restart with fresh hypothesis
                hyp = scenario.hypothesis
                tf  = scenario.timeframe
                self._scenarios[sid] = Scenario(
                    sid, hyp, self.instrument, tf
                )

        return signals

    def confirmed_scenarios(self) -> list[Scenario]:
        """Return scenarios ready for trade entry."""
        return [s for s in self._scenarios.values()
                if s.state == ScenarioState.CONFIRMED]

    def active_scenarios(self) -> list[Scenario]:
        return [s for s in self._scenarios.values()
                if s.state == ScenarioState.ACTIVE]

    def all_statuses(self) -> list[ScenarioStatus]:
        return [s.status for s in self._scenarios.values()]

    def display_block(self) -> str:
        lines = ["  ┌─ SCENARIOS ────────────────────────────────────────────────────┐"]
        for s in self._scenarios.values():
            lines.append(s.status.display_line())
        lines.append("  └────────────────────────────────────────────────────────────────┘")
        return "\n".join(lines)

    def get_scenario(self, scenario_id: int) -> Optional[Scenario]:
        return self._scenarios.get(scenario_id)

    def mark_profit(self, scenario_id: int, exit_price: float) -> None:
        s = self._scenarios.get(scenario_id)
        if s:
            s.mark_profit(exit_price)

    def mark_stopped(self, scenario_id: int, exit_price: float) -> None:
        s = self._scenarios.get(scenario_id)
        if s:
            s.mark_stopped(exit_price)

    def expire_all(self) -> None:
        """Call at 3:20 PM to time-exit all open scenarios."""
        for s in self._scenarios.values():
            if s.state == ScenarioState.ACTIVE:
                s.mark_expired()
