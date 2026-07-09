"""
VIX Seller — Live / Paper Engine
────────────────────────────────
The only SELLING strategy. Activates ONLY when India VIX > threshold AND the index
is range-bound (inside the prior day's range). Sells an ATM strangle (short CE +
short PE) to harvest the IV crush / theta decay that usually follows a vol spike.

Entry: VIX ≥ vix_trigger AND spot within [prev_low, prev_high].
Exit:  +target% of collected premium (buy back cheap), OR stop (premium expands
       past stop_mult × collected), OR hard close at EOD.

SELLING has larger risk than buying — this stays PAPER by policy. P&L is tracked
mark-to-market from the real option quotes (collected − current buy-back cost).
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
    get_spot_price, get_india_vix, get_intraday_ohlcv, get_previous_close,
)
from src.utils.market_calendar import get_nifty_weekly_expiry
from src.utils.helpers import round_to_strike
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("vix_seller_live", "VIX_SELLER_v1")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 60


@dataclass
class Strangle:
    instrument: str
    strike:     int
    expiry_str: str
    ce_entry:   float          # premium collected per unit on the CE leg
    pe_entry:   float
    collected:  float          # (ce_entry + pe_entry) per unit
    quantity:   int
    entry_time: str
    target_px:  float          # buy-back level (per unit) to bank +target%
    stop_px:    float          # buy-back level (per unit) that trips the stop
    is_paper:   bool
    exit_cost:  float = 0.0
    exit_reason:str  = ""
    pnl:        float = 0.0


class VixSellerLive:
    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc = strategy_config
        self.broker = broker
        self.mode = "paper"            # SELLING stays paper by policy, always
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065))
        self._status_callback = status_callback
        self._stop_event = None

        g = strategy_config.get("vix_seller", strategy_config.get("params", {})) or {}
        self.vix_trigger = float(g.get("vix_trigger", 22.0))
        self.target_pct  = float(g.get("target_pct", 30.0)) / 100    # bank 30% of collected
        self.stop_mult   = float(g.get("stop_mult", 1.5))            # loss if value > 1.5× collected
        self.lots        = int(g.get("lots", 1))
        self.slippage    = strategy_config.get("backtest", {}).get("slippage_pct", 0.3) / 100
        self.day_stop    = strategy_config.get("risk", {}).get("daily_loss_limit", 15000)

        def _hm(s): return _dt.time(int(str(s)[:2]), int(str(s)[3:5]))
        self.entry_start = _hm(g.get("entry_start", "09:30"))
        self.entry_end   = _hm(g.get("entry_end",   "14:30"))
        self.close_t     = _hm(g.get("hard_close",  "15:15"))

        self.instrument = "NIFTY"
        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.lot  = inst_n.get("lot_size", 65)
        self.step = inst_n.get("strike_step", 50)

        self.vix = 15.0
        self.prev_high = 0.0
        self.prev_low  = 0.0
        self.fired = False
        self.pos: Optional[Strangle] = None
        self.day_pnl = 0.0

    def _now(self): return datetime.now(IST)
    def _now_hm(self):
        n = self._now(); return _dt.time(n.hour, n.minute)
    def _stopped(self):
        return self._stop_event is not None and self._stop_event.is_set()
    def _t_hours_to_close(self):
        n = self._now(); c = n.replace(hour=15, minute=30, second=0, microsecond=0)
        return max((c - n).total_seconds() / 3600.0, 0.25)

    def _leg_ltp(self, spot, expiry, strike, opt_type):
        try:
            sym, exch = self.broker.get_tradingsymbol(self.instrument, expiry, strike, opt_type)
            px = self.broker.get_ltp(sym, exch, strike, opt_type, expiry.strftime("%Y%m%d"))
            if px and px > 0:
                return px
        except Exception:
            pass
        return self.pricer.price(spot, strike, self.vix, self._t_hours_to_close(), opt_type).price

    def _resolve(self, expiry, strike, opt_type):
        try:
            return self.broker.get_tradingsymbol(self.instrument, expiry, strike, opt_type)
        except Exception:
            return self.instrument, "NFO"

    def _emit(self, trade_event=None, signal=None, notable=False):
        if not self._status_callback: return
        kw = dict(direction="NEUTRAL", score=0,
                  real_pnl=0.0, paper_pnl=self.day_pnl,
                  open_positions=1 if self.pos else 0)
        if trade_event: kw["trade_event"] = trade_event
        if signal: kw["signal"] = signal; kw["notable"] = notable
        try: self._status_callback(**kw)
        except Exception: pass

    def _sleep(self):
        for _ in range(TICK_SECONDS):
            if self._stopped(): break
            time.sleep(1)

    def run(self) -> None:
        today = date.today()
        expiry = get_nifty_weekly_expiry(today)
        exp_str = expiry.strftime("%Y%m%d")
        try:
            self.vix = get_india_vix() or 15.0
            df = get_intraday_ohlcv(self.instrument, today - timedelta(days=1), interval="15m")
            if df is not None and not df.empty:
                self.prev_high = float(df["High"].max()); self.prev_low = float(df["Low"].min())
        except Exception:
            pass
        self._emit(signal=(f"VIX Seller ready — arms ONLY when VIX≥{self.vix_trigger} and spot is "
                           f"range-bound [{self.prev_low:.0f},{self.prev_high:.0f}]. VIX now {self.vix:.1f}. "
                           f"SELLING → stays PAPER. expiry {expiry}"), notable=True)

        while True:
            if self._stopped():
                self._emit(signal="Stop requested — VIX Seller halted.", notable=True); break
            now_hm = self._now_hm()

            if self.pos:
                spot = get_spot_price(self.instrument) or 0.0
                p = self.pos
                ce = self._leg_ltp(spot, expiry, p.strike, "CE")
                pe = self._leg_ltp(spot, expiry, p.strike, "PE")
                value = ce + pe
                p.pnl = (p.collected - value) * p.quantity     # short: profit as value falls
                if value <= p.target_px:
                    self._close(p, "TARGET_HIT", value)
                elif value >= p.stop_px:
                    self._close(p, "STOP_LOSS", value)
                elif now_hm >= self.close_t:
                    self._close(p, "FORCE_CLOSE", value)
                else:
                    self._emit(signal=f"SHORT STRANGLE {p.strike} value={value:.1f} "
                                      f"(collected {p.collected:.1f}) pnl={p.pnl:+.0f}")
                self._sleep(); continue

            if self.fired:
                self._emit(signal=f"Done for today. day_pnl=Rs.{self.day_pnl:+.0f}"); self._sleep(); continue
            if self.vix < self.vix_trigger:
                self._emit(signal=f"VIX {self.vix:.1f} < {self.vix_trigger} — no vol to sell, stand aside")
                self._sleep(); continue
            if now_hm < self.entry_start or now_hm > self.entry_end:
                self._emit(signal=f"Outside sell window {self.entry_start:%H:%M}-{self.entry_end:%H:%M}")
                self._sleep(); continue

            spot = get_spot_price(self.instrument) or 0.0
            if self.prev_high and not (self.prev_low <= spot <= self.prev_high):
                self._emit(signal=f"Spot {spot:.0f} outside prior range [{self.prev_low:.0f},"
                                  f"{self.prev_high:.0f}] — not range-bound, skip")
                self._sleep(); continue

            self._sell(spot, expiry, exp_str)
            self._sleep()

    def _sell(self, spot, expiry, exp_str):
        atm = round_to_strike(spot, self.step)
        ce = self._leg_ltp(spot, expiry, atm, "CE")
        pe = self._leg_ltp(spot, expiry, atm, "PE")
        if ce <= 0 or pe <= 0:
            self._emit(signal="No option quotes for strangle — skip"); self.fired = True; return
        qty = self.lots * self.lot
        collected = round((ce + pe) * (1 - self.slippage), 2)   # sell → receive the bid
        pos = Strangle(
            instrument=self.instrument, strike=atm, expiry_str=exp_str,
            ce_entry=round(ce, 2), pe_entry=round(pe, 2), collected=collected, quantity=qty,
            entry_time=self._now().strftime("%H:%M:%S"),
            target_px=round(collected * (1 - self.target_pct), 2),   # buy back cheaper
            stop_px=round(collected * self.stop_mult, 2),
            is_paper=True)
        for opt in ("CE", "PE"):
            sym, exch = self._resolve(expiry, atm, opt)
            order = Order(symbol=sym, exchange=exch, option_type=opt, strike=atm,
                          expiry=exp_str, transaction="SELL", quantity=qty,
                          price=(ce if opt == "CE" else pe))
            try: self.broker.place_order(order)
            except Exception as e: log.error(f"VIX Seller SELL {opt} failed: {e}")
        self.pos = pos; self.fired = True
        self._emit(trade_event={
            "event": "ENTRY", "instrument": self.instrument, "direction": "NEUTRAL",
            "option_type": "STR", "strike": atm, "price": collected, "quantity": qty,
            "pnl": "", "exit_reason": "", "window": "VIX_SELLER"})

    def _close(self, pos: Strangle, reason: str, value: float):
        exit_cost = round(value * (1 + self.slippage), 2)         # buy back → pay the ask
        pos.exit_cost = exit_cost; pos.exit_reason = reason
        pos.pnl = (pos.collected - exit_cost) * pos.quantity
        self.day_pnl += pos.pnl; self.pos = None
        for opt in ("CE", "PE"):
            sym, exch = self._resolve(get_nifty_weekly_expiry(date.today()), pos.strike, opt)
            order = Order(symbol=sym, exchange=exch, option_type=opt, strike=pos.strike,
                          expiry=pos.expiry_str, transaction="BUY", quantity=pos.quantity)
            try: self.broker.place_order(order)
            except Exception as e: log.error(f"VIX Seller buy-back {opt} failed: {e}")
        self._emit(trade_event={
            "event": reason, "instrument": self.instrument, "direction": "NEUTRAL",
            "option_type": "STR", "strike": pos.strike, "price": exit_cost,
            "quantity": pos.quantity, "pnl": round(pos.pnl, 2),
            "exit_reason": reason, "window": "VIX_SELLER"})
