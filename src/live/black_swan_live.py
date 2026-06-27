"""
Black Swan Scalper — Live / Paper Engine
─────────────────────────────────────────
Fires on ANY trading day when the market shows extreme moves:

  Trigger A — Gap open:  Nifty opens ≥1.5% away from previous close
  Trigger B — Intraday:  Nifty moves ≥2.0% from the 9:15 day open
  Trigger C — VIX spike: When VIX ≥22, threshold drops to 1.2%

One trade per day maximum. Buys 1 OTM call/put in the direction of the
extreme move. 90-minute time stop kills the position if nothing happens.

Use case: PM/President assassination, war outbreak, election shock,
          global circuit breaker, flash crash, budget day panic.
"""

from __future__ import annotations

import time
import datetime as _dt
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import (
    get_spot_price, get_india_vix, get_previous_close, get_day_open_spot,
)
from src.utils.market_calendar import get_nifty_weekly_expiry
from src.utils.helpers import round_to_strike
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("black_swan_live", "BLACK_SWAN_v1")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 60


@dataclass
class SwanTrade:
    instrument:   str
    direction:    str
    option_type:  str
    strike:       int
    expiry_str:   str
    exchange:     str
    entry_price:  float
    entry_time:   str
    quantity:     int
    target_price: float
    stop_price:   float
    time_stop:    datetime
    is_paper:     bool
    order_id:     str   = ""
    exit_price:   float = 0.0
    exit_reason:  str   = ""
    exit_time:    str   = ""
    pnl:          float = 0.0


class BlackSwanLive:

    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc     = strategy_config
        self.broker = broker
        self.mode   = mode
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065)
        )
        self._status_callback = status_callback

        sc_bs = strategy_config.get("black_swan", {})
        self.gap_trigger      = sc_bs.get("gap_trigger_pct",      1.5) / 100
        self.intraday_trigger = sc_bs.get("intraday_trigger_pct", 2.0) / 100
        self.vix_alert        = sc_bs.get("vix_alert_level",      22.0)
        self.vix_trigger      = sc_bs.get("vix_trigger_pct",      1.2) / 100
        self.otm_n            = sc_bs.get("otm_strikes",          1)
        self.budget           = sc_bs.get("trade_budget_rs",      20000)
        self.max_prem         = sc_bs.get("max_premium_rs",       500.0)
        self.min_prem         = sc_bs.get("min_premium_rs",       20.0)
        self.tgt_mult         = sc_bs.get("target_multiplier",    4.0)
        self.stop_pct         = sc_bs.get("stop_loss_pct",        40) / 100
        self.time_stop_min    = sc_bs.get("time_stop_minutes",    90)
        self.slippage         = strategy_config.get("backtest", {}).get("slippage_pct", 0.3) / 100
        self.day_stop         = strategy_config.get("risk", {}).get("daily_loss_limit", 20000)

        def _hm(s): return _dt.time(int(s[:2]), int(s[3:]))
        self.entry_start = _hm(sc_bs.get("entry_start", "09:15"))
        self.entry_end   = _hm(sc_bs.get("entry_end",   "13:30"))
        close_str        = sc_bs.get("hard_close_time", "15:20")
        self.close_h, self.close_m = int(close_str[:2]), int(close_str[3:])

        inst_n     = strategy_config.get("instruments", {}).get("nifty", {})
        self.lot   = inst_n.get("lot_size",   65)
        self.step  = inst_n.get("strike_step", 50)

        # State
        self.instrument = "NIFTY"
        self.prev_close = 0.0
        self.day_open   = 0.0
        self.vix        = 15.0
        self.fired      = False
        self.open_trade: Optional[SwanTrade] = None
        self.trade:      Optional[SwanTrade] = None
        self.day_pnl    = 0.0

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return datetime.now(IST)

    def _now_hm(self) -> _dt.time:
        n = self._now()
        return _dt.time(n.hour, n.minute)

    def _t_years_to_close(self) -> float:
        now   = self._now()
        close = now.replace(hour=self.close_h, minute=self.close_m, second=0, microsecond=0)
        secs  = max((close - now).total_seconds(), 60)
        return (secs / 3600) / (6.25 * 252)

    def _check_trigger(self, spot: float) -> Optional[str]:
        """
        Returns 'BULLISH' or 'BEARISH' if any trigger condition is met.
        Gap trigger (vs prev close) takes priority over intraday trigger.
        When VIX is elevated (≥vix_alert), intraday threshold is reduced.
        """
        if self.prev_close > 0:
            gap = (spot - self.prev_close) / self.prev_close
            if abs(gap) >= self.gap_trigger:
                log.info(f"Gap trigger fired: {gap*100:+.2f}% (thr={self.gap_trigger*100:.1f}%)")
                return "BULLISH" if gap > 0 else "BEARISH"

        if self.day_open > 0:
            move = (spot - self.day_open) / self.day_open
            thr  = self.vix_trigger if self.vix >= self.vix_alert else self.intraday_trigger
            if abs(move) >= thr:
                log.info(f"Intraday trigger fired: {move*100:+.2f}% (thr={thr*100:.1f}%, VIX={self.vix:.1f})")
                return "BULLISH" if move > 0 else "BEARISH"

        return None

    def _get_ltp(self, spot: float, expiry: date, strike: int, opt_type: str) -> float:
        T = self._t_years_to_close()
        if self.mode == "live":
            ts, exchange = self._resolve_tradingsymbol(expiry, strike, opt_type)
            try:
                return self.broker.get_ltp(
                    ts, exchange, strike, opt_type, expiry.strftime("%Y%m%d")
                )
            except Exception:
                pass
        result = self.pricer.price(
            spot=spot, strike=strike, vix=self.vix,
            T_hours=T * 365 * 24,
            option_type=opt_type,
        )
        return result.price

    def _resolve_tradingsymbol(self, expiry: date, strike: int, opt_type: str) -> tuple[str, str]:
        if self.mode == "live":
            from src.broker.kite_broker import KiteBroker
            if isinstance(self.broker, KiteBroker):
                try:
                    return self.broker.get_tradingsymbol(
                        self.instrument, expiry, strike, opt_type
                    )
                except Exception as e:
                    log.warning(f"get_tradingsymbol failed ({e}) — using fallback")
        return self.instrument, "NFO"

    def _qty(self, ltp: float) -> int:
        if ltp < 0.5:
            return 0
        return max(1, int(self.budget / (ltp * self.lot))) * self.lot

    def _place_order(self, trade: SwanTrade, expiry: date) -> None:
        symbol, exchange = self._resolve_tradingsymbol(expiry, trade.strike, trade.option_type)
        order = Order(
            symbol=symbol, exchange=exchange,
            option_type=trade.option_type, strike=trade.strike,
            expiry=trade.expiry_str, transaction="BUY",
            quantity=trade.quantity,
        )
        try:
            oid = self.broker.place_order(order)
            trade.order_id = oid
            log.info(
                f"[LIVE] BUY {symbol} qty={trade.quantity} "
                f"@ Rs.{trade.entry_price:.1f} | order_id={oid}"
            )
        except Exception as e:
            log.error(f"Black Swan BUY order failed: {e}")

    def _close_trade(self, trade: SwanTrade, reason: str, expiry: date) -> None:
        spot    = get_spot_price(self.instrument) or trade.entry_price
        ltp     = self._get_ltp(spot, expiry, trade.strike, trade.option_type)
        exit_px = ltp * (1 - self.slippage)

        trade.exit_price  = exit_px
        trade.exit_reason = reason
        trade.exit_time   = self._now().strftime("%H:%M")
        trade.pnl         = (exit_px - trade.entry_price) * trade.quantity
        self.day_pnl     += trade.pnl
        self.open_trade   = None
        sign = "+" if trade.pnl >= 0 else ""

        symbol, exchange = self._resolve_tradingsymbol(expiry, trade.strike, trade.option_type)
        order = Order(
            symbol=symbol, exchange=exchange,
            option_type=trade.option_type, strike=trade.strike,
            expiry=trade.expiry_str, transaction="SELL",
            quantity=trade.quantity,
        )
        try:
            oid = self.broker.place_order(order)
            log.info(
                f"[LIVE] SELL {symbol} qty={trade.quantity} "
                f"@ Rs.{exit_px:.1f} | order_id={oid} | P&L={sign}Rs.{trade.pnl:,.0f}"
            )
        except Exception as e:
            log.error(f"Black Swan SELL order failed: {e}")

        print(
            f"\n  [BLACK_SWAN] {reason}  "
            f"{trade.direction} {trade.option_type}{trade.strike}  "
            f"entry=Rs.{trade.entry_price:.1f} → exit=Rs.{exit_px:.1f}  "
            f"P&L: {sign}Rs.{trade.pnl:,.0f}"
        )
        self._update_status(
            direction=trade.direction,
            trade_event={
                "event":       reason,
                "instrument":  trade.instrument,
                "direction":   trade.direction,
                "option_type": trade.option_type,
                "strike":      trade.strike,
                "price":       round(exit_px, 2),
                "quantity":    trade.quantity,
                "pnl":         round(trade.pnl, 2),
                "exit_reason": reason,
                "window":      "BLACK_SWAN",
            },
        )

    def _update_status(self, direction: str = "NEUTRAL",
                       trade_event: dict = None,
                       signal: str = None, notable: bool = False) -> None:
        if not self._status_callback:
            return
        kwargs = dict(
            direction=direction,
            score=0,
            budget=self.budget,
            real_pnl=self.day_pnl  if self.mode == "live"  else 0.0,
            paper_pnl=self.day_pnl if self.mode == "paper" else 0.0,
            open_positions=1 if self.open_trade else 0,
        )
        if trade_event:
            kwargs["trade_event"] = trade_event
        if signal:
            kwargs["signal"]  = signal
            kwargs["notable"] = notable
        self._status_callback(**kwargs)

    # ── Pre-market ─────────────────────────────────────────────────────────────

    def _pre_market(self, expiry: date) -> None:
        print("\n  Black Swan — pre-market setup...")
        try:
            self.vix = get_india_vix() or 15.0
        except Exception:
            self.vix = 15.0
        print(f"  India VIX: {self.vix:.1f}  {'[ELEVATED — lower trigger active]' if self.vix >= self.vix_alert else ''}")

        try:
            self.prev_close = get_previous_close(self.instrument)
        except Exception:
            self.prev_close = 0.0
        if self.prev_close:
            print(f"  Prev close ({self.instrument}): {self.prev_close:,.1f}")

        try:
            self.day_open = get_day_open_spot(self.instrument)
        except Exception:
            self.day_open = 0.0
        if self.day_open:
            print(f"  Day open ({self.instrument}):   {self.day_open:,.1f}")
        else:
            print(f"  Day open: unavailable — will use first live tick as fallback ref")

        thr = self.vix_trigger if self.vix >= self.vix_alert else self.intraday_trigger
        print(
            f"\n  Triggers active:\n"
            f"    A) Gap open ≥ {self.gap_trigger*100:.1f}% from prev close ({self.prev_close:,.0f})\n"
            f"    B) Intraday ≥ {thr*100:.1f}% from 9:15 open"
            f"  (VIX {'ELEVATED' if self.vix >= self.vix_alert else 'normal'})\n"
            f"\n  Entry window: {self.entry_start.strftime('%H:%M')}–{self.entry_end.strftime('%H:%M')}"
            f"  |  Target: {self.tgt_mult}×  |  Stop: {int(self.stop_pct*100)}%"
            f"  |  Time stop: {self.time_stop_min} min\n"
            f"  Expiry: {expiry}  |  Budget: Rs.{self.budget:,}\n"
        )

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        today  = date.today()
        expiry = get_nifty_weekly_expiry(today)
        exp_str = expiry.strftime("%Y%m%d")

        print(
            f"\n  ══════════════════════════════════════════════════════\n"
            f"  BLACK SWAN SCALPER  |  {today}  |  NIFTY  |  "
            f"{'LIVE' if self.mode == 'live' else 'PAPER'}\n"
            f"  ══════════════════════════════════════════════════════"
        )

        self._pre_market(expiry)

        thr = self.vix_trigger if self.vix >= self.vix_alert else self.intraday_trigger
        self._update_status(
            signal=(
                f"Pre-mkt ✓  VIX={self.vix:.1f}"
                f"  gap-thr≥{self.gap_trigger*100:.1f}%"
                f"  intraday-thr≥{thr*100:.1f}%"
                f"  prev_close={self.prev_close:,.0f}"
                f"  day_open={self.day_open:,.0f}"
                f"  expiry={expiry}"
            ),
            notable=True,
        )

        try:
            while True:
                now_hm  = self._now_hm()
                close_t = _dt.time(self.close_h, self.close_m)

                # ── Hard close ────────────────────────────────────────────────
                if now_hm >= close_t:
                    if self.open_trade:
                        self._close_trade(self.open_trade, "FORCE_CLOSE", expiry)
                    self._print_eod()
                    return

                # ── Monitor open position ─────────────────────────────────────
                if self.open_trade:
                    spot = get_spot_price(self.instrument)
                    if spot:
                        ltp = self._get_ltp(spot, expiry,
                                            self.open_trade.strike, self.open_trade.option_type)
                        now = self._now()
                        if ltp >= self.open_trade.target_price:
                            self._close_trade(self.open_trade, "TARGET_HIT", expiry)
                        elif ltp <= self.open_trade.stop_price:
                            self._close_trade(self.open_trade, "STOP_LOSS", expiry)
                        elif now >= self.open_trade.time_stop:
                            self._close_trade(self.open_trade, "TIME_STOP", expiry)
                        else:
                            remaining = int((self.open_trade.time_stop - now).total_seconds() / 60)
                            self._update_status(
                                direction=self.open_trade.direction,
                                signal=(
                                    f"POS OPEN  {self.open_trade.direction}"
                                    f"  {self.open_trade.option_type}{self.open_trade.strike}"
                                    f"  LTP=Rs.{ltp:.1f}"
                                    f"  tgt=Rs.{self.open_trade.target_price:.1f}"
                                    f"  stop=Rs.{self.open_trade.stop_price:.1f}"
                                    f"  time_stop_in={remaining}min"
                                ),
                            )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Already fired (trade done or skipped) ─────────────────────
                if self.fired:
                    self._update_status(
                        signal=(
                            f"Done for today — 1-trade-per-day limit"
                            f"  day_pnl=Rs.{self.day_pnl:+,.0f}"
                        )
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Daily stop ────────────────────────────────────────────────
                if self.day_pnl <= -self.day_stop:
                    print(f"\n  [BLACK_SWAN] Daily stop hit: Rs.{self.day_pnl:,.0f}")
                    self._print_eod()
                    return

                # ── Outside entry window ──────────────────────────────────────
                if now_hm < self.entry_start or now_hm > self.entry_end:
                    spot     = get_spot_price(self.instrument) or 0.0
                    gap_pct  = ((spot - self.prev_close) / self.prev_close * 100
                                if self.prev_close and spot else 0.0)
                    move_pct = ((spot - self.day_open) / self.day_open * 100
                                if self.day_open and spot else 0.0)
                    self._update_status(
                        signal=(
                            f"Outside entry window ({self.entry_start.strftime('%H:%M')}–"
                            f"{self.entry_end.strftime('%H:%M')})"
                            f"  spot={spot:,.0f}"
                            f"  gap={gap_pct:+.2f}%"
                            f"  intraday={move_pct:+.2f}%"
                        )
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Poll for trigger ──────────────────────────────────────────
                spot = get_spot_price(self.instrument)
                if not spot:
                    time.sleep(TICK_SECONDS)
                    continue

                if not self.day_open:
                    self.day_open = spot    # fallback if yfinance returned 0

                direction = self._check_trigger(spot)

                if not direction:
                    gap_pct  = ((spot - self.prev_close) / self.prev_close * 100
                                if self.prev_close else 0.0)
                    move_pct = ((spot - self.day_open) / self.day_open * 100
                                if self.day_open else 0.0)
                    thr = self.vix_trigger if self.vix >= self.vix_alert else self.intraday_trigger
                    self._update_status(
                        signal=(
                            f"{self._now().strftime('%H:%M')}"
                            f"  spot={spot:,.0f}"
                            f"  gap={gap_pct:+.2f}% (need {self.gap_trigger*100:.1f}%)"
                            f"  intraday={move_pct:+.2f}% (need {thr*100:.1f}%)"
                            f"  VIX={self.vix:.1f}  → watching"
                        )
                    )
                    print(
                        f"  [BLACK_SWAN] {self._now().strftime('%H:%M')}"
                        f"  spot={spot:,.1f}"
                        f"  gap={gap_pct:+.2f}%"
                        f"  intraday={move_pct:+.2f}%"
                        f"  VIX={self.vix:.1f}  watching...",
                        end="\r",
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Trigger met — calculate entry ─────────────────────────────
                opt_type = "CE" if direction == "BULLISH" else "PE"
                atm      = round_to_strike(spot, self.step)
                strike   = (atm + self.otm_n * self.step if direction == "BULLISH"
                            else atm - self.otm_n * self.step)

                ltp      = self._get_ltp(spot, expiry, strike, opt_type)
                gap_pct  = ((spot - self.prev_close) / self.prev_close * 100
                            if self.prev_close else 0.0)
                move_pct = ((spot - self.day_open) / self.day_open * 100
                            if self.day_open else 0.0)

                print(
                    f"\n  ⚡ [BLACK_SWAN] TRIGGER  {direction}"
                    f"  gap={gap_pct:+.2f}%  intraday={move_pct:+.2f}%  VIX={self.vix:.1f}"
                )

                if ltp < self.min_prem or ltp > self.max_prem:
                    print(
                        f"  [BLACK_SWAN] {opt_type}{strike}  "
                        f"LTP=Rs.{ltp:.2f} outside range [{self.min_prem:.0f}–{self.max_prem:.0f}] — skip"
                    )
                    self._update_status(
                        signal=(
                            f"BLACK_SWAN TRIGGERED {direction} but {opt_type}{strike}"
                            f"  prem=Rs.{ltp:.1f} outside [{self.min_prem:.0f}–{self.max_prem:.0f}] → skip"
                        ),
                        notable=True,
                    )
                    self.fired = True
                    time.sleep(TICK_SECONDS)
                    continue

                entry_price  = ltp * (1 + self.slippage)
                qty          = self._qty(entry_price)
                if qty == 0:
                    print(f"  [BLACK_SWAN] qty=0 (budget Rs.{self.budget:,} too small for Rs.{ltp:.2f}) — skip")
                    self._update_status(
                        signal=f"BLACK_SWAN TRIGGERED {direction} but qty=0 (budget insufficient) → skip",
                        notable=True,
                    )
                    self.fired = True
                    time.sleep(TICK_SECONDS)
                    continue

                target_price = entry_price * self.tgt_mult
                stop_price   = entry_price * (1 - self.stop_pct)
                time_stop_dt = self._now() + timedelta(minutes=self.time_stop_min)

                trade = SwanTrade(
                    instrument=self.instrument,
                    direction=direction,
                    option_type=opt_type,
                    strike=strike,
                    expiry_str=exp_str,
                    exchange="NFO",
                    entry_price=round(entry_price, 2),
                    entry_time=self._now().strftime("%H:%M"),
                    quantity=qty,
                    target_price=round(target_price, 2),
                    stop_price=round(stop_price, 2),
                    time_stop=time_stop_dt,
                    is_paper=(self.mode == "paper"),
                )

                print(
                    f"\n  ▶ [BLACK_SWAN] ENTRY  {direction}  {opt_type}{strike}"
                    f"  qty={qty}  entry=Rs.{entry_price:.1f}"
                    f"  target=Rs.{target_price:.1f} ({self.tgt_mult}×)"
                    f"  stop=Rs.{stop_price:.1f} ({int(self.stop_pct*100)}%)"
                    f"  time_stop={time_stop_dt.strftime('%H:%M')}"
                )

                if self.mode == "live":
                    self._place_order(trade, expiry)
                    if not trade.order_id:
                        print(f"  [BLACK_SWAN] ORDER FAILED — not tracking position.")
                        self._update_status(
                            signal=(
                                f"BLACK_SWAN ORDER FAILED — NOT tracking position."
                                f" Place {opt_type}{strike} manually on Kite if desired."
                            ),
                            notable=True,
                        )
                        self.fired = True
                        time.sleep(TICK_SECONDS)
                        continue
                    order_status = f"ORDER PLACED #{trade.order_id}"
                else:
                    trade.order_id = "PAPER"
                    order_status   = "PAPER trade recorded"

                self._update_status(
                    direction=direction,
                    signal=(
                        f"BLACK_SWAN {direction} {opt_type}{strike}"
                        f"  gap={gap_pct:+.2f}%  intraday={move_pct:+.2f}%"
                        f"  entry=Rs.{entry_price:.1f}  tgt=Rs.{target_price:.1f}"
                        f"  stop=Rs.{stop_price:.1f}  qty={qty}"
                        f"  time_stop={time_stop_dt.strftime('%H:%M')}"
                        f"  {order_status}"
                    ),
                    notable=True,
                )

                self.trade      = trade
                self.open_trade = trade
                self.fired      = True

                self._update_status(
                    direction=direction,
                    trade_event={
                        "event":       "ENTRY",
                        "instrument":  self.instrument,
                        "direction":   direction,
                        "option_type": opt_type,
                        "strike":      strike,
                        "price":       round(entry_price, 2),
                        "quantity":    qty,
                        "pnl":         "",
                        "exit_reason": "",
                        "window":      "BLACK_SWAN",
                    },
                )

                time.sleep(TICK_SECONDS)

        except KeyboardInterrupt:
            print("\n\n  Ctrl+C — closing position...")
            if self.open_trade:
                self._close_trade(self.open_trade, "FORCE_CLOSE", expiry)
            self._print_eod()

    def _print_eod(self) -> None:
        print(f"\n  {'─'*55}")
        print(f"  Black Swan EOD  |  {date.today()}  |  {self.instrument}")
        print(f"  {'─'*55}")
        if not self.trade:
            print("  No trigger fired today.")
        else:
            t    = self.trade
            sign = "+" if t.pnl >= 0 else ""
            print(
                f"  {t.direction} {t.option_type}{t.strike}"
                f"  entry={t.entry_time}  exit={t.exit_time}"
                f"  P&L: {sign}Rs.{t.pnl:,.0f}  ({t.exit_reason})"
            )
        sign = "+" if self.day_pnl >= 0 else ""
        print(f"  {'─'*55}")
        print(f"  Total day P&L: {sign}Rs.{self.day_pnl:,.0f}")
        print(f"  {'─'*55}\n")
