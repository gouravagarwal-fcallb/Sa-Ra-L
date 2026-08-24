"""
INRUSD Pre-Session Currency Bias Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Computes a BIAS SCORE (-100 to +100) before 09:15 IST.

Positive score → expect USDINR to RISE (USD strengthening vs INR)
                  → strategy will favour LONG USDINR trades
Negative score → expect USDINR to FALL (INR strengthening vs USD)
                  → strategy will favour SHORT USDINR trades

Scores are weighted contributions from:
  DXY overnight change (35%)
  WTI Crude overnight change (20%)   ← India is big crude importer; crude up = INR weaker
  US 10-year yield change in bps (20%)
  EUR/USD change (15%)               ← inverse: EUR/USD up → USD down → USDINR down
  India VIX level (10%)              ← high VIX = FII outflow = INR weaker
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from src.inrusd.data_fetcher import PreSessionSnapshot


@dataclass
class BiasResult:
    date:           date
    score:          int            # -100 to +100
    label:          str            # STRONGLY_LONG | LONG | NEUTRAL | SHORT | STRONGLY_SHORT
    direction:      str            # LONG | SHORT | NEUTRAL
    breakdown:      dict[str, int] = field(default_factory=dict)  # factor → contribution
    skip_trading:   bool = False
    reason:         str = ""

    def format_message(self) -> str:
        sign  = "+" if self.score >= 0 else ""
        lines = [
            f"INRUSD Pre-Session Bias | {self.date.strftime('%d-%b-%Y')}",
            f"SCORE: {sign}{self.score} ({self.label})",
            f"Direction: {self.direction}",
            "",
            "Factor Breakdown:",
        ]
        for k, v in self.breakdown.items():
            sign2 = "+" if v >= 0 else ""
            lines.append(f"  {k:<18}: {sign2}{v:+d}")
        if self.skip_trading:
            lines.append(f"\n  *** SKIP TRADING: {self.reason} ***")
        return "\n".join(lines)


class CurrencyBiasEngine:
    """
    Scores the pre-session currency bias from global data captured
    in a PreSessionSnapshot.
    """

    def __init__(self, config: dict):
        pm = config.get("premarket_bias", {})
        self._cfg = pm
        thresholds = pm.get("bias_thresholds", {})
        self._th_strong_bull = thresholds.get("strongly_bullish", 50)
        self._th_bull        = thresholds.get("bullish", 20)
        self._th_bear        = thresholds.get("bearish", -50)
        self._skip_neutral   = pm.get("skip_if_neutral", True)

    def compute(self, snap: PreSessionSnapshot) -> BiasResult:
        breakdown: dict[str, int] = {}
        total = 0

        # ── DXY (weight 35) ─────────────────────────────────────────────────
        dxy_score = self._score_pct(
            snap.dxy_change_pct,
            strong_up=0.5, mild_up=0.15, mild_down=-0.15, strong_down=-0.5,
            strong_weight=35, mild_weight=18,
        )
        breakdown["DXY"] = dxy_score
        total += dxy_score

        # ── Crude Oil (weight 20) — same direction as USDINR ────────────────
        crude_score = self._score_pct(
            snap.crude_change_pct,
            strong_up=1.5, mild_up=0.5, mild_down=-0.5, strong_down=-1.5,
            strong_weight=20, mild_weight=10,
        )
        breakdown["Crude Oil"] = crude_score
        total += crude_score

        # ── US 10Y Yield (weight 20) — in basis points ───────────────────────
        us10y_score = self._score_bps(
            snap.us_10y_change_bps,
            strong_up=5.0, mild_up=1.5, mild_down=-1.5, strong_down=-5.0,
            strong_weight=20, mild_weight=10,
        )
        breakdown["US 10Y Yield"] = us10y_score
        total += us10y_score

        # ── EUR/USD (weight 15) — inverse direction ──────────────────────────
        eurusd_score = self._score_pct(
            snap.eurusd_change_pct,
            strong_up=0.5, mild_up=0.15, mild_down=-0.15, strong_down=-0.5,
            strong_weight=15, mild_weight=8,
            inverse=True,  # EUR/USD up → USD down → USDINR down
        )
        breakdown["EUR/USD"] = eurusd_score
        total += eurusd_score

        # ── India VIX (weight 10) — elevated VIX = FII outflow = INR weaker ─
        vix_score = self._score_vix(snap.india_vix)
        breakdown["India VIX"] = vix_score
        total += vix_score

        # Clamp to -100..+100
        total = max(-100, min(100, total))

        label, direction = self._classify(total)
        skip = self._skip_neutral and direction == "NEUTRAL"
        reason = "bias score in neutral zone (-20 to +20), no clear directional edge" if skip else ""

        return BiasResult(
            date=snap.date,
            score=total,
            label=label,
            direction=direction,
            breakdown=breakdown,
            skip_trading=skip,
            reason=reason,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _score_pct(
        self,
        change_pct: Optional[float],
        strong_up: float, mild_up: float, mild_down: float, strong_down: float,
        strong_weight: int, mild_weight: int,
        inverse: bool = False,
    ) -> int:
        if change_pct is None:
            return 0
        v = -change_pct if inverse else change_pct
        if v >= strong_up:
            return strong_weight
        if v >= mild_up:
            return mild_weight
        if v <= strong_down:
            return -strong_weight
        if v <= mild_down:
            return -mild_weight
        return 0

    def _score_bps(
        self,
        change_bps: Optional[float],
        strong_up: float, mild_up: float, mild_down: float, strong_down: float,
        strong_weight: int, mild_weight: int,
    ) -> int:
        if change_bps is None:
            return 0
        if change_bps >= strong_up:
            return strong_weight
        if change_bps >= mild_up:
            return mild_weight
        if change_bps <= strong_down:
            return -strong_weight
        if change_bps <= mild_down:
            return -mild_weight
        return 0

    def _score_vix(self, vix: Optional[float]) -> int:
        if vix is None:
            return 0
        if vix >= 25.0:   # panic — strong FII outflow expected
            return 10
        if vix >= 18.0:   # elevated
            return 5
        if vix <= 12.0:   # calm — FII inflow friendly
            return -5
        return 0

    def _classify(self, score: int) -> tuple[str, str]:
        if score >= self._th_strong_bull:
            return "STRONGLY_LONG", "LONG"
        if score >= self._th_bull:
            return "LONG", "LONG"
        if score <= self._th_bear:
            return "STRONGLY_SHORT", "SHORT"
        if score <= -self._th_bull:
            return "SHORT", "SHORT"
        return "NEUTRAL", "NEUTRAL"
