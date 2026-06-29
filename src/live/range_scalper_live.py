"""
Range Scalper — Live / Paper Engine
────────────────────────────────────
Mean-reversion strategy for range-bound days (index within ±0.10–0.20%).

Three-phase gate:
  FORMING    (09:15–09:45)  — collect first 30 min of 1-min bars, compute range
  VALIDATING (09:45–10:00)  — 15 min without breakout confirms the range is real
  TRADING    (10:00–13:00)  — buy CE at R_low, buy PE at R_high on each bounce
  INVALIDATED (any time)    — 2 bars outside range → close all, no new entries

Entry logic:
  - Spot touches R_high (within 0.05%) → buy PE (sell-off expected)
  - Spot touches R_low  (within 0.05%) → buy CE (bounce expected)
  - ATM options only (delta ≈ 0.5 → better bounce response than OTM)
  - Target +40% gain, stop −25% loss

Auto-cut:
  Two consecutive 1-min bars with close outside the breakout buffer
  → all open positions closed, no further entries accepted today.

Not active on expiry days (use ExpiryScalperLive instead).
"""

from __future__ import annotations

import time
import datetime as _dt
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import get_spot_price, get_india_vix
from src.data.gift_nifty import get_gift_nifty_premium
from src.data.market_data import get_dow_jones_change_pct
from src.utils.market_calendar import (
    get_day_instrument,
    is_nifty_expiry_day,
    is_sensex_expiry_day,
    get_nifty_weekly_expiry,
    get_sensex_weekly_expiry,
)
from src.utils.helpers import round_to_strike
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("range_scalper_live", "RANGE_SCALPER_v1")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 60


# ── Trade record ──────────────────────────────────────────────────────────────

@dataclass
class RangeTrade:
    trade_id:     int
    direction:    str    # "BULLISH" or "BEARISH"
    option_type:  str    # "CE" or "PE"
    strike:       int
    entry_price:  float
    target_price: float
    stop_price:   float
    quantity:     int
    boundary:     str    # "R_LOW" or "R_HIGH"
    is_paper:     bool
    order_id:     str   = ""
    exit_price:   float = 0.0
    exit_reason:  str   = ""
    pnl:          float = 0.0


# ── Engine ────────────────────────────────────────────────────────────────────

class RangeScalperLive:

    # Range phase labels (used in dashboard direction column)
    FORMING     = "FORMING"
    VALIDATING  = "VALIDATING"
    TRADING     = "TRADING"
    INVALIDATED = "INVALIDATED"

    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc     = strategy_config
        self.broker = broker
        self.mode   = mode
        self._status_callback = status_callback
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065)
        )

        rs = strategy_config.get("range_scalper", {})
        self.formation_mins      = rs.get("formation_minutes", 30)
        self.confirm_mins        = rs.get("confirmation_minutes", 15)
        self.max_range_pct       = rs.get("max_range_pct", 0.20) / 100
        self.touch_pct           = rs.get("boundary_touch_pct", 0.05) / 100
        self.breakout_pct        = rs.get("breakout_buffer_pct", 0.07) / 100
        self.breakout_confirm_n  = rs.get("breakout_confirm_bars", 2)
        self.budget              = rs.get("trade_budget_rs", 10000)
        self.otm_n               = rs.get("otm_strikes", 0)
        self.min_prem            = rs.get("min_premium_rs", 15.0)
        self.max_prem            = rs.get("max_premium_rs", 120.0)
        self.target_pct          = rs.get("target_pct", 40) / 100
        self.stop_pct            = rs.get("stop_loss_pct", 25) / 100
        self.max_trades          = rs.get("max_trades_per_day", 6)
        close_str                = rs.get("hard_close_time", "13:00")
        self.close_h, self.close_m = int(close_str[:2]), int(close_str[3:])

        rf = strategy_config.get("range_day_filter", {})
        self.max_vix        = rf.get("max_vix", 16.0)
        self.max_dow_chg    = rf.get("max_dow_change_pct", 0.30)
        self.max_gift_prem  = rf.get("max_gift_nifty_prem", 40)
        self.skip_expiry    = rf.get("skip_expiry_days", True)

        self.slippage = strategy_config.get("backtest", {}).get("slippage_pct", 0.3) / 100
        self.day_stop = strategy_config.get("risk", {}).get("daily_loss_limit", 20000)

        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.nifty_lot  = inst_n.get("lot_size", 65)
        self.nifty_step = inst_n.get("strike_step", 50)
        inst_s = strategy_config.get("instruments", {}).get("sensex", {})
        self.sensex_lot  = inst_s.get("lot_size", 20)
        self.sensex_step = inst_s.get("strike_step", 100)

        # ── Mutable day state ─────────────────────────────────────────────────
        self.instrument: str   = ""
        self.expiry:     date  = date.today()
        self.vix:        float = 15.0

        self.phase:        str           = self.FORMING
        self.R_high:       Optional[float] = None
        self.R_low:        Optional[float] = None
        self.R_center:     Optional[float] = None
        self.spot_open:    Optional[float] = None

        self._form_highs:  list[float] = []
        self._form_lows:   list[float] = []
        self._breakout_ct: int         = 0

        self.trades:       list[RangeTrade]   = []
        self.open_trade:   Optional[RangeTrade] = None
        self.trade_count:  int   = 0
        self.day_pnl:      float = 0.0
        self._tid:         int   = 0
        self._shadow:      bool  = False  # True when pre-mkt filter failed; paper-only

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return datetime.now(IST)

    def _now_hm(self) -> _dt.time:
        n = self._now()
        return _dt.time(n.hour, n.minute)

    def _t_years(self) -> float:
        now = self._now()
        close_dt = now.replace(hour=self.close_h, minute=self.close_m,
                               second=0, microsecond=0)
        secs = max((close_dt - now).total_seconds(), 60)
        return (secs / 3600) / (6.25 * 252)

    def _get_ltp(self, spot: float, strike: int, opt_type: str) -> float:
        if self.mode == "live":
            exp_str = self.expiry.strftime("%Y%m%d")
            exch = "NFO" if self.instrument == "NIFTY" else "BFO"
            try:
                return self.broker.get_ltp(self.instrument, exch, strike, opt_type, exp_str)
            except Exception:
                pass
        result = self.pricer.price_option(
            spot=spot, strike=strike, opt_type=opt_type,
            T_years=self._t_years(), vix=self.vix,
        )
        return result.get("ltp", 0.0) if isinstance(result, dict) else getattr(result, "ltp", 0.0)

    def _qty(self, ltp: float) -> int:
        lot = self.nifty_lot if self.instrument == "NIFTY" else self.sensex_lot
        if ltp < 0.5:
            return 0
        return max(1, int(self.budget / (ltp * lot))) * lot

    # ── Dashboard callback ────────────────────────────────────────────────────

    def _emit(self, trade_event=None, signal: str = None, notable: bool = False) -> None:
        if not self._status_callback:
            return
        # In shadow mode every bar's analysis should be visible in Live Analysis panel
        if self._shadow and not notable:
            notable = True
        is_live   = self.mode == "live" and not self._shadow
        pnl_real  = self.day_pnl if is_live  else 0.0
        pnl_paper = self.day_pnl if not is_live else 0.0
        kw = dict(
            direction=self.phase,
            score=self.trade_count,
            budget=self.budget * self.max_trades,
            real_pnl=pnl_real,
            paper_pnl=pnl_paper,
            open_positions=1 if self.open_trade else 0,
            trades_today=self.trade_count,
        )
        if self._shadow:
            kw["state"] = "SHADOW"
        if trade_event:
            kw["trade_event"] = trade_event
        if signal:
            kw["signal"]  = signal
            kw["notable"] = notable
        self._status_callback(**kw)

    # ── Trade execution ───────────────────────────────────────────────────────

    def _open_trade(self, spot: float, step: int, boundary: str) -> None:
        direction = "BEARISH" if boundary == "R_HIGH" else "BULLISH"
        opt_type  = "PE"      if boundary == "R_HIGH" else "CE"
        atm       = round_to_strike(spot, step)
        strike    = atm - self.otm_n * step if opt_type == "PE" else atm + self.otm_n * step

        ltp = self._get_ltp(spot, strike, opt_type)
        if not (self.min_prem <= ltp <= self.max_prem):
            self._emit(
                signal=f"Near {boundary} → {opt_type}{strike} LTP=Rs.{ltp:.1f} outside [{self.min_prem:.0f}–{self.max_prem:.0f}] → skip",
                notable=True,
            )
            return

        entry_px  = ltp * (1 + self.slippage)
        qty       = self._qty(entry_px)
        if qty == 0:
            self._emit(
                signal=f"Near {boundary} → {opt_type}{strike} Rs.{ltp:.1f} qty=0 (budget insufficient) → skip",
                notable=True,
            )
            return

        target_px = round(entry_px * (1 + self.target_pct), 2)
        stop_px   = round(entry_px * (1 - self.stop_pct), 2)
        self._tid += 1

        trade = RangeTrade(
            trade_id=self._tid,
            direction=direction,
            option_type=opt_type,
            strike=strike,
            entry_price=round(entry_px, 2),
            target_price=target_px,
            stop_price=stop_px,
            quantity=qty,
            boundary=boundary,
            is_paper=(self.mode == "paper"),
        )

        if self.mode == "live":
            exch = "NFO" if self.instrument == "NIFTY" else "BFO"
            order = Order(
                symbol=self.instrument, exchange=exch,
                option_type=opt_type, strike=strike,
                expiry=self.expiry.strftime("%Y%m%d"),
                transaction="BUY", quantity=qty,
            )
            try:
                trade.order_id = self.broker.place_order(order)
            except Exception as e:
                log.error(f"Entry order failed: {e}")
        else:
            trade.order_id = "PAPER"

        self.trades.append(trade)
        self.open_trade  = trade
        self.trade_count += 1

        print(
            f"\n  ▶ ENTRY  {direction}  {opt_type}{strike}  @ {boundary}"
            f"  entry=Rs.{entry_px:.1f}  tgt=Rs.{target_px:.1f} (+{self.target_pct*100:.0f}%)"
            f"  stop=Rs.{stop_px:.1f} (-{self.stop_pct*100:.0f}%)  qty={qty}"
        )
        self._emit(
            trade_event={
                "event": "ENTRY", "instrument": self.instrument,
                "direction": direction, "option_type": opt_type,
                "strike": strike, "price": round(entry_px, 2),
                "quantity": qty, "pnl": "", "exit_reason": "",
                "window": f"RANGE@{boundary}",
            },
            signal=(
                f"ENTRY {direction} {opt_type}{strike}"
                f"  @ {boundary}  Rs.{entry_px:.1f}"
                f"  tgt=Rs.{target_px:.1f}  stop=Rs.{stop_px:.1f}"
            ),
            notable=True,
        )

    def _close_trade(self, trade: RangeTrade, reason: str) -> None:
        spot    = get_spot_price(self.instrument) or trade.entry_price
        ltp     = self._get_ltp(spot, trade.strike, trade.option_type)
        exit_px = round(ltp * (1 - self.slippage), 2)

        trade.exit_price  = exit_px
        trade.exit_reason = reason
        trade.pnl         = (exit_px - trade.entry_price) * trade.quantity
        self.day_pnl     += trade.pnl
        self.open_trade   = None

        if self.mode == "live":
            exch = "NFO" if self.instrument == "NIFTY" else "BFO"
            order = Order(
                symbol=self.instrument, exchange=exch,
                option_type=trade.option_type, strike=trade.strike,
                expiry=self.expiry.strftime("%Y%m%d"),
                transaction="SELL", quantity=trade.quantity,
            )
            try:
                self.broker.place_order(order)
            except Exception as e:
                log.error(f"Exit order failed: {e}")

        sign = "+" if trade.pnl >= 0 else ""
        print(
            f"\n  ◀ EXIT  {trade.direction}  {trade.option_type}{trade.strike}"
            f"  entry=Rs.{trade.entry_price:.1f} → exit=Rs.{exit_px:.1f}"
            f"  P&L: {sign}Rs.{trade.pnl:,.0f}  ({reason})"
        )
        self._emit(
            trade_event={
                "event": reason, "instrument": self.instrument,
                "direction": trade.direction, "option_type": trade.option_type,
                "strike": trade.strike, "price": exit_px,
                "quantity": trade.quantity, "pnl": round(trade.pnl, 2),
                "exit_reason": reason, "window": f"RANGE@{trade.boundary}",
            },
            signal=f"{reason}  {trade.option_type}{trade.strike}  P&L={sign}Rs.{trade.pnl:,.0f}",
            notable=True,
        )

    # ── Pre-market gate ───────────────────────────────────────────────────────

    def _pre_market_ok(self) -> bool:
        """Returns False if global cues suggest a directional (non-range) day."""
        try:
            self.vix = get_india_vix() or 15.0
            if self.vix > self.max_vix:
                self._emit(
                    signal=f"Pre-mkt: VIX={self.vix:.1f} > {self.max_vix} → volatile day, skip",
                    notable=True,
                )
                return False
        except Exception:
            self.vix = 15.0

        try:
            dow = get_dow_jones_change_pct()
            if dow and abs(dow) > self.max_dow_chg:
                self._emit(
                    signal=f"Pre-mkt: Dow={dow:+.2f}% > ±{self.max_dow_chg}% → directional, skip",
                    notable=True,
                )
                return False
        except Exception:
            pass

        try:
            gift = get_gift_nifty_premium()
            if gift and abs(gift) > self.max_gift_prem:
                self._emit(
                    signal=f"Pre-mkt: Gift Nifty={gift:+.0f} → gapped open expected, skip",
                    notable=True,
                )
                return False
        except Exception:
            pass

        self._emit(
            signal=f"Pre-mkt ✓  VIX={self.vix:.1f}  Dow/Gift Nifty flat  Range day candidate",
            notable=True,
        )
        return True

    # ── Main run ──────────────────────────────────────────────────────────────

    def _reconstruct_range_from_history(self, end_t):
        """Rebuild the 09:15–end_t high/low/open from historical 1-min bars (Kite-first
        via charts.fresh_chart, yfinance fallback). Returns (low, high, open) or None
        so a late/auto start can still trade the day's range."""
        try:
            from src.api import charts as charts_mod
            bars = (charts_mod.fresh_chart(self.instrument, "1m") or {}).get("bars") or []
            today_str = date.today().isoformat()
            end_hm = end_t.strftime("%H:%M")
            hi = lo = s_open = None
            for b in bars:
                t = str(b.get("t", ""))
                if not t.startswith(today_str):
                    continue
                hhmm = t[11:16]
                if hhmm < "09:15" or hhmm >= end_hm:
                    continue
                c = b.get("c")
                h = b.get("h") if b.get("h") is not None else c
                l = b.get("l") if b.get("l") is not None else c
                o = b.get("o") if b.get("o") is not None else c
                if s_open is None:
                    s_open = o
                if h is not None:
                    hi = h if hi is None else max(hi, h)
                if l is not None:
                    lo = l if lo is None else min(lo, l)
            if hi is not None and lo is not None and s_open:
                return (lo, hi, s_open)
        except Exception:
            pass
        return None

    def run(self) -> None:
        today      = date.today()
        instrument = get_day_instrument(today)

        if not instrument:
            print(f"\n  Range Scalper: {today} is not a trading day — skipping.")
            return

        if self.skip_expiry:
            is_expiry = (
                (instrument == "NIFTY"  and is_nifty_expiry_day(today)) or
                (instrument == "SENSEX" and is_sensex_expiry_day(today))
            )
            if is_expiry:
                print(f"\n  Range Scalper: {today} is expiry day — skipping (use EXPIRY_SCALPER_v1).")
                return

        self.instrument = instrument
        if instrument == "NIFTY":
            self.expiry = get_nifty_weekly_expiry(today)
            lot, step   = self.nifty_lot, self.nifty_step
        else:
            self.expiry = get_sensex_weekly_expiry(today)
            lot, step   = self.sensex_lot, self.sensex_step

        # Compute phase boundary times
        open_min        = 9 * 60 + 15
        form_end_min    = open_min + self.formation_mins      # 9:45
        valid_end_min   = form_end_min + self.confirm_mins    # 10:00
        form_end_t  = _dt.time(form_end_min  // 60, form_end_min  % 60)
        valid_end_t = _dt.time(valid_end_min // 60, valid_end_min % 60)
        close_t     = _dt.time(self.close_h, self.close_m)

        print(
            f"\n  ══════════════════════════════════════════════════════\n"
            f"  RANGE SCALPER  |  {today}  |  {instrument}  |  "
            f"{'LIVE' if self.mode == 'live' else 'PAPER'}\n"
            f"  Forming:    09:15 – {form_end_t.strftime('%H:%M')}"
            f"  (range ≤ {self.max_range_pct*100:.2f}% of open)\n"
            f"  Validating: {form_end_t.strftime('%H:%M')} – {valid_end_t.strftime('%H:%M')}"
            f"  (no breakout allowed)\n"
            f"  Trading:    {valid_end_t.strftime('%H:%M')} – {self.close_h:02d}:{self.close_m:02d}"
            f"  (max {self.max_trades} trades)\n"
            f"  Touch:      ±{self.touch_pct*100:.2f}%   Break: ±{self.breakout_pct*100:.2f}%"
            f"  ({self.breakout_confirm_n} bars)\n"
            f"  ══════════════════════════════════════════════════════"
        )

        # If the formation window has already closed (late / auto start), don't just
        # give up — RECONSTRUCT the 09:15–form_end range from historical 1-min bars
        # (which exist in Kite/backfill) and proceed. This makes the strategy
        # start-time-independent. Only bail if history is genuinely unavailable.
        _now_start = self._now()
        _now_t     = _dt.time(_now_start.hour, _now_start.minute)
        if _now_t >= form_end_t and _now_t < close_t:
            recon = self._reconstruct_range_from_history(form_end_t)
            if recon:
                r_lo, r_hi, s_open = recon
                r_pct = (r_hi - r_lo) / s_open if s_open else 1.0
                if r_pct <= self.max_range_pct:
                    self.spot_open = s_open
                    self.R_high, self.R_low = r_hi, r_lo
                    self.R_center = (r_hi + r_lo) / 2
                    self.phase = self.TRADING if _now_t >= valid_end_t else self.VALIDATING
                    msg = (f"Late start {_now_start.strftime('%H:%M')} — range rebuilt from "
                           f"history: {r_lo:.0f}–{r_hi:.0f} ({r_pct*100:.3f}%) → {self.phase}")
                    print(f"\n  Range Scalper: {msg}")
                    self._emit(signal=msg, notable=True)
                    # fall through into the main loop — do NOT return
                else:
                    msg = (f"Late start — reconstructed range {r_pct*100:.3f}% > "
                           f"{self.max_range_pct*100:.2f}% → not a range day. No trades.")
                    print(f"\n  Range Scalper: {msg}")
                    self._emit(signal=msg, notable=True)
                    return
            else:
                msg = (f"Formation window (09:15–{form_end_t.strftime('%H:%M')}) closed — "
                       f"started {_now_start.strftime('%H:%M')}, history unavailable. No trades today.")
                print(f"\n  Range Scalper: {msg}")
                self._emit(signal=msg, notable=True)
                return

        if not self._pre_market_ok():
            self._shadow = True
            self.mode    = "paper"   # force paper — no real orders in shadow
            self._emit(
                signal="Pre-mkt: directional day — SHADOW mode (watching, no orders)",
                notable=True,
            )
            print("  Pre-market filters failed — running in SHADOW (paper-only, no orders).")

        try:
            while True:
                if getattr(self, "_stop_event", None) is not None and self._stop_event.is_set():
                    break
                now    = self._now()
                now_hm = _dt.time(now.hour, now.minute)

                # ── Hard close ────────────────────────────────────────────
                if now_hm >= close_t:
                    if self.open_trade:
                        self._close_trade(self.open_trade, "FORCE_CLOSE")
                    self._print_eod()
                    return

                # ── Pre-market wait ───────────────────────────────────────
                if now.hour < 9 or (now.hour == 9 and now.minute < 15):
                    time.sleep(TICK_SECONDS)
                    continue

                spot = get_spot_price(instrument)
                if not spot:
                    time.sleep(TICK_SECONDS)
                    continue

                if self.spot_open is None:
                    self.spot_open = spot
                    log.info(f"Day open: {instrument}={spot:.2f}")

                # ══ PHASE 1: FORMING ═════════════════════════════════════
                if self.phase == self.FORMING:
                    self._form_highs.append(spot)
                    self._form_lows.append(spot)

                    if now_hm >= form_end_t:
                        r_hi = max(self._form_highs)
                        r_lo = min(self._form_lows)
                        r_pct = (r_hi - r_lo) / self.spot_open

                        if r_pct <= self.max_range_pct:
                            self.R_high   = r_hi
                            self.R_low    = r_lo
                            self.R_center = (r_hi + r_lo) / 2
                            self.phase    = self.VALIDATING
                            print(
                                f"\n  Range candidate  {r_lo:.1f} – {r_hi:.1f}"
                                f"  ({r_pct*100:.3f}% ≤ {self.max_range_pct*100:.2f}%)"
                                f"  → validating for {self.confirm_mins} min..."
                            )
                            self._emit(
                                signal=(
                                    f"Range formed: {r_lo:.0f}–{r_hi:.0f}"
                                    f"  ({r_pct*100:.3f}%)  → validating"
                                ),
                                notable=True,
                            )
                        else:
                            print(
                                f"\n  Range too wide: {r_pct*100:.3f}%"
                                f" > {self.max_range_pct*100:.2f}% → NOT a range day. Exiting."
                            )
                            self._emit(
                                signal=f"Range too wide: {r_pct*100:.3f}% → not a range day",
                                notable=True,
                            )
                            return
                    else:
                        cur_hi = max(self._form_highs) if self._form_highs else spot
                        cur_lo = min(self._form_lows) if self._form_lows else spot
                        cur_pct = (cur_hi - cur_lo) / self.spot_open
                        self._emit(
                            signal=(
                                f"FORMING {now.strftime('%H:%M')}  spot={spot:.0f}"
                                f"  range={cur_lo:.0f}–{cur_hi:.0f}  ({cur_pct*100:.3f}%)"
                            )
                        )

                # ══ PHASE 2: VALIDATING ══════════════════════════════════
                elif self.phase == self.VALIDATING:
                    buf_h = self.R_high * (1 + self.breakout_pct)
                    buf_l = self.R_low  * (1 - self.breakout_pct)

                    if spot > buf_h or spot < buf_l:
                        direction = "UP" if spot > buf_h else "DOWN"
                        print(
                            f"\n  Validation FAILED: spot={spot:.1f} broke range"
                            f" [{self.R_low:.1f}–{self.R_high:.1f}] {direction} during confirmation."
                            f"\n  Not a range day — exiting."
                        )
                        self._emit(
                            signal=f"Validation FAILED: spot={spot:.0f} broke {direction} — exit",
                            notable=True,
                        )
                        return

                    if now_hm >= valid_end_t:
                        self.phase = self.TRADING
                        print(
                            f"\n  ✓ Range VALIDATED  {self.R_low:.1f} – {self.R_high:.1f}"
                            f"  center={self.R_center:.1f}\n"
                            f"  Buy CE near {self.R_low:.1f}  |  Buy PE near {self.R_high:.1f}\n"
                            f"  Breakout kills all trades if spot exits"
                            f" [{self.R_low*(1-self.breakout_pct):.1f},"
                            f" {self.R_high*(1+self.breakout_pct):.1f}]"
                            f" for {self.breakout_confirm_n} bars\n"
                        )
                        self._emit(
                            signal=(
                                f"Range VALIDATED ✓  {self.R_low:.0f}–{self.R_high:.0f}"
                                f"  center={self.R_center:.0f}  TRADING ACTIVE"
                            ),
                            notable=True,
                        )
                    else:
                        self._emit(
                            signal=(
                                f"VALIDATING {now.strftime('%H:%M')}  spot={spot:.0f}"
                                f"  range={self.R_low:.0f}–{self.R_high:.0f}  holding…"
                            )
                        )

                # ══ PHASE 3: TRADING ═════════════════════════════════════
                elif self.phase == self.TRADING:

                    # Daily stop
                    if self.day_pnl <= -self.day_stop:
                        print(f"\n  Daily stop hit: Rs.{self.day_pnl:,.0f} — stopping.")
                        if self.open_trade:
                            self._close_trade(self.open_trade, "DAILY_STOP")
                        self._print_eod()
                        return

                    # Breakout check
                    buf_h = self.R_high * (1 + self.breakout_pct)
                    buf_l = self.R_low  * (1 - self.breakout_pct)
                    if spot > buf_h or spot < buf_l:
                        self._breakout_ct += 1
                        direction = "UP" if spot > buf_h else "DOWN"
                        self._emit(
                            signal=(
                                f"BREAKOUT WARNING {direction}  spot={spot:.0f}"
                                f"  [{self._breakout_ct}/{self.breakout_confirm_n}]"
                            ),
                            notable=True,
                        )
                        if self._breakout_ct >= self.breakout_confirm_n:
                            print(
                                f"\n  ⚠ RANGE BROKEN {direction}  spot={spot:.1f}"
                                f"  — {self.breakout_confirm_n} bars outside boundary"
                            )
                            self._emit(
                                signal=f"RANGE INVALIDATED — {direction} breakout at {spot:.0f}",
                                notable=True,
                            )
                            if self.open_trade:
                                self._close_trade(self.open_trade, "RANGE_BROKEN")
                            self.phase = self.INVALIDATED
                            self._print_eod()
                            return
                    else:
                        self._breakout_ct = 0  # reset if back inside

                    # Monitor open position
                    if self.open_trade:
                        ltp = self._get_ltp(spot, self.open_trade.strike,
                                            self.open_trade.option_type)
                        self._emit(
                            signal=(
                                f"POS OPEN  {self.open_trade.option_type}{self.open_trade.strike}"
                                f"  LTP=Rs.{ltp:.1f}"
                                f"  tgt=Rs.{self.open_trade.target_price:.1f}"
                                f"  stop=Rs.{self.open_trade.stop_price:.1f}"
                            )
                        )
                        if ltp >= self.open_trade.target_price:
                            self._close_trade(self.open_trade, "TARGET_HIT")
                        elif ltp <= self.open_trade.stop_price:
                            self._close_trade(self.open_trade, "STOP_LOSS")

                    # Scan for new entry
                    elif self.trade_count < self.max_trades:
                        near_high = spot >= self.R_high * (1 - self.touch_pct)
                        near_low  = spot <= self.R_low  * (1 + self.touch_pct)

                        if near_high:
                            self._open_trade(spot, step, "R_HIGH")
                        elif near_low:
                            self._open_trade(spot, step, "R_LOW")
                        else:
                            dist_to_hi = (self.R_high - spot) / self.R_high * 100
                            dist_to_lo = (spot - self.R_low) / self.R_low * 100
                            self._emit(
                                signal=(
                                    f"TRADING {now.strftime('%H:%M')}  spot={spot:.0f}"
                                    f"  ↑{dist_to_hi:.2f}% to R_HIGH={self.R_high:.0f}"
                                    f"  ↓{dist_to_lo:.2f}% to R_LOW={self.R_low:.0f}"
                                    f"  trades={self.trade_count}/{self.max_trades}"
                                )
                            )
                    else:
                        self._emit(
                            signal=f"Max trades reached ({self.max_trades}) — monitoring only"
                        )

                # ══ PHASE 4: INVALIDATED ═════════════════════════════════
                elif self.phase == self.INVALIDATED:
                    if self.open_trade:
                        self._close_trade(self.open_trade, "RANGE_BROKEN")
                    self._print_eod()
                    return

                time.sleep(TICK_SECONDS)

        except KeyboardInterrupt:
            print("\n  Stopped by user.")
            if self.open_trade:
                self._close_trade(self.open_trade, "FORCE_CLOSE")

        self._print_eod()

    # ── EOD summary ───────────────────────────────────────────────────────────

    def _print_eod(self) -> None:
        print(f"\n{'═'*60}")
        print(f"  Range Scalper EOD  —  {date.today()}  |  {self.instrument}")
        print(f"{'═'*60}")
        print(f"  Phase final  : {self.phase}")
        if self.R_high:
            print(f"  Range        : {self.R_low:.1f} – {self.R_high:.1f}"
                  f"  center={self.R_center:.1f}")
        print(f"  Trades today : {self.trade_count} / {self.max_trades}")
        sign = "+" if self.day_pnl >= 0 else ""
        print(f"  Day P&L      : {sign}Rs.{self.day_pnl:,.0f}")
        wins = sum(1 for t in self.trades if t.pnl > 0)
        if self.trades:
            print(f"  Win / Total  : {wins} / {len(self.trades)}"
                  f"  ({wins/len(self.trades)*100:.0f}%)")
        print(f"{'═'*60}\n")
