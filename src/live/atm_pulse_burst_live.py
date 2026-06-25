"""
ATM Pulse Burst — Live / Paper Engine
───────────────────────────────────────
ATM CE intraday momentum scalper targeting 25 premium-point bursts.

Architecture (4-layer decision model):
  Layer 1 – Market regime  : BULLISH_BREAKOUT / BULLISH_TREND / RANGE / CHOP / OVEREXTENDED
  Layer 2 – OI structure   : SUPPORTIVE / NEUTRAL_POS / NEUTRAL / CONFLICTING / TRAP_RISK
  Layer 3 – Premium trigger: NOT_READY / BREAKING / CONFIRMED / LATE / OVEREXTENDED
  Layer 4 – Decision       : NO_TRADE / WATCHING / ARMED / ENTER / MANAGE / EXIT

State machine:
  IDLE → ORB_BUILDING → WATCHING → ARMED → IN_POSITION → PARTIAL_EXIT → COOLDOWN → IDLE
  DAILY_STOPPED (daily loss / max trades) → next day only

Entry requires ALL of:
  - Spot above VWAP + EMA9 > EMA21 + spot above ORB high
  - OI structure: SUPPORTIVE or NEUTRAL_POS  (not TRAP_RISK)
  - Premium trigger: CONFIRMED (2 bars sustained above 5-bar high)
  - Signal score ≥ 75
  - All portfolio risk gates passing

Emits a detailed JSON + human log every bar regardless of trade state.
"""

from __future__ import annotations

import json
import time
import datetime as _dt
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import get_spot_price, get_india_vix
from src.data.option_chain import (
    fetch_nse_option_chain, parse_option_chain,
    OptionChainSnapshot, synthetic_option_data,
)
from src.utils.market_calendar import (
    get_day_instrument,
    is_nifty_expiry_day,
    is_sensex_expiry_day,
    get_nifty_weekly_expiry,
    get_sensex_weekly_expiry,
)
from src.utils.helpers import round_to_strike
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("atm_pulse_burst_live", "ATM_PULSE_BURST_v1")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 60

# ─────────────────────────────────────────────────────
#  Regime / OI / Premium enums (string constants)
# ─────────────────────────────────────────────────────

class Regime:
    BULLISH_BREAKOUT = "BULLISH_BREAKOUT"
    BULLISH_TREND    = "BULLISH_TREND"
    RANGE            = "RANGE"
    CHOP             = "CHOP"
    OVEREXTENDED     = "OVEREXTENDED"
    BEARISH          = "BEARISH"
    ORB_BUILDING     = "ORB_BUILDING"

class OIStructure:
    SUPPORTIVE       = "SUPPORTIVE"
    NEUTRAL_POS      = "NEUTRAL_POS"
    NEUTRAL          = "NEUTRAL"
    NEUTRAL_NEG      = "NEUTRAL_NEG"
    CONFLICTING      = "CONFLICTING"
    TRAP_RISK        = "TRAP_RISK"
    UNAVAILABLE      = "UNAVAILABLE"

class PremiumState:
    NOT_READY        = "NOT_READY"
    BREAKING         = "BREAKING"
    CONFIRMED        = "CONFIRMED"
    LATE             = "LATE"
    FAILED           = "FAILED"
    OVEREXTENDED     = "OVEREXTENDED"

class EngineState:
    IDLE             = "IDLE"
    ORB_BUILDING     = "ORB_BUILDING"
    WATCHING         = "WATCHING"
    ARMED            = "ARMED"
    IN_POSITION      = "IN_POSITION"
    PARTIAL_EXIT     = "PARTIAL_EXIT"
    COOLDOWN         = "COOLDOWN"
    DAILY_STOPPED    = "DAILY_STOPPED"


# ─────────────────────────────────────────────────────
#  Trade record
# ─────────────────────────────────────────────────────

@dataclass
class PulseTrade:
    trade_id:       int
    symbol:         str
    strike:         int
    expiry:         date
    entry_ltp:      float
    quantity:       int
    qty_remaining:  int
    sl_ltp:         float
    target_ltp:     float
    partial1_ltp:   float
    spot_at_entry:  float
    vwap_at_entry:  float
    orb_high:       float
    signal_score:   int
    regime:         str
    oi_structure:   str
    is_paper:       bool
    order_id:       str  = ""
    peak_ltp:       float = 0.0
    breakeven_promoted: bool = False
    trailing_active: bool = False
    bars_in_trade:  int  = 0
    realized_pnl:   float = 0.0
    exit_price:     float = 0.0
    exit_reason:    str  = ""


# ─────────────────────────────────────────────────────
#  Engine
# ─────────────────────────────────────────────────────

class ATMPulseBurstLive:

    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc     = strategy_config
        self.broker = broker
        self.mode   = mode
        self._status_callback = status_callback
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065)
        )

        cfg = strategy_config.get("atm_pulse_burst", {})
        self.max_premium_entry  = cfg.get("max_premium_entry_rs", 150)
        self.lots_default       = cfg.get("lots_default", 1)
        self.lots_hc            = cfg.get("lots_high_conviction", 1)
        self.hard_sl_pts        = cfg.get("hard_sl_pts", 8)
        self.daily_loss_limit   = cfg.get("daily_loss_limit_rs", 3000)
        self.daily_profit_lock  = cfg.get("daily_profit_lock_rs", 6000)
        self.target_pts         = cfg.get("target_premium_pts", 25)
        self.target_mode        = cfg.get("target_mode", "partial")
        self.partial1_pts       = cfg.get("partial1_at_pts", 15)
        self.trail_activate_pts = cfg.get("trail_activate_pts", 20)
        self.trail_sl_pct       = cfg.get("trail_sl_pct", 0.40)
        self.breakeven_at_pts   = cfg.get("breakeven_at_pts", 12)
        self.avoid_first_min    = cfg.get("avoid_first_min", 15)
        self.orb_minutes        = cfg.get("orb_minutes", 15)
        self.orb_buffer_pct     = cfg.get("orb_buffer_pct", 0.05) / 100
        self.min_orb_range_pts  = cfg.get("min_orb_range_pts", 20)
        self.overextended_pct   = cfg.get("overextended_pct", 1.5) / 100
        self.chase_limit_pct    = cfg.get("chase_limit_pct", 2.00) / 100
        self.ema_short          = cfg.get("ema_short", 9)
        self.ema_long           = cfg.get("ema_long", 21)
        self.time_stop_bars     = cfg.get("time_stop_bars", 15)
        self.time_stop_min_gain = cfg.get("time_stop_min_gain", 5)
        self.stagnation_bars    = cfg.get("stagnation_bars", 8)
        self.mom_fail_pct       = cfg.get("momentum_fail_drawdown", 0.30)
        self.min_oi_abs         = cfg.get("min_oi_abs_change", 100000)
        self.min_oi_base        = cfg.get("min_oi_base", 500000)
        self.call_resist_thr    = cfg.get("call_oi_resist_thresh", 0.15)
        self.put_support_thr    = cfg.get("put_oi_support_thresh", 0.10)
        self.prem_lookback      = cfg.get("premium_lookback_bars", 5)
        self.prem_buf           = cfg.get("premium_breakout_buffer", 0.5)
        self.prem_confirm_bars  = cfg.get("premium_confirm_bars", 2)
        self.late_entry_pts     = cfg.get("late_entry_pts", 8)
        self.extension_rej_pts  = cfg.get("extension_reject_pts", 20)
        self.score_entry        = cfg.get("score_entry", 75)
        self.score_hc           = cfg.get("score_high_conviction", 85)
        self.score_analysis     = cfg.get("score_analysis", 60)
        self.cooldown_loss_min  = cfg.get("cooldown_after_loss_min", 20)
        self.cooldown_win_min   = cfg.get("cooldown_after_profit_min", 5)
        self.max_trades         = cfg.get("max_trades_per_day", 4)
        self.max_consec_losses  = cfg.get("max_consecutive_losses", 2)
        self.max_spread_pct     = cfg.get("max_spread_pct", 0.05)
        self.max_vix_open       = cfg.get("max_vix_open", 25.0)

        close_str = cfg.get("hard_close_time", "15:10")
        self.close_h, self.close_m = int(close_str[:2]), int(close_str[3:])
        amode_str = cfg.get("afternoon_mode_after", "13:30")
        self.amode_h, self.amode_m = int(amode_str[:2]), int(amode_str[3:])

        inst = strategy_config.get("instruments", {}).get("nifty", {})
        self.lot_size   = inst.get("lot_size", 75)
        self.strike_step = inst.get("strike_step", 50)

        self.slippage = strategy_config.get("backtest", {}).get("slippage_pct", 0.5) / 100

        # ── Day state (reset each run) ────────────────────────────────
        self.instrument: str  = "NIFTY"
        self.expiry:    date  = date.today()
        self.vix:       float = 15.0

        # ORB
        self.orb_high:    float = 0.0
        self.orb_low:     float = 0.0
        self.orb_locked:  bool  = False
        self._orb_bars:   list[float] = []

        # Indicators (running state)
        self._ema9:     Optional[float] = None
        self._ema21:    Optional[float] = None
        self._vwap_sum: float = 0.0
        self._vwap_cnt: int   = 0
        self._vwap:     float = 0.0
        self._atr_bars: list[float] = []   # last 14 bar ranges

        # Premium tracker
        self._prem_history:      list[float] = []   # last 10 CE LTPs
        self._prem_breakout_cnt: int   = 0
        self._prem_breakout_px:  float = 0.0
        self._prem_state:        str   = PremiumState.NOT_READY

        # Engine state
        self.state:            str   = EngineState.IDLE
        self.trade:            Optional[PulseTrade] = None
        self.day_pnl:          float = 0.0
        self.trades_today:     int   = 0
        self.wins_today:       int   = 0
        self.consec_losses:    int   = 0
        self._last_exit_time:  Optional[datetime] = None
        self._last_exit_sl:    bool  = False
        self._tid:             int   = 0
        self._bar_count:       int   = 0
        self._armed_bar:       int   = 0   # bar_count when ARMED was entered
        self._day_open_spot:   float = 0.0

    # ── Helpers ────────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return datetime.now(IST)

    def _now_hm(self) -> _dt.time:
        n = self._now()
        return _dt.time(n.hour, n.minute)

    def _is_afternoon(self) -> bool:
        t = self._now_hm()
        return t >= _dt.time(self.amode_h, self.amode_m)

    def _alpha(self, period: int) -> float:
        return 2.0 / (period + 1)

    def _update_ema(self, price: float) -> None:
        a9  = self._alpha(self.ema_short)
        a21 = self._alpha(self.ema_long)
        if self._ema9 is None:
            self._ema9 = price
            self._ema21 = price
        else:
            self._ema9  = a9  * price + (1 - a9)  * self._ema9
            self._ema21 = a21 * price + (1 - a21) * self._ema21

    def _update_vwap(self, price: float) -> None:
        self._vwap_sum += price
        self._vwap_cnt += 1
        self._vwap = self._vwap_sum / self._vwap_cnt

    def _t_years(self) -> float:
        now = self._now()
        close_dt = now.replace(hour=self.close_h, minute=self.close_m,
                               second=0, microsecond=0)
        secs = max((close_dt - now).total_seconds(), 60)
        return (secs / 3600) / (6.25 * 252)

    def _get_ltp(self, spot: float, strike: int, opt_type: str) -> float:
        if self.mode == "live":
            exp_str = self.expiry.strftime("%Y%m%d")
            exch = "NFO"
            try:
                return self.broker.get_ltp(self.instrument, exch, strike, opt_type, exp_str)
            except Exception:
                pass
        result = self.pricer.price(
            spot=spot, strike=strike, vix=self.vix,
            T_hours=self._t_years() * 365 * 24,
            option_type=opt_type,
        )
        return result.price

    def _qty(self, score: int) -> int:
        lots = self.lots_hc if score >= self.score_hc else self.lots_default
        # Halve if daily profit lock hit
        if self.day_pnl >= self.daily_profit_lock:
            lots = max(1, lots // 2)
        return lots * self.lot_size

    # ── Indicator update (called every bar) ───────────────────────────

    def _update_indicators(self, spot: float) -> None:
        if self._day_open_spot == 0.0:
            self._day_open_spot = spot
        self._update_ema(spot)
        self._update_vwap(spot)
        self._atr_bars.append(spot)
        if len(self._atr_bars) > 15:
            self._atr_bars.pop(0)

    # ── ORB management ─────────────────────────────────────────────────

    def _update_orb(self, spot: float, bar_count: int) -> None:
        if not self.orb_locked:
            self._orb_bars.append(spot)
            if bar_count >= self.orb_minutes:
                self.orb_high   = max(self._orb_bars)
                self.orb_low    = min(self._orb_bars)
                self.orb_locked = True
                log.info(
                    f"ORB locked  high={self.orb_high:.1f}  low={self.orb_low:.1f}"
                    f"  range={self.orb_high - self.orb_low:.1f} pts"
                )

    # ── Regime detection ───────────────────────────────────────────────

    def _regime(self, spot: float) -> str:
        if not self.orb_locked:
            return Regime.ORB_BUILDING
        if self._ema9 is None or self._vwap == 0:
            return Regime.WATCHING if hasattr(Regime, 'WATCHING') else Regime.RANGE
        vwap = self._vwap
        e9   = self._ema9
        e21  = self._ema21

        # Day range check
        day_range = max(self._orb_bars + [spot]) - min(self._orb_bars + [spot]) if self._orb_bars else 0
        chop = day_range / max(self._day_open_spot, 1) < 0.004

        if chop:
            return Regime.CHOP

        # Overextended: spot > ORB + 1.5%
        if spot > self.orb_high * (1 + self.overextended_pct):
            return Regime.OVEREXTENDED

        # Chase limit
        if spot > self.orb_high * (1 + self.chase_limit_pct):
            return Regime.OVEREXTENDED

        bullish = spot > vwap and e9 > e21 and spot > self.orb_high

        # Fresh breakout: just crossed ORB high in last 2 bars
        pct_above = (spot - self.orb_high) / self.orb_high
        fresh     = 0 < pct_above <= 0.015 and spot > self.orb_high * (1 + self.orb_buffer_pct)

        if bullish and fresh:
            return Regime.BULLISH_BREAKOUT
        if bullish:
            return Regime.BULLISH_TREND
        if spot < vwap and e9 < e21:
            return Regime.BEARISH
        return Regime.RANGE

    # ── OI structure analysis ──────────────────────────────────────────

    def _oi_structure(self, chain: Optional[OptionChainSnapshot], atm: int) -> tuple[str, dict]:
        if chain is None or not chain.calls or not chain.puts:
            return OIStructure.UNAVAILABLE, {}

        def _trusted(oi_abs: int, oi_base: int) -> bool:
            return abs(oi_abs) >= self.min_oi_abs and oi_base >= self.min_oi_base

        details = {}
        put_score  = 0
        call_score = 0

        # Below spot: put OI build = bullish support
        below_put_strikes = sorted(
            [p for p in chain.puts if p.strike <= atm],
            key=lambda x: x.strike, reverse=True
        )[:2]
        for p in below_put_strikes:
            trusted = _trusted(p.oi_change, p.oi)
            interp  = "PUT_BUILD" if (trusted and p.oi_change > 0) else \
                      "PUT_UNWIND" if (trusted and p.oi_change < 0) else "NOISY"
            details[f"PE_{p.strike}"] = interp
            if interp == "PUT_BUILD":    put_score += 2
            elif interp == "PUT_UNWIND": put_score -= 1

        # Above spot: call OI unwind = resistance weakening
        above_call_strikes = sorted(
            [c for c in chain.calls if c.strike >= atm],
            key=lambda x: x.strike
        )[:2]
        for c in above_call_strikes:
            trusted  = _trusted(c.oi_change, c.oi)
            pct_chg  = abs(c.oi_change / c.oi * 100) if c.oi else 0
            heavy    = pct_chg > self.call_resist_thr * 100
            interp   = "CALL_BUILD_HEAVY" if (trusted and c.oi_change > 0 and heavy) else \
                       "CALL_BUILD_MOD"   if (trusted and c.oi_change > 0) else \
                       "CALL_UNWIND"      if (trusted and c.oi_change < 0) else "NOISY"
            details[f"CE_{c.strike}"] = interp
            if interp == "CALL_UNWIND":        call_score -= 2   # resistance weakening = good
            elif interp == "CALL_BUILD_HEAVY": call_score += 2   # heavy resistance = bad
            elif interp == "CALL_BUILD_MOD":   call_score += 1

        net = put_score - call_score
        if net >= 3:                          struct = OIStructure.SUPPORTIVE
        elif net >= 1:                        struct = OIStructure.NEUTRAL_POS
        elif net == 0:                        struct = OIStructure.NEUTRAL
        elif call_score >= 3:                 struct = OIStructure.TRAP_RISK
        elif net <= -2:                       struct = OIStructure.CONFLICTING
        else:                                 struct = OIStructure.NEUTRAL_NEG
        return struct, details

    # ── Premium trigger ────────────────────────────────────────────────

    def _update_premium(self, ce_ltp: float) -> str:
        self._prem_history.append(ce_ltp)
        if len(self._prem_history) > 10:
            self._prem_history.pop(0)

        if len(self._prem_history) < self.prem_lookback:
            self._prem_state = PremiumState.NOT_READY
            return self._prem_state

        recent_high = max(self._prem_history[-self.prem_lookback:])
        recent_low  = min(self._prem_history[-min(10, len(self._prem_history)):])

        # Overextension check
        if ce_ltp - recent_low > self.extension_rej_pts:
            self._prem_state = PremiumState.OVEREXTENDED
            self._prem_breakout_cnt = 0
            return self._prem_state

        # Breakout check
        thr = recent_high + self.prem_buf
        if ce_ltp > thr:
            self._prem_breakout_cnt += 1
            if self._prem_breakout_cnt == 1:
                self._prem_breakout_px = ce_ltp
                self._prem_state = PremiumState.BREAKING
            elif self._prem_breakout_cnt >= self.prem_confirm_bars:
                if ce_ltp > self._prem_breakout_px + self.late_entry_pts:
                    self._prem_state = PremiumState.LATE
                else:
                    self._prem_state = PremiumState.CONFIRMED
        else:
            if self._prem_breakout_cnt > 0:
                self._prem_state = PremiumState.FAILED
            else:
                self._prem_state = PremiumState.NOT_READY
            self._prem_breakout_cnt = 0

        return self._prem_state

    # ── Signal scoring ─────────────────────────────────────────────────

    def _score(self, spot: float, regime: str, oi_struct: str, prem_state: str) -> tuple[int, dict]:
        s = {}

        # Trend quality (0-20)
        t = 0
        if self._vwap and spot > self._vwap:          t += 8
        if self._ema9 and self._ema21 and self._ema9 > self._ema21: t += 6
        if self.orb_locked and spot > self.orb_high:  t += 6
        s["trend"] = min(t, 20)

        # Breakout quality (0-20)
        b = 0
        if self.orb_locked:
            pct = (spot - self.orb_high) / max(self.orb_high, 1) * 100
            if 0.05 <= pct <= 0.50:   b += 15
            elif 0.50 < pct <= 1.00:  b += 10
            elif pct > 1.00:          b += 4
        if self._ema9 and len(self._prem_history) > 1 and spot > (self._prem_history[-2] if len(self._prem_history) > 1 else spot - 1):
            b += 5
        s["breakout"] = min(b, 20)

        # Premium confirmation (0-15)
        p = {"CONFIRMED": 15, "BREAKING": 8, "LATE": 3}.get(prem_state, 0)
        s["premium"] = p

        # OI scores (0-15 put, ±10 call)
        oi_map = {
            OIStructure.SUPPORTIVE:   (15, 10),
            OIStructure.NEUTRAL_POS:  (8,  5),
            OIStructure.NEUTRAL:      (4,  0),
            OIStructure.NEUTRAL_NEG:  (2, -5),
            OIStructure.CONFLICTING:  (0, -10),
            OIStructure.TRAP_RISK:    (0, -15),
            OIStructure.UNAVAILABLE:  (4,  0),   # neutral when no OI data
        }
        po, co = oi_map.get(oi_struct, (4, 0))
        s["put_oi"]  = max(0, po)
        s["call_oi"] = co

        # Liquidity (0-10): simplified — can't check bid/ask without live chain
        s["liquidity"] = 6   # default acceptable

        # Penalties
        ext_pen = 0
        if self._prem_history:
            ext = self._prem_history[-1] - min(self._prem_history[-10:] if len(self._prem_history) >= 10 else self._prem_history)
            if ext > 18:   ext_pen = -15
            elif ext > 12: ext_pen = -8
            elif ext > 8:  ext_pen = -3
        s["ext_penalty"] = ext_pen

        chop_pen = 0
        orb_range = self.orb_high - self.orb_low if self.orb_locked else 0
        if orb_range < self.min_orb_range_pts: chop_pen = -8
        if regime == Regime.CHOP: chop_pen = -15
        s["chop_penalty"] = chop_pen

        raw   = sum(s.values())
        final = max(0, min(100, raw))
        return final, s

    # ── Entry execution ────────────────────────────────────────────────

    def _enter(self, spot: float, ce_ltp: float, score: int, regime: str, oi_struct: str) -> None:
        atm    = round_to_strike(spot, self.strike_step)
        qty    = self._qty(score)
        entry  = round(ce_ltp * (1 + self.slippage), 2)
        sl     = round(entry - self.hard_sl_pts, 2)
        target = round(entry + self.target_pts, 2)
        part1  = round(entry + self.partial1_pts, 2)
        exp    = get_nifty_weekly_expiry(date.today())
        sym    = f"NIFTY{exp.strftime('%d%b%y').upper()}{atm}CE"

        self._tid += 1
        t = PulseTrade(
            trade_id       = self._tid,
            symbol         = sym,
            strike         = atm,
            expiry         = exp,
            entry_ltp      = entry,
            quantity       = qty,
            qty_remaining  = qty,
            sl_ltp         = sl,
            target_ltp     = target,
            partial1_ltp   = part1,
            spot_at_entry  = spot,
            vwap_at_entry  = self._vwap,
            orb_high       = self.orb_high,
            signal_score   = score,
            regime         = regime,
            oi_structure   = oi_struct,
            is_paper       = (self.mode == "paper"),
            peak_ltp       = entry,
        )

        if self.mode == "live":
            exch  = "NFO"
            order = Order(
                symbol=self.instrument, exchange=exch,
                option_type="CE", strike=atm,
                expiry=exp.strftime("%Y%m%d"),
                transaction="BUY", quantity=qty,
            )
            try:
                t.order_id = self.broker.place_order(order)
            except Exception as e:
                log.error(f"Entry order failed: {e}")
                return
        else:
            t.order_id = "PAPER"

        self.trade = t
        self.trades_today += 1
        self.state = EngineState.IN_POSITION

        reason_codes = self._build_reason_codes(regime, oi_struct)
        log.info(
            f"ENTRY  {sym}  qty={qty}  @ Rs.{entry:.1f}"
            f"  SL=Rs.{sl:.1f}  T1=Rs.{part1:.1f}  TGT=Rs.{target:.1f}"
            f"  score={score}  {' '.join(reason_codes)}"
        )
        print(
            f"\n  ▶ ENTRY {sym}  qty={qty}"
            f"\n    Entry: Rs.{entry:.1f}  SL: Rs.{sl:.1f}"
            f"  T1: Rs.{part1:.1f}  Target: Rs.{target:.1f}"
            f"\n    Score: {score}  Regime: {regime}  OI: {oi_struct}"
        )
        self._emit(
            trade_event={
                "event": "ENTRY", "instrument": self.instrument,
                "direction": "BULLISH", "option_type": "CE",
                "strike": atm, "price": entry, "quantity": qty,
                "pnl": "", "exit_reason": "", "window": "APB",
            },
            signal=(
                f"ENTRY {sym}  @ Rs.{entry:.1f}"
                f"  SL=Rs.{sl:.1f}  Tgt=Rs.{target:.1f}  Score={score}"
            ),
            notable=True,
        )

    def _build_reason_codes(self, regime: str, oi_struct: str) -> list[str]:
        codes = []
        if regime in (Regime.BULLISH_BREAKOUT, Regime.BULLISH_TREND):
            codes.append("TREND_BULLISH")
        if self._vwap and self._ema9 and self._ema9 > self._vwap:
            codes.append("SPOT_BREAKOUT_CONFIRMED")
        if oi_struct in (OIStructure.SUPPORTIVE, OIStructure.NEUTRAL_POS):
            codes.append("PUT_OI_SUPPORTIVE")
        if oi_struct == OIStructure.SUPPORTIVE:
            codes.append("CALL_OI_RESISTANCE_WEAKENING")
        if self._prem_state == PremiumState.CONFIRMED:
            codes.append("PREMIUM_BREAKOUT_CONFIRMED")
        codes.append("ENTRY_LONG_CE")
        return codes

    # ── Position management ────────────────────────────────────────────

    def _manage(self, spot: float, ce_ltp: float) -> None:
        t = self.trade
        if not t:
            return
        t.bars_in_trade += 1
        t.peak_ltp       = max(t.peak_ltp, ce_ltp)

        # Breakeven promotion
        if not t.breakeven_promoted and ce_ltp >= t.entry_ltp + self.breakeven_at_pts:
            if t.sl_ltp < t.entry_ltp:
                t.sl_ltp = t.entry_ltp
                t.breakeven_promoted = True
                log.info(f"BREAKEVEN promoted — SL={t.entry_ltp:.1f}")

        # Trailing activation
        if self.target_mode == "trailing" and not t.trailing_active:
            if ce_ltp >= t.entry_ltp + self.trail_activate_pts:
                t.trailing_active = True

        if t.trailing_active:
            new_trail = round(t.peak_ltp * (1 - self.trail_sl_pct), 2)
            if new_trail > t.sl_ltp:
                t.sl_ltp = new_trail

    # ── Exit conditions ────────────────────────────────────────────────

    def _check_exits(self, spot: float, ce_ltp: float) -> Optional[str]:
        t = self.trade
        if not t:
            return None

        if ce_ltp <= t.sl_ltp:
            return "EXIT_STOPLOSS"

        if self.target_mode == "fixed" and ce_ltp >= t.target_ltp:
            return "EXIT_TARGET_HIT"

        now_hm = self._now_hm()
        if now_hm >= _dt.time(self.close_h, self.close_m):
            return "EXIT_EOD"

        if t.bars_in_trade >= self.time_stop_bars and ce_ltp < t.entry_ltp + self.time_stop_min_gain:
            return "EXIT_TIME_STOP"

        if t.bars_in_trade >= self.stagnation_bars and t.peak_ltp < t.entry_ltp + 3:
            return "EXIT_STAGNATION"

        if (t.peak_ltp >= t.entry_ltp + 10
                and ce_ltp < t.peak_ltp * (1 - self.mom_fail_pct)
                and len(self._prem_history) >= 2
                and self._prem_history[-1] < self._prem_history[-2]):
            return "EXIT_MOMENTUM_FADE"

        if spot < self.orb_high * 0.998 and t.bars_in_trade > 3:
            return "EXIT_STRUCTURE_FAIL"

        return None

    def _check_partial(self, ce_ltp: float) -> bool:
        t = self.trade
        if not t or self.target_mode == "fixed":
            return False
        if self.state != EngineState.IN_POSITION:
            return False
        return ce_ltp >= t.partial1_ltp

    # ── Execute partial exit ────────────────────────────────────────────

    def _do_partial(self, ce_ltp: float) -> None:
        t = self.trade
        if not t:
            return
        sell_qty = t.qty_remaining // 2
        if sell_qty == 0:
            sell_qty = t.qty_remaining

        exit_px  = round(ce_ltp * (1 - self.slippage), 2)
        pnl_part = (exit_px - t.entry_ltp) * sell_qty
        t.qty_remaining -= sell_qty
        t.realized_pnl  += pnl_part
        t.sl_ltp         = t.entry_ltp   # breakeven on runner
        self.day_pnl    += pnl_part
        self.state       = EngineState.PARTIAL_EXIT

        if self.mode == "live":
            exch  = "NFO"
            order = Order(
                symbol=self.instrument, exchange=exch,
                option_type="CE", strike=t.strike,
                expiry=t.expiry.strftime("%Y%m%d"),
                transaction="SELL", quantity=sell_qty,
            )
            try:
                self.broker.place_order(order)
            except Exception as e:
                log.error(f"Partial exit order failed: {e}")

        log.info(
            f"PARTIAL EXIT  sold={sell_qty}  @ Rs.{exit_px:.1f}"
            f"  pnl=+Rs.{pnl_part:,.0f}  runner={t.qty_remaining}"
            f"  SL promoted to entry Rs.{t.entry_ltp:.1f}"
        )
        print(
            f"\n  ◀ PARTIAL  Sold {sell_qty} qty @ Rs.{exit_px:.1f}"
            f"  +Rs.{pnl_part:,.0f}  Runner: {t.qty_remaining} qty"
        )
        self._emit(
            trade_event={
                "event": "PARTIAL", "instrument": self.instrument,
                "direction": "BULLISH", "option_type": "CE",
                "strike": t.strike, "price": exit_px,
                "quantity": sell_qty, "pnl": round(pnl_part, 2),
                "exit_reason": "PARTIAL1_HIT", "window": "APB",
            },
            signal=(
                f"PARTIAL EXIT  sold={sell_qty} @ Rs.{exit_px:.1f}"
                f"  +Rs.{pnl_part:,.0f}  SL→BE Rs.{t.entry_ltp:.1f}"
            ),
            notable=True,
        )

    # ── Execute full exit ───────────────────────────────────────────────

    def _do_exit(self, reason: str, ce_ltp: float) -> None:
        t = self.trade
        if not t:
            return

        exit_px  = round(ce_ltp * (1 - self.slippage), 2)
        pnl      = (exit_px - t.entry_ltp) * t.qty_remaining + t.realized_pnl
        t.exit_price  = exit_px
        t.exit_reason = reason
        self.day_pnl += (exit_px - t.entry_ltp) * t.qty_remaining

        if self.mode == "live":
            exch  = "NFO"
            order = Order(
                symbol=self.instrument, exchange=exch,
                option_type="CE", strike=t.strike,
                expiry=t.expiry.strftime("%Y%m%d"),
                transaction="SELL", quantity=t.qty_remaining,
            )
            try:
                self.broker.place_order(order)
            except Exception as e:
                log.error(f"Exit order failed: {e}")

        won = pnl > 0
        if won:
            self.wins_today   += 1
            self.consec_losses = 0
        else:
            self.consec_losses += 1

        self._last_exit_time = self._now()
        self._last_exit_sl   = (reason == "EXIT_STOPLOSS")
        sign = "+" if pnl >= 0 else ""

        log.info(
            f"EXIT  {reason}  {t.symbol}  @ Rs.{exit_px:.1f}"
            f"  P&L={sign}Rs.{pnl:,.0f}  day={sign}Rs.{self.day_pnl:,.0f}"
        )
        print(
            f"\n  ◀ EXIT  {reason}  {t.symbol}  @ Rs.{exit_px:.1f}"
            f"  P&L: {sign}Rs.{pnl:,.0f}  |  Day: {sign}Rs.{self.day_pnl:,.0f}"
        )
        self._emit(
            trade_event={
                "event": reason, "instrument": self.instrument,
                "direction": "BULLISH", "option_type": "CE",
                "strike": t.strike, "price": exit_px,
                "quantity": t.qty_remaining, "pnl": round(pnl, 2),
                "exit_reason": reason, "window": "APB",
            },
            signal=f"{reason}  {t.symbol}  P&L={sign}Rs.{pnl:,.0f}  day={sign}Rs.{self.day_pnl:,.0f}",
            notable=True,
        )

        self.trade = None
        cd_min = self.cooldown_loss_min if not won else self.cooldown_win_min
        self.state = EngineState.COOLDOWN
        log.info(f"Cooldown {cd_min} min  consec_losses={self.consec_losses}")

    # ── Pre-trade gates ────────────────────────────────────────────────

    def _gates_pass(self, regime: str, oi_struct: str, score: int) -> tuple[bool, list[str]]:
        now_hm = self._now_hm()
        blocked = []

        open_time = _dt.time(9, 15 + self.avoid_first_min)
        close_time = _dt.time(self.close_h, self.close_m - 20)

        if now_hm < open_time:
            blocked.append("TRADE_BLOCKED_TIME_WINDOW")
        if now_hm >= _dt.time(self.close_h, self.close_m):
            blocked.append("TRADE_BLOCKED_HARD_CLOSE")
        if self.trades_today >= self.max_trades:
            blocked.append("TRADE_BLOCKED_MAX_TRADES")
        if self.day_pnl <= -self.daily_loss_limit:
            blocked.append("TRADE_BLOCKED_DAILY_LOSS")
        if self.consec_losses >= self.max_consec_losses:
            blocked.append("TRADE_BLOCKED_CONSEC_LOSSES")
        if self._last_exit_time:
            cd_min = self.cooldown_loss_min if self._last_exit_sl else self.cooldown_win_min
            elapsed = (self._now() - self._last_exit_time).total_seconds() / 60
            if elapsed < cd_min:
                blocked.append(f"TRADE_BLOCKED_COOLDOWN")
        if regime not in (Regime.BULLISH_TREND, Regime.BULLISH_BREAKOUT):
            blocked.append(f"TREND_NOT_BULLISH")
        if oi_struct == OIStructure.TRAP_RISK:
            blocked.append("TRADE_BLOCKED_OI_TRAP")
        if score < self.score_entry:
            blocked.append("SIGNAL_SCORE_TOO_LOW")
        if self._prem_state not in (PremiumState.CONFIRMED,):
            blocked.append(f"PREMIUM_NOT_CONFIRMED")

        return len(blocked) == 0, blocked

    # ── Dashboard emit ────────────────────────────────────────────────

    def _emit(self, trade_event=None, signal: str = None, notable: bool = False) -> None:
        if not self._status_callback:
            return
        kw = dict(
            direction  = ("BULLISH" if self._ema9 and self._ema21 and self._ema9 > self._ema21
                          else "BEARISH" if self._ema9 and self._ema21 and self._ema9 < self._ema21
                          else "NEUTRAL"),
            score      = 0 if not self.trade else self.trade.signal_score,
            budget     = self.lot_size * self.lots_default * 100,  # rough
            real_pnl   = self.day_pnl if self.mode == "live" else 0.0,
            paper_pnl  = self.day_pnl if self.mode == "paper" else 0.0,
            open_positions = 1 if self.trade else 0,
            trades_today   = self.trades_today,
            wins_today     = self.wins_today,
            state          = self.state,
        )
        if trade_event: kw["trade_event"] = trade_event
        if signal:
            kw["signal"]  = signal
            kw["notable"] = notable
        self._status_callback(**kw)

    # ── Per-bar analysis log ───────────────────────────────────────────

    def _log_bar(self, spot: float, ce_ltp: float, regime: str,
                 oi_struct: str, oi_detail: dict,
                 score: int, score_bd: dict, blocked: list[str]) -> None:
        now = self._now()
        atm = round_to_strike(spot, self.strike_step) if spot else 0

        # Machine-readable JSON (ANALYSIS level)
        payload = {
            "ts":          now.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
            "strategy":    "ATM_PULSE_BURST_v1",
            "event":       "BAR_SCAN",
            "symbol":      self.instrument,
            "spot":        round(spot, 1) if spot else 0,
            "atm_strike":  atm,
            "atm_ce_ltp":  round(ce_ltp, 2) if ce_ltp else 0,
            "vwap":        round(self._vwap, 1) if self._vwap else 0,
            "ema9":        round(self._ema9, 1) if self._ema9 else 0,
            "ema21":       round(self._ema21, 1) if self._ema21 else 0,
            "orb_high":    round(self.orb_high, 1),
            "orb_low":     round(self.orb_low, 1),
            "orb_locked":  self.orb_locked,
            "regime":      regime,
            "oi_structure": oi_struct,
            "oi_details":  oi_detail,
            "premium_state": self._prem_state,
            "prem_history":  [round(p, 1) for p in self._prem_history[-5:]],
            "signal_score":  score,
            "score_breakdown": score_bd,
            "confidence":  "HIGH" if score >= 75 else "MEDIUM" if score >= 60 else "LOW",
            "engine_state": self.state,
            "blocked_by":  blocked,
            "position": {
                "active":     self.trade is not None,
                "entry_ltp":  round(self.trade.entry_ltp, 2) if self.trade else None,
                "sl_ltp":     round(self.trade.sl_ltp, 2) if self.trade else None,
                "target_ltp": round(self.trade.target_ltp, 2) if self.trade else None,
                "ltp_now":    round(ce_ltp, 2) if ce_ltp and self.trade else None,
                "qty_rem":    self.trade.qty_remaining if self.trade else None,
                "unrealised": round((ce_ltp - self.trade.entry_ltp) * self.trade.qty_remaining, 0)
                              if ce_ltp and self.trade else None,
                "bars_in":    self.trade.bars_in_trade if self.trade else None,
                "trailing":   self.trade.trailing_active if self.trade else None,
            },
            "day_pnl_rs":    round(self.day_pnl, 0),
            "trades_today":  self.trades_today,
            "consec_losses": self.consec_losses,
        }
        log.analysis(json.dumps(payload))

        # Human-readable one-liner
        pos_s = ""
        if self.trade:
            unr = round((ce_ltp - self.trade.entry_ltp) * self.trade.qty_remaining, 0)
            sign = "+" if unr >= 0 else ""
            pos_s = f"  POS CE{self.trade.strike} unr={sign}Rs.{unr:.0f} SL={self.trade.sl_ltp:.0f}"

        blocked_s = " | " + blocked[0] if blocked else ""
        human = (
            f"[{now.strftime('%H:%M')}] {self.instrument} {spot:.0f}"
            f" | ATM:{atm} CE₹{ce_ltp:.0f}"
            f" | {regime[:10]}"
            f" | OI:{oi_struct[:9]}"
            f" | PREM:{self._prem_state[:8]}"
            f" | Score:{score}"
            f" | {self.state[:9]}"
            f"{pos_s}"
            f"{blocked_s}"
        )
        log.analysis(human)

        # Dashboard signal (notable only on state changes or significant events)
        notable_conditions = (
            score >= self.score_analysis or
            self.trade is not None or
            self.state in (EngineState.ARMED, EngineState.COOLDOWN, EngineState.DAILY_STOPPED)
        )
        self._emit(signal=human, notable=notable_conditions)

    # ── Main run ───────────────────────────────────────────────────────

    def run(self) -> None:
        today      = date.today()
        instrument = get_day_instrument(today)
        if not instrument:
            print(f"\n  ATM Pulse Burst: {today} is not a trading day — skipping.")
            return

        self.instrument = instrument
        self.expiry     = get_nifty_weekly_expiry(today)

        # Block entire day if expiry + late session
        is_exp  = is_nifty_expiry_day(today) or is_sensex_expiry_day(today)

        print(
            f"\n  {'═'*58}\n"
            f"  ATM PULSE BURST  |  {today}  |  {instrument}  |"
            f"  {'LIVE' if self.mode == 'live' else 'PAPER'}\n"
            f"  Expiry: {self.expiry}  {'★ EXPIRY DAY' if is_exp else ''}\n"
            f"  Target: +{self.target_pts} CE pts  |  SL: -{self.hard_sl_pts} pts"
            f"  |  Max trades: {self.max_trades}\n"
            f"  {'═'*58}"
        )

        try:
            self.vix = get_india_vix() or 15.0
        except Exception:
            self.vix = 15.0

        if self.vix > self.max_vix_open:
            self.state = EngineState.DAILY_STOPPED
            msg = f"VIX={self.vix:.1f} > {self.max_vix_open} — all trades blocked today"
            print(f"\n  {msg}")
            self._emit(signal=msg, notable=True)
            return

        self.state = EngineState.IDLE
        chain_cache: Optional[OptionChainSnapshot] = None
        chain_age:   int = 0    # bars since last successful chain fetch

        try:
            while True:
                now    = self._now()
                now_hm = _dt.time(now.hour, now.minute)
                self._bar_count += 1

                # Hard close
                if now_hm >= _dt.time(self.close_h, self.close_m):
                    if self.trade:
                        spot = get_spot_price(self.instrument) or self.trade.spot_at_entry
                        ce   = self._get_ltp(spot, self.trade.strike, "CE")
                        self._do_exit("EXIT_EOD", ce)
                    self._print_eod()
                    return

                # Pre-market wait
                if now.hour < 9 or (now.hour == 9 and now.minute < 15):
                    time.sleep(TICK_SECONDS)
                    continue

                # Fetch spot
                spot = get_spot_price(self.instrument)
                if not spot:
                    log.warning("Spot fetch failed — skipping bar")
                    time.sleep(TICK_SECONDS)
                    continue

                # Update indicators and ORB
                self._update_indicators(spot)
                self._update_orb(spot, self._bar_count)

                if not self.orb_locked:
                    self.state = EngineState.ORB_BUILDING
                    self._emit(
                        signal=(
                            f"ORB BUILDING {now.strftime('%H:%M')}"
                            f"  spot={spot:.0f}  range so far:"
                            f"  {min(self._orb_bars) if self._orb_bars else spot:.0f}"
                            f"–{max(self._orb_bars) if self._orb_bars else spot:.0f}"
                        ),
                        notable=False,
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # Fetch option chain (try NSE, fallback to BS)
                chain_age += 1
                if chain_age >= 3 or chain_cache is None:  # refresh every 3 bars ≈ 3 min
                    try:
                        raw = fetch_nse_option_chain(self.instrument)
                        if raw:
                            chain_cache = parse_option_chain(raw, spot, strike_step=self.strike_step)
                            chain_age   = 0
                    except Exception:
                        pass  # keep stale cache

                atm    = round_to_strike(spot, self.strike_step)
                ce_ltp = self._get_ltp(spot, atm, "CE")

                # Update premium tracker
                prem_state = self._update_premium(ce_ltp)

                # Evaluate layers
                regime     = self._regime(spot)
                oi_struct, oi_detail = self._oi_structure(chain_cache, atm)
                score, score_bd      = self._score(spot, regime, oi_struct, prem_state)

                # State machine transitions
                if self.state in (EngineState.IN_POSITION, EngineState.PARTIAL_EXIT):
                    t = self.trade

                    # Check partial (only in IN_POSITION with partial mode)
                    if self.target_mode == "partial" and self.state == EngineState.IN_POSITION:
                        if self._check_partial(ce_ltp):
                            self._do_partial(ce_ltp)

                    # Check full exit
                    self._manage(spot, ce_ltp)
                    reason = self._check_exits(spot, ce_ltp)
                    if reason:
                        self._do_exit(reason, ce_ltp)

                elif self.state == EngineState.COOLDOWN:
                    if self._last_exit_time:
                        cd_min  = self.cooldown_loss_min if self._last_exit_sl else self.cooldown_win_min
                        elapsed = (self._now() - self._last_exit_time).total_seconds() / 60
                        if elapsed >= cd_min:
                            if (self.trades_today < self.max_trades
                                    and self.consec_losses < self.max_consec_losses
                                    and self.day_pnl > -self.daily_loss_limit):
                                self.state = EngineState.WATCHING
                            else:
                                self.state = EngineState.DAILY_STOPPED

                elif self.state == EngineState.DAILY_STOPPED:
                    pass  # just log

                elif self.state in (EngineState.WATCHING, EngineState.ARMED, EngineState.IDLE):
                    if self.state == EngineState.IDLE:
                        self.state = EngineState.WATCHING

                    gates_ok, blocked = self._gates_pass(regime, oi_struct, score)

                    if gates_ok and self.state == EngineState.WATCHING:
                        self.state      = EngineState.ARMED
                        self._armed_bar = self._bar_count
                        log.info(f"ARMED  score={score}  regime={regime}  oi={oi_struct}")
                        self._emit(
                            signal=f"ARMED  score={score}  regime={regime}  waiting for 2nd confirm bar",
                            notable=True,
                        )
                    elif gates_ok and self.state == EngineState.ARMED:
                        if self._bar_count > self._armed_bar:   # 2nd bar
                            self._enter(spot, ce_ltp, score, regime, oi_struct)
                    elif not gates_ok:
                        if self.state == EngineState.ARMED:
                            self.state = EngineState.WATCHING
                            log.info(f"DISARMED  {blocked}")
                        _, _ = blocked, []   # use below in log

                    _, blocked = self._gates_pass(regime, oi_struct, score)
                else:
                    _, blocked = [], []

                # Per-bar log
                if self.state in (EngineState.DAILY_STOPPED,):
                    blocked_msg = ["DAILY_STOPPED"]
                else:
                    _, blocked = self._gates_pass(regime, oi_struct, score)
                self._log_bar(spot, ce_ltp, regime, oi_struct, oi_detail, score, score_bd, blocked)

                time.sleep(TICK_SECONDS)

        except KeyboardInterrupt:
            print("\n  Stopped by user.")
            if self.trade:
                spot = get_spot_price(self.instrument) or self.trade.spot_at_entry
                ce   = self._get_ltp(spot, self.trade.strike, "CE")
                self._do_exit("FORCE_CLOSE", ce)

        self._print_eod()

    # ── EOD summary ─────────────────────────────────────────────────────

    def _print_eod(self) -> None:
        sign = "+" if self.day_pnl >= 0 else ""
        print(f"\n{'═'*58}")
        print(f"  ATM PULSE BURST EOD  —  {date.today()}  |  {self.instrument}")
        print(f"{'═'*58}")
        print(f"  Trades today : {self.trades_today} / {self.max_trades}")
        print(f"  Wins / Total : {self.wins_today} / {self.trades_today}")
        print(f"  Day P&L      : {sign}Rs.{self.day_pnl:,.0f}")
        print(f"  Consec losses: {self.consec_losses}")
        print(f"{'═'*58}\n")
        log.info(
            f"EOD  trades={self.trades_today}  wins={self.wins_today}"
            f"  day_pnl={sign}Rs.{self.day_pnl:,.0f}"
        )

    # ── Status update shortcut ──────────────────────────────────────────

    def _update_status(self, signal: str, notable: bool = False) -> None:
        self._emit(signal=signal, notable=notable)
