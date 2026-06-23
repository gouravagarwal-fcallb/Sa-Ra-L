"""
Expiry Scalper — Live / Paper Engine
──────────────────────────────────────
Runs ONLY on expiry days (Nifty: Tuesday, Sensex: Thursday).
Three configurable entry windows per day:
  W1  Morning Momentum  09:30-11:30  Premium Rs.25-150  Target 2.5×
  W2  Midday Compression 13:00-14:30  Premium Rs.5-60    Target 3.5×
  W3  End-of-Day Gamma  14:45-15:10  Premium Rs.0.5-25  Target 5×

Fixed Rs.10K budget per trade. Force-exits everything at 15:29.
"""

from __future__ import annotations

import time
import datetime as _dt
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import get_spot_price, get_india_vix
from src.data.gift_nifty import get_gift_nifty_premium
from src.data.market_data import get_dow_jones_change_pct
from src.strategy.direction_engine import DirectionEngine, DirectionInputs
from src.utils.market_calendar import (
    get_day_instrument,
    is_nifty_expiry_day,
    is_sensex_expiry_day,
    get_nifty_weekly_expiry,
    get_sensex_weekly_expiry,
)
from src.utils.helpers import round_to_strike
from src.utils.logger import setup_logger

log = setup_logger("expiry_scalper_live")
IST = timezone(timedelta(hours=5, minutes=30))

TICK_SECONDS = 60    # Poll interval: 1 minute


@dataclass
class ScalperTrade:
    window_id:   str
    instrument:  str
    direction:   str
    option_type: str
    strike:      int
    expiry_str:  str       # YYYYMMDD for broker
    exchange:    str
    entry_price: float
    entry_time:  str
    quantity:    int
    target_price: float
    stop_price:   float
    is_paper:    bool
    order_id:    str = ""
    exit_price:  float = 0.0
    exit_reason: str  = ""
    exit_time:   str  = ""
    pnl:         float = 0.0


class ExpiryScalperLive:

    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc     = strategy_config
        self.broker = broker
        self.mode   = mode
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065)
        )
        self.direction_engine = DirectionEngine(strategy_config)
        self._status_callback = status_callback

        sc_es = strategy_config.get("expiry_scalper", {})
        self.budget      = sc_es.get("trade_budget_rs", 10000)
        self.otm_n       = sc_es.get("otm_strikes", 2)
        self.vol_mult    = sc_es.get("volume_surge_multiplier", 1.3)
        close_str        = sc_es.get("hard_close_time", "15:29")
        self.close_h, self.close_m = int(close_str[:2]), int(close_str[3:])
        self.day_stop    = strategy_config.get("risk", {}).get("daily_loss_limit", 30000)
        self.slippage    = strategy_config.get("backtest", {}).get("slippage_pct", 0.2) / 100

        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.nifty_lot   = inst_n.get("lot_size", 75)
        self.nifty_step  = inst_n.get("strike_step", 50)
        inst_s = strategy_config.get("instruments", {}).get("sensex", {})
        self.sensex_lot  = inst_s.get("lot_size", 20)
        self.sensex_step = inst_s.get("strike_step", 100)

        # Parse windows
        raw_windows = sc_es.get("windows")
        if not raw_windows:
            raw_windows = [{
                "id": "W3", "name": "End-of-Day Gamma",
                "start": sc_es.get("entry_after_time", "14:45"),
                "end":   "15:10",
                "momentum_threshold_pct": sc_es.get("momentum_threshold_pct", 0.25),
                "max_premium_rs": sc_es.get("max_premium_rs", 25.0),
                "min_premium_rs": sc_es.get("min_premium_rs", 0.5),
                "target_multiplier": sc_es.get("target_multiplier", 5.0),
                "stop_loss_pct": sc_es.get("stop_loss_pct", 50),
                "require_score_direction": False,
            }]

        def _hm(s):
            return _dt.time(int(s[:2]), int(s[3:]))

        self.windows = []
        for w in raw_windows:
            self.windows.append({
                "id":       w["id"],
                "name":     w.get("name", w["id"]),
                "start":    _hm(w["start"]),
                "end":      _hm(w["end"]),
                "mom_thr":  w.get("momentum_threshold_pct", 0.25) / 100,
                "max_prem": w.get("max_premium_rs", 25.0),
                "min_prem": w.get("min_premium_rs", 0.5),
                "tgt_mult": w.get("target_multiplier", 5.0),
                "stop_pct": w.get("stop_loss_pct", 50) / 100,
                "req_dir":  w.get("require_score_direction", False),
                "otm_n":    w.get("otm_strikes", self.otm_n),  # per-window OTM offset
                "fired":    False,
                "ref_spot": None,
                "recent_volumes": [],
            })

        self.trades:      list[ScalperTrade] = []
        self.open_trade:  Optional[ScalperTrade] = None
        self.day_pnl:     float = 0.0
        self.pre_score:   int   = 0
        self.instrument:  str   = ""
        self.expiry:      date  = date.today()
        self.vix:         float = 15.0

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return datetime.now(IST)

    def _now_hm(self) -> _dt.time:
        n = self._now()
        return _dt.time(n.hour, n.minute)

    def _t_years_to_close(self) -> float:
        now = self._now()
        close_today = now.replace(hour=self.close_h, minute=self.close_m,
                                  second=0, microsecond=0)
        secs = max((close_today - now).total_seconds(), 60)
        hours = secs / 3600
        return hours / (6.25 * 252)

    def _get_ltp(self, spot: float, strike: int, opt_type: str) -> float:
        T = self._t_years_to_close()
        if self.mode == "live":
            exp_str = self.expiry.strftime("%Y%m%d")
            exchange = "NFO" if self.instrument == "NIFTY" else "BFO"
            try:
                return self.broker.get_ltp(
                    self.instrument, exchange, strike, opt_type, exp_str
                )
            except Exception:
                pass  # Fall through to pricer
        result = self.pricer.price_option(
            spot=spot, strike=strike, opt_type=opt_type,
            T_years=T, vix=self.vix,
        )
        return result.get("ltp", 0.0) if isinstance(result, dict) else getattr(result, "ltp", 0.0)

    def _qty(self, ltp: float) -> int:
        lot = self.nifty_lot if self.instrument == "NIFTY" else self.sensex_lot
        if ltp < 0.5:
            return 0
        return max(1, int(self.budget / (ltp * lot))) * lot

    def _resolve_tradingsymbol(self, strike: int, opt_type: str) -> tuple[str, str]:
        """Return (tradingsymbol, exchange) for a live order. Falls back to instrument name."""
        exchange = "NFO" if self.instrument == "NIFTY" else "BFO"
        if self.mode == "live":
            from src.broker.kite_broker import KiteBroker
            if isinstance(self.broker, KiteBroker):
                try:
                    return self.broker.get_tradingsymbol(
                        self.instrument, self.expiry, strike, opt_type
                    )
                except Exception as e:
                    log.warning(f"get_tradingsymbol failed ({e}) — using fallback symbol")
        return self.instrument, exchange

    def _place_order(self, trade: ScalperTrade) -> None:
        symbol, exchange = self._resolve_tradingsymbol(trade.strike, trade.option_type)
        order = Order(
            symbol=symbol,
            exchange=exchange,
            option_type=trade.option_type,
            strike=trade.strike,
            expiry=trade.expiry_str,
            transaction="BUY",
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
            log.error(f"Order failed: {e}")

    def _close_trade(self, trade: ScalperTrade, reason: str) -> None:
        spot    = get_spot_price(self.instrument)
        step    = self.nifty_step if self.instrument == "NIFTY" else self.sensex_step
        ltp     = self._get_ltp(spot or trade.entry_price, trade.strike, trade.option_type)
        exit_px = ltp * (1 - self.slippage)
        trade.exit_price  = exit_px
        trade.exit_reason = reason
        trade.exit_time   = self._now().strftime("%H:%M")
        trade.pnl         = (exit_px - trade.entry_price) * trade.quantity
        self.day_pnl     += trade.pnl
        self.open_trade   = None

        # Place sell order
        symbol, exchange = self._resolve_tradingsymbol(trade.strike, trade.option_type)
        order = Order(
            symbol=symbol,
            exchange=exchange,
            option_type=trade.option_type,
            strike=trade.strike,
            expiry=trade.expiry_str,
            transaction="SELL",
            quantity=trade.quantity,
        )
        try:
            oid = self.broker.place_order(order)
            log.info(
                f"[LIVE] SELL {symbol} qty={trade.quantity} "
                f"@ Rs.{exit_px:.1f} | order_id={oid} | P&L={sign}Rs.{trade.pnl:,.0f}"
            )
        except Exception as e:
            log.error(f"Exit order failed: {e}")

        sign = "+" if trade.pnl >= 0 else ""
        print(
            f"\n  [{trade.window_id}] {reason}  "
            f"{trade.direction} {trade.option_type}{trade.strike}  "
            f"entry=Rs.{trade.entry_price:.1f} → exit=Rs.{exit_px:.1f}  "
            f"P&L: {sign}Rs.{trade.pnl:,.0f}"
        )
        self._update_status(trade_event={
            "event":       reason,
            "instrument":  trade.instrument,
            "direction":   trade.direction,
            "option_type": trade.option_type,
            "strike":      trade.strike,
            "price":       round(exit_px, 2),
            "quantity":    trade.quantity,
            "pnl":         round(trade.pnl, 2),
            "exit_reason": reason,
            "window":      trade.window_id,
        })

    def _update_status(self, trade_event: dict = None,
                       signal: str = None, notable: bool = False) -> None:
        if not self._status_callback:
            return
        kwargs = dict(
            direction="EXPIRY",
            score=self.pre_score,
            budget=self.budget * len(self.windows),
            real_pnl=self.day_pnl if self.mode == "live" else 0.0,
            paper_pnl=self.day_pnl if self.mode == "paper" else 0.0,
            open_positions=1 if self.open_trade else 0,
        )
        if trade_event:
            kwargs["trade_event"] = trade_event
        if signal:
            kwargs["signal"]  = signal
            kwargs["notable"] = notable
        self._status_callback(**kwargs)

    # ── Pre-market setup ──────────────────────────────────────────────────────

    def _pre_market(self) -> None:
        print("\n  Expiry Scalper — pre-market analysis...")
        try:
            result = self.direction_engine.evaluate_from_live_data()
            self.pre_score = result.score
            print(f"  Pre-market score: {self.pre_score:+d} → {result.direction.value}")
        except Exception as e:
            log.warning(f"Pre-market eval failed: {e}. Using score=0 (neutral).")
            self.pre_score = 0

        try:
            self.vix = get_india_vix() or 15.0
        except Exception:
            self.vix = 15.0
        print(f"  India VIX: {self.vix:.1f}")

        for w in self.windows:
            w["fired"]  = False
            w["ref_spot"] = None
            w["recent_volumes"] = []

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        today      = date.today()
        instrument = get_day_instrument(today)

        if not instrument:
            print(f"\n  Expiry Scalper: {today} is not a trading day — skipping.")
            return

        nifty_exp   = is_nifty_expiry_day(today)
        sensex_exp  = is_sensex_expiry_day(today)
        is_expiry   = (instrument == "NIFTY" and nifty_exp) or \
                      (instrument == "SENSEX" and sensex_exp)

        if not is_expiry:
            print(
                f"\n  Expiry Scalper: {today} is NOT an expiry day for {instrument}.\n"
                f"  Next Nifty expiry:  {get_nifty_weekly_expiry(today)}\n"
                f"  Next Sensex expiry: {get_sensex_weekly_expiry(today)}\n"
                f"  Strategy will idle until the next expiry."
            )
            # Wait and check again tomorrow
            return

        self.instrument = instrument
        self.expiry = (get_nifty_weekly_expiry(today)
                       if instrument == "NIFTY" else get_sensex_weekly_expiry(today))
        lot = self.nifty_lot if instrument == "NIFTY" else self.sensex_lot
        step = self.nifty_step if instrument == "NIFTY" else self.sensex_step

        print(
            f"\n  ══════════════════════════════════════════════════════\n"
            f"  EXPIRY SCALPER  |  {today}  |  {instrument}  |  "
            f"{'LIVE' if self.mode == 'live' else 'PAPER'}\n"
            f"  Budget: Rs.{self.budget:,} per trade  |  "
            f"Windows: {len(self.windows)}  |  "
            f"Daily stop: Rs.{self.day_stop:,}\n"
            f"  ══════════════════════════════════════════════════════"
        )

        self._pre_market()
        self._update_status(
            signal=(
                f"Pre-mkt ✓  Score={self.pre_score:+d}  VIX={self.vix:.1f}"
                f"  Budget=Rs.{self.budget:,}/trade"
                f"  {'[score-filter ON]' if any(w['req_dir'] for w in self.windows) else ''}"
            ).strip(),
            notable=True,
        )

        print(f"\n  Windows active today:")
        for w in self.windows:
            dir_filter = " [score-aligned]" if w["req_dir"] else ""
            print(
                f"    {w['id']} {w['name']:25s}  "
                f"{w['start'].strftime('%H:%M')}–{w['end'].strftime('%H:%M')}  "
                f"prem Rs.{w['min_prem']:.0f}–{w['max_prem']:.0f}  "
                f"target {w['tgt_mult']}×  stop {int(w['stop_pct']*100)}%"
                f"{dir_filter}"
            )
        print(f"\n  Press Ctrl+C to abort.\n")

        exp_str = self.expiry.strftime("%Y%m%d")

        try:
            while True:
                now_hm = self._now_hm()
                close_t = _dt.time(self.close_h, self.close_m)

                # ── Hard close ───────────────────────────────────────────
                if now_hm >= close_t:
                    if self.open_trade:
                        self._close_trade(self.open_trade, "FORCE_CLOSE")
                    self._print_eod()
                    return

                # ── Monitor open position ────────────────────────────────
                if self.open_trade:
                    spot = get_spot_price(instrument)
                    if spot:
                        ltp = self._get_ltp(spot, self.open_trade.strike,
                                            self.open_trade.option_type)
                        if ltp >= self.open_trade.target_price:
                            self._close_trade(self.open_trade, "TARGET_HIT")
                        elif ltp <= self.open_trade.stop_price:
                            self._close_trade(self.open_trade, "STOP_LOSS")
                        else:
                            self._update_status(
                                signal=(
                                    f"POS OPEN  {self.open_trade.option_type}"
                                    f"{self.open_trade.strike}  LTP=Rs.{ltp:.1f}"
                                    f"  tgt=Rs.{self.open_trade.target_price:.1f}"
                                    f"  stop=Rs.{self.open_trade.stop_price:.1f}"
                                )
                            )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Daily stop check ─────────────────────────────────────
                if self.day_pnl <= -self.day_stop:
                    print(f"\n  Daily stop hit: Rs.{self.day_pnl:,.0f}. No more entries today.")
                    self._print_eod()
                    return

                # ── Scan windows for entry opportunity ───────────────────
                for win in self.windows:
                    if win["fired"]:
                        continue
                    if now_hm < win["start"] or now_hm > win["end"]:
                        continue

                    spot = get_spot_price(instrument)
                    if not spot:
                        continue

                    # Set reference spot when window first opens
                    if win["ref_spot"] is None:
                        win["ref_spot"] = spot
                        print(
                            f"\n  [{win['id']}] {win['name']} opened — "
                            f"ref spot: {spot:,.1f}  "
                            f"({self._now().strftime('%H:%M')} IST)"
                        )
                        win["recent_volumes"].append(1)  # placeholder; no volume from spot API
                        self._update_status(
                            signal=(
                                f"{win['id']} opened  ref={spot:,.0f}"
                                f"  need {win['mom_thr']*100:.2f}% move"
                                f"  prem Rs.{win['min_prem']:.0f}–{win['max_prem']:.0f}"
                                f"  target {win['tgt_mult']}×"
                            ),
                            notable=True,
                        )
                        continue

                    move = (spot - win["ref_spot"]) / win["ref_spot"]

                    # Volume check is skipped when using spot-price-only API
                    vol_ok = True

                    if move >= win["mom_thr"]:
                        direction = "BULLISH"
                        opt_type  = "CE"
                        atm       = round_to_strike(spot, step)
                        strike    = atm + win["otm_n"] * step
                    elif move <= -win["mom_thr"]:
                        direction = "BEARISH"
                        opt_type  = "PE"
                        atm       = round_to_strike(spot, step)
                        strike    = atm - win["otm_n"] * step
                    else:
                        self._update_status(
                            signal=(
                                f"{win['id']} {self._now().strftime('%H:%M')}"
                                f"  spot={spot:,.0f}  move={move*100:+.2f}%"
                                f"  need {win['mom_thr']*100:.2f}%  → waiting"
                            )
                        )
                        print(
                            f"  [{win['id']}] {self._now().strftime('%H:%M')}  "
                            f"spot={spot:,.1f}  move={move*100:+.2f}%  "
                            f"threshold={win['mom_thr']*100:.2f}%  waiting...",
                            end="\r",
                        )
                        continue

                    # W1 direction filter
                    if win["req_dir"] and abs(self.pre_score) >= 3:
                        score_dir = "BULLISH" if self.pre_score > 0 else "BEARISH"
                        if direction != score_dir:
                            print(
                                f"\n  [{win['id']}] Breakout {direction} opposes "
                                f"pre-market score {self.pre_score:+d} ({score_dir}) — skip"
                            )
                            self._update_status(
                                signal=(
                                    f"{win['id']} breakout {direction}"
                                    f" opposes pre-score {self.pre_score:+d} ({score_dir}) → skip"
                                ),
                                notable=True,
                            )
                            win["fired"] = True  # Don't re-scan this window
                            continue

                    ltp = self._get_ltp(spot, strike, opt_type)

                    if ltp < win["min_prem"] or ltp > win["max_prem"]:
                        print(
                            f"\n  [{win['id']}] {direction} {opt_type}{strike}  "
                            f"LTP=Rs.{ltp:.2f} outside range "
                            f"[{win['min_prem']}, {win['max_prem']}] — skip"
                        )
                        self._update_status(
                            signal=(
                                f"{win['id']} {direction} {opt_type}{strike}"
                                f"  prem=Rs.{ltp:.1f} outside"
                                f" [{win['min_prem']:.0f}–{win['max_prem']:.0f}] → skip"
                            ),
                            notable=True,
                        )
                        win["fired"] = True
                        continue

                    entry_price  = ltp * (1 + self.slippage)
                    qty          = self._qty(entry_price)
                    if qty == 0:
                        print(f"\n  [{win['id']}] Quantity = 0 (budget too small for LTP Rs.{ltp:.2f}) — skip")
                        self._update_status(
                            signal=f"{win['id']} {opt_type}{strike}  LTP=Rs.{ltp:.1f} → qty=0 (budget insufficient) → skip",
                            notable=True,
                        )
                        win["fired"] = True
                        continue

                    target_price = entry_price * win["tgt_mult"]
                    stop_price   = entry_price * (1 - win["stop_pct"])
                    self._update_status(
                        signal=(
                            f"{win['id']} {direction} {opt_type}{strike}"
                            f"  entry=Rs.{entry_price:.1f}  tgt=Rs.{target_price:.1f}"
                            f"  stop=Rs.{stop_price:.1f}  qty={qty} → queuing entry"
                        ),
                        notable=True,
                    )

                    trade = ScalperTrade(
                        window_id=win["id"],
                        instrument=instrument,
                        direction=direction,
                        option_type=opt_type,
                        strike=strike,
                        expiry_str=exp_str,
                        exchange="NFO" if instrument == "NIFTY" else "BFO",
                        entry_price=round(entry_price, 2),
                        entry_time=self._now().strftime("%H:%M"),
                        quantity=qty,
                        target_price=round(target_price, 2),
                        stop_price=round(stop_price, 2),
                        is_paper=(self.mode == "paper"),
                    )

                    print(
                        f"\n  ▶ [{win['id']}] ENTRY  {direction}  {opt_type}{strike}  "
                        f"qty={qty}  entry=Rs.{entry_price:.1f}  "
                        f"target=Rs.{target_price:.1f} ({win['tgt_mult']}×)  "
                        f"stop=Rs.{stop_price:.1f} ({int(win['stop_pct']*100)}%)  "
                        f"budget=Rs.{self.budget:,}"
                    )

                    if self.mode == "live":
                        self._place_order(trade)
                    else:
                        trade.order_id = "PAPER"

                    self.trades.append(trade)
                    self.open_trade = trade
                    win["fired"] = True
                    self._update_status(trade_event={
                        "event":       "ENTRY",
                        "instrument":  instrument,
                        "direction":   direction,
                        "option_type": opt_type,
                        "strike":      strike,
                        "price":       round(entry_price, 2),
                        "quantity":    qty,
                        "pnl":         "",
                        "exit_reason": "",
                        "window":      win["id"],
                    })
                    break  # Only one entry at a time

                time.sleep(TICK_SECONDS)

        except KeyboardInterrupt:
            print("\n\n  Ctrl+C — closing all positions...")
            if self.open_trade:
                self._close_trade(self.open_trade, "FORCE_CLOSE")
            self._print_eod()

    def _print_eod(self) -> None:
        print(f"\n  {'─'*55}")
        print(f"  Expiry Scalper EOD  |  {date.today()}  |  {self.instrument}")
        print(f"  {'─'*55}")
        if not self.trades:
            print("  No trades fired today.")
        for t in self.trades:
            sign = "+" if t.pnl >= 0 else ""
            print(
                f"  [{t.window_id}] {t.direction} {t.option_type}{t.strike}  "
                f"entry={t.entry_time}  exit={t.exit_time}  "
                f"P&L: {sign}Rs.{t.pnl:,.0f}  ({t.exit_reason})"
            )
        sign = "+" if self.day_pnl >= 0 else ""
        print(f"  {'─'*55}")
        print(f"  Total day P&L: {sign}Rs.{self.day_pnl:,.0f}")
        print(f"  {'─'*55}\n")
