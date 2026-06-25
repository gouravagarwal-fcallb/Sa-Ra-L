"""
Confluence Scorer — weighted voting across all 25 indicators.
Outputs a score −100 to +100.
+100 = all indicators screaming BUY; −100 = all screaming SELL.
Score ≥ +65 → BULLISH signal; ≤ −65 → BEARISH signal.

Weight allocation (total 100):
  Trend group (40):  EMA structure(10), Supertrend(10), ADX(8), Ichimoku(12)
  Momentum (30):     RSI(8), MACD(8), StochRSI(6), ROC(4), Aroon(4)
  Volatility (15):   BB position(7), ATR regime(4), VIX(4)
  Volume (10):       VWAP position(5), OBV trend(3), Volume Profile zone(2)
  Pattern (5):       Candlestick patterns (best signal confidence × 5)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.brahmastra.indicators.ema        import EMAStack
    from src.brahmastra.indicators.rsi        import RSI
    from src.brahmastra.indicators.macd       import MACD
    from src.brahmastra.indicators.bollinger  import BollingerBands
    from src.brahmastra.indicators.vwap       import VWAP
    from src.brahmastra.indicators.adx        import ADX
    from src.brahmastra.indicators.supertrend import Supertrend
    from src.brahmastra.indicators.stochastic_rsi import StochasticRSI
    from src.brahmastra.indicators.ichimoku   import Ichimoku
    from src.brahmastra.indicators.momentum   import OBV, ROC, Aroon
    from src.brahmastra.indicators.volume_profile import VolumeProfile
    from src.brahmastra.indicators.candlestick_patterns import CandlestickScanner
    from src.brahmastra.data.bar_builder import Bar


@dataclass
class ConfluenceVote:
    indicator: str
    direction: str    # 'BULL' | 'BEAR' | 'NEUTRAL'
    weight:    float
    reason:    str


@dataclass
class ConfluenceResult:
    score:      float              # −100 to +100
    direction:  str                # 'BULLISH' | 'BEARISH' | 'NEUTRAL'
    strength:   str                # 'STRONG' | 'MODERATE' | 'WEAK'
    votes:      list[ConfluenceVote] = field(default_factory=list)
    bull_score: float = 0.0        # raw positive component
    bear_score: float = 0.0        # raw negative component
    agreement:  float = 0.0        # 0–1: how unanimously aligned are the votes?

    def summary_line(self) -> str:
        arrow = "▲" if self.direction == "BULLISH" else ("▼" if self.direction == "BEARISH" else "─")
        return (f"{arrow} {self.direction} {self.strength} | "
                f"score={self.score:+.1f} | agree={self.agreement:.0%} | "
                f"bull={self.bull_score:.1f} bear={self.bear_score:.1f}")


class ConfluenceScorer:
    """
    Aggregates live indicator readings into a single directional score.

    Usage:
        scorer = ConfluenceScorer()
        scorer.set_ema(ema_stack)
        scorer.set_rsi(rsi)
        ...
        result = scorer.score(bar)
    """

    def __init__(self):
        self._ema:       Optional[object] = None
        self._rsi:       Optional[object] = None
        self._macd:      Optional[object] = None
        self._bb:        Optional[object] = None
        self._vwap:      Optional[object] = None
        self._adx:       Optional[object] = None
        self._st:        Optional[object] = None  # Supertrend
        self._stoch_rsi: Optional[object] = None
        self._ichimoku:  Optional[object] = None
        self._obv:       Optional[object] = None
        self._roc:       Optional[object] = None
        self._aroon:     Optional[object] = None
        self._vp:        Optional[object] = None  # VolumeProfile
        self._cs:        Optional[object] = None  # CandlestickScanner
        self._atr:       Optional[object] = None
        self._india_vix: Optional[float]  = None
        self._orb:       Optional[object] = None
        self._gap:       Optional[object] = None
        self._ha:        Optional[object] = None
        self._instrument: str = "NIFTY"

        self._last_result: Optional[ConfluenceResult] = None

    # ── Setters ──────────────────────────────────────────────────────────────

    def set_ema(self, ema): self._ema = ema
    def set_rsi(self, rsi): self._rsi = rsi
    def set_macd(self, macd): self._macd = macd
    def set_bollinger(self, bb): self._bb = bb
    def set_vwap(self, vwap): self._vwap = vwap
    def set_adx(self, adx): self._adx = adx
    def set_supertrend(self, st): self._st = st
    def set_stoch_rsi(self, sr): self._stoch_rsi = sr
    def set_ichimoku(self, ich): self._ichimoku = ich
    def set_obv(self, obv): self._obv = obv
    def set_roc(self, roc): self._roc = roc
    def set_aroon(self, aroon): self._aroon = aroon
    def set_volume_profile(self, vp): self._vp = vp
    def set_candlestick_scanner(self, cs): self._cs = cs
    def set_atr(self, atr): self._atr = atr
    def set_india_vix(self, vix: float): self._india_vix = vix
    def set_orb(self, orb_calc): self._orb = orb_calc
    def set_gap(self, gap_analyzer): self._gap = gap_analyzer
    def set_heikin_ashi(self, ha): self._ha = ha
    def set_instrument(self, instrument: str): self._instrument = instrument

    # ── Scoring ───────────────────────────────────────────────────────────────

    def score(self, bar) -> ConfluenceResult:
        votes: list[ConfluenceVote] = []

        def vote(indicator: str, direction: str, weight: float, reason: str):
            votes.append(ConfluenceVote(indicator, direction, weight, reason))

        # ── EMA structure (weight 10) ────────────────────────────────────────
        if self._ema and self._ema.is_ready(200):
            structure = self._ema.trend_structure()
            vals = self._ema.values()
            e9, e21 = vals.get(9), vals.get(21)
            price = bar.close
            if structure == "BULL":
                vote("EMA", "BULL", 10, f"EMA 9>21>50>200; price={price:.0f}")
            elif structure == "BEAR":
                vote("EMA", "BEAR", 10, f"EMA 9<21<50<200")
            elif e9 and e21:
                d = "BULL" if e9 > e21 else "BEAR"
                vote("EMA", d, 5, f"EMA mixed: 9={'above' if e9>e21 else 'below'} 21")

        # ── Supertrend (weight 10) ───────────────────────────────────────────
        if self._st and self._st.is_ready:
            st = self._st.value
            d  = "BULL" if st.direction == "UP" else "BEAR"
            w  = 12 if st.flipped else 10
            vote("Supertrend", d, w,
                 f"ST={st.direction} val={st.value:.0f} dist={st.distance:.0f}{'  FLIP' if st.flipped else ''}")

        # ── ADX (weight 8) ───────────────────────────────────────────────────
        if self._adx and self._adx.is_ready:
            adx = self._adx.value
            w   = 8 if adx.adx >= 25 else 4
            d   = "BULL" if adx.plus_di > adx.minus_di else "BEAR"
            if adx.adx < 20:
                d = "NEUTRAL"
            vote("ADX", d, w,
                 f"ADX={adx.adx:.1f} +DI={adx.plus_di:.1f} -DI={adx.minus_di:.1f} {adx.trend}")

        # ── Ichimoku (weight 12) ─────────────────────────────────────────────
        if self._ichimoku and self._ichimoku.is_ready:
            ich = self._ichimoku.value
            b   = ich.bias()
            w   = 14 if "STRONG" in b else 12
            d   = ("BULL" if "BULL" in b else
                   "BEAR" if "BEAR" in b else "NEUTRAL")
            vote("Ichimoku", d, w,
                 f"bias={b} cloud={'Bull' if ich.cloud_bullish else 'Bear'} "
                 f"price={ich.price_vs_cloud} strength={ich.strength}/6")

        # ── RSI (weight 8) ───────────────────────────────────────────────────
        if self._rsi and self._rsi.is_ready:
            rsi = self._rsi.value
            div = self._rsi.divergence()
            if div == "BULLISH":
                vote("RSI", "BULL", 10, f"RSI={rsi:.1f} BULLISH divergence")
            elif div == "BEARISH":
                vote("RSI", "BEAR", 10, f"RSI={rsi:.1f} BEARISH divergence")
            elif rsi < 30:
                vote("RSI", "BULL", 8, f"RSI={rsi:.1f} oversold")
            elif rsi > 70:
                vote("RSI", "BEAR", 8, f"RSI={rsi:.1f} overbought")
            elif rsi > 50:
                vote("RSI", "BULL", 4, f"RSI={rsi:.1f} above midline")
            elif rsi < 50:
                vote("RSI", "BEAR", 4, f"RSI={rsi:.1f} below midline")

        # ── MACD (weight 8) ──────────────────────────────────────────────────
        if self._macd and self._macd.is_ready:
            m = self._macd.value
            w = 10 if m.crossover else 8
            if m.crossover == "BULLISH":
                vote("MACD", "BULL", w, f"MACD BULLISH cross hist={m.histogram:+.3f}")
            elif m.crossover == "BEARISH":
                vote("MACD", "BEAR", w, f"MACD BEARISH cross hist={m.histogram:+.3f}")
            elif m.zero_cross == "UP":
                vote("MACD", "BULL", 8, f"MACD zero-cross UP hist={m.histogram:+.3f}")
            elif m.zero_cross == "DOWN":
                vote("MACD", "BEAR", 8, f"MACD zero-cross DOWN")
            elif m.histogram > 0:
                vote("MACD", "BULL", 4, f"MACD hist positive={m.histogram:+.3f}")
            else:
                vote("MACD", "BEAR", 4, f"MACD hist negative={m.histogram:+.3f}")

        # ── Stoch RSI (weight 6) ─────────────────────────────────────────────
        if self._stoch_rsi and self._stoch_rsi.is_ready:
            sr = self._stoch_rsi.value
            if sr.signal == "BULLISH_CROSS":
                vote("StochRSI", "BULL", 8, f"K={sr.k:.1f} D={sr.d:.1f} BULLISH cross")
            elif sr.signal == "BEARISH_CROSS":
                vote("StochRSI", "BEAR", 8, f"K={sr.k:.1f} D={sr.d:.1f} BEARISH cross")
            elif sr.k < 20:
                vote("StochRSI", "BULL", 6, f"StochRSI oversold K={sr.k:.1f}")
            elif sr.k > 80:
                vote("StochRSI", "BEAR", 6, f"StochRSI overbought K={sr.k:.1f}")

        # ── Aroon (weight 4) ─────────────────────────────────────────────────
        if self._aroon:
            ar = self._aroon.update(bar) if hasattr(self._aroon, 'update') else None
            if ar and ar.signal != "NEUTRAL":
                d = "BULL" if ar.signal == "BULL" else "BEAR"
                vote("Aroon", d, 4, f"Aroon up={ar.up:.0f} dn={ar.down:.0f} osc={ar.osc:+.0f}")

        # ── ROC (weight 4) ───────────────────────────────────────────────────
        if self._roc:
            roc_val = self._roc.update(bar) if hasattr(self._roc, 'update') else None
            if roc_val is not None:
                d = "BULL" if roc_val > 0.5 else ("BEAR" if roc_val < -0.5 else "NEUTRAL")
                if d != "NEUTRAL":
                    vote("ROC", d, 4, f"ROC={roc_val:+.2f}%")

        # ── Bollinger Bands (weight 7) ───────────────────────────────────────
        if self._bb and self._bb.is_ready:
            bb = self._bb.value
            if bb.breakout == "UP":
                vote("BB", "BULL", 9, f"BB breakout UP pct_b={bb.pct_b:.2f}")
            elif bb.breakout == "DOWN":
                vote("BB", "BEAR", 9, f"BB breakout DOWN pct_b={bb.pct_b:.2f}")
            elif bb.squeeze:
                vote("BB", "NEUTRAL", 3, f"BB squeeze width={bb.width:.4f}")
            elif bb.pct_b > 0.8:
                vote("BB", "BEAR", 4, f"BB upper zone pct_b={bb.pct_b:.2f}")
            elif bb.pct_b < 0.2:
                vote("BB", "BULL", 4, f"BB lower zone pct_b={bb.pct_b:.2f}")

        # ── VWAP (weight 5) ──────────────────────────────────────────────────
        if self._vwap and self._vwap.is_ready:
            vw = self._vwap.value
            if vw.position == "ABOVE":
                vote("VWAP", "BULL", 5, f"price above VWAP={vw.vwap:.0f}")
            elif vw.position == "BELOW":
                vote("VWAP", "BEAR", 5, f"price below VWAP={vw.vwap:.0f}")

        # ── OBV trend (weight 3) ─────────────────────────────────────────────
        if self._obv:
            obv_val = self._obv.value
            # Rising OBV with price up = confirmation; needs 2-period diff check
            # Use simple sign of OBV as proxy
            if obv_val > 0:
                vote("OBV", "BULL", 3, f"OBV positive={obv_val:,.0f}")
            elif obv_val < 0:
                vote("OBV", "BEAR", 3, f"OBV negative={obv_val:,.0f}")

        # ── Volume Profile (weight 2) ────────────────────────────────────────
        if self._vp and self._vp.is_ready:
            vp = self._vp.value
            zone = vp.zone(bar.close)
            if zone in ("POC", "HVN"):
                vote("VP", "NEUTRAL", 2, f"price at {zone}={vp.poc:.0f}")
            elif zone == "LVN":
                # Price in LVN = fast move zone, direction follows trend
                vote("VP", "NEUTRAL", 1, f"price at LVN={bar.close:.0f}")

        # ── India VIX (weight 4) ─────────────────────────────────────────────
        if self._india_vix is not None:
            vix = self._india_vix
            if vix > 20:
                vote("VIX", "BEAR", 6, f"VIX={vix:.1f} elevated — caution")
            elif vix < 14:
                vote("VIX", "BULL", 4, f"VIX={vix:.1f} low — complacency/bull")
            else:
                vote("VIX", "NEUTRAL", 2, f"VIX={vix:.1f} normal range")

        # ── Candlestick patterns (weight 5) ──────────────────────────────────
        if self._cs:
            strongest = self._cs.strongest()
            if strongest and strongest.direction != "NEUTRAL":
                d  = "BULL" if strongest.direction == "BULLISH" else "BEAR"
                w  = round(strongest.confidence * 7, 1)
                vote("Candle", d, w, f"{strongest.name} conf={strongest.confidence:.0%}")

        # ── ORB signal (weight 8) ─────────────────────────────────────────────
        if self._orb is not None:
            orb_result = getattr(self._orb, "_current_result", None)
            if orb_result is None and hasattr(self._orb, "value"):
                orb_result = self._orb.value
            if orb_result is not None and hasattr(orb_result, "status"):
                if orb_result.status == "BULL_BREAK" and orb_result.break_confirmed:
                    vote("ORB", "BULL", 8,
                         f"ORB BULL_BREAK confirmed bars_above={orb_result.bars_above_orb_high}")
                elif orb_result.status == "BEAR_BREAK" and orb_result.break_confirmed:
                    vote("ORB", "BEAR", 8,
                         f"ORB BEAR_BREAK confirmed bars_below={orb_result.bars_below_orb_low}")
                elif orb_result.status == "BULL_BREAK":
                    vote("ORB", "BULL", 4, "ORB above high — awaiting confirmation")
                elif orb_result.status == "BEAR_BREAK":
                    vote("ORB", "BEAR", 4, "ORB below low — awaiting confirmation")

        # ── Gap signal (weight 6) ─────────────────────────────────────────────
        if self._gap is not None:
            gap_result = getattr(self._gap, "_last_result", None)
            if gap_result is None and hasattr(self._gap, "value"):
                gap_result = self._gap.value
            if gap_result is not None and hasattr(gap_result, "gap_type"):
                gt = gap_result.gap_type
                if gt == "GAP_AND_GO_UP":
                    vote("Gap", "BULL", 6,
                         f"GAP_AND_GO_UP gap={gap_result.gap_pct:+.2f}%")
                elif gt == "GAP_AND_GO_DOWN":
                    vote("Gap", "BEAR", 6,
                         f"GAP_AND_GO_DOWN gap={gap_result.gap_pct:+.2f}%")
                elif gt == "GAP_FILL":
                    d = "BEAR" if gap_result.direction == "BULL" else "BULL"
                    vote("Gap", d, 4,
                         f"GAP_FILL mean-reversion gap_dir={gap_result.direction}")
                elif gt == "GAP_UP_LARGE":
                    vote("Gap", "BULL", 4,
                         f"GAP_UP_LARGE gap={gap_result.gap_pct:+.2f}%")
                elif gt == "GAP_DOWN_LARGE":
                    vote("Gap", "BEAR", 4,
                         f"GAP_DOWN_LARGE gap={gap_result.gap_pct:+.2f}%")

        # ── Round numbers (weight 4) ──────────────────────────────────────────
        if bar.close > 0:
            from src.brahmastra.indicators.price_action import get_round_levels
            rl = get_round_levels(bar.close, self._instrument)
            if rl.at_round_level and rl.round_level is not None:
                # Use previous bar direction to determine approach angle
                if rl.round_level >= bar.close:
                    vote("RoundLevel", "BEAR", 4,
                         f"at round level {rl.round_level:.0f} — resistance")
                else:
                    vote("RoundLevel", "BULL", 4,
                         f"at round level {rl.round_level:.0f} — support")

        # ── Heikin Ashi (weight 5) ────────────────────────────────────────────
        if self._ha is not None and self._ha.is_ready:
            ha = self._ha.value
            if ha is not None:
                if ha.is_doji:
                    vote("HA", "NEUTRAL", 2, "HA doji — indecision")
                elif ha.direction == "BULL" and ha.no_lower_wick:
                    vote("HA", "BULL", 6,
                         f"HA strong bull no_lower_wick open={ha.ha_open:.0f} close={ha.ha_close:.0f}")
                elif ha.direction == "BEAR" and ha.no_upper_wick:
                    vote("HA", "BEAR", 6,
                         f"HA strong bear no_upper_wick open={ha.ha_open:.0f} close={ha.ha_close:.0f}")
                elif ha.direction == "BULL":
                    vote("HA", "BULL", 5,
                         f"HA bull open={ha.ha_open:.0f} close={ha.ha_close:.0f}")
                else:
                    vote("HA", "BEAR", 5,
                         f"HA bear open={ha.ha_open:.0f} close={ha.ha_close:.0f}")

        # ── Compute final score ───────────────────────────────────────────────
        bull_score = sum(v.weight for v in votes if v.direction == "BULL")
        bear_score = sum(v.weight for v in votes if v.direction == "BEAR")
        total_w    = sum(v.weight for v in votes) or 1
        score      = (bull_score - bear_score) / total_w * 100

        bull_count = sum(1 for v in votes if v.direction == "BULL")
        bear_count = sum(1 for v in votes if v.direction == "BEAR")
        n_votes    = len(votes) or 1
        majority   = max(bull_count, bear_count)
        agreement  = majority / n_votes

        if score >= 65:
            direction, strength = "BULLISH", ("STRONG" if score >= 80 else "MODERATE")
        elif score <= -65:
            direction, strength = "BEARISH", ("STRONG" if score <= -80 else "MODERATE")
        else:
            direction, strength = "NEUTRAL", "WEAK"

        self._last_result = ConfluenceResult(
            score      = round(score, 1),
            direction  = direction,
            strength   = strength,
            votes      = votes,
            bull_score = round(bull_score, 1),
            bear_score = round(bear_score, 1),
            agreement  = round(agreement, 3),
        )
        return self._last_result

    @property
    def last_result(self) -> Optional[ConfluenceResult]:
        return self._last_result
