"""
BB Expiry Scalper — Live / Paper Engine
─────────────────────────────────────────
Bollinger Bands strategy designed for expiry days only.

Mode A (BB Breakout):
  Spot closes above upper BB for bb_breakout_confirm_bars → BUY CE
  Spot closes below lower BB for bb_breakout_confirm_bars → BUY PE

Mode B (BB Squeeze Expansion):
  Bandwidth (upper-lower)/middle < bb_squeeze_threshold% for min_squeeze_bars
  → then bandwidth starts expanding → BUY CE (if upper break) or PE (if lower)

Mode C: Disabled on expiry (mean reversion too risky with gamma).

Non-expiry days: SHADOW mode — full bar-by-bar analysis, no orders.
"""

from __future__ import annotations

import time
import math
import datetime as _dt
from datetime import date, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional, Deque
from collections import deque

from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import get_spot_price, get_india_vix
from src.data.option_chain import fetch_nse_option_chain, parse_option_chain
from src.utils.market_calendar import (
    is_nifty_expiry_day,
    is_sensex_expiry_day,
    get_nifty_weekly_expiry,
    get_sensex_weekly_expiry,
    get_day_instrument,
)
from src.utils.helpers import round_to_strike
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("bb_expiry_scalper_live", "BB_EXPIRY_SCALPER_v1")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 60


# ── Signal constants ──────────────────────────────────────────────────────────

class BBState:
    NORMAL    = "NORMAL"
    SQUEEZE   = "SQUEEZE"
    EXPANDING = "EXPANDING"
    BREAKOUT  = "BREAKOUT"


@dataclass
class BBScalperTrade:
    trade_id:     str
    symbol:       str
    strike:       int
    expiry:       date
    entry_ltp:    float
    quantity:     int
    sl_ltp:       float
    target_ltp:   float
    mode:         str          # "A" or "B"
    direction:    str          # "CE" or "PE"
    spot_at_entry: float
    bb_upper_at_entry: float
    bb_lower_at_entry: float
    signal_score: int
    is_paper:     bool
    order_id:     str  = ""
    peak_ltp:     float = 0.0
    bars_in_trade: int  = 0
    realized_pnl: float = 0.0
    exit_price:   float = 0.0
    exit_reason:  str   = ""


# ── Main engine ───────────────────────────────────────────────────────────────

class BBExpiryScalperLive:
    """Bollinger Bands Expiry Day Scalper — live/paper engine."""

    def __init__(
        self,
        config:          dict,
        broker:          BaseBroker,
        mode:            str = "paper",
        status_callback  = None,
    ):
        self.broker   = broker
        self.mode     = mode
        self._status_callback = status_callback
        self._shadow  = False

        cfg = config.get("bb_expiry_scalper", {})
        inst = config.get("instruments", {})

        # Instruments
        nifty_cfg  = inst.get("nifty",  {})
        sensex_cfg = inst.get("sensex", {})
        self.nifty_lot   = int(nifty_cfg.get("lot_size",    65))
        self.nifty_step  = int(nifty_cfg.get("strike_step", 50))
        self.sensex_lot  = int(sensex_cfg.get("lot_size",   20))
        self.sensex_step = int(sensex_cfg.get("strike_step",100))

        # Capital / risk
        self.budget    = float(cfg.get("trade_budget_rs",     10000))
        self.day_stop  = float(cfg.get("daily_loss_limit_rs", 25000))
        self.max_trades = int(cfg.get("max_trades_per_day",       5))

        # BB parameters
        self.bb_period    = int(cfg.get("bb_period",   20))
        self.bb_std       = float(cfg.get("bb_std_dev", 2.0))
        self.bb_squeeze_thresh = float(cfg.get("bb_squeeze_threshold", 0.5))
        self.bb_confirm_bars   = int(cfg.get("bb_breakout_confirm_bars", 2))

        # Modes
        self.enable_a = bool(cfg.get("enable_mode_a", True))
        self.enable_b = bool(cfg.get("enable_mode_b", True))

        # Mode A
        self.mode_a_min_score  = int(cfg.get("mode_a_min_score",   65))
        self.mode_a_otm        = int(cfg.get("mode_a_otm_strikes",  1))
        self.mode_a_target     = float(cfg.get("mode_a_target_mult", 2.5))
        self.mode_a_stop_pct   = float(cfg.get("mode_a_stop_pct",  35)) / 100

        # Mode B
        self.mode_b_min_score  = int(cfg.get("mode_b_min_score",   70))
        self.mode_b_min_squeeze= int(cfg.get("mode_b_min_squeeze_bars", 5))
        self.mode_b_otm        = int(cfg.get("mode_b_otm_strikes",  0))
        self.mode_b_target     = float(cfg.get("mode_b_target_mult", 3.0))
        self.mode_b_stop_pct   = float(cfg.get("mode_b_stop_pct",  40)) / 100

        # Entry timing
        self.avoid_first_min   = int(cfg.get("avoid_first_min", 15))
        self.hard_close_time   = cfg.get("hard_close_time", "15:15")
        self.min_premium       = float(cfg.get("min_premium_rs",   2.0))
        self.max_premium       = float(cfg.get("max_premium_rs", 200.0))

        # Scoring
        self.score_no_trade    = int(cfg.get("score_no_trade",        40))
        self.score_entry       = int(cfg.get("score_entry",           65))
        self.score_high_conv   = int(cfg.get("score_high_conviction", 80))

        # Cooldowns
        self.cooldown_loss_min  = int(cfg.get("cooldown_after_loss_min",  15))
        self.cooldown_profit_min = int(cfg.get("cooldown_after_profit_min", 5))

        # Liquidity
        self.max_spread_pct  = float(cfg.get("max_spread_pct",    0.05))
        self.max_vix         = float(cfg.get("max_vix",           28.0))
        self.min_vol_surge   = float(cfg.get("min_volume_surge",   1.2))

        hc = self.hard_close_time.split(":")
        self._close_t = _dt.time(int(hc[0]), int(hc[1]))
        self._avoid_t = _dt.time(9, 15 + self.avoid_first_min)

        # ── State ─────────────────────────────────────────────────────────────
        self._spots: Deque[float] = deque(maxlen=self.bb_period + 5)
        self.bb_upper = self.bb_middle = self.bb_lower = 0.0
        self.bandwidth_pct = 0.0
        self.bb_state  = BBState.NORMAL

        self._squeeze_bars   = 0
        self._breakout_bars  = 0
        self._breakout_dir   = ""       # "CE" or "PE"

        self.open_trade:  Optional[BBScalperTrade] = None
        self.trade_count  = 0
        self.day_pnl      = 0.0
        self._cooldown_until: Optional[_dt.datetime] = None
        self._pricer = OptionPricer()
        self.instrument = ""
        self.expiry: Optional[date] = None

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def _update_status(self, signal: str = "", notable: bool = False,
                       trade_event=None) -> None:
        if not self._status_callback:
            return
        is_live   = self.mode == "live" and not self._shadow
        pnl_real  = self.day_pnl if is_live  else 0.0
        pnl_paper = self.day_pnl if not is_live else 0.0
        self._status_callback(
            direction=self.bb_state,
            score=self.trade_count,
            budget=self.budget,
            real_pnl=pnl_real,
            paper_pnl=pnl_paper,
            trade_event=trade_event,
            signal=signal,
            notable=notable,
        )

    def _now(self) -> _dt.datetime:
        return _dt.datetime.now(IST)

    def _now_hm(self) -> _dt.time:
        n = self._now()
        return _dt.time(n.hour, n.minute)

    # ── Bollinger Band calculation ────────────────────────────────────────────

    def _update_bb(self, spot: float) -> None:
        self._spots.append(spot)
        if len(self._spots) < self.bb_period:
            return
        vals = list(self._spots)[-self.bb_period:]
        mean = sum(vals) / self.bb_period
        variance = sum((v - mean) ** 2 for v in vals) / self.bb_period
        std  = math.sqrt(variance)
        self.bb_upper  = mean + self.bb_std * std
        self.bb_middle = mean
        self.bb_lower  = mean - self.bb_std * std
        if mean > 0:
            self.bandwidth_pct = (self.bb_upper - self.bb_lower) / mean * 100
        else:
            self.bandwidth_pct = 0.0

    def _bb_ready(self) -> bool:
        return len(self._spots) >= self.bb_period

    def _update_bb_state(self, spot: float) -> None:
        if not self._bb_ready():
            self.bb_state = BBState.NORMAL
            return

        prev_state = self.bb_state
        in_squeeze = self.bandwidth_pct <= self.bb_squeeze_thresh

        if in_squeeze:
            self._squeeze_bars += 1
            if self._breakout_bars > 0:
                self._breakout_bars = 0
                self._breakout_dir  = ""
            self.bb_state = BBState.SQUEEZE
        else:
            if prev_state == BBState.SQUEEZE and self._squeeze_bars >= self.mode_b_min_squeeze:
                self.bb_state = BBState.EXPANDING
            else:
                # Check breakout
                if spot > self.bb_upper:
                    if self._breakout_dir != "CE":
                        self._breakout_bars = 0
                        self._breakout_dir  = "CE"
                    self._breakout_bars += 1
                elif spot < self.bb_lower:
                    if self._breakout_dir != "PE":
                        self._breakout_bars = 0
                        self._breakout_dir  = "PE"
                    self._breakout_bars += 1
                else:
                    self._breakout_bars = 0
                    self._breakout_dir  = ""

                if self._breakout_bars >= self.bb_confirm_bars and self._breakout_dir:
                    self.bb_state = BBState.BREAKOUT
                else:
                    self.bb_state = BBState.NORMAL

            if not in_squeeze:
                self._squeeze_bars = 0

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score(self, spot: float, vix: float) -> tuple[int, dict]:
        bd: dict = {}
        total = 0

        # BB position quality (max 30)
        if self._bb_ready():
            if self.bb_state == BBState.BREAKOUT:
                pts = 30
                bd["bb_breakout"] = 30
            elif self.bb_state == BBState.EXPANDING:
                pts = 25
                bd["bb_expanding"] = 25
            elif self.bb_state == BBState.SQUEEZE:
                pts = 10
                bd["bb_squeeze"] = 10
            else:
                pts = 5
                bd["bb_normal"] = 5
            total += pts
        else:
            bd["bb_warmup"] = 0

        # Squeeze quality for Mode B (max 20)
        if self.bb_state in (BBState.SQUEEZE, BBState.EXPANDING):
            sq_score = min(20, int(self._squeeze_bars / self.mode_b_min_squeeze * 20))
            total += sq_score
            bd["squeeze_quality"] = sq_score
        else:
            bd["squeeze_quality"] = 0

        # Breakout confirm bars (max 15)
        if self.bb_state == BBState.BREAKOUT:
            br_score = min(15, self._breakout_bars * 7)
            total += br_score
            bd["breakout_confirm"] = br_score
        else:
            bd["breakout_confirm"] = 0

        # VIX (max 20) — ideal VIX 15–25
        if vix < 28:
            if 15 <= vix <= 22:
                vix_pts = 20
            elif vix < 15:
                vix_pts = 12
            else:
                vix_pts = 8
        else:
            vix_pts = 0
        total += vix_pts
        bd["vix"] = vix_pts

        # Distance from middle band (max 15)
        if self._bb_ready() and self.bb_middle > 0:
            dist_pct = abs(spot - self.bb_middle) / self.bb_middle * 100
            if dist_pct >= 0.3:
                dist_pts = 15
            elif dist_pct >= 0.15:
                dist_pts = 10
            else:
                dist_pts = 5
            total += dist_pts
            bd["bb_distance"] = dist_pts
        else:
            bd["bb_distance"] = 0

        return min(100, max(0, total)), bd

    # ── Premium fetch ─────────────────────────────────────────────────────────

    def _get_premium(self, spot: float, direction: str, otm_strikes: int,
                     vix: float) -> tuple[float, int]:
        """Returns (ltp, strike). Falls back to Black-Scholes if chain unavailable."""
        step = self.nifty_step if self.instrument == "NIFTY" else self.sensex_step
        atm  = round_to_strike(spot, step)
        if direction == "CE":
            strike = atm + otm_strikes * step
        else:
            strike = atm - otm_strikes * step

        # Primary: the REAL option quote via the broker (PaperBroker serves read-only
        # Kite quotes in paper too). NSE-chain and Black-Scholes are fallbacks only.
        try:
            sym, exch = self.broker.get_tradingsymbol(self.instrument, self.expiry, strike, direction)
            px = self.broker.get_ltp(sym, exch, strike, direction, self.expiry.strftime("%Y%m%d"))
            if px and px > 0:
                return round(px, 2), strike
        except Exception:
            pass

        try:
            raw   = fetch_nse_option_chain(self.instrument)
            chain = parse_option_chain(raw, spot, step)
            opts  = chain.calls if direction == "CE" else chain.puts
            for opt in opts:
                if opt.strike == strike:
                    if opt.ltp and opt.ltp > 0:
                        return opt.ltp, strike
        except Exception:
            pass

        # Fallback
        today = date.today()
        exp   = self.expiry or today
        t_cal = (exp - today).days + 1
        t_hrs = max(0.01, t_cal * 6.25)
        # price(spot, strike, vix, T_hours, option_type) → PricedOption(.price).
        # (Was price(spot, strike, direction, t_hrs, vix/100) — args out of order:
        # the CE/PE string landed in the vix slot, so pricing crashed/garbaged.)
        priced = self._pricer.price(spot, strike, vix, t_hrs, direction)
        return round(priced.price, 2), strike

    # ── Trade open / close ────────────────────────────────────────────────────

    def _open_trade(self, spot: float, vix: float, direction: str,
                    mode_char: str, score: int) -> None:
        if self.open_trade:
            return
        if self.trade_count >= self.max_trades:
            self._update_status(
                signal=f"MAX_TRADES reached ({self.trade_count}/{self.max_trades}) — skip",
                notable=True,
            )
            return

        otm = self.mode_a_otm if mode_char == "A" else self.mode_b_otm
        ltp, strike = self._get_premium(spot, direction, otm, vix)

        if ltp < self.min_premium:
            self._update_status(
                signal=f"Reject: premium Rs.{ltp:.1f} < min Rs.{self.min_premium:.0f}",
                notable=True,
            )
            return
        if ltp > self.max_premium:
            self._update_status(
                signal=f"Reject: premium Rs.{ltp:.1f} > max Rs.{self.max_premium:.0f}",
                notable=True,
            )
            return

        lot  = self.nifty_lot if self.instrument == "NIFTY" else self.sensex_lot
        qty  = max(lot, int(self.budget / (ltp * lot)) * lot)

        stop_pct   = self.mode_a_stop_pct if mode_char == "A" else self.mode_b_stop_pct
        target_mult = self.mode_a_target if mode_char == "A" else self.mode_b_target
        sl_ltp     = round(ltp * (1 - stop_pct), 2)
        tgt_ltp    = round(ltp * target_mult, 2)

        step = self.nifty_step if self.instrument == "NIFTY" else self.sensex_step
        today = date.today()
        exp   = self.expiry or today
        symbol = (f"{self.instrument}{exp.strftime('%y%m%d')}"
                  f"{strike}{direction}")

        order_id = ""
        if self.mode == "live" and not self._shadow:
            try:
                # Resolve the real Kite tradingsymbol (the hand-built one above does
                # not match Kite's weekly format; place_order uses order.symbol
                # verbatim). Live broker is Kite here.
                try:
                    symbol, exch = self.broker.get_tradingsymbol(self.instrument, exp, strike, direction)
                except Exception:
                    exch = "NFO" if self.instrument == "NIFTY" else "BFO"
                order = Order(
                    symbol=symbol,
                    exchange=exch,
                    option_type=direction,
                    strike=strike,
                    expiry=exp.strftime("%Y%m%d"),
                    transaction="BUY",
                    quantity=qty,
                )
                # place_order returns the order_id STRING (not a dict).
                order_id = str(self.broker.place_order(order))
            except Exception as e:
                log.error(f"place_order failed: {e}")
                self._update_status(
                    signal=f"ORDER FAILED: {e}",
                    notable=True,
                )
                return

        trade = BBScalperTrade(
            trade_id=f"BB-{mode_char}-{self._now().strftime('%H%M%S')}",
            symbol=symbol,
            strike=strike,
            expiry=exp,
            entry_ltp=ltp,
            quantity=qty,
            sl_ltp=sl_ltp,
            target_ltp=tgt_ltp,
            mode=mode_char,
            direction=direction,
            spot_at_entry=spot,
            bb_upper_at_entry=self.bb_upper,
            bb_lower_at_entry=self.bb_lower,
            signal_score=score,
            is_paper=self.mode != "live" or self._shadow,
            order_id=order_id,
            peak_ltp=ltp,
        )
        self.open_trade = trade
        self.trade_count += 1

        log.info(
            f"ENTER Mode={mode_char} {direction}  strike={strike}  "
            f"ltp={ltp:.1f}  sl={sl_ltp:.1f}  tgt={tgt_ltp:.1f}  "
            f"score={score}  bb_bw={self.bandwidth_pct:.3f}%"
        )
        label = "PAPER" if trade.is_paper else "LIVE"
        self._update_status(
            signal=(
                f"[{label}] ENTER Mode-{mode_char} {direction} "
                f"strike={strike}  LTP={ltp:.1f}  SL={sl_ltp:.1f}  TGT={tgt_ltp:.1f}  "
                f"score={score}"
            ),
            notable=True,
            trade_event={"action": "open", "symbol": symbol, "strike": strike,
                         "ltp": ltp, "qty": qty, "direction": direction},
        )

    def _close_trade(self, trade: BBScalperTrade, reason: str, ltp: float = 0.0) -> None:
        exit_ltp = ltp if ltp > 0 else trade.entry_ltp
        pnl      = (exit_ltp - trade.entry_ltp) * trade.quantity
        self.day_pnl += pnl
        trade.exit_price  = exit_ltp
        trade.exit_reason = reason
        trade.realized_pnl = pnl

        if self.mode == "live" and not self._shadow and trade.order_id:
            try:
                try:
                    sym, exch = self.broker.get_tradingsymbol(
                        self.instrument, trade.expiry, trade.strike, trade.direction)
                except Exception:
                    sym = trade.symbol
                    exch = "NFO" if self.instrument == "NIFTY" else "BFO"
                order = Order(
                    symbol=sym,
                    exchange=exch,
                    option_type=trade.direction,
                    strike=trade.strike,
                    expiry=trade.expiry.strftime("%Y%m%d"),
                    transaction="SELL",
                    quantity=trade.quantity,
                )
                self.broker.place_order(order)
            except Exception as e:
                log.error(f"close order failed: {e}")

        log.info(
            f"EXIT {reason}  strike={trade.strike}  "
            f"entry={trade.entry_ltp:.1f}  exit={exit_ltp:.1f}  "
            f"pnl=Rs.{pnl:+,.0f}  day_pnl=Rs.{self.day_pnl:+,.0f}"
        )
        label = "PAPER" if trade.is_paper else "LIVE"
        self._update_status(
            signal=(
                f"[{label}] EXIT {reason}  {trade.direction}={trade.strike}"
                f"  entry={trade.entry_ltp:.1f}  exit={exit_ltp:.1f}"
                f"  PnL=Rs.{pnl:+,.0f}"
            ),
            notable=True,
            trade_event={"action": "close", "symbol": trade.symbol,
                         "reason": reason, "pnl": pnl, "exit_ltp": exit_ltp},
        )

        # Set cooldown
        now = self._now()
        if pnl < 0:
            self._cooldown_until = now + _dt.timedelta(minutes=self.cooldown_loss_min)
        else:
            self._cooldown_until = now + _dt.timedelta(minutes=self.cooldown_profit_min)
        self.open_trade = None

    # ── Exit logic ────────────────────────────────────────────────────────────

    def _check_exits(self, ltp: float) -> Optional[str]:
        t = self.open_trade
        if not t:
            return None
        t.bars_in_trade += 1
        if ltp > t.peak_ltp:
            t.peak_ltp = ltp
        if ltp <= t.sl_ltp:
            return "SL"
        if ltp >= t.target_ltp:
            return "TARGET"
        return None

    # ── Bar log ───────────────────────────────────────────────────────────────

    def _log_bar(self, spot: float, vix: float, score: int, score_bd: dict,
                 ce_ltp: float = 0.0, pe_ltp: float = 0.0) -> None:
        bw_str = f"{self.bandwidth_pct:.3f}%" if self._bb_ready() else "—"
        ub_str = f"{self.bb_upper:.1f}" if self._bb_ready() else "—"
        lb_str = f"{self.bb_lower:.1f}" if self._bb_ready() else "—"
        mb_str = f"{self.bb_middle:.1f}" if self._bb_ready() else "—"
        squeeze_str = f"SQ={self._squeeze_bars}" if self._squeeze_bars > 0 else ""
        trade_str = ""
        if self.open_trade:
            t = self.open_trade
            cur_pnl = (ce_ltp if t.direction == "CE" else pe_ltp) - t.entry_ltp
            trade_str = (
                f"  | TRADE Mode-{t.mode} {t.direction}={t.strike}"
                f"  entry={t.entry_ltp:.1f}  pnl={cur_pnl:+.1f}"
            )
        shadow_tag = "[SHADOW] " if self._shadow else ""
        log.info(
            f"{shadow_tag}"
            f"spot={spot:,.0f}  VIX={vix:.1f}"
            f"  BB=[{lb_str}|{mb_str}|{ub_str}]  BW={bw_str}"
            f"  state={self.bb_state}  {squeeze_str}"
            f"  score={score}"
            f"{trade_str}"
        )

    # ── Shadow analysis loop (non-expiry days) ────────────────────────────────

    def _run_shadow(self, instrument: str) -> None:
        today = date.today()
        nxt_n = get_nifty_weekly_expiry(today)
        nxt_s = get_sensex_weekly_expiry(today)
        self._shadow = True
        self.mode    = "paper"
        self.instrument = instrument
        step = self.nifty_step if instrument == "NIFTY" else self.sensex_step

        print(
            f"\n  BB Expiry Scalper: {today} is NOT an expiry day for {instrument}.\n"
            f"  Next Nifty expiry:  {nxt_n}   Next Sensex: {nxt_s}\n"
            f"  Running in SHADOW — computing BB bars, no orders.\n"
        )
        self._update_status(
            signal=(
                f"Not expiry — SHADOW  next Nifty={nxt_n}  Sensex={nxt_s}"
            ),
            notable=True,
        )
        if self._status_callback:
            self._status_callback(state="SHADOW")

        try:
            while True:
                now_hm = self._now_hm()
                if now_hm >= _dt.time(15, 30):
                    break
                if now_hm < _dt.time(9, 15):
                    time.sleep(TICK_SECONDS)
                    continue

                try:
                    spot = get_spot_price(instrument)
                    vix  = get_india_vix() or 15.0
                    if spot:
                        self._update_bb(spot)
                        self._update_bb_state(spot)
                        score, score_bd = self._score(spot, vix)

                        atm = round_to_strike(spot, step)
                        bw_str = (f"{self.bandwidth_pct:.3f}%"
                                  if self._bb_ready() else f"warming({len(self._spots)}/{self.bb_period})")
                        ub_str = f"{self.bb_upper:.1f}" if self._bb_ready() else "—"
                        lb_str = f"{self.bb_lower:.1f}" if self._bb_ready() else "—"
                        mb_str = f"{self.bb_middle:.1f}" if self._bb_ready() else "—"
                        squeeze_str = (f"  SQ_BARS={self._squeeze_bars}"
                                       if self._squeeze_bars > 0 else "")
                        mode_hint = ""
                        if self._bb_ready():
                            if self.bb_state == BBState.BREAKOUT and self.enable_a:
                                needed = self.mode_a_min_score
                                mode_hint = (
                                    f" → Mode-A {self._breakout_dir} "
                                    f"(score={score} need≥{needed}"
                                    f"{'✓' if score>=needed else '✗'})"
                                )
                            elif self.bb_state in (BBState.SQUEEZE, BBState.EXPANDING) and self.enable_b:
                                needed = self.mode_b_min_score
                                mode_hint = (
                                    f" → Mode-B squeeze={self._squeeze_bars}bars "
                                    f"(score={score} need≥{needed}"
                                    f"{'✓' if score>=needed else '✗'})"
                                )

                        self._update_status(
                            signal=(
                                f"SHADOW {now_hm.strftime('%H:%M')} | "
                                f"{instrument}={spot:,.0f}  VIX={vix:.1f}  ATM={atm}"
                                f"  BB=[{lb_str}|{mb_str}|{ub_str}]  BW={bw_str}"
                                f"  {self.bb_state}{squeeze_str}"
                                f"  score={score}"
                                f"{mode_hint}"
                            ),
                            notable=True,
                        )
                        self._log_bar(spot, vix, score, score_bd)
                except Exception as e:
                    log.debug(f"Shadow bar error: {e}")
                time.sleep(TICK_SECONDS)
        except KeyboardInterrupt:
            pass

    # ── Main run loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        today      = date.today()
        instrument = get_day_instrument()

        nifty_exp  = is_nifty_expiry_day(today)
        sensex_exp = is_sensex_expiry_day(today)
        is_expiry  = (instrument == "NIFTY"   and nifty_exp) or \
                     (instrument == "SENSEX"  and sensex_exp)

        if not is_expiry:
            self._run_shadow(instrument)
            return

        self.instrument = instrument
        self.expiry = (get_nifty_weekly_expiry(today)
                       if instrument == "NIFTY" else get_sensex_weekly_expiry(today))
        lot  = self.nifty_lot  if instrument == "NIFTY" else self.sensex_lot
        step = self.nifty_step if instrument == "NIFTY" else self.sensex_step

        print(
            f"\n  ══════════════════════════════════════════════════════\n"
            f"  BB EXPIRY SCALPER  |  {today}  |  {instrument}  |  "
            f"{'LIVE' if self.mode == 'live' else 'PAPER'}\n"
            f"  Budget: Rs.{self.budget:,.0f}/trade  |  Max trades: {self.max_trades}  |  "
            f"Daily stop: Rs.{self.day_stop:,}\n"
            f"  BB({self.bb_period},{self.bb_std})  squeeze<{self.bb_squeeze_thresh}%  "
            f"Mode-A={'ON' if self.enable_a else 'OFF'}  "
            f"Mode-B={'ON' if self.enable_b else 'OFF'}\n"
            f"  ══════════════════════════════════════════════════════"
        )

        self._update_status(
            signal=(
                f"Expiry day {today}  {instrument}  BB({self.bb_period},{self.bb_std})"
                f"  Modes: A={'on' if self.enable_a else 'off'}  B={'on' if self.enable_b else 'off'}"
            ),
            notable=True,
        )

        try:
            while True:
                if getattr(self, "_stop_event", None) is not None and self._stop_event.is_set():
                    break
                now    = self._now()
                now_hm = _dt.time(now.hour, now.minute)

                if now_hm >= self._close_t:
                    if self.open_trade:
                        spot = get_spot_price(instrument) or self.open_trade.entry_ltp
                        ltp, _ = self._get_premium(
                            spot, self.open_trade.direction,
                            self.mode_a_otm if self.open_trade.mode == "A" else self.mode_b_otm,
                            15.0,
                        )
                        self._close_trade(self.open_trade, "FORCE_CLOSE", ltp)
                    self._print_eod()
                    return

                if now.hour < 9 or (now.hour == 9 and now.minute < 15):
                    time.sleep(TICK_SECONDS)
                    continue

                try:
                    spot = get_spot_price(instrument)
                    vix  = get_india_vix() or 15.0
                except Exception:
                    time.sleep(TICK_SECONDS)
                    continue

                if not spot:
                    time.sleep(TICK_SECONDS)
                    continue

                self._update_bb(spot)
                self._update_bb_state(spot)
                score, score_bd = self._score(spot, vix)

                # ── Manage open trade ─────────────────────────────────────
                if self.open_trade:
                    t = self.open_trade
                    ltp, _ = self._get_premium(
                        spot, t.direction,
                        self.mode_a_otm if t.mode == "A" else self.mode_b_otm,
                        vix,
                    )
                    reason = self._check_exits(ltp)
                    if reason:
                        self._close_trade(t, reason, ltp)
                    else:
                        self._log_bar(spot, vix, score, score_bd,
                                      ce_ltp=ltp if t.direction == "CE" else 0,
                                      pe_ltp=ltp if t.direction == "PE" else 0)
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Cooldown check ────────────────────────────────────────
                if self._cooldown_until and now < self._cooldown_until:
                    rem = int((self._cooldown_until - now).total_seconds() / 60)
                    self._update_status(
                        signal=f"COOLDOWN {rem}min  spot={spot:,.0f}  BW={self.bandwidth_pct:.3f}%",
                        notable=True,
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Daily loss limit ──────────────────────────────────────
                if self.day_pnl <= -self.day_stop:
                    self._update_status(
                        signal=f"DAILY STOP hit Rs.{self.day_pnl:,.0f} — no more trades",
                        notable=True,
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── VIX gate ──────────────────────────────────────────────
                if vix > self.max_vix:
                    self._update_status(
                        signal=f"VIX BLOCK: {vix:.1f} > {self.max_vix}",
                        notable=True,
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Avoid first N minutes ─────────────────────────────────
                if now_hm < self._avoid_t:
                    self._update_status(
                        signal=(
                            f"AVOID_OPEN {now_hm.strftime('%H:%M')} | "
                            f"spot={spot:,.0f}  VIX={vix:.1f}  warming BB"
                            f" ({len(self._spots)}/{self.bb_period} bars)"
                        ),
                        notable=True,
                    )
                    self._log_bar(spot, vix, score, score_bd)
                    time.sleep(TICK_SECONDS)
                    continue

                # ── BB not ready ──────────────────────────────────────────
                if not self._bb_ready():
                    self._update_status(
                        signal=(
                            f"BB warmup {len(self._spots)}/{self.bb_period}"
                            f"  spot={spot:,.0f}"
                        ),
                        notable=True,
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Entry logic ───────────────────────────────────────────
                entered = False

                if self.enable_a and self.bb_state == BBState.BREAKOUT:
                    min_score = self.mode_a_min_score
                    direction = self._breakout_dir
                    if score >= min_score and direction:
                        self._open_trade(spot, vix, direction, "A", score)
                        entered = True

                if not entered and self.enable_b and self.bb_state == BBState.EXPANDING:
                    min_score = self.mode_b_min_score
                    if score >= min_score:
                        direction = "CE" if spot > self.bb_middle else "PE"
                        self._open_trade(spot, vix, direction, "B", score)
                        entered = True

                if not entered:
                    atm = round_to_strike(spot, step)
                    bw_str = f"{self.bandwidth_pct:.3f}%"
                    sq_str = (f"  squeeze={self._squeeze_bars}bars"
                              if self._squeeze_bars > 0 else "")
                    self._update_status(
                        signal=(
                            f"{now_hm.strftime('%H:%M')} | "
                            f"spot={spot:,.0f}  VIX={vix:.1f}  ATM={atm}"
                            f"  BW={bw_str}  {self.bb_state}{sq_str}"
                            f"  score={score} (need≥{self.score_entry})"
                        ),
                        notable=(score >= self.score_no_trade),
                    )
                    self._log_bar(spot, vix, score, score_bd)

                time.sleep(TICK_SECONDS)

        except KeyboardInterrupt:
            pass

        if self.open_trade:
            self._update_status(
                signal=f"Interrupted — open trade {self.open_trade.symbol} left open",
                notable=True,
            )

        self._print_eod()

    # ── EOD summary ───────────────────────────────────────────────────────────

    def _print_eod(self) -> None:
        label = "PAPER" if (self.mode != "live" or self._shadow) else "LIVE"
        print(
            f"\n  ══ BB EXPIRY SCALPER EOD [{label}] ══════════════════\n"
            f"  Trades: {self.trade_count}  |  Day PnL: Rs.{self.day_pnl:+,.0f}\n"
            f"  BB({self.bb_period},{self.bb_std})  Bandwidth at close: "
            f"{self.bandwidth_pct:.3f}%\n"
            f"  ════════════════════════════════════════════════════\n"
        )
        log.info(
            f"EOD  trades={self.trade_count}  pnl=Rs.{self.day_pnl:+,.0f}"
            f"  bw={self.bandwidth_pct:.3f}%"
        )
        self._update_status(
            signal=(
                f"EOD [{label}]  trades={self.trade_count}"
                f"  PnL=Rs.{self.day_pnl:+,.0f}"
            ),
            notable=True,
        )
