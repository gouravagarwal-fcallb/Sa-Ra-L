"""
BRAHMASTRA Market Narrator
━━━━━━━━━━━━━━━━━━━━━━━━━━
Called after every 5m bar. Generates:
  • Score trajectory  (rising / falling / stable)
  • Alert tier        (QUIET / WATCH / ALERT / SIGNAL)
  • 30-min forward view  (estimated time to entry, key levels, warnings)
  • Plain-language headline + multi-line detail block

Example output:
  [10:40] NIFTY BULL setup BUILDING | score=68↑+4 | entry ~10m if trend holds
  ────────────────────────────────────────────────
  SCORE: 68 ↑ RISING (+4 over last 3 bars)  |  threshold=70  |  ~2 bars to trigger
  TREND: EMA=BULL | ST=BULL | ADX=32(trending)
  MOMENTUM: RSI=64(mid) | MACD hist=+1.80(expanding)
  VWAP: price 210pts ABOVE (21,850)
  ────────────────────────────────────────────────
  FORWARD VIEW (next 30 min):
    If BULL trend holds, entry signal likely around 10:50.
    Expected entry zone: 22,060±40  SL≈21,980  T1≈22,220
  ────────────────────────────────────────────────
  RISK: VIX=15.2(normal)  |  3h 40m until EOD
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class NarratorInput:
    timestamp:      datetime
    instrument:     str
    price:          float
    score:          float          # signed: +ve=bull, -ve=bear
    threshold:      float          # entry threshold (e.g. 70.0)
    atr:            Optional[float]
    vwap:           Optional[float]
    vwap_position:  Optional[str]  # "ABOVE" / "BELOW"
    rsi:            Optional[float]
    macd_hist:      Optional[float]
    adx:            Optional[float]
    supertrend_dir: Optional[str]
    ema_structure:  Optional[str]
    india_vix:      Optional[float]


@dataclass
class NarratorUpdate:
    timestamp:     str
    instrument:    str
    score:         float
    score_dir:     str            # "RISING" / "FALLING" / "STABLE"
    score_delta:   float          # change over last 3 bars
    bars_to_entry: Optional[int]  # estimated bars until entry (None = can't tell)
    alert_tier:    str            # "QUIET" / "WATCH" / "ALERT" / "SIGNAL"
    headline:      str            # single-line summary for log / dashboard
    detail:        str            # multi-line full narrative for Telegram / terminal


_WATCH_OFFSET = 10.0   # score within 10pts of threshold → WATCH tier
_ALERT_OFFSET  = 5.0   # score within 5pts of threshold AND rising → ALERT tier


class MarketNarrator:
    """
    Stateful per-instrument narrator.  One instance per instrument.

    Call update() after every 5m bar that has a valid confluence score.
    The returned NarratorUpdate contains headline + full detail text.
    """

    def __init__(self, entry_threshold: float = 70.0, score_window: int = 5):
        self._threshold  = entry_threshold
        self._history: deque[float] = deque(maxlen=score_window)
        self._prev_macd_hist: Optional[float] = None

    # ── Public ───────────────────────────────────────────────────────────────

    def update(self, inp: NarratorInput) -> NarratorUpdate:
        abs_score = abs(inp.score)
        self._history.append(abs_score)

        direction, delta = self._trajectory()
        tier             = self._tier(abs_score, direction)
        bars_to_entry    = self._bars_to_entry(abs_score, direction, delta)

        headline = self._headline(inp, abs_score, direction, delta, bars_to_entry)
        detail   = self._detail(inp, abs_score, direction, delta, bars_to_entry, tier)

        self._prev_macd_hist = inp.macd_hist

        return NarratorUpdate(
            timestamp     = inp.timestamp.strftime("%H:%M"),
            instrument    = inp.instrument,
            score         = inp.score,
            score_dir     = direction,
            score_delta   = round(delta, 1),
            bars_to_entry = bars_to_entry,
            alert_tier    = tier,
            headline      = headline,
            detail        = detail,
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    def _trajectory(self) -> tuple[str, float]:
        h = list(self._history)
        if len(h) < 2:
            return "STABLE", 0.0
        recent = h[-3:] if len(h) >= 3 else h
        delta  = recent[-1] - recent[0]
        if delta > 2.0:
            return "RISING", delta
        if delta < -2.0:
            return "FALLING", delta
        return "STABLE", delta

    def _tier(self, abs_score: float, direction: str) -> str:
        if abs_score >= self._threshold:
            return "SIGNAL"
        gap = self._threshold - abs_score
        if gap <= _ALERT_OFFSET and direction == "RISING":
            return "ALERT"
        if gap <= _WATCH_OFFSET:
            return "WATCH" if direction != "FALLING" else "QUIET"
        return "QUIET"

    def _bars_to_entry(
        self, abs_score: float, direction: str, delta: float
    ) -> Optional[int]:
        if abs_score >= self._threshold:
            return 0
        if direction != "RISING" or delta <= 0:
            return None
        rate = delta / max(len(list(self._history)[-3:]) - 1, 1)
        if rate <= 0:
            return None
        bars = math.ceil((self._threshold - abs_score) / rate)
        return min(bars, 12)  # cap at 12 bars (1 hour)

    # ── Text builders ─────────────────────────────────────────────────────────

    def _headline(
        self, inp: NarratorInput, abs_score: float,
        direction: str, delta: float, bars_to_entry: Optional[int]
    ) -> str:
        hyp = "BULL" if inp.score >= 0 else "BEAR"
        sym = {"RISING": "↑", "FALLING": "↓", "STABLE": "→"}[direction]
        delta_str = f"{delta:+.0f}" if abs(delta) >= 1 else ""

        if bars_to_entry == 0:
            return (f"{inp.instrument} {hyp} SIGNAL ACTIVE  "
                    f"score={abs_score:.0f}{sym}  entry imminent")
        if bars_to_entry and bars_to_entry <= 3:
            return (f"{inp.instrument} {hyp} setup BUILDING  "
                    f"score={abs_score:.0f}{sym}{delta_str}  "
                    f"entry ~{bars_to_entry*5}m away")
        if bars_to_entry:
            return (f"{inp.instrument} {hyp} building  "
                    f"score={abs_score:.0f}{sym}  "
                    f"entry ~{bars_to_entry*5}m if trend holds")
        if direction == "FALLING":
            return (f"{inp.instrument} setup fading  "
                    f"score={abs_score:.0f}{sym}{delta_str}  no trade forming")
        return (f"{inp.instrument} score={abs_score:.0f}{sym}  "
                f"watching for setup ({self._threshold:.0f} needed)")

    def _detail(
        self, inp: NarratorInput, abs_score: float,
        direction: str, delta: float, bars_to_entry: Optional[int],
        tier: str,
    ) -> str:
        hyp     = "BULL" if inp.score >= 0 else "BEAR"
        now_str = inp.timestamp.strftime("%H:%M")
        bar     = "━" * 56

        # Minutes remaining until EOD (3:20 PM)
        eod  = inp.timestamp.replace(hour=15, minute=20, second=0, microsecond=0)
        mins = max(0, int((eod - inp.timestamp).total_seconds() / 60))
        time_str = f"{mins//60}h{mins%60:02d}m until EOD"

        lines = [
            f"[{now_str}] {inp.instrument}  price={inp.price:.0f}  {hyp} hypothesis",
            bar,
        ]

        # ── Score block ─────────────────────────────────────────────────────
        sym  = {"RISING": "↑ RISING", "FALLING": "↓ FALLING", "STABLE": "→ STABLE"}[direction]
        sc   = f"  SCORE: {abs_score:.0f} {sym}"
        if abs(delta) >= 1:
            sc += f" ({delta:+.0f} over last 3 bars)"
        sc += f"  |  threshold={self._threshold:.0f}"
        if bars_to_entry == 0:
            sc += "  *** SIGNAL ACTIVE ***"
        elif bars_to_entry:
            sc += f"  |  ~{bars_to_entry} bar{'s' if bars_to_entry!=1 else ''} to trigger"
        lines.append(sc)

        # ── Indicator read ──────────────────────────────────────────────────
        trend_parts = []
        if inp.ema_structure:
            trend_parts.append(f"EMA={inp.ema_structure}")
        if inp.supertrend_dir:
            trend_parts.append(f"ST={inp.supertrend_dir}")
        if inp.adx is not None:
            adx_label = ("strong" if inp.adx > 35 else
                         "trending" if inp.adx > 25 else "ranging")
            trend_parts.append(f"ADX={inp.adx:.0f}({adx_label})")
        if trend_parts:
            lines.append(f"  TREND: {' | '.join(trend_parts)}")

        mom_parts = []
        if inp.rsi is not None:
            rsi_label = "OB" if inp.rsi > 70 else "OS" if inp.rsi < 30 else "mid"
            mom_parts.append(f"RSI={inp.rsi:.0f}({rsi_label})")
        if inp.macd_hist is not None:
            if self._prev_macd_hist is not None:
                expanding = abs(inp.macd_hist) > abs(self._prev_macd_hist)
                mstr = "expanding" if expanding else "contracting"
            else:
                mstr = "n/a"
            mom_parts.append(f"MACD hist={inp.macd_hist:+.2f}({mstr})")
        if mom_parts:
            lines.append(f"  MOMENTUM: {' | '.join(mom_parts)}")

        if inp.vwap is not None and inp.vwap_position:
            dist = abs(inp.price - inp.vwap)
            lines.append(f"  VWAP: price {dist:.0f}pts {inp.vwap_position} ({inp.vwap:.0f})")

        # ── Forward view ────────────────────────────────────────────────────
        lines.append(f"  {'─'*54}")
        lines.append("  FORWARD VIEW (next 30 min):")
        for fline in self._forward(inp, hyp, abs_score, direction, delta, bars_to_entry):
            lines.append(f"    {fline}")

        # ── Risk footer ─────────────────────────────────────────────────────
        lines.append(f"  {'─'*54}")
        vix_str  = f"VIX={inp.india_vix:.1f}" if inp.india_vix else "VIX=n/a"
        vix_warn = ""
        if inp.india_vix:
            if inp.india_vix > 25:
                vix_warn = " ⚠ HIGH — widen SL or skip"
            elif inp.india_vix > 18:
                vix_warn = " (elevated)"
        lines.append(f"  RISK: {vix_str}{vix_warn}  |  {time_str}")

        return "\n".join(lines)

    def _forward(
        self, inp: NarratorInput, hyp: str, abs_score: float,
        direction: str, delta: float, bars_to_entry: Optional[int],
    ) -> list[str]:
        atr   = inp.atr or 50.0
        price = inp.price
        lines: list[str] = []

        if bars_to_entry == 0:
            sl = price - atr * 2 if hyp == "BULL" else price + atr * 2
            t1 = price + atr * 4 if hyp == "BULL" else price - atr * 4
            lines.append("Signal is ACTIVE — entry imminent.")
            lines.append(f"Expected SL≈{sl:.0f}  T1≈{t1:.0f}  (2× / 4× ATR)")

        elif bars_to_entry and bars_to_entry <= 6:
            eta = inp.timestamp + timedelta(minutes=bars_to_entry * 5)
            sl  = price - atr * 2 if hyp == "BULL" else price + atr * 2
            t1  = price + atr * 4 if hyp == "BULL" else price - atr * 4
            lines.append(
                f"If {hyp} trend holds, entry likely around {eta.strftime('%H:%M')}."
            )
            lines.append(
                f"Expected entry zone: {price:.0f}±{atr*0.5:.0f}  SL≈{sl:.0f}  T1≈{t1:.0f}"
            )
            # Warnings
            if inp.rsi and inp.rsi > 68 and hyp == "BULL":
                lines.append("⚠ RSI approaching OB (70) — watch for divergence before entry")
            if (inp.macd_hist is not None and self._prev_macd_hist is not None
                    and abs(inp.macd_hist) < abs(self._prev_macd_hist)
                    and abs(inp.macd_hist) < 0.5):
                lines.append("⚠ MACD hist contracting — momentum slowing")
            if inp.vwap and abs(price - inp.vwap) < atr * 0.4:
                lines.append("⚠ Price near VWAP — watch for rejection vs continuation")

        elif direction == "RISING":
            lines.append(
                f"Setup building slowly (score {abs_score:.0f}, rising "
                f"{delta:+.0f} pts last 3 bars)."
            )
            lines.append(
                f"Need {self._threshold - abs_score:.0f} more points to trigger — "
                f"may take {int((self._threshold - abs_score) / max(delta/3, 0.5))} bars."
            )

        elif direction == "FALLING":
            lines.append(
                f"Setup deteriorating (score falling). No trade expected now."
            )
            lines.append("Wait for score to stabilize and turn rising again.")

        else:
            lines.append(
                f"Market consolidating — score flat at {abs_score:.0f}."
            )
            lines.append(
                f"No directional bias. Wait for score to break above {self._threshold:.0f}."
            )

        return lines
