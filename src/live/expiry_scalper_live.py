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
from src.data.market_data import get_spot_price, get_india_vix, get_day_open_spot
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
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("expiry_scalper_live", "EXPIRY_SCALPER_v1")
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
    # Partial-book + trailing-stop state (used only when those flags are ON;
    # defaults keep single-shot all-or-nothing behaviour identical to before).
    qty_remaining: int = 0       # open qty still running (set = quantity at entry)
    partial_done:  bool = False  # a partial profit has been booked
    peak_ltp:      float = 0.0   # highest premium seen (drives the trailing stop)
    realized_pnl:  float = 0.0   # P&L already booked from partial exits


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

        # ── Polish flags (ALL default OFF → byte-identical to the original engine
        #    until the operator opts in via config AND validates on a backtest) ──
        # #1 Breakout confirmation: require the move to PERSIST for N consecutive
        #    scans before entering (filters one-tick spikes; no volume data needed).
        self.confirm_breakout   = bool(sc_es.get("confirm_breakout", False))
        self.confirmation_ticks = max(1, int(sc_es.get("confirmation_ticks", 2)))
        # #2 Partial-book + trailing stop: book a slice when the option pops, move the
        #    remainder's stop to breakeven, and (optionally) trail it below the peak —
        #    turns "ran up then round-tripped to a full stop" losers into small wins
        #    while keeping a runner for the big tail. Payoff-preserving, not win-chasing.
        self.partial_book          = bool(sc_es.get("partial_book", False))
        self.partial_trigger_mult  = float(sc_es.get("partial_trigger_mult", 1.6))
        self.partial_fraction      = min(max(float(sc_es.get("partial_fraction", 0.5)), 0.1), 0.9)
        self.move_stop_to_breakeven = bool(sc_es.get("move_stop_to_breakeven", True))
        self.trail_stop            = bool(sc_es.get("trail_stop", False))
        self.trail_pct             = min(max(float(sc_es.get("trail_pct", 0.30)), 0.05), 0.90)

        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.nifty_lot   = inst_n.get("lot_size", 65)
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
                # Per-window polish overrides — fall back to the strategy-level flag
                # (so e.g. W1/W3 can trail while W2 keeps the fixed stop). Default OFF.
                "confirm":  bool(w.get("confirm_breakout", self.confirm_breakout)),
                "conf_n":   max(1, int(w.get("confirmation_ticks", self.confirmation_ticks))),
                "pbook":    bool(w.get("partial_book", self.partial_book)),
                "ptrig":    float(w.get("partial_trigger_mult", self.partial_trigger_mult)),
                "pfrac":    min(max(float(w.get("partial_fraction", self.partial_fraction)), 0.1), 0.9),
                "be":       bool(w.get("move_stop_to_breakeven", self.move_stop_to_breakeven)),
                "trail":    bool(w.get("trail_stop", self.trail_stop)),
                "tpct":     min(max(float(w.get("trail_pct", self.trail_pct)), 0.05), 0.90),
                "fired":    False,
                "ref_spot": None,
                "recent_volumes": [],
            })
        self._open_win: dict = {}   # window dict backing the currently-open trade

        self.trades:          list[ScalperTrade] = []
        self.open_trade:      Optional[ScalperTrade] = None
        self.day_pnl:         float = 0.0
        self.pre_score:       int   = 0
        self.instrument:      str   = ""
        self.expiry:          date  = date.today()
        self.vix:             float = 15.0
        self.day_open_spot:   float = 0.0   # 9:15 open — shared ref for all windows

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
        # Price at the REAL market quote in BOTH paper and live (PaperBroker serves
        # read-only Kite quotes). The model below is only an offline fallback — it
        # badly overprices cheap OTM/expiry options, so it must never be the primary
        # source when a live quote is available.
        try:
            ts, exchange = self.broker.get_tradingsymbol(self.instrument, self.expiry, strike, opt_type)
            px = self.broker.get_ltp(ts, exchange, strike, opt_type, self.expiry.strftime("%Y%m%d"))
            if px and px > 0:
                return px
        except Exception:
            pass  # fall through to the model
        # OptionPricer exposes price(spot, strike, vix, T_hours, option_type) and
        # returns a PricedOption (.price). T here is in years → convert to calendar
        # hours. (Was price_option(...) with wrong kwargs/return — it crash-looped
        # every pricing cycle with "OptionPricer has no attribute 'price_option'".)
        T = self._t_years_to_close()
        result = self.pricer.price(spot, strike, self.vix, T * 365 * 24, opt_type)
        return result.price

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
        # Book only the REMAINDER (a partial may already have sold part of the size).
        # When no partial happened, qty_remaining == quantity → identical to before.
        close_qty = trade.qty_remaining or trade.quantity
        leg_pnl   = (exit_px - trade.entry_price) * close_qty
        trade.exit_price  = exit_px
        trade.exit_reason = reason
        trade.exit_time   = self._now().strftime("%H:%M")
        trade.pnl         = trade.realized_pnl + leg_pnl   # full round-trip for the record
        trade.qty_remaining = 0
        self.day_pnl     += leg_pnl                        # partial already added at book-time
        self.open_trade   = None
        sign = "+" if trade.pnl >= 0 else ""

        # Place sell order
        symbol, exchange = self._resolve_tradingsymbol(trade.strike, trade.option_type)
        order = Order(
            symbol=symbol,
            exchange=exchange,
            option_type=trade.option_type,
            strike=trade.strike,
            expiry=trade.expiry_str,
            transaction="SELL",
            quantity=close_qty,
        )
        try:
            oid = self.broker.place_order(order)
            log.info(
                f"[LIVE] SELL {symbol} qty={trade.quantity} "
                f"@ Rs.{exit_px:.1f} | order_id={oid} | P&L={sign}Rs.{trade.pnl:,.0f}"
            )
        except Exception as e:
            log.error(f"Exit order failed: {e}")
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

    # ── Polish helpers (pure decision logic → unit-testable, no I/O) ───────────
    def _should_fire(self, win: dict, direction: str) -> bool:
        """#1 Breakout-confirmation gate. OFF → always True (original behaviour:
        fire on the first qualifying tick). ON → the SAME-direction breakout must
        persist for `confirmation_ticks` consecutive scans before we enter, which
        filters one-tick spikes/wicks without needing option-volume data. Mutates
        the window's confirm counters."""
        if not win.get("confirm", self.confirm_breakout):
            return True
        if win.get("_pending_dir") == direction:
            win["_confirm_count"] = win.get("_confirm_count", 0) + 1
        else:
            win["_pending_dir"] = direction
            win["_confirm_count"] = 1
        return win["_confirm_count"] >= win.get("conf_n", self.confirmation_ticks)

    def _reset_confirm(self, win: dict) -> None:
        """Breakout fell back below threshold — the persistence count restarts."""
        win["_pending_dir"] = None
        win["_confirm_count"] = 0

    def _exit_action(self, trade: ScalperTrade, ltp: float) -> str:
        """#2 Decide what to do with an open position at this premium. Mutates
        trade.peak_ltp and (when trailing) trade.stop_price. Returns one of:
        'TARGET_HIT' | 'PARTIAL_BOOK' | 'TRAIL_STOP' | 'STOP_LOSS' | 'HOLD'.
        With both flags OFF this reduces EXACTLY to the original target/stop check."""
        w = self._open_win or {}
        pbook = w.get("pbook", self.partial_book)
        ptrig = w.get("ptrig", self.partial_trigger_mult)
        trail_on = w.get("trail", self.trail_stop)
        tpct  = w.get("tpct", self.trail_pct)
        trade.peak_ltp = max(trade.peak_ltp, ltp)
        # Full target on the remaining qty always wins.
        if ltp >= trade.target_price:
            return "TARGET_HIT"
        # Book a partial once, when the runner has popped enough.
        if (pbook and not trade.partial_done
                and ltp >= trade.entry_price * ptrig):
            return "PARTIAL_BOOK"
        # Trail the remainder's stop up under the peak (after a partial, or when
        # trailing is used on its own without partial-booking).
        if trail_on and (trade.partial_done or not pbook):
            trail = round(trade.peak_ltp * (1 - tpct), 2)
            if trail > trade.stop_price:
                trade.stop_price = trail
        # Stop check uses the possibly-raised stop.
        if ltp <= trade.stop_price:
            # A stop at/above entry means we're protecting a gain → call it a trail
            # (a scratch/small win), not a loss.
            return "TRAIL_STOP" if trade.stop_price >= trade.entry_price else "STOP_LOSS"
        return "HOLD"

    def _partial_close(self, trade: ScalperTrade, fraction: float) -> None:
        """Book `fraction` of the CURRENTLY-open qty, keep the rest running. Books
        in whole lots; no-ops if the slice would be < 1 lot or the whole position."""
        lot = self.nifty_lot if self.instrument == "NIFTY" else self.sensex_lot
        book_qty = int((trade.qty_remaining * fraction) / lot) * lot
        if book_qty < lot or book_qty >= trade.qty_remaining:
            return
        spot    = get_spot_price(self.instrument)
        ltp     = self._get_ltp(spot or trade.entry_price, trade.strike, trade.option_type)
        exit_px = ltp * (1 - self.slippage)
        pnl     = (exit_px - trade.entry_price) * book_qty
        trade.qty_remaining -= book_qty
        trade.realized_pnl  += pnl
        trade.partial_done   = True
        self.day_pnl        += pnl
        if self.mode == "live":
            symbol, exchange = self._resolve_tradingsymbol(trade.strike, trade.option_type)
            try:
                self.broker.place_order(Order(
                    symbol=symbol, exchange=exchange, option_type=trade.option_type,
                    strike=trade.strike, expiry=trade.expiry_str,
                    transaction="SELL", quantity=book_qty))
            except Exception as e:
                log.error(f"Partial exit order failed: {e}")
        sign = "+" if pnl >= 0 else ""
        print(f"\n  [{trade.window_id}] PARTIAL_BOOK  sold {book_qty} of "
              f"{trade.qty_remaining + book_qty}  @ Rs.{exit_px:.1f}  "
              f"P&L {sign}Rs.{pnl:,.0f}  (runner: {trade.qty_remaining} left)")
        self._update_status(trade_event={
            "event": "PARTIAL_BOOK", "instrument": trade.instrument,
            "direction": trade.direction, "option_type": trade.option_type,
            "strike": trade.strike, "price": round(exit_px, 2), "quantity": book_qty,
            "pnl": round(pnl, 2), "exit_reason": "PARTIAL_BOOK", "window": trade.window_id,
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

        # Anchor all windows to the day's 9:15 open so restarting mid-day
        # never resets the momentum reference.
        try:
            self.day_open_spot = get_day_open_spot(self.instrument)
        except Exception:
            self.day_open_spot = 0.0
        if self.day_open_spot:
            print(f"  Day open ({self.instrument}): {self.day_open_spot:,.1f}")
        else:
            print(f"  Day open: unavailable — will use first window tick as fallback ref")

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
            next_nifty  = get_nifty_weekly_expiry(today)
            next_sensex = get_sensex_weekly_expiry(today)
            print(
                f"\n  Expiry Scalper: {today} is NOT an expiry day for {instrument}.\n"
                f"  Next Nifty expiry:  {next_nifty}\n"
                f"  Next Sensex expiry: {next_sensex}\n"
                f"  Running in SHADOW — watching windows, no orders.\n"
            )
            self._update_status(
                signal=(
                    f"Not expiry day — SHADOW  "
                    f"next Nifty={next_nifty}  next Sensex={next_sensex}"
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
                        time.sleep(60)
                        continue
                    try:
                        spot = get_spot_price(instrument)
                        vix  = get_india_vix() or 15.0
                        # Determine active window if any
                        active_w = None
                        for w in self.windows:
                            if w["start"] <= now_hm < w["end"]:
                                active_w = w
                                break
                        # Compute ATM strike for context (round_to_strike imported at module level)
                        step = self.nifty_step if instrument == "NIFTY" else self.sensex_step
                        atm  = round_to_strike(spot, step) if spot else 0
                        if active_w:
                            window_str = (
                                f"  WIN={active_w['id']}[{active_w['start'].strftime('%H:%M')}"
                                f"–{active_w['end'].strftime('%H:%M')}]"
                                f"  prem=Rs.{active_w['min_prem']:.0f}–{active_w['max_prem']:.0f}"
                                f"  tgt={active_w['tgt_mult']}×"
                            )
                        else:
                            next_w = next(
                                (w for w in self.windows if w["start"] > now_hm), None
                            )
                            window_str = (
                                f"  next_win={next_w['id']}@{next_w['start'].strftime('%H:%M')}"
                                if next_w else "  no more windows today"
                            )
                        self._update_status(
                            signal=(
                                f"SHADOW {now_hm.strftime('%H:%M')} | "
                                f"{instrument}={spot:,.0f}  VIX={vix:.1f}  ATM={atm}"
                                f"{window_str}"
                                f"  (not expiry — watching {nxt_n}/{nxt_s})"
                            ),
                            notable=True,
                        )
                    except Exception:
                        pass
                    time.sleep(60)
            except KeyboardInterrupt:
                pass
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
                if getattr(self, "_stop_event", None) is not None and self._stop_event.is_set():
                    break
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
                    t = self.open_trade
                    spot = get_spot_price(instrument)
                    if spot:
                        ltp = self._get_ltp(spot, t.strike, t.option_type)
                        action = self._exit_action(t, ltp)
                        if action == "PARTIAL_BOOK":
                            self._partial_close(t, self._open_win.get("pfrac", self.partial_fraction))
                            if self._open_win.get("be", self.move_stop_to_breakeven) and t.stop_price < t.entry_price:
                                t.stop_price = round(t.entry_price, 2)
                        elif action in ("TARGET_HIT", "STOP_LOSS", "TRAIL_STOP"):
                            self._close_trade(t, action)
                        else:
                            self._update_status(
                                signal=(
                                    f"POS OPEN  {t.option_type}{t.strike}  LTP=Rs.{ltp:.1f}"
                                    f"  tgt=Rs.{t.target_price:.1f}  stop=Rs.{t.stop_price:.1f}"
                                    + (f"  runner={t.qty_remaining}" if t.partial_done else "")
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

                    # Set reference spot when window first opens.
                    # Always use the 9:15 day-open so restarting mid-day
                    # doesn't lose the day's momentum context.
                    if win["ref_spot"] is None:
                        ref = self.day_open_spot or spot
                        ref_src = "day open" if self.day_open_spot else "current (day open unavailable)"
                        win["ref_spot"] = ref
                        current_move = (spot - ref) / ref * 100 if ref else 0.0
                        print(
                            f"\n  [{win['id']}] {win['name']} opened — "
                            f"ref={ref:,.1f} ({ref_src})  "
                            f"current move={current_move:+.2f}%  "
                            f"need ±{win['mom_thr']*100:.2f}%  "
                            f"({self._now().strftime('%H:%M')} IST)"
                        )
                        win["recent_volumes"].append(1)
                        self._update_status(
                            signal=(
                                f"{win['id']} opened  ref={ref:,.0f} ({ref_src})"
                                f"  now={spot:,.0f} ({current_move:+.2f}%)"
                                f"  need ±{win['mom_thr']*100:.2f}%"
                                f"  prem Rs.{win['min_prem']:.0f}–{win['max_prem']:.0f}"
                                f"  target {win['tgt_mult']}×"
                            ),
                            notable=True,
                        )
                        # If already past threshold at window open (e.g. late start),
                        # don't skip — fall through to the move check on next tick.
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
                        self._reset_confirm(win)  # breakout faded — restart persistence count
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

                    # #1 Breakout-confirmation gate — require persistence before entry.
                    # (No-op when confirm_breakout is off.) The window is NOT marked
                    # fired, so it keeps re-scanning until the move confirms or fades.
                    if not self._should_fire(win, direction):
                        self._update_status(signal=(
                            f"{win['id']} {direction} breakout unconfirmed "
                            f"({win.get('_confirm_count', 0)}/{self.confirmation_ticks}) "
                            f"— waiting for persistence"))
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
                        if not trade.order_id:
                            # BUY rejected by broker — do NOT track a phantom position
                            print(
                                f"\n  [{win['id']}] ORDER FAILED — position NOT tracked. "
                                f"Place {opt_type}{strike} manually on Kite if desired."
                            )
                            self._update_status(
                                signal=(
                                    f"{win['id']} ORDER FAILED — NOT tracking position."
                                    f" Place {opt_type}{strike} manually on Kite if desired."
                                ),
                                notable=True,
                            )
                            win["fired"] = True
                            break
                        order_status = f"ORDER PLACED #{trade.order_id}"
                    else:
                        trade.order_id = "PAPER"
                        order_status = "PAPER trade recorded"

                    self._update_status(
                        signal=(
                            f"{win['id']} {direction} {opt_type}{strike}"
                            f"  entry=Rs.{entry_price:.1f}  tgt=Rs.{target_price:.1f}"
                            f"  stop=Rs.{stop_price:.1f}  qty={qty}"
                            f"  {order_status}"
                        ),
                        notable=True,
                    )

                    trade.qty_remaining = qty     # full size runs until partial/close
                    trade.peak_ltp      = entry_price
                    self.trades.append(trade)
                    self.open_trade = trade
                    self._open_win  = win          # per-window flags for exit management
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

                # ── Between-window heartbeat ─────────────────────────────
                # If no window matched this tick, emit a status so the
                # dashboard doesn't go silent between windows.
                active_wins = [w for w in self.windows
                               if not w["fired"] and w["start"] <= now_hm <= w["end"]]
                if not active_wins:
                    next_wins = [w for w in self.windows
                                 if not w["fired"] and now_hm < w["start"]]
                    if next_wins:
                        nw = next_wins[0]
                        spot = get_spot_price(instrument) or 0.0
                        day_move = ((spot - self.day_open_spot) / self.day_open_spot * 100
                                    if self.day_open_spot and spot else 0.0)
                        self._update_status(
                            signal=(
                                f"Between windows — waiting for {nw['id']} {nw['name']}"
                                f" @ {nw['start'].strftime('%H:%M')}"
                                f"  spot={spot:,.0f}  day={day_move:+.2f}%"
                            )
                        )

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
