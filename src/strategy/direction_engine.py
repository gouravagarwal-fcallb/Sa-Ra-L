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
from typing import Optional, List

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
        if vix <= calm:   # ≤ so VIX=13.0 earns +1 (calm market bonus)
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

    def _score_spot_intraday(self, change_pct: float) -> int:
        """Score intraday spot % change from previous close. Same bands as Dow Jones."""
        thresholds = self.cfg.get("intraday", {}).get(
            "spot_change", self.cfg.get("dow_jones", {})
        )
        s_up = thresholds.get("strong_up",   1.0)
        m_up = thresholds.get("mild_up",     0.3)
        m_dn = thresholds.get("mild_down",  -0.3)
        s_dn = thresholds.get("strong_down", -1.0)
        if change_pct >= s_up:  return  2
        if change_pct >= m_up:  return  1
        if change_pct >  m_dn:  return  0
        if change_pct >= s_dn:  return -1
        return -2

    def _score_momentum(self, momentum_pct: float) -> int:
        """Score intraday momentum: spot % change from today's open."""
        mom  = self.cfg.get("intraday", {}).get("momentum", {})
        bull = mom.get("bullish_above",  0.2)
        bear = mom.get("bearish_below", -0.2)
        if momentum_pct > bull: return  1
        if momentum_pct < bear: return -1
        return 0

    def evaluate_intraday(
        self,
        spot: float,
        spot_prev_close: float,
        spot_day_open: float,
        vix: float,
    ) -> DirectionResult:
        """
        Re-evaluate direction using live intraday signals at every 5-min candle.
        Called within every trading slot — can flip direction vs pre-market assessment.

        Score components:
          Spot % chg from prev close  : -2 to +2  (same thresholds as Dow Jones)
          Intraday momentum vs open   : -1 to +1  (is the intraday move continuing?)
          India VIX level             : -2 to +1  (same as pre-market)
          ──────────────────────────────────────────────────────────────────────
          Total                       : -5 to +4
          Default thresholds: bullish ≥ 2, bearish ≤ -2
          (lower bar than pre-market since this is confirmed live price action)
        """
        spot_chg_pct = (spot - spot_prev_close) / spot_prev_close * 100 if spot_prev_close else 0.0
        momentum_pct = (spot - spot_day_open)   / spot_day_open   * 100 if spot_day_open   else 0.0

        spot_score = self._score_spot_intraday(spot_chg_pct)
        mom_score  = self._score_momentum(momentum_pct)
        vix_score  = self._score_vix(vix)
        total      = spot_score + mom_score + vix_score

        intra_cfg   = self.cfg.get("intraday", {})
        bullish_min = intra_cfg.get("score_thresholds", {}).get("bullish_min",  2)
        bearish_max = intra_cfg.get("score_thresholds", {}).get("bearish_max", -2)

        if total >= bullish_min:
            direction = Direction.BULLISH
            reason    = f"Intraday score {total} ≥ {bullish_min} → BUY CALL"
        elif total <= bearish_max:
            direction = Direction.BEARISH
            reason    = f"Intraday score {total} ≤ {bearish_max} → BUY PUT"
        else:
            direction = Direction.NEUTRAL
            reason    = f"Intraday score {total} neutral [{bearish_max+1},{bullish_min-1}] → SKIP"

        breakdown = {
            "spot_chg_pct":    round(spot_chg_pct, 3),
            "spot_score":      spot_score,
            "momentum_pct":    round(momentum_pct, 3),
            "momentum_score":  mom_score,
            "vix":             vix,
            "vix_score":       vix_score,
            "total":           total,
        }

        return DirectionResult(
            direction=direction,
            score=total,
            breakdown=breakdown,
            reason=reason,
        )

    def evaluate_1min(
        self,
        bars_1m: list,
        bars_5m: list,
        vix: float,
        day_open: float,
        prev_close: float,
        vwap: float,
    ) -> DirectionResult:
        """
        1-min entry trigger using three-layer confluence:
          Layer 1 — 5-min structure : EMA9/21 aligned, spot side of VWAP, RSI > 50
          Layer 2 — 1-min trigger   : EMA5/13 aligned, RSI 55-78, volume surge
          Layer 3 — Candle quality  : body ≥ 40%, consecutive closes, micro-breakout

        Requires ≥ 15 1-min bars and ≥ 3 5-min bars (warmup period).
        Returns NEUTRAL during warmup or when any layer fails.
        """
        from src.data.candle_builder import ema as _ema, rsi as _rsi

        # ── Warmup guard ─────────────────────────────────────────────────────
        if len(bars_1m) < 15 or len(bars_5m) < 3:
            return DirectionResult(
                direction=Direction.NEUTRAL, score=0,
                reason=f"Warming up ({len(bars_1m)} 1-min, {len(bars_5m)} 5-min bars)"
            )

        c  = bars_1m[-1]   # current 1-min candle
        p  = bars_1m[-2]   # prior
        pp = bars_1m[-3]   # two bars ago

        closes_1m  = [b.close for b in bars_1m]
        closes_5m  = [b.close for b in bars_5m]
        volumes_1m = [b.volume for b in bars_1m]

        # ── Layer 1: 5-min structure ──────────────────────────────────────────
        ema9_5m  = _ema(closes_5m, 9)
        ema21_5m = _ema(closes_5m, 21)
        rsi14_5m = _rsi(closes_5m, 14)
        spot_5m  = bars_5m[-1].close

        # ── Layer 2: 1-min trigger ────────────────────────────────────────────
        ema5_1m  = _ema(closes_1m, 5)
        ema13_1m = _ema(closes_1m, 13)
        rsi7_1m  = _rsi(closes_1m, 7)
        avg_vol  = sum(volumes_1m[-20:]) / max(len(volumes_1m[-20:]), 1)

        # ── VIX gate ──────────────────────────────────────────────────────────
        vix_score = self._score_vix(vix)
        if vix_score <= -2:
            return DirectionResult(
                direction=Direction.NEUTRAL, score=0, reason=f"VIX panic ({vix:.1f})"
            )

        vol_ok  = c.volume >= 1.3 * avg_vol if avg_vol > 0 else True
        body_ok = c.body_ratio >= 0.40

        # ── BULLISH: all three layers must pass ───────────────────────────────
        if (
            # Layer 1
            ema9_5m > ema21_5m and
            rsi14_5m > 50 and
            spot_5m > vwap and
            # Layer 2
            ema5_1m > ema13_1m and
            55 <= rsi7_1m <= 78 and
            vol_ok and
            # Layer 3
            c.close > p.high and        # micro-breakout above prior high
            c.is_bullish and
            p.is_bullish and            # two consecutive bullish bars
            body_ok and
            c.close > vwap
        ):
            score = 3 + vix_score
            return DirectionResult(
                direction=Direction.BULLISH, score=score,
                reason=(
                    f"1-min BULLISH | EMA5={ema5_1m:.0f}>EMA13={ema13_1m:.0f} "
                    f"RSI={rsi7_1m:.0f} Vol={c.volume/max(avg_vol,1):.1f}× "
                    f"VWAP={vwap:.0f} Body={c.body_ratio:.0%}"
                ),
                breakdown={
                    "ema9_5m": round(ema9_5m, 1), "ema21_5m": round(ema21_5m, 1),
                    "rsi14_5m": round(rsi14_5m, 1),
                    "ema5_1m": round(ema5_1m, 1), "ema13_1m": round(ema13_1m, 1),
                    "rsi7_1m": round(rsi7_1m, 1),
                    "vwap": round(vwap, 1), "vix_score": vix_score,
                },
            )

        # ── BEARISH: mirror conditions ────────────────────────────────────────
        if (
            ema9_5m < ema21_5m and
            rsi14_5m < 50 and
            spot_5m < vwap and
            ema5_1m < ema13_1m and
            22 <= rsi7_1m <= 45 and
            vol_ok and
            c.close < p.low and
            c.is_bearish and
            p.is_bearish and
            body_ok and
            c.close < vwap
        ):
            score = -(3 + abs(vix_score))
            return DirectionResult(
                direction=Direction.BEARISH, score=score,
                reason=(
                    f"1-min BEARISH | EMA5={ema5_1m:.0f}<EMA13={ema13_1m:.0f} "
                    f"RSI={rsi7_1m:.0f} Vol={c.volume/max(avg_vol,1):.1f}× "
                    f"VWAP={vwap:.0f} Body={c.body_ratio:.0%}"
                ),
                breakdown={
                    "ema9_5m": round(ema9_5m, 1), "ema21_5m": round(ema21_5m, 1),
                    "rsi14_5m": round(rsi14_5m, 1),
                    "ema5_1m": round(ema5_1m, 1), "ema13_1m": round(ema13_1m, 1),
                    "rsi7_1m": round(rsi7_1m, 1),
                    "vwap": round(vwap, 1), "vix_score": vix_score,
                },
            )

        return DirectionResult(
            direction=Direction.NEUTRAL, score=0,
            reason=(
                f"No 1-min signal | 5m EMA {'↑' if ema9_5m>ema21_5m else '↓'} "
                f"RSI5m={rsi14_5m:.0f} RSI1m={rsi7_1m:.0f} "
                f"Spot {'>' if c.close>vwap else '<'} VWAP={vwap:.0f}"
            ),
        )

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
