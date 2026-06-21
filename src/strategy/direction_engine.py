"""
Pre-Market Direction Engine
────────────────────────────
Scores 5 signals → BULLISH / BEARISH / NEUTRAL

Scoring grid:
  Dow Jones change %  : -2 to +2
  Gift Nifty premium  : -2 to +2
  India VIX level     : -2 to +1
  Sensex direction    : -1 to +1
  ─────────────────────────────
  Total               : -7 to +6

  Score ≥ +3 → BULLISH  (buy CALL)
  Score ≤ -3 → BEARISH  (buy PUT)
  -2 to +2   → NEUTRAL  (skip)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.utils.logger import setup_logger

log = setup_logger("direction_engine")


class Direction(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class IntradayGreeks:
    """Live option Greeks monitored continuously during the session."""
    delta: float = 0.0      # Price sensitivity to spot move (target: ≥ 0.40 ATM)
    gamma: float = 0.0      # Rate of change of delta
    theta: float = 0.0      # Time decay per day (negative for option buyers)
    vega: float = 0.0       # Sensitivity per 1% IV change
    beta: float = 0.0       # Underlying's correlation to broader market


@dataclass
class IntradaySignals:
    """
    Continuous intraday signals checked at every 5-min bar.
    These complement the pre-market direction score and can
    override or confirm hold/exit decisions.
    """
    spot: float = 0.0                # Current Nifty/Sensex spot
    iv_percentile: float = 0.0       # Current IV vs 52-week range (0–100%)
    oi_change: float = 0.0           # Absolute OI change from prev day
    oi_change_pct: float = 0.0       # % OI change from prev day
    greeks: IntradayGreeks = None    # Live Greeks of the position

    def __post_init__(self):
        if self.greeks is None:
            self.greeks = IntradayGreeks()


@dataclass
class DirectionInputs:
    dow_change_pct: float = 0.0      # % change in Dow Jones (prev night)
    gift_nifty_premium: float = 0.0  # Gift Nifty − Nifty prev close (points)
    india_vix: float = 15.0          # India VIX current level
    sensex_change_pct: float = 0.0   # Sensex % change vs its prev close
    nifty_prev_close: float = 0.0    # For context / logging
    intraday: IntradaySignals = None  # Optional live signals (None in pre-market eval)

    def __post_init__(self):
        if self.intraday is None:
            self.intraday = IntradaySignals()


@dataclass
class DirectionResult:
    direction: Direction = Direction.NEUTRAL
    score: int = 0
    breakdown: dict = field(default_factory=dict)
    reason: str = ""
    inputs: DirectionInputs = field(default_factory=DirectionInputs)


class DirectionEngine:
    def __init__(self, config: dict):
        self.cfg = config.get("direction", {})
        self.bullish_min = self.cfg.get("score_thresholds", {}).get("bullish_min", 3)
        self.bearish_max = self.cfg.get("score_thresholds", {}).get("bearish_max", -3)

    def _score_dow(self, change_pct: float) -> int:
        thresholds = self.cfg.get("dow_jones", {})
        s_up = thresholds.get("strong_up", 1.0)
        m_up = thresholds.get("mild_up", 0.3)
        m_dn = thresholds.get("mild_down", -0.3)
        s_dn = thresholds.get("strong_down", -1.0)

        if change_pct >= s_up:
            return 2
        if change_pct >= m_up:
            return 1
        if change_pct > m_dn:
            return 0
        if change_pct >= s_dn:
            return -1
        return -2

    def _score_gift_nifty(self, premium: float) -> int:
        thresholds = self.cfg.get("gift_nifty", {})
        s_pr = thresholds.get("strong_premium", 100)
        m_pr = thresholds.get("mild_premium", 30)
        m_dc = thresholds.get("mild_discount", -30)
        s_dc = thresholds.get("strong_discount", -100)

        if premium >= s_pr:
            return 2
        if premium >= m_pr:
            return 1
        if premium > m_dc:
            return 0
        if premium >= s_dc:
            return -1
        return -2

    def _score_vix(self, vix: float) -> int:
        thresholds = self.cfg.get("india_vix", {})
        calm = thresholds.get("calm_below", 13.0)
        elevated = thresholds.get("elevated_above", 18.0)
        panic = thresholds.get("panic_above", 22.0)

        if vix >= panic:
            return -2
        if vix >= elevated:
            return -1
        if vix < calm:
            return 1
        return 0

    def _score_sensex(self, change_pct: float) -> int:
        thresholds = self.cfg.get("sensex", {})
        up = thresholds.get("up_threshold", 0.3)
        dn = thresholds.get("down_threshold", -0.3)

        if change_pct >= up:
            return 1
        if change_pct <= dn:
            return -1
        return 0

    def evaluate(self, inputs: DirectionInputs) -> DirectionResult:
        dow_score = self._score_dow(inputs.dow_change_pct)
        gift_score = self._score_gift_nifty(inputs.gift_nifty_premium)
        vix_score = self._score_vix(inputs.india_vix)
        sensex_score = self._score_sensex(inputs.sensex_change_pct)

        total = dow_score + gift_score + vix_score + sensex_score

        breakdown = {
            "dow_jones": {"change_pct": round(inputs.dow_change_pct, 2), "score": dow_score},
            "gift_nifty": {"premium_pts": round(inputs.gift_nifty_premium, 1), "score": gift_score},
            "india_vix": {"level": inputs.india_vix, "score": vix_score},
            "sensex": {"change_pct": round(inputs.sensex_change_pct, 2), "score": sensex_score},
            "total": total,
        }

        if total >= self.bullish_min:
            direction = Direction.BULLISH
            reason = f"Score {total} ≥ {self.bullish_min} → BUY CALL"
        elif total <= self.bearish_max:
            direction = Direction.BEARISH
            reason = f"Score {total} ≤ {self.bearish_max} → BUY PUT"
        else:
            direction = Direction.NEUTRAL
            reason = f"Score {total} in neutral band [{self.bearish_max+1}, {self.bullish_min-1}] → SKIP"

        result = DirectionResult(
            direction=direction,
            score=total,
            breakdown=breakdown,
            reason=reason,
            inputs=inputs,
        )

        log.info(
            f"Direction: {direction.value} | Score: {total} | "
            f"DOW={dow_score} GIFT={gift_score} VIX={vix_score} SENSEX={sensex_score}"
        )
        return result

    def evaluate_from_live_data(self) -> DirectionResult:
        """Fetch all signals from live data sources and evaluate."""
        from src.data.market_data import (
            get_dow_jones_change_pct,
            get_india_vix,
            get_spot_price,
            get_previous_close,
        )
        from src.data.gift_nifty import get_gift_nifty_premium

        dow_chg = get_dow_jones_change_pct()
        gift_prem = get_gift_nifty_premium()
        vix = get_india_vix()
        sensex_now = get_spot_price("SENSEX")
        sensex_prev = get_previous_close("SENSEX")
        sensex_chg = ((sensex_now - sensex_prev) / sensex_prev * 100) if sensex_prev else 0.0
        nifty_prev = get_previous_close("NIFTY")

        inputs = DirectionInputs(
            dow_change_pct=dow_chg,
            gift_nifty_premium=gift_prem,
            india_vix=vix,
            sensex_change_pct=sensex_chg,
            nifty_prev_close=nifty_prev,
        )
        return self.evaluate(inputs)
