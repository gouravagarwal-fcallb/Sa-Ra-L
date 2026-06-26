"""
BRAHMASTRA Scout Watchman — Phase 6d Real-Time 3-State Machine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs alongside the existing indicator pipeline on every 5m bar.

States
------
  SCOUT  — default; monitors all indicators, classifies day type,
           writes analysis. Zero trades on RANGE/CHOPPY days.
  ALERT  — activated at 10:00 AM when ALL 3 gates pass:
             (a) ADX >= adx_min          (trending, not sideways)
             (b) 10am range >= adr_threshold × proxy daily ATR
             (c) morning VWAP crossings < max_vwap_crosses (not choppy)
  TRADE  — momentum score >= threshold found in valid window.
           Manages position with ATR trailing stop + reversal gate.
  DONE   — traded for the day; back to watching, no more entries.

Config keys (under strategy.scout_mode in settings.yaml)
---------------------------------------------------------
  enabled            : true / false
  adx_min            : 22.0
  adr_threshold      : 0.35
  max_vwap_crosses   : 3
  momentum_threshold : 65.0
  reversal_threshold : 5
  atr_trail_mult     : 2.0
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, date, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class ScoutBarResult:
    """Returned by ScoutWatchman.on_bar() — consumed by dashboard + logs."""
    state:           str   = "SCOUT"   # SCOUT / ALERT / TRADE / DONE
    momentum_score:  float = 0.0
    direction:       Optional[str] = None
    trail_stop:      Optional[float] = None
    entry_price:     Optional[float] = None
    exit_price:      Optional[float] = None
    exit_reason:     Optional[str]  = None
    pnl:             Optional[float] = None
    vwap_crossings:  int   = 0
    alert_reason:    str   = ""
    scout_reason:    str   = ""


class ScoutWatchman:
    """
    Real-time Phase 6d scout mode for one instrument.
    One instance per instrument in _InstrumentState.
    """

    def __init__(self, instrument: str, lot_size: int, config: dict, notifier=None):
        self.instrument = instrument
        self.lot_size   = lot_size
        self._notifier  = notifier

        scout_cfg = config.get("scout_mode", {})
        self.enabled            = scout_cfg.get("enabled",            True)
        self.adx_min            = scout_cfg.get("adx_min",            22.0)
        self.adr_threshold      = scout_cfg.get("adr_threshold",      0.35)
        self.max_vwap_crosses   = scout_cfg.get("max_vwap_crosses",   3)
        self.momentum_threshold = scout_cfg.get("momentum_threshold", 65.0)
        self.reversal_threshold = scout_cfg.get("reversal_threshold", 5)
        self.atr_trail_mult     = scout_cfg.get("atr_trail_mult",     2.0)

        self._MIN_HOLD = 3   # bars before reversal check

        # Day-level state
        self._state:          str             = "SCOUT"
        self._session_date:   Optional[date]  = None
        self._bars_into_day:  int             = 0
        self._vwap_crossings: int             = 0
        self._prev_above_vwap: Optional[bool] = None
        self._day_high:       Optional[float] = None
        self._day_low:        Optional[float]  = None

        # Trade state
        self._hypothesis:   Optional[str]   = None
        self._entry_price:  Optional[float] = None
        self._entry_time:   str             = ""
        self._atr_at_entry: float           = 0.0
        self._trail_stop:   Optional[float] = None
        self._best_price:   Optional[float] = None
        self._hold_bars:    int             = 0

        # Rolling indicator buffers (FIFO, last 10 values)
        self._hist_buf: list[float] = []
        self._obv_buf:  list[float] = []
        self._adx_buf:  list[float] = []
        self._rsi_buf:  list[float] = []

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_in_trade(self) -> bool:
        return self._state == "TRADE"

    def on_bar(
        self,
        bar_close:  float,
        bar_high:   float,
        bar_low:    float,
        bar_dt:     datetime,
        adx:        Optional[float],
        vwap:       Optional[float],
        ema9:       Optional[float],
        ema21:      Optional[float],
        rsi:        Optional[float],
        macd_hist:  Optional[float],
        atr:        Optional[float],
        obv:        Optional[float],
        roc:        Optional[float],
    ) -> ScoutBarResult:
        """
        Call once per completed 5m bar with current indicator values.
        Returns ScoutBarResult with current state and any trade signals.
        """
        if not self.enabled:
            return ScoutBarResult()

        bar_date = bar_dt.date()
        result   = ScoutBarResult(
            state          = self._state,
            vwap_crossings = self._vwap_crossings,
        )

        # ── New session day — reset all per-day state ─────────────────────────
        if bar_date != self._session_date:
            self._reset_day(bar_date)

        self._bars_into_day += 1

        # ── Update day range ──────────────────────────────────────────────────
        if self._day_high is None or bar_high > self._day_high:
            self._day_high = bar_high
        if self._day_low is None or bar_low < self._day_low:
            self._day_low = bar_low

        # ── Track VWAP crossings (chop indicator) ─────────────────────────────
        if _ok(vwap) and _ok(bar_close):
            above = bar_close > vwap
            if self._prev_above_vwap is not None and above != self._prev_above_vwap:
                self._vwap_crossings += 1
            self._prev_above_vwap = above
        result.vwap_crossings = self._vwap_crossings

        # ── Buffer indicators ─────────────────────────────────────────────────
        for buf, val in [
            (self._hist_buf, macd_hist),
            (self._obv_buf,  obv),
            (self._adx_buf,  adx),
            (self._rsi_buf,  rsi),
        ]:
            if _ok(val):
                buf.append(val)
                if len(buf) > 10:
                    buf.pop(0)

        # ── DONE: already traded today ────────────────────────────────────────
        if self._state == "DONE":
            return result

        # ── SCOUT: 10:00 AM classification (9th bar from day start = 10:00) ──
        if self._state == "SCOUT" and self._bars_into_day == 9:
            result = self._classify_day_at_10am(bar_dt, adx, atr, result)

        # ── ALERT: search for momentum entry in valid windows ────────────────
        if self._state == "ALERT":
            result = self._check_entry(bar_close, bar_high, bar_low, bar_dt,
                                       adx, vwap, ema9, ema21, rsi, atr, obv, roc, result)

        # ── TRADE: manage open position ───────────────────────────────────────
        elif self._state == "TRADE":
            result = self._manage_trade(bar_close, bar_high, bar_low, bar_dt,
                                        adx, vwap, ema9, ema21, rsi, result)

        result.state = self._state
        return result

    def force_eod_close(self, price: float, dt: datetime) -> Optional[float]:
        """Call at 3:20 PM. Closes open trade at market; returns P&L or None."""
        if self._state != "TRADE":
            return None
        pnl = self._compute_pnl(price)
        self._send_trade_exit(price, "EOD", pnl, dt)
        self._state = "DONE"
        return pnl

    # ── Private: day classification ───────────────────────────────────────────

    def _classify_day_at_10am(
        self, bar_dt, adx, atr, result: ScoutBarResult
    ) -> ScoutBarResult:
        """Evaluate 3 scout gates at 10:00 AM and transition SCOUT → ALERT."""
        if not _ok(atr) or atr <= 0 or self._day_high is None:
            result.scout_reason = "SCOUT: ATR/range unavailable at 10am"
            return result

        # Proxy daily ATR: 5m ATR × √75 (75 bars in a session)
        daily_atr_proxy = atr * math.sqrt(75)
        range_10am      = self._day_high - self._day_low
        rpct            = range_10am / daily_atr_proxy if daily_atr_proxy > 0 else 0.0

        ok_adx   = _ok(adx) and adx   >= self.adx_min
        ok_range = rpct                >= self.adr_threshold
        ok_chop  = self._vwap_crossings < self.max_vwap_crosses

        if ok_adx and ok_range and ok_chop:
            self._state      = "ALERT"
            result.state     = "ALERT"
            result.alert_reason = (
                f"ADX={adx:.1f}  range={rpct:.0%}  vwap_x={self._vwap_crossings}"
            )
            self._send_scout_alert(adx, rpct, self._vwap_crossings, bar_dt)
        else:
            reasons = []
            if not ok_adx:   reasons.append(f"ADX={adx:.1f}<{self.adx_min}")
            if not ok_range: reasons.append(f"range={rpct:.0%}<{self.adr_threshold:.0%}")
            if not ok_chop:  reasons.append(f"vwap_x={self._vwap_crossings}>={self.max_vwap_crosses}")
            result.scout_reason = "SCOUT: " + " | ".join(reasons)

        return result

    # ── Private: entry check ──────────────────────────────────────────────────

    def _check_entry(
        self, close, high, low, bar_dt,
        adx, vwap, ema9, ema21, rsi, atr, obv, roc, result: ScoutBarResult
    ) -> ScoutBarResult:
        h, m = bar_dt.hour, bar_dt.minute
        in_morning   = (h == 9 and m >= 20) or h == 10 or (h == 11 and m <= 25)
        in_afternoon = (h == 13 and m >= 20) or (h == 14 and m <= 10)

        if not (in_morning or in_afternoon):
            return result
        if not _ok(atr) or atr <= 0:
            return result

        score, direction = self._momentum_score(
            close, adx, vwap, ema9, ema21, rsi, obv, roc
        )
        result.momentum_score = score
        result.direction      = direction

        if abs(score) >= self.momentum_threshold and direction:
            self._state         = "TRADE"
            self._hypothesis    = direction
            self._entry_price   = close
            self._atr_at_entry  = atr
            self._trail_stop    = (
                close - atr * self.atr_trail_mult
                if direction == "BULL"
                else close + atr * self.atr_trail_mult
            )
            self._best_price    = close
            self._hold_bars     = 0
            self._entry_time    = bar_dt.strftime("%H:%M")
            result.entry_price  = close
            result.trail_stop   = self._trail_stop
            self._send_trade_entry(close, direction, self._trail_stop, score, bar_dt)

        return result

    # ── Private: trade management ─────────────────────────────────────────────

    def _manage_trade(
        self, close, high, low, bar_dt,
        adx, vwap, ema9, ema21, rsi, result: ScoutBarResult
    ) -> ScoutBarResult:
        self._hold_bars += 1
        atr = self._atr_at_entry

        if self._hypothesis == "BULL":
            new_trail        = high - atr * self.atr_trail_mult
            self._trail_stop = max(self._trail_stop, new_trail)
            if high > self._best_price:
                self._best_price = high
            if low <= self._trail_stop:
                return self._exit_trade(self._trail_stop, "TRAIL_STOP", bar_dt, result)
        else:
            new_trail        = low + atr * self.atr_trail_mult
            self._trail_stop = min(self._trail_stop, new_trail)
            if low < self._best_price:
                self._best_price = low
            if high >= self._trail_stop:
                return self._exit_trade(self._trail_stop, "TRAIL_STOP", bar_dt, result)

        if self._hold_bars >= self._MIN_HOLD:
            rev = self._reversal_score(close, adx, vwap, ema9, ema21, rsi)
            if rev >= self.reversal_threshold:
                return self._exit_trade(close, "REVERSAL", bar_dt, result)

        result.trail_stop = self._trail_stop
        return result

    def _exit_trade(
        self, exit_price: float, reason: str, dt: datetime, result: ScoutBarResult
    ) -> ScoutBarResult:
        pnl = self._compute_pnl(exit_price)
        self._send_trade_exit(exit_price, reason, pnl, dt)
        self._state        = "DONE"
        result.exit_price  = exit_price
        result.exit_reason = reason
        result.pnl         = pnl
        return result

    # ── Private: indicator scoring ────────────────────────────────────────────

    def _momentum_score(
        self, price, adx, vwap, ema9, ema21, rsi, obv, roc
    ) -> tuple[float, Optional[str]]:
        votes = 0.0
        max_w = 0.0

        # MACD histogram slope (3 consecutive bars)
        if len(self._hist_buf) >= 3:
            max_w += 3.0
            h = self._hist_buf
            if h[-1] > h[-2] > h[-3]:
                votes += 3.0 if h[-1] >= 0 else 1.5
            elif h[-1] < h[-2] < h[-3]:
                votes -= 3.0 if h[-1] <= 0 else 1.5

        # VWAP position
        if _ok(vwap) and _ok(price):
            max_w += 3.0
            votes += 3.0 if price > vwap else -3.0

        # OBV 4-bar slope (≥5 values in buffer)
        if len(self._obv_buf) >= 5:
            max_w += 3.0
            votes += 3.0 if self._obv_buf[-1] > self._obv_buf[-5] else -3.0

        # EMA 9 vs 21 alignment
        if _ok(ema9) and _ok(ema21):
            max_w += 2.0
            votes += 2.0 if ema9 > ema21 else -2.0

        # RSI momentum zone
        if _ok(rsi):
            max_w += 2.0
            if   55 <= rsi <= 72: votes += 2.0
            elif 28 <= rsi <= 45: votes -= 2.0
            elif rsi > 72:        votes += 1.0
            elif rsi < 28:        votes -= 1.0

        # ROC direction
        if _ok(roc):
            max_w += 1.0
            votes += 1.0 if roc > 0 else -1.0

        if max_w == 0:
            return 0.0, None

        base = votes / max_w * 100

        if _ok(adx):
            if adx > 40:   base = max(-100.0, min(100.0, base * 1.35))
            elif adx > 25: base = max(-100.0, min(100.0, base * 1.15))
            elif adx < 18: base *= 0.70

        score     = round(max(-100.0, min(100.0, base)), 1)
        direction = "BULL" if score > 0 else ("BEAR" if score < 0 else None)
        return score, direction

    def _reversal_score(self, price, adx, vwap, ema9, ema21, rsi) -> int:
        score = 0
        bull  = (self._hypothesis == "BULL")

        if len(self._hist_buf) >= 3:
            h = self._hist_buf
            if bull     and h[-1] < h[-2] and h[-2] > h[-3] and h[-2] > 0: score += 2
            elif not bull and h[-1] > h[-2] and h[-2] < h[-3] and h[-2] < 0: score += 2

        if _ok(rsi):
            if bull and rsi > 74:       score += 2
            elif not bull and rsi < 26: score += 2

        if _ok(vwap) and _ok(price):
            if bull and price < vwap:       score += 2
            elif not bull and price > vwap: score += 2

        if len(self._adx_buf) >= 2:
            if self._adx_buf[-1] > 22 and self._adx_buf[-1] < self._adx_buf[-2] - 0.5:
                score += 1

        if len(self._obv_buf) >= 6:
            obv_up = self._obv_buf[-1] > self._obv_buf[-6]
            if bull and not obv_up:    score += 1
            elif not bull and obv_up:  score += 1

        if _ok(ema9) and _ok(ema21):
            if bull and ema9 < ema21:       score += 3
            elif not bull and ema9 > ema21: score += 3

        return score

    # ── Private: P&L ─────────────────────────────────────────────────────────

    def _compute_pnl(self, exit_price: float) -> float:
        atr  = self._atr_at_entry
        option_prem = max(atr * 0.40, 50.0)
        risk        = option_prem * self.lot_size
        spot_move   = (exit_price - self._entry_price) if self._hypothesis == "BULL" \
                      else (self._entry_price - exit_price)
        captured    = spot_move / (atr * self.atr_trail_mult) if atr > 0 else 0.0
        captured    = max(-1.5, min(5.0, captured))
        delta       = min(0.50 + captured * 0.11, 0.72) if captured >= 0 else 0.60
        return max(-risk, risk * captured * delta)

    # ── Private: state reset ──────────────────────────────────────────────────

    def _reset_day(self, new_date: date) -> None:
        self._session_date    = new_date
        self._state           = "SCOUT"
        self._bars_into_day   = 0
        self._vwap_crossings  = 0
        self._prev_above_vwap = None
        self._day_high        = None
        self._day_low         = None
        self._hypothesis      = None
        self._entry_price     = None
        self._entry_time      = ""
        self._atr_at_entry    = 0.0
        self._trail_stop      = None
        self._best_price      = None
        self._hold_bars       = 0

    # ── Private: notifications ────────────────────────────────────────────────

    def _send_scout_alert(self, adx, rpct, vwap_x, dt):
        if not self._notifier:
            return
        try:
            self._notifier.send_scout_alert(
                instrument=self.instrument, adx=adx,
                range_pct=rpct * 100, vwap_crossings=vwap_x,
                time_str=dt.strftime("%H:%M"),
            )
        except Exception:
            pass

    def _send_trade_entry(self, price, direction, trail_stop, score, dt):
        if not self._notifier:
            return
        try:
            self._notifier.send_scout_trade_entry(
                instrument=self.instrument, direction=direction,
                entry_price=price, trail_stop=trail_stop,
                momentum_score=score, time_str=dt.strftime("%H:%M"),
            )
        except Exception:
            pass

    def _send_trade_exit(self, exit_price, reason, pnl, dt):
        if not self._notifier:
            return
        try:
            self._notifier.send_scout_trade_exit(
                instrument=self.instrument, direction=self._hypothesis,
                entry_price=self._entry_price, exit_price=exit_price,
                exit_reason=reason, pnl=pnl,
                time_str=dt.strftime("%H:%M"),
            )
        except Exception:
            pass


# ── Helper ────────────────────────────────────────────────────────────────────

def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))
