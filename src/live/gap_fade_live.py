"""
Gap Fade — Live / Paper Engine
──────────────────────────────
Opening-range reversion. When the market opens with a gap > 0.5% that then shows
a reversal in the first 30 minutes, fade it with an ATM option in the OPPOSITE
direction (gap-up → buy PE, gap-down → buy CE). One trade per day, small size.

Entry (opening window 09:15–09:45):
  • |gap%| ≥ gap_min   AND  India VIX < vix_max (calm tape only)
  • a completed 5-min candle reverses AGAINST the gap (gap-up → bearish candle,
    gap-down → bullish candle) with RSI not confirming the gap.
Exit: +target% on premium, OR stop%, OR the reversion window closes.

Prices come from the broker (real Kite quote in paper too); model only offline.
Paper places simulated orders; live is gated by mode + the arm/confirm guard.
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
    get_intraday_ohlcv,
)
from src.data.candle_builder import rsi as rsi_of
from src.utils.market_calendar import get_nifty_weekly_expiry
from src.utils.helpers import round_to_strike
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("gap_fade_live", "GAP_FADE_v1")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 30


@dataclass
class FadeTrade:
    instrument:  str
    direction:   str          # BULLISH (CE) / BEARISH (PE) — the FADE direction
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


class GapFadeLive:
    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc     = strategy_config
        self.broker = broker
        self.mode   = mode
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065))
        self._status_callback = status_callback
        self._stop_event = None

        g = strategy_config.get("gap_fade", strategy_config.get("params", {})) or {}
        self.gap_min    = float(g.get("gap_min_pct", 0.5)) / 100
        # Read the ACTUAL config keys (config uses max_vix / window_start / window_end /
        # hard_close_time; the engine previously read vix_max / entry_start / entry_end /
        # exit_by and so silently ignored the config, running on defaults).
        self.vix_max    = float(g.get("max_vix", g.get("vix_max", 15.0)))
        # target_pct / stop_pct accept a FRACTION (0.15 = 15%) or a PERCENT (15 = 15%).
        # The config uses fractions (0.15/0.30); the old code did an unconditional /100,
        # turning 0.15 into 0.15% — so live was exiting at ±a fraction of a percent, not
        # the intended +15% / -30%. Normalise instead.
        _t = float(g.get("target_pct", 0.15)); self.target_pct = _t / 100 if _t > 1 else _t
        _s = float(g.get("stop_pct",   0.30)); self.stop_pct   = _s / 100 if _s > 1 else _s
        self.budget_min = float(g.get("budget_min_rs", 5000))
        self.budget_max = float(g.get("budget_max_rs", 10000))
        self.otm_n      = int(g.get("otm_strikes", 0))       # 0 = ATM
        self.slippage   = strategy_config.get("backtest", {}).get("slippage_pct", 0.2) / 100
        self.day_stop   = strategy_config.get("risk", {}).get("daily_loss_limit", 2000)

        def _hm(s): return _dt.time(int(str(s)[:2]), int(str(s)[3:5]))
        self.entry_start = _hm(g.get("window_start", g.get("entry_start", "09:15")))
        self.entry_end   = _hm(g.get("window_end",   g.get("entry_end",   "09:45")))
        self.exit_by     = _hm(g.get("hard_close_time", g.get("exit_by",  "10:15")))

        inst_n    = strategy_config.get("instruments", {}).get("nifty", {})
        self.lot  = inst_n.get("lot_size", 65)
        self.step = inst_n.get("strike_step", 50)

        self.instrument = "NIFTY"
        self.prev_close = 0.0
        self.day_open   = 0.0
        self.gap_pct    = 0.0
        self.vix        = 15.0
        self.fired      = False
        self.open_trade: Optional[FadeTrade] = None
        self.day_pnl    = 0.0

    # ── helpers ─────────────────────────────────────────────────────────────
    def _now(self): return datetime.now(IST)
    def _now_hm(self):
        n = self._now(); return _dt.time(n.hour, n.minute)
    def _stopped(self):
        return self._stop_event is not None and self._stop_event.is_set()

    def _t_hours_to_close(self) -> float:
        n = self._now()
        close = n.replace(hour=15, minute=30, second=0, microsecond=0)
        return max((close - n).total_seconds() / 3600.0, 0.25)

    def _get_ltp(self, spot: float, expiry: date, strike: int, opt_type: str) -> float:
        try:
            sym, exch = self.broker.get_tradingsymbol(self.instrument, expiry, strike, opt_type)
            px = self.broker.get_ltp(sym, exch, strike, opt_type, expiry.strftime("%Y%m%d"))
            if px and px > 0:
                return px
        except Exception:
            pass
        return self.pricer.price(spot, strike, self.vix, self._t_hours_to_close(), opt_type).price

    def _resolve(self, expiry: date, strike: int, opt_type: str):
        try:
            return self.broker.get_tradingsymbol(self.instrument, expiry, strike, opt_type)
        except Exception:
            return self.instrument, "NFO"

    def _qty(self, ltp: float) -> int:
        budget = self.budget_max if abs(self.gap_pct) >= 0.8 / 100 else self.budget_min
        if ltp <= 0:
            return 0
        return max(0, int(budget / (ltp * self.lot))) * self.lot

    def _emit(self, direction="NEUTRAL", trade_event=None, signal=None, notable=False):
        if not self._status_callback:
            return
        kw = dict(direction=direction, score=0,
                  real_pnl=self.day_pnl if self.mode == "live" else 0.0,
                  paper_pnl=self.day_pnl if self.mode == "paper" else 0.0,
                  open_positions=1 if self.open_trade else 0)
        if trade_event: kw["trade_event"] = trade_event
        if signal: kw["signal"] = signal; kw["notable"] = notable
        try: self._status_callback(**kw)
        except Exception: pass

    # ── signal ──────────────────────────────────────────────────────────────
    def _reversal(self, spot: float) -> Optional[str]:
        """Return the FADE option type ('PE' for gap-up fade, 'CE' for gap-down),
        or None. Needs a completed 5-min candle reversing against the gap + RSI."""
        try:
            df = get_intraday_ohlcv(self.instrument, date.today(), interval="5m")
            if df is None or df.empty or len(df) < 2:
                return None
            closes = [float(c) for c in df["Close"].tolist()]
            last_o = float(df["Open"].iloc[-1]); last_c = float(df["Close"].iloc[-1])
            r = rsi_of(closes, 14)
        except Exception:
            return None
        if self.gap_pct >= self.gap_min:            # gap UP → fade DOWN with a PE
            if last_c < last_o and r < 60:
                return "PE"
        elif self.gap_pct <= -self.gap_min:         # gap DOWN → fade UP with a CE
            if last_c > last_o and r > 40:
                return "CE"
        return None

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self) -> None:
        today = date.today()
        expiry = get_nifty_weekly_expiry(today)
        exp_str = expiry.strftime("%Y%m%d")
        try:
            self.vix = get_india_vix() or 15.0
            self.prev_close = get_previous_close(self.instrument) or 0.0
            self.day_open = get_day_open_spot(self.instrument) or 0.0
        except Exception:
            pass
        if self.prev_close and self.day_open:
            self.gap_pct = (self.day_open - self.prev_close) / self.prev_close
        self._emit(signal=(f"Gap Fade ready — gap={self.gap_pct*100:+.2f}% VIX={self.vix:.1f} "
                           f"(needs |gap|≥{self.gap_min*100:.1f}%, VIX<{self.vix_max}); expiry {expiry}"),
                   notable=True)

        while True:
            if self._stopped():
                self._emit(signal="Stop requested — Gap Fade halted.", notable=True); break
            now_hm = self._now_hm()

            # manage open position
            if self.open_trade:
                spot = get_spot_price(self.instrument) or 0.0
                ltp = self._get_ltp(spot, expiry, self.open_trade.strike, self.open_trade.option_type)
                t = self.open_trade
                if ltp >= t.target_px:
                    self._close(t, "TARGET_HIT", expiry)
                elif ltp <= t.stop_px:
                    self._close(t, "STOP_LOSS", expiry)
                elif now_hm >= self.exit_by:
                    self._close(t, "WINDOW_CLOSE", expiry)
                else:
                    t.pnl = (ltp - t.entry_price) * t.quantity
                    self._emit(direction=t.direction,
                               signal=f"POS {t.option_type}{t.strike} LTP={ltp:.1f} "
                                      f"tgt={t.target_px:.1f} sl={t.stop_px:.1f}")
                self._sleep(); continue

            # done for the day?
            if self.fired:
                self._emit(signal=f"Done for today (1-trade gap fade). day_pnl=Rs.{self.day_pnl:+.0f}")
                self._sleep(); continue

            # outside the opening window?
            if now_hm < self.entry_start or now_hm > self.entry_end:
                self._emit(signal=f"Outside fade window {self.entry_start:%H:%M}-{self.entry_end:%H:%M} "
                                  f"(gap={self.gap_pct*100:+.2f}%)")
                self._sleep(); continue

            # gates
            if abs(self.gap_pct) < self.gap_min:
                self._emit(signal=f"No qualifying gap ({self.gap_pct*100:+.2f}% < {self.gap_min*100:.1f}%) — stand aside")
                self._sleep(); continue
            if self.vix >= self.vix_max:
                self._emit(signal=f"VIX {self.vix:.1f} ≥ {self.vix_max} — too hot to fade, skip")
                self._sleep(); continue

            spot = get_spot_price(self.instrument) or 0.0
            opt = self._reversal(spot)
            if not opt:
                self._emit(signal=f"Gap {self.gap_pct*100:+.2f}% but no reversal candle yet — waiting")
                self._sleep(); continue

            self._enter(spot, expiry, exp_str, opt)
            self._sleep()

    def _sleep(self):
        for _ in range(TICK_SECONDS):
            if self._stopped(): break
            time.sleep(1)

    def _enter(self, spot: float, expiry: date, exp_str: str, opt_type: str) -> None:
        atm = round_to_strike(spot, self.step)
        strike = atm + (self.otm_n * self.step if opt_type == "CE" else -self.otm_n * self.step)
        ltp = self._get_ltp(spot, expiry, strike, opt_type)
        qty = self._qty(ltp)
        if qty == 0 or ltp <= 0:
            self._emit(signal=f"Fade signal but qty=0 @ Rs.{ltp:.1f} — skip"); self.fired = True; return
        entry = round(ltp * (1 + self.slippage), 2)
        direction = "BULLISH" if opt_type == "CE" else "BEARISH"
        trade = FadeTrade(
            instrument=self.instrument, direction=direction, option_type=opt_type,
            strike=strike, expiry_str=exp_str, exchange=("NFO"),
            entry_price=entry, entry_time=self._now().strftime("%H:%M:%S"), quantity=qty,
            target_px=round(entry * (1 + self.target_pct), 2),
            stop_px=round(entry * (1 - self.stop_pct), 2),
            is_paper=(self.mode != "live"))
        sym, exch = self._resolve(expiry, strike, opt_type)
        trade.exchange = exch
        order = Order(symbol=sym, exchange=exch, option_type=opt_type, strike=strike,
                      expiry=exp_str, transaction="BUY", quantity=qty, price=entry)
        try:
            trade.order_id = self.broker.place_order(order)
        except Exception as e:
            log.error(f"Gap Fade BUY failed: {e}")
        self.open_trade = trade
        self.fired = True
        self._emit(direction=direction, trade_event={
            "event": "ENTRY", "instrument": self.instrument, "direction": direction,
            "option_type": opt_type, "strike": strike, "price": entry, "quantity": qty,
            "pnl": "", "exit_reason": "", "window": "GAP_FADE"})

    def _close(self, trade: FadeTrade, reason: str, expiry: date) -> None:
        spot = get_spot_price(self.instrument) or trade.entry_price
        ltp = self._get_ltp(spot, expiry, trade.strike, trade.option_type)
        exit_px = round(ltp * (1 - self.slippage), 2)
        trade.exit_price = exit_px; trade.exit_reason = reason
        trade.pnl = (exit_px - trade.entry_price) * trade.quantity
        self.day_pnl += trade.pnl
        self.open_trade = None
        sym, exch = self._resolve(expiry, trade.strike, trade.option_type)
        order = Order(symbol=sym, exchange=exch, option_type=trade.option_type, strike=trade.strike,
                      expiry=trade.expiry_str, transaction="SELL", quantity=trade.quantity, price=exit_px)
        try: self.broker.place_order(order)
        except Exception as e: log.error(f"Gap Fade SELL failed: {e}")
        self._emit(direction=trade.direction, trade_event={
            "event": reason, "instrument": self.instrument, "direction": trade.direction,
            "option_type": trade.option_type, "strike": trade.strike, "price": exit_px,
            "quantity": trade.quantity, "pnl": round(trade.pnl, 2),
            "exit_reason": reason, "window": "GAP_FADE"})
