"""
INRUSD Strategy Engine
━━━━━━━━━━━━━━━━━━━━━━
Core signal logic for INRUSD_v1 — Currency Futures Trend Scalper.

Operates on 15-min OHLCV bars (primary) and 1-hour bars (trend filter).
Uses EMA crossover + RSI + MACD momentum + pre-session bias to generate
LONG / SHORT / NO_TRADE signals.

Design philosophy:
  - Only trade when multiple factors agree (score ≥ signal_gate)
  - Pre-session bias acts as a session-level direction gate
  - ATR gate ensures minimum volatility (skip quiet/holiday sessions)
  - One trade per window; no pyramiding
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


# ── Lightweight per-bar indicators (no external dependencies) ────────────────

class _EMA:
    def __init__(self, period: int):
        self.period = period
        self._k     = 2.0 / (period + 1)
        self._val:  Optional[float] = None
        self._buf:  list[float] = []

    def update(self, price: float) -> Optional[float]:
        if self._val is None:
            self._buf.append(price)
            if len(self._buf) >= self.period:
                self._val = sum(self._buf) / len(self._buf)
                self._buf = []
        else:
            self._val = price * self._k + self._val * (1 - self._k)
        return self._val

    @property
    def value(self) -> Optional[float]:
        return self._val


class _RSI:
    def __init__(self, period: int = 14):
        self.period  = period
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None
        self._prev:  Optional[float] = None

    def update(self, price: float) -> Optional[float]:
        if self._prev is None:
            self._prev = price
            return None
        change = price - self._prev
        self._prev = price
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if self._avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) >= self.period:
                self._avg_gain = sum(self._gains) / self.period
                self._avg_loss = sum(self._losses) / self.period
                self._gains = []
                self._losses = []
        else:
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
        if self._avg_gain is None or self._avg_loss is None:
            return None
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100 - 100 / (1 + rs)

    @property
    def value(self) -> Optional[float]:
        if self._avg_gain is None:
            return None
        if self._avg_loss == 0:
            return 100.0
        rs = self._avg_gain / (self._avg_loss or 1e-9)
        return 100 - 100 / (1 + rs)


class _ATR:
    def __init__(self, period: int = 14):
        self.period = period
        self._prev_close: Optional[float] = None
        self._buf: list[float] = []
        self._atr: Optional[float] = None

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        if self._prev_close is None:
            self._prev_close = close
            return None
        tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        if self._atr is None:
            self._buf.append(tr)
            if len(self._buf) >= self.period:
                self._atr = sum(self._buf) / self.period
                self._buf = []
        else:
            self._atr = (self._atr * (self.period - 1) + tr) / self.period
        return self._atr

    @property
    def value(self) -> Optional[float]:
        return self._atr


class _MACD:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self._ema_fast   = _EMA(fast)
        self._ema_slow   = _EMA(slow)
        self._ema_signal = _EMA(signal)
        self._hist:  Optional[float] = None

    def update(self, price: float) -> tuple[Optional[float], Optional[float], Optional[float]]:
        f = self._ema_fast.update(price)
        s = self._ema_slow.update(price)
        if f is None or s is None:
            return None, None, None
        macd_line = f - s
        sig  = self._ema_signal.update(macd_line)
        if sig is None:
            return macd_line, None, None
        hist = macd_line - sig
        self._hist = hist
        return macd_line, sig, hist

    @property
    def histogram(self) -> Optional[float]:
        return self._hist


# ── Signal dataclass ─────────────────────────────────────────────────────────

@dataclass
class TradeSignal:
    direction:    str           # LONG | SHORT | NO_TRADE
    score:        int           # number of conditions met (0-4)
    entry_price:  Optional[float]
    target_price: Optional[float]
    stop_price:   Optional[float]
    atr:          Optional[float]
    lots:         int
    budget_rs:    float
    reasons:      list[str] = field(default_factory=list)
    window_id:    str = ""


# ── Main engine ───────────────────────────────────────────────────────────────

class INRUSDEngine:
    """
    Per-session engine instance.  One instance per trading session.
    Feed 15-min bars as they close; call evaluate() to get a signal.
    """

    def __init__(self, config: dict, bias_direction: str, bias_score: int):
        self._cfg           = config
        self._bias_dir      = bias_direction   # LONG | SHORT | NEUTRAL
        self._bias_score    = bias_score

        entry_cfg           = config.get("entry", {})
        ind_cfg             = entry_cfg.get("indicators", {})

        self._ema_fast   = _EMA(ind_cfg.get("ema_fast",   9))
        self._ema_slow   = _EMA(ind_cfg.get("ema_slow",  21))
        self._ema_trend  = _EMA(ind_cfg.get("ema_trend", 50))
        self._rsi        = _RSI(ind_cfg.get("rsi_period", 14))
        self._atr        = _ATR(ind_cfg.get("atr_period", 14))
        self._macd       = _MACD(
            ind_cfg.get("macd_fast",   12),
            ind_cfg.get("macd_slow",   26),
            ind_cfg.get("macd_signal",  9),
        )

        self._signal_gate = entry_cfg.get("signal_score_gate", 3)

        exit_cfg = config.get("exit", {})
        self._tgt_mult    = exit_cfg.get("target_atr_mult", 1.5)
        self._sl_mult     = exit_cfg.get("stop_atr_mult",   1.0)
        self._min_atr_p   = config.get("risk", {}).get("min_atr_paise", 3.0)
        self._max_atr_p   = config.get("risk", {}).get("max_atr_paise", 40.0)

        ps  = config.get("position_sizing", {})
        self._budget      = ps.get("budget_per_trade", 15000)
        self._max_lots    = ps.get("max_lots", 5)
        self._margin_lot  = ps.get("margin_per_lot", 3000)
        self._lot_size    = config.get("instrument", {}).get("lot_size", 1000)

        # Previous MACD histogram for crossover detection
        self._prev_hist:  Optional[float] = None

        # Previous EMA values for crossover detection
        self._prev_ema_fast: Optional[float] = None
        self._prev_ema_slow: Optional[float] = None

        # Bars fed since reset
        self._bar_count = 0

    def feed_bar(self, open_: float, high: float, low: float, close: float) -> None:
        """Feed one completed 15-min bar to update all indicators."""
        self._prev_ema_fast = self._ema_fast.value
        self._prev_ema_slow = self._ema_slow.value
        self._prev_hist     = self._macd.histogram

        self._ema_fast.update(close)
        self._ema_slow.update(close)
        self._ema_trend.update(close)
        self._rsi.update(close)
        self._atr.update(high, low, close)
        self._macd.update(close)
        self._bar_count += 1

    def evaluate(self, current_price: float, window_id: str = "") -> TradeSignal:
        """
        Evaluate current bar for a trade signal.
        Returns TradeSignal with direction=NO_TRADE if no entry.
        """
        no_trade = TradeSignal(
            direction="NO_TRADE", score=0,
            entry_price=None, target_price=None, stop_price=None,
            atr=None, lots=0, budget_rs=0,
            window_id=window_id,
        )

        # Need enough bars for indicators to warm up
        if self._bar_count < 30:
            no_trade.reasons.append(f"warming up ({self._bar_count}/30 bars)")
            return no_trade

        ema_f = self._ema_fast.value
        ema_s = self._ema_slow.value
        ema_t = self._ema_trend.value
        rsi   = self._rsi.value
        atr   = self._atr.value
        hist  = self._macd.histogram

        if any(v is None for v in [ema_f, ema_s, ema_t, rsi, atr]):
            no_trade.reasons.append("indicators not ready")
            return no_trade

        # ATR gate (convert to paise: USDINR ~85, 1 paise = 0.01 INR)
        atr_paise = atr * 100  # ATR in INR → paise
        if atr_paise < self._min_atr_p:
            no_trade.reasons.append(f"ATR too quiet ({atr_paise:.1f} paise < {self._min_atr_p})")
            return no_trade
        if atr_paise > self._max_atr_p:
            no_trade.reasons.append(f"ATR too volatile ({atr_paise:.1f} paise > {self._max_atr_p})")
            return no_trade

        # Bias gate
        if self._bias_dir == "NEUTRAL":
            no_trade.reasons.append("neutral pre-session bias, no directional edge")
            return no_trade

        # Score conditions
        reasons: list[str] = []
        score = 0

        if self._bias_dir == "LONG":
            # Condition 1: EMA fast > slow (bullish cross or sustained above)
            c1 = ema_f > ema_s
            if c1:
                score += 1
                reasons.append("EMA9>EMA21 (bullish)")
            # Condition 2: Price above 50-EMA trend filter
            c2 = current_price > ema_t
            if c2:
                score += 1
                reasons.append("price>EMA50 (above trend)")
            # Condition 3: RSI > 50
            c3 = rsi > 50
            if c3:
                score += 1
                reasons.append(f"RSI={rsi:.1f}>50")
            # Condition 4: MACD histogram positive
            c4 = hist is not None and hist > 0
            if c4:
                score += 1
                reasons.append(f"MACD hist={hist:.5f}>0")

            if score < self._signal_gate:
                no_trade.reasons = reasons + [f"score {score}/{self._signal_gate} gates"]
                return no_trade

            # Build LONG signal
            sl     = current_price - self._sl_mult * atr
            target = current_price + self._tgt_mult * atr
            lots   = min(self._max_lots, max(1, int(self._budget / self._margin_lot)))
            return TradeSignal(
                direction="LONG", score=score,
                entry_price=round(current_price, 4),
                target_price=round(target, 4),
                stop_price=round(sl, 4),
                atr=round(atr, 4),
                lots=lots, budget_rs=lots * self._margin_lot,
                reasons=reasons,
                window_id=window_id,
            )

        else:  # SHORT (expect USDINR to fall)
            c1 = ema_f < ema_s
            if c1:
                score += 1
                reasons.append("EMA9<EMA21 (bearish)")
            c2 = current_price < ema_t
            if c2:
                score += 1
                reasons.append("price<EMA50 (below trend)")
            c3 = rsi < 50
            if c3:
                score += 1
                reasons.append(f"RSI={rsi:.1f}<50")
            c4 = hist is not None and hist < 0
            if c4:
                score += 1
                reasons.append(f"MACD hist={hist:.5f}<0")

            if score < self._signal_gate:
                no_trade.reasons = reasons + [f"score {score}/{self._signal_gate} gates"]
                return no_trade

            sl     = current_price + self._sl_mult * atr
            target = current_price - self._tgt_mult * atr
            lots   = min(self._max_lots, max(1, int(self._budget / self._margin_lot)))
            return TradeSignal(
                direction="SHORT", score=score,
                entry_price=round(current_price, 4),
                target_price=round(target, 4),
                stop_price=round(sl, 4),
                atr=round(atr, 4),
                lots=lots, budget_rs=lots * self._margin_lot,
                reasons=reasons,
                window_id=window_id,
            )
