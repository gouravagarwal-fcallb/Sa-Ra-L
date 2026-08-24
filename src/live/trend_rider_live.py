"""
Trend Rider — Live / Paper Engine
─────────────────────────────────
The opposite of RAMS: instead of scalping many bursts, take ONE position on a
strongly trending day and hold it. Entry after 09:45 when the index makes a new
30-minute extreme with expanding volume and an RSI extreme; hold through the day.

Entry (after 09:45):
  • new 30-min HIGH + RSI ≥ rsi_hi + volume expansion  → buy CE (ride up)
  • new 30-min LOW  + RSI ≤ rsi_lo + volume expansion  → buy PE (ride down)
Exit: +target% (let it run), OR stop%, OR hard close (default 14:45). One trade,
no re-entry — this is a hold strategy.

Prices from the broker (real Kite quote in paper too); model only offline.
"""
from __future__ import annotations

import time
import datetime as _dt
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import get_spot_price, get_india_vix, get_intraday_ohlcv
from src.data.candle_builder import rsi as rsi_of
from src.utils.market_calendar import get_nifty_weekly_expiry
from src.utils.helpers import round_to_strike
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("trend_rider_live", "TREND_RIDER_v1")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 30


@dataclass
class RiderTrade:
    instrument:  str
    direction:   str
    option_type: str
    strike:      int
    expiry_str:  str
    exchange:    str
    entry_price: float
    entry_time:  str
    quantity:    int
    target_px:   float
    stop_px:     float
    is_paper:    bool
    order_id:    str   = ""
    exit_price:  float = 0.0
    exit_reason: str   = ""
    pnl:         float = 0.0


class TrendRiderLive:
    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc = strategy_config
        self.broker = broker
        self.mode = mode
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065))
        self._status_callback = status_callback
        self._stop_event = None

        g = strategy_config.get("trend_rider", strategy_config.get("params", {})) or {}
        self.rsi_hi     = float(g.get("rsi_high", 65))
        self.rsi_lo     = float(g.get("rsi_low", 35))
        self.vol_mult   = float(g.get("volume_mult", 1.2))
        self.target_pct = float(g.get("target_pct", 50.0)) / 100
        self.stop_pct   = float(g.get("stop_pct", 10.0)) / 100
        self.budget_min = float(g.get("budget_min_rs", 10000))
        self.budget_max = float(g.get("budget_max_rs", 25000))
        self.slippage   = strategy_config.get("backtest", {}).get("slippage_pct", 0.3) / 100
        self.day_stop   = strategy_config.get("risk", {}).get("daily_loss_limit", 5000)

        def _hm(s): return _dt.time(int(str(s)[:2]), int(str(s)[3:5]))
        self.entry_start = _hm(g.get("entry_start", "09:45"))
        self.entry_end   = _hm(g.get("entry_end",   "14:00"))
        self.close_t     = _hm(g.get("hard_close",  "14:45"))

        self.instrument = "NIFTY"
        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.lot  = inst_n.get("lot_size", 65)
        self.step = inst_n.get("strike_step", 50)

        self.vix = 15.0
        self.fired = False
        self.open_trade: Optional[RiderTrade] = None
        self.day_pnl = 0.0

    def _now(self): return datetime.now(IST)
    def _now_hm(self):
        n = self._now(); return _dt.time(n.hour, n.minute)
    def _stopped(self):
        return self._stop_event is not None and self._stop_event.is_set()
    def _t_hours_to_close(self):
        n = self._now(); c = n.replace(hour=15, minute=30, second=0, microsecond=0)
        return max((c - n).total_seconds() / 3600.0, 0.25)

    def _get_ltp(self, spot, expiry, strike, opt_type):
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

    def _qty(self, ltp, strong):
        budget = self.budget_max if strong else self.budget_min
        if ltp <= 0: return 0
        return max(0, int(budget / (ltp * self.lot))) * self.lot

    def _emit(self, direction="NEUTRAL", trade_event=None, signal=None, notable=False):
        if not self._status_callback: return
        kw = dict(direction=direction, score=0,
                  real_pnl=self.day_pnl if self.mode == "live" else 0.0,
                  paper_pnl=self.day_pnl if self.mode == "paper" else 0.0,
                  open_positions=1 if self.open_trade else 0)
        if trade_event: kw["trade_event"] = trade_event
        if signal: kw["signal"] = signal; kw["notable"] = notable
        try: self._status_callback(**kw)
        except Exception: pass

    def _breakout(self, spot: float):
        """Return ('CE'|'PE', strong) on a new 30-min extreme + RSI + volume, else None."""
        try:
            df = get_intraday_ohlcv(self.instrument, date.today(), interval="5m")
            if df is None or df.empty or len(df) < 7:
                return None
            closes = [float(c) for c in df["Close"].tolist()]
            highs = [float(c) for c in df["High"].tolist()]
            lows = [float(c) for c in df["Low"].tolist()]
            vols = [float(c) for c in df["Volume"].tolist()] if "Volume" in df else []
            r = rsi_of(closes, 14)
            # last 6 bars ≈ 30 min; a "new extreme" = latest breaks the prior-6 range.
            prior_hi = max(highs[-7:-1]); prior_lo = min(lows[-7:-1])
            last_hi = highs[-1]; last_lo = lows[-1]
            vol_ok = True
            if vols and len(vols) >= 7 and sum(vols[-7:-1]) > 0:
                avg = sum(vols[-7:-1]) / 6
                vol_ok = vols[-1] >= self.vol_mult * avg
        except Exception:
            return None
        if last_hi > prior_hi and r >= self.rsi_hi and vol_ok:
            return "CE", (r >= self.rsi_hi + 8)
        if last_lo < prior_lo and r <= self.rsi_lo and vol_ok:
            return "PE", (r <= self.rsi_lo - 8)
        return None

    def run(self) -> None:
        today = date.today()
        expiry = get_nifty_weekly_expiry(today)
        exp_str = expiry.strftime("%Y%m%d")
        try: self.vix = get_india_vix() or 15.0
        except Exception: pass
        self._emit(signal=(f"Trend Rider ready — waits for a new 30-min extreme after "
                           f"{self.entry_start:%H:%M} (RSI≥{self.rsi_hi}/≤{self.rsi_lo}, "
                           f"vol×{self.vol_mult}); holds to {self.close_t:%H:%M}. expiry {expiry}"),
                   notable=True)

        while True:
            if self._stopped():
                self._emit(signal="Stop requested — Trend Rider halted.", notable=True); break
            now_hm = self._now_hm()

            if self.open_trade:
                spot = get_spot_price(self.instrument) or 0.0
                t = self.open_trade
                ltp = self._get_ltp(spot, expiry, t.strike, t.option_type)
                if ltp >= t.target_px:
                    self._close(t, "TARGET_HIT", expiry)
                elif ltp <= t.stop_px:
                    self._close(t, "STOP_LOSS", expiry)
                elif now_hm >= self.close_t:
                    self._close(t, "FORCE_CLOSE", expiry)
                else:
                    t.pnl = (ltp - t.entry_price) * t.quantity
                    self._emit(direction=t.direction,
                               signal=f"RIDING {t.option_type}{t.strike} LTP={ltp:.1f} "
                                      f"tgt={t.target_px:.1f} sl={t.stop_px:.1f} pnl={t.pnl:+.0f}")
                self._sleep(); continue

            if self.fired:
                self._emit(signal=f"Done for today (rider is 1-and-hold). day_pnl=Rs.{self.day_pnl:+.0f}")
                self._sleep(); continue
            if now_hm < self.entry_start:
                self._emit(signal=f"Pre-{self.entry_start:%H:%M} — no trend entries before the range forms")
                self._sleep(); continue
            if now_hm > self.entry_end:
                self._emit(signal=f"Past {self.entry_end:%H:%M} entry cutoff — no new rides today")
                self._sleep(); continue

            spot = get_spot_price(self.instrument) or 0.0
            sig = self._breakout(spot)
            if not sig:
                self._emit(signal="No new 30-min extreme with RSI+volume — waiting for a real trend")
                self._sleep(); continue
            opt, strong = sig
            self._enter(spot, expiry, exp_str, opt, strong)
            self._sleep()

    def _sleep(self):
        for _ in range(TICK_SECONDS):
            if self._stopped(): break
            time.sleep(1)

    def _enter(self, spot, expiry, exp_str, opt_type, strong):
        atm = round_to_strike(spot, self.step)
        ltp = self._get_ltp(spot, expiry, atm, opt_type)
        qty = self._qty(ltp, strong)
        if qty == 0 or ltp <= 0:
            self._emit(signal=f"Trend signal but qty=0 @ Rs.{ltp:.1f} — skip"); self.fired = True; return
        entry = round(ltp * (1 + self.slippage), 2)
        direction = "BULLISH" if opt_type == "CE" else "BEARISH"
        trade = RiderTrade(
            instrument=self.instrument, direction=direction, option_type=opt_type, strike=atm,
            expiry_str=exp_str, exchange="NFO", entry_price=entry,
            entry_time=self._now().strftime("%H:%M:%S"), quantity=qty,
            target_px=round(entry * (1 + self.target_pct), 2),
            stop_px=round(entry * (1 - self.stop_pct), 2),
            is_paper=(self.mode != "live"))
        sym, exch = self._resolve(expiry, atm, opt_type); trade.exchange = exch
        order = Order(symbol=sym, exchange=exch, option_type=opt_type, strike=atm,
                      expiry=exp_str, transaction="BUY", quantity=qty, price=entry)
        try: trade.order_id = self.broker.place_order(order)
        except Exception as e: log.error(f"Trend Rider BUY failed: {e}")
        self.open_trade = trade; self.fired = True
        self._emit(direction=direction, trade_event={
            "event": "ENTRY", "instrument": self.instrument, "direction": direction,
            "option_type": opt_type, "strike": atm, "price": entry, "quantity": qty,
            "pnl": "", "exit_reason": "", "window": "TREND_RIDER"})

    def _close(self, trade, reason, expiry):
        spot = get_spot_price(self.instrument) or trade.entry_price
        ltp = self._get_ltp(spot, expiry, trade.strike, trade.option_type)
        exit_px = round(ltp * (1 - self.slippage), 2)
        trade.exit_price = exit_px; trade.exit_reason = reason
        trade.pnl = (exit_px - trade.entry_price) * trade.quantity
        self.day_pnl += trade.pnl; self.open_trade = None
        sym, exch = self._resolve(expiry, trade.strike, trade.option_type)
        order = Order(symbol=sym, exchange=exch, option_type=trade.option_type, strike=trade.strike,
                      expiry=trade.expiry_str, transaction="SELL", quantity=trade.quantity, price=exit_px)
        try: self.broker.place_order(order)
        except Exception as e: log.error(f"Trend Rider SELL failed: {e}")
        self._emit(direction=trade.direction, trade_event={
            "event": reason, "instrument": self.instrument, "direction": trade.direction,
            "option_type": trade.option_type, "strike": trade.strike, "price": exit_px,
            "quantity": trade.quantity, "pnl": round(trade.pnl, 2),
            "exit_reason": reason, "window": "TREND_RIDER"})
