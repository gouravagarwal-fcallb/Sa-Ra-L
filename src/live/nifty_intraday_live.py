"""
NIFTY_INTRADAY_v1 — Pure Options Buying Intraday Scalper
─────────────────────────────────────────────────────────
Capital: ₹50,000  |  Max/trade: ₹10,000  |  Stop: ₹1,200  |  Target: ₹2,750
Daily loss lock: ₹4,800  |  Daily profit lock: ₹8,250 (→ half-size)
Cooldown: 15 min (stop hit) / 5 min (profit)
Hard close: 15:10 IST  |  No overnight holding  |  No short options

Regime classification:
  TREND → 15-min ORB breakout + VWAP confirmation + volume surge
  RANGE → S/R level rejection + RSI extreme + reversal candle

Position sizing guarantee:
  qty = max lots where (entry_prem - stop_prem) × qty ≤ ₹1,200
  AND entry_prem × qty ≤ ₹10,000
"""

from __future__ import annotations

import time
import datetime as _dt
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import (
    get_spot_price, get_india_vix, get_intraday_ohlcv, get_day_open_spot,
)
from src.utils.market_calendar import get_nifty_weekly_expiry
from src.utils.helpers import round_to_strike
from src.utils.logger import setup_logger

log = setup_logger("nifty_intraday_live")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 60


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _to_ist(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure DataFrame index is in IST. Returns df unchanged on failure."""
    try:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
        else:
            df.index = df.index.tz_convert("Asia/Kolkata")
    except Exception:
        pass
    return df


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    """Average True Range over last n bars."""
    if len(df) < n + 1:
        return float(df["High"].iloc[-1] - df["Low"].iloc[-1])
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def _rsi(series: pd.Series, n: int = 14) -> float:
    """RSI using Wilder smoothing."""
    if len(series) < n + 1:
        return 50.0
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return float(100 - 100 / (1 + rs.iloc[-1]))


def _vwap_series(df: pd.DataFrame) -> pd.Series:
    """VWAP from start of df (session-level)."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    vol     = df["Volume"].replace(0, 1)
    return (typical * vol).cumsum() / vol.cumsum()


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _is_hammer(bar: pd.Series) -> bool:
    """Bullish hammer: long lower wick, small body, close near high."""
    body  = abs(bar["Close"] - bar["Open"])
    rng   = bar["High"] - bar["Low"]
    if rng < 1e-4:
        return False
    lower = min(bar["Open"], bar["Close"]) - bar["Low"]
    upper = bar["High"] - max(bar["Open"], bar["Close"])
    return lower >= 2 * body and upper <= 0.3 * rng


def _is_shooting_star(bar: pd.Series) -> bool:
    """Bearish shooting star: long upper wick, small body, close near low."""
    body  = abs(bar["Close"] - bar["Open"])
    rng   = bar["High"] - bar["Low"]
    if rng < 1e-4:
        return False
    upper = bar["High"] - max(bar["Open"], bar["Close"])
    lower = min(bar["Open"], bar["Close"]) - bar["Low"]
    return upper >= 2 * body and lower <= 0.3 * rng


def _is_bullish_engulf(prev: pd.Series, curr: pd.Series) -> bool:
    """Bullish engulfing: prev bar bearish, curr bullish and wider."""
    prev_bear = prev["Close"] < prev["Open"]
    curr_bull = curr["Close"] > curr["Open"]
    engulf    = curr["Open"] <= prev["Close"] and curr["Close"] >= prev["Open"]
    return prev_bear and curr_bull and engulf


def _is_bearish_engulf(prev: pd.Series, curr: pd.Series) -> bool:
    """Bearish engulfing: prev bar bullish, curr bearish and wider."""
    prev_bull = prev["Close"] > prev["Open"]
    curr_bear = curr["Close"] < curr["Open"]
    engulf    = curr["Open"] >= prev["Close"] and curr["Close"] <= prev["Open"]
    return prev_bull and curr_bear and engulf


# ── Trade record ───────────────────────────────────────────────────────────────

@dataclass
class IntradayTrade:
    direction:    str        # BULLISH | BEARISH
    option_type:  str        # CE | PE
    strike:       int
    expiry_str:   str
    entry_price:  float      # option premium at entry (with slippage)
    stop_price:   float      # option premium at stop exit
    target_price: float      # option premium at target exit
    be_price:     float      # option premium at breakeven trigger
    quantity:     int
    entry_time:   str
    setup_type:   str        # "TREND_ORB" | "RANGE_SR"
    is_paper:     bool
    order_id:     str   = ""
    exit_price:   float = 0.0
    exit_reason:  str   = ""
    exit_time:    str   = ""
    pnl:          float = 0.0
    slippage_rs:  float = 0.0
    be_triggered: bool  = False   # breakeven stop activated


# ── Main engine ────────────────────────────────────────────────────────────────

class NiftyIntradayLive:
    """
    State machine states:
      IDLE          — scanning for setups
      IN_TRADE      — position open, monitoring exit
      COOLDOWN      — post-trade wait (15 min after stop, 5 min after profit)
      LOCKED_LOSS   — daily ₹4,800 loss limit hit, no more trades
      LOCKED_PROFIT — daily ₹8,250 profit lock hit, half-size mode only
    """

    STATES = ("IDLE", "IN_TRADE", "COOLDOWN", "LOCKED_LOSS", "LOCKED_PROFIT")

    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc     = strategy_config
        self.broker = broker
        self.mode   = mode
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065)
        )
        self._status_callback = status_callback

        ni = strategy_config.get("nifty_intraday", {})
        self.max_trade_rs        = ni.get("max_trade_rs",           10000)
        self.hard_stop_rs        = ni.get("hard_stop_rs",            1200)
        self.profit_target_rs    = ni.get("profit_target_rs",        2750)
        self.daily_loss_lock_rs  = ni.get("daily_loss_lock_rs",      4800)
        self.daily_profit_lock_rs= ni.get("daily_profit_lock_rs",    8250)
        self.cooldown_loss_min   = ni.get("cooldown_loss_min",          15)
        self.cooldown_profit_min = ni.get("cooldown_profit_min",         5)
        self.orb_minutes         = ni.get("opening_range_minutes",      15)
        self.avoid_first_min     = ni.get("avoid_first_minutes",        10)
        self.itm_offset          = ni.get("prefer_itm_strikes",          0)
        self.atr_trend_mult      = ni.get("atr_trend_multiplier",      1.2)
        self.vwap_slope_thr      = ni.get("vwap_slope_threshold",     0.05)
        self.orb_buffer_pct      = ni.get("orb_buffer_pct",           0.05) / 100
        self.rsi_oversold        = ni.get("rsi_oversold",               35)
        self.rsi_overbought      = ni.get("rsi_overbought",             65)
        self.min_vol_ratio       = ni.get("min_volume_ratio",          1.3)
        self.sr_lookback         = ni.get("sr_lookback_bars",           30)
        self.sr_touch_pct        = ni.get("sr_touch_pct",             0.15) / 100
        self.be_at_pct           = ni.get("breakeven_at_pct_target",  0.50)
        self.half_size_at_lock   = ni.get("half_size_at_profit_lock", True)

        self.slippage            = strategy_config.get("backtest", {}).get("slippage_pct", 0.3) / 100

        close_str      = ni.get("hard_close_time", "15:10")
        self.close_h, self.close_m = int(close_str[:2]), int(close_str[3:])

        inst           = strategy_config.get("instruments", {}).get("nifty", {})
        self.lot_size  = inst.get("lot_size",   75)
        self.step      = inst.get("strike_step", 50)

        # Session state
        self.state:        str  = "IDLE"
        self.day_pnl:      float = 0.0
        self.trades:       list[IntradayTrade] = []
        self.open_trade:   Optional[IntradayTrade] = None
        self.cooldown_until: Optional[datetime] = None
        self.vix:          float = 15.0
        self.regime:       str  = "RANGE"   # TREND | RANGE
        self.regime_ts:    Optional[datetime] = None
        self.orb_high:     float = 0.0
        self.orb_low:      float = 0.0
        self.orb_set:      bool  = False
        self.day_open:     float = 0.0
        self._bar_cache:   dict  = {}   # {interval: (timestamp, df)}
        self._trade_count: int   = 0

    # ── Timing helpers ────────────────────────────────────────────────────────

    def _now(self) -> datetime:
        return datetime.now(IST)

    def _now_hm(self) -> _dt.time:
        n = self._now()
        return _dt.time(n.hour, n.minute)

    def _t_hours_to_close(self) -> float:
        now   = self._now()
        close = now.replace(hour=self.close_h, minute=self.close_m, second=0, microsecond=0)
        secs  = max((close - now).total_seconds(), 60)
        return secs / 3600

    def _market_minutes_elapsed(self) -> int:
        """Minutes since 9:15 IST."""
        now   = self._now()
        open_ = now.replace(hour=9, minute=15, second=0, microsecond=0)
        return max(0, int((now - open_).total_seconds() / 60))

    # ── Bar data ──────────────────────────────────────────────────────────────

    def _bars(self, interval: str) -> pd.DataFrame:
        """Fetch today's intraday bars, cached by interval."""
        now = self._now()
        ttl = 65 if interval == "1m" else 300
        if interval in self._bar_cache:
            cached_ts, cached_df = self._bar_cache[interval]
            if (now - cached_ts).total_seconds() < ttl:
                return cached_df

        df = get_intraday_ohlcv("NIFTY", date.today(), interval=interval)
        if not df.empty:
            df = _to_ist(df)
            today = date.today()
            try:
                df = df[df.index.date == today]
            except Exception:
                pass

        self._bar_cache[interval] = (now, df)
        return df

    # ── Option pricing ────────────────────────────────────────────────────────

    def _get_ltp(self, spot: float, expiry: date, strike: int, opt_type: str) -> float:
        T_hours = self._t_hours_to_close()
        if self.mode == "live":
            ts, exchange = self._resolve_sym(expiry, strike, opt_type)
            try:
                return self.broker.get_ltp(
                    ts, exchange, strike, opt_type, expiry.strftime("%Y%m%d")
                )
            except Exception:
                pass
        result = self.pricer.price(spot, strike, self.vix, T_hours, opt_type)
        return result.price

    def _resolve_sym(self, expiry: date, strike: int, opt_type: str) -> tuple[str, str]:
        if self.mode == "live":
            from src.broker.kite_broker import KiteBroker
            if isinstance(self.broker, KiteBroker):
                try:
                    return self.broker.get_tradingsymbol("NIFTY", expiry, strike, opt_type)
                except Exception as e:
                    log.warning(f"get_tradingsymbol failed ({e}) — fallback")
        return "NIFTY", "NFO"

    # ── Regime classification ─────────────────────────────────────────────────

    def _classify_regime(self) -> str:
        """
        Classify as TREND or RANGE using:
          1. ATR relative to recent average (expansion = trend)
          2. VWAP slope on 1-min bars (trending = trend)
          3. Price vs Opening Range position (breakout = trend)
        Returns "TREND" or "RANGE".
        """
        df15 = self._bars("15m")
        df1  = self._bars("1m")

        trend_score = 0

        # ── ATR check (15m bars) ─────────────────────────────────────────
        if len(df15) >= 10:
            current_atr = _atr(df15, min(14, len(df15) - 1))
            # Compare to avg of first half of today's session
            median_atr = float(
                (df15["High"] - df15["Low"]).median()
            )
            if median_atr > 0 and current_atr > median_atr * self.atr_trend_mult:
                trend_score += 1

        # ── VWAP slope check (1m bars) ───────────────────────────────────
        if len(df1) >= 15:
            vwap = _vwap_series(df1)
            slope = float(vwap.iloc[-1] - vwap.iloc[-10]) / 10
            norm_slope = slope / float(df1["Close"].iloc[-1]) * 100  # as % per bar
            if abs(norm_slope) >= self.vwap_slope_thr:
                trend_score += 1

        # ── ORB position check ───────────────────────────────────────────
        if self.orb_set and self.orb_high > 0 and len(df1) > 0:
            spot = float(df1["Close"].iloc[-1])
            orb_range = self.orb_high - self.orb_low
            if spot > self.orb_high + orb_range * 0.1:
                trend_score += 1
            elif spot < self.orb_low - orb_range * 0.1:
                trend_score += 1

        regime = "TREND" if trend_score >= 2 else "RANGE"
        log.info(f"Regime: {regime} (trend_score={trend_score})")
        return regime

    # ── Opening range builder ─────────────────────────────────────────────────

    def _update_orb(self) -> None:
        """Set ORB from first orb_minutes of 1-min bars."""
        if self.orb_set:
            return
        df = self._bars("1m")
        if df.empty:
            return
        orb_end = _dt.time(9, 15 + self.orb_minutes)
        orb_df  = df[df.index.time <= orb_end]
        if len(orb_df) >= self.orb_minutes:
            self.orb_high = float(orb_df["High"].max())
            self.orb_low  = float(orb_df["Low"].min())
            self.orb_set  = True
            log.info(f"ORB set: H={self.orb_high:.1f}  L={self.orb_low:.1f}")

    # ── Signal scanning ───────────────────────────────────────────────────────

    def _scan_trend_setup(self, df1: pd.DataFrame, spot: float) -> Optional[dict]:
        """
        Trend setup: 15-min ORB breakout with VWAP + volume confirmation.
        Returns signal dict or None.
        """
        if not self.orb_set or self.orb_high == 0:
            return None
        if len(df1) < 5:
            return None

        close   = float(df1["Close"].iloc[-1])
        vwap    = float(_vwap_series(df1).iloc[-1])
        avg_vol = float(df1["Volume"].iloc[-10:-1].mean()) if len(df1) >= 10 else 1.0
        curr_vol= float(df1["Volume"].iloc[-1])
        vol_ok  = avg_vol > 0 and (curr_vol / avg_vol) >= self.min_vol_ratio

        # Bullish breakout
        if close > self.orb_high * (1 + self.orb_buffer_pct):
            if close > vwap and vol_ok:
                return {"direction": "BULLISH", "setup": "TREND_ORB",
                        "note": f"ORB breakout UP  close={close:.1f} > orb_h={self.orb_high:.1f}  VWAP={vwap:.1f}  vol_ratio={curr_vol/avg_vol:.1f}×"}

        # Bearish breakout
        if close < self.orb_low * (1 - self.orb_buffer_pct):
            if close < vwap and vol_ok:
                return {"direction": "BEARISH", "setup": "TREND_ORB",
                        "note": f"ORB breakout DN  close={close:.1f} < orb_l={self.orb_low:.1f}  VWAP={vwap:.1f}  vol_ratio={curr_vol/avg_vol:.1f}×"}

        return None

    def _scan_range_setup(self, df1: pd.DataFrame) -> Optional[dict]:
        """
        Range setup: S/R level rejection with RSI extreme + reversal candle.
        Returns signal dict or None.
        """
        if len(df1) < max(self.sr_lookback, 15):
            return None

        lookback    = df1.iloc[-self.sr_lookback:]
        resistance  = float(lookback["High"].max())
        support     = float(lookback["Low"].min())
        close       = float(df1["Close"].iloc[-1])
        rsi         = _rsi(df1["Close"], 14)
        last_bar    = df1.iloc[-1]
        prev_bar    = df1.iloc[-2]

        # Bullish: near support + RSI oversold + reversal candle
        at_support = close <= support * (1 + self.sr_touch_pct)
        if at_support and rsi < self.rsi_oversold:
            if _is_hammer(last_bar) or _is_bullish_engulf(prev_bar, last_bar):
                return {"direction": "BULLISH", "setup": "RANGE_SR",
                        "note": f"Support bounce  close={close:.1f}  S={support:.1f}  RSI={rsi:.0f}"}

        # Bearish: near resistance + RSI overbought + reversal candle
        at_resist = close >= resistance * (1 - self.sr_touch_pct)
        if at_resist and rsi > self.rsi_overbought:
            if _is_shooting_star(last_bar) or _is_bearish_engulf(prev_bar, last_bar):
                return {"direction": "BEARISH", "setup": "RANGE_SR",
                        "note": f"Resistance reject  close={close:.1f}  R={resistance:.1f}  RSI={rsi:.0f}"}

        return None

    # ── Position sizing ───────────────────────────────────────────────────────

    def _compute_qty(self, entry_prem: float, half_size: bool = False) -> tuple[int, float, float, float]:
        """
        Returns (qty, stop_prem, target_prem, be_prem) or (0, ...) if not tradeable.

        Guarantee: (entry_prem - stop_prem) × qty ≤ hard_stop_rs
                   entry_prem × qty ≤ max_trade_rs
        """
        if entry_prem <= 0:
            return 0, 0.0, 0.0, 0.0

        budget = self.max_trade_rs
        stop   = self.hard_stop_rs
        target = self.profit_target_rs

        if half_size:
            budget //= 2
            stop   //= 2
            target //= 2

        # Max lots by budget
        max_lots = int(budget / (entry_prem * self.lot_size))
        if max_lots < 1:
            return 0, 0.0, 0.0, 0.0

        qty = max_lots * self.lot_size

        # Verify stop headroom: loss_per_lot ≥ 1 (avoid degenerate)
        loss_per_unit = stop / qty
        if entry_prem - loss_per_unit <= 0:
            # Stop would be at or below zero — widen stop to 90% of entry
            loss_per_unit = entry_prem * 0.90
            stop_actual   = loss_per_unit * qty
            if stop_actual > self.hard_stop_rs * 1.5:
                return 0, 0.0, 0.0, 0.0

        stop_prem   = round(entry_prem - stop   / qty, 2)
        target_prem = round(entry_prem + target / qty, 2)
        be_prem     = round(entry_prem + (target / qty) * self.be_at_pct, 2)

        if stop_prem <= 0:
            stop_prem = round(entry_prem * 0.10, 2)   # floor at 10% of entry

        return qty, stop_prem, target_prem, be_prem

    # ── Strike selection ──────────────────────────────────────────────────────

    def _pick_strike(self, spot: float, direction: str) -> int:
        atm = round_to_strike(spot, self.step)
        if direction == "BULLISH":
            # itm_offset=-1 → ITM CE (lower strike), 0 → ATM, 1 → OTM
            return atm + self.itm_offset * self.step
        else:
            # For PE: ITM = higher strike, OTM = lower strike
            return atm - self.itm_offset * self.step

    # ── Order management ──────────────────────────────────────────────────────

    def _place_buy(self, trade: IntradayTrade, expiry: date) -> None:
        sym, exch = self._resolve_sym(expiry, trade.strike, trade.option_type)
        order = Order(
            symbol=sym, exchange=exch,
            option_type=trade.option_type, strike=trade.strike,
            expiry=trade.expiry_str, transaction="BUY",
            quantity=trade.quantity,
        )
        try:
            oid = self.broker.place_order(order)
            trade.order_id = oid
            log.info(
                f"[{self.mode.upper()}] BUY {sym} qty={trade.quantity}"
                f" @ Rs.{trade.entry_price:.1f} | order_id={oid}"
            )
        except Exception as e:
            log.error(f"BUY order failed: {e}")

    def _place_sell(self, trade: IntradayTrade, expiry: date, exit_px: float) -> None:
        sym, exch = self._resolve_sym(expiry, trade.strike, trade.option_type)
        order = Order(
            symbol=sym, exchange=exch,
            option_type=trade.option_type, strike=trade.strike,
            expiry=trade.expiry_str, transaction="SELL",
            quantity=trade.quantity,
        )
        try:
            oid = self.broker.place_order(order)
            sign = "+" if trade.pnl >= 0 else ""
            log.info(
                f"[{self.mode.upper()}] SELL {sym} qty={trade.quantity}"
                f" @ Rs.{exit_px:.1f} | order_id={oid}"
                f" | P&L={sign}Rs.{trade.pnl:,.0f}"
            )
        except Exception as e:
            log.error(f"SELL order failed: {e}")

    def _close_trade(self, reason: str, expiry: date) -> str:
        """Exit open position. Returns actual exit reason."""
        trade = self.open_trade
        spot  = get_spot_price("NIFTY") or 0.0
        ltp   = self._get_ltp(spot or trade.entry_price, expiry,
                               trade.strike, trade.option_type)
        exit_px = max(ltp * (1 - self.slippage), 0.05)

        trade.exit_price  = exit_px
        trade.exit_reason = reason
        trade.exit_time   = self._now().strftime("%H:%M:%S")
        trade.pnl         = (exit_px - trade.entry_price) * trade.quantity

        # Slippage accounting (paper: vs theoretical, live: tracked separately)
        trade.slippage_rs = abs(exit_px - ltp) * trade.quantity

        self.day_pnl     += trade.pnl
        self.open_trade   = None
        self._trade_count += 1

        if trade.pnl > 0:
            self.trades.append(trade)
        else:
            self.trades.append(trade)

        sign = "+" if trade.pnl >= 0 else ""
        print(
            f"\n  [INTRADAY] {reason}  {trade.direction}"
            f"  {trade.option_type}{trade.strike}"
            f"  entry=Rs.{trade.entry_price:.2f} → exit=Rs.{exit_px:.2f}"
            f"  P&L: {sign}Rs.{trade.pnl:,.0f}"
            f"  day_pnl={sign}Rs.{self.day_pnl:,.0f}"
        )

        if self.mode == "live" or trade.is_paper is False:
            self._place_sell(trade, expiry, exit_px)

        self._update_status(
            direction="NEUTRAL",
            trade_event={
                "event":       reason,
                "instrument":  "NIFTY",
                "direction":   trade.direction,
                "option_type": trade.option_type,
                "strike":      trade.strike,
                "price":       round(exit_px, 2),
                "quantity":    trade.quantity,
                "pnl":         round(trade.pnl, 2),
                "exit_reason": reason,
                "window":      trade.setup_type,
            },
        )
        return reason

    # ── Trade monitoring ──────────────────────────────────────────────────────

    def _monitor_trade(self, expiry: date) -> Optional[str]:
        """
        Check LTP against stops/targets. Returns exit reason if closed.
        Also implements breakeven trail.
        """
        trade = self.open_trade
        spot  = get_spot_price("NIFTY") or 0.0
        ltp   = self._get_ltp(spot or trade.entry_price, expiry,
                               trade.strike, trade.option_type)
        pnl   = (ltp - trade.entry_price) * trade.quantity

        # Activate breakeven stop when half-target is reached
        if not trade.be_triggered and ltp >= trade.be_price:
            trade.be_triggered = True
            trade.stop_price   = trade.entry_price   # move stop to entry (BE)
            log.info(f"Breakeven trail activated: stop now at Rs.{trade.entry_price:.2f}")
            self._update_status(
                direction=trade.direction,
                signal=(
                    f"BE TRAIL  {trade.option_type}{trade.strike}"
                    f"  LTP=Rs.{ltp:.2f}  stop→Rs.{trade.entry_price:.2f} (breakeven)"
                ),
                notable=True,
            )

        # Check exits
        if ltp >= trade.target_price:
            return self._close_trade("TARGET_HIT", expiry)
        if ltp <= trade.stop_price:
            reason = "BE_STOP" if trade.be_triggered else "STOP_LOSS"
            return self._close_trade(reason, expiry)

        # Live status
        self._update_status(
            direction=trade.direction,
            signal=(
                f"POS OPEN  {trade.direction}  {trade.option_type}{trade.strike}"
                f"  LTP=Rs.{ltp:.2f}"
                f"  pnl={'+'if pnl>=0 else ''}Rs.{pnl:,.0f}"
                f"  tgt=Rs.{trade.target_price:.2f}"
                f"  stop=Rs.{trade.stop_price:.2f}"
                f"  {'[BE active]' if trade.be_triggered else ''}"
            ),
        )
        return None

    # ── Status callback ───────────────────────────────────────────────────────

    def _update_status(self, direction: str = "NEUTRAL",
                       trade_event: dict = None,
                       signal: str = None, notable: bool = False) -> None:
        if not self._status_callback:
            return
        wins  = sum(1 for t in self.trades if t.pnl > 0)
        total = len(self.trades)
        kwargs = dict(
            direction=direction,
            score=0,
            budget=self.max_trade_rs,
            real_pnl=self.day_pnl  if self.mode == "live"  else 0.0,
            paper_pnl=self.day_pnl if self.mode == "paper" else 0.0,
            open_positions=1 if self.open_trade else 0,
            trades_today=total,
            wins_today=wins,
        )
        if trade_event:
            kwargs["trade_event"] = trade_event
        if signal:
            kwargs["signal"]  = signal
            kwargs["notable"] = notable
        self._status_callback(**kwargs)

    # ── Pre-market ────────────────────────────────────────────────────────────

    def _pre_market(self, expiry: date) -> None:
        print("\n  Nifty Intraday — pre-market setup...")
        try:
            self.vix = get_india_vix() or 15.0
        except Exception:
            self.vix = 15.0
        print(f"  India VIX: {self.vix:.1f}")

        try:
            self.day_open = get_day_open_spot("NIFTY") or 0.0
        except Exception:
            self.day_open = 0.0

        spot = get_spot_price("NIFTY") or self.day_open or 0.0
        if spot:
            print(f"  Current spot: {spot:,.1f}")

        print(
            f"\n  Risk config:\n"
            f"    Stop/trade: Rs.{self.hard_stop_rs:,}  |  Target/trade: Rs.{self.profit_target_rs:,}\n"
            f"    Daily loss lock: Rs.{self.daily_loss_lock_rs:,}"
            f"  |  Daily profit lock: Rs.{self.daily_profit_lock_rs:,}\n"
            f"    Cooldown: {self.cooldown_loss_min}min (stop) / {self.cooldown_profit_min}min (profit)\n"
            f"    Hard close: {self.close_h:02d}:{self.close_m:02d}  |  Expiry: {expiry}\n"
        )

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        today  = date.today()
        expiry = get_nifty_weekly_expiry(today)
        exp_str = expiry.strftime("%Y%m%d")

        print(
            f"\n  ══════════════════════════════════════════════════════════\n"
            f"  NIFTY INTRADAY  |  {today}  |  {'LIVE' if self.mode == 'live' else 'PAPER'}\n"
            f"  Capital: Rs.50,000  |  Max/trade: Rs.{self.max_trade_rs:,}"
            f"  |  Stop: Rs.{self.hard_stop_rs:,}  |  Target: Rs.{self.profit_target_rs:,}\n"
            f"  ══════════════════════════════════════════════════════════"
        )
        self._pre_market(expiry)

        self._update_status(
            signal=(
                f"Pre-mkt ✓  VIX={self.vix:.1f}"
                f"  expiry={expiry}"
                f"  stop=Rs.{self.hard_stop_rs:,}/trade"
                f"  target=Rs.{self.profit_target_rs:,}/trade"
                f"  daily_lock=Rs.{self.daily_loss_lock_rs:,}"
            ),
            notable=True,
        )

        try:
            while True:
                now     = self._now()
                now_hm  = _dt.time(now.hour, now.minute)
                close_t = _dt.time(self.close_h, self.close_m)

                # ── Hard close ────────────────────────────────────────────────
                if now_hm >= close_t:
                    if self.open_trade:
                        self._close_trade("TIME_EXIT", expiry)
                    self._print_eod()
                    return

                # ── State: IN_TRADE — monitor exit ────────────────────────────
                if self.state == "IN_TRADE":
                    if self.open_trade:
                        exit_reason = self._monitor_trade(expiry)
                        if exit_reason:
                            # Post-exit: set cooldown
                            if "STOP" in exit_reason:
                                self.cooldown_until = now + timedelta(minutes=self.cooldown_loss_min)
                                self.state = "COOLDOWN"
                                print(f"  Cooldown: {self.cooldown_loss_min} min (stop hit)")
                                self._update_status(
                                    signal=f"COOLDOWN {self.cooldown_loss_min}min after stop  day_pnl=Rs.{self.day_pnl:+,.0f}",
                                    notable=True,
                                )
                            else:  # TARGET_HIT, TIME_EXIT, BE_STOP
                                self.cooldown_until = now + timedelta(minutes=self.cooldown_profit_min)
                                self.state = "COOLDOWN"
                                print(f"  Cooldown: {self.cooldown_profit_min} min (profit/time)")
                                self._update_status(
                                    signal=f"COOLDOWN {self.cooldown_profit_min}min after profit  day_pnl=Rs.{self.day_pnl:+,.0f}",
                                    notable=True,
                                )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── State: COOLDOWN ───────────────────────────────────────────
                if self.state == "COOLDOWN":
                    if self.cooldown_until and now >= self.cooldown_until:
                        self.state = "IDLE"
                        self._update_status(signal="Cooldown over — scanning for setups")
                    else:
                        remaining = int((self.cooldown_until - now).total_seconds() / 60) + 1
                        spot = get_spot_price("NIFTY") or 0.0
                        self._update_status(
                            signal=(
                                f"COOLDOWN {remaining}min remaining"
                                f"  spot={spot:,.0f}"
                                f"  day_pnl=Rs.{self.day_pnl:+,.0f}"
                            )
                        )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── State: LOCKED_LOSS ────────────────────────────────────────
                if self.state == "LOCKED_LOSS":
                    self._update_status(
                        signal=f"LOCKED: daily loss Rs.{-self.day_pnl:,.0f} ≥ limit Rs.{self.daily_loss_lock_rs:,}"
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Daily gates (IDLE / LOCKED_PROFIT) ───────────────────────
                if self.day_pnl <= -self.daily_loss_lock_rs:
                    print(f"\n  DAILY LOSS LOCK: Rs.{self.day_pnl:,.0f}. No more trades today.")
                    self.state = "LOCKED_LOSS"
                    self._update_status(
                        signal=f"DAILY LOSS LOCK — Rs.{self.day_pnl:,.0f} ≥ limit — all trading stopped",
                        notable=True,
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                half_size = False
                if self.day_pnl >= self.daily_profit_lock_rs:
                    if self.state != "LOCKED_PROFIT":
                        print(f"\n  DAILY PROFIT LOCK: Rs.{self.day_pnl:,.0f}. Half-size mode.")
                        self.state = "LOCKED_PROFIT"
                        self._update_status(
                            signal=f"DAILY PROFIT LOCK Rs.{self.day_pnl:,.0f} — switching to half-size",
                            notable=True,
                        )
                    if self.half_size_at_lock:
                        half_size = True
                    else:
                        self._update_status(signal=f"PROFIT LOCKED Rs.{self.day_pnl:,.0f} — trading stopped")
                        time.sleep(TICK_SECONDS)
                        continue

                # ── Avoid first N minutes unless exceptional ──────────────────
                elapsed = self._market_minutes_elapsed()
                if elapsed < self.avoid_first_min:
                    spot = get_spot_price("NIFTY") or 0.0
                    self._update_status(
                        signal=(
                            f"First {self.avoid_first_min}min avoidance ({elapsed}min elapsed)"
                            f"  spot={spot:,.0f}  regime={self.regime}"
                        )
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Build ORB (once) ──────────────────────────────────────────
                self._update_orb()

                # ── Refresh regime every 15 min ───────────────────────────────
                if (self.regime_ts is None or
                        (now - self.regime_ts).total_seconds() >= 900):
                    self.regime    = self._classify_regime()
                    self.regime_ts = now
                    self._update_status(
                        signal=f"Regime: {self.regime}  ORB={self.orb_high:.0f}/{self.orb_low:.0f}"
                               f"  VIX={self.vix:.1f}",
                        notable=True,
                    )

                # ── Scan for entry setup ──────────────────────────────────────
                df1  = self._bars("1m")
                spot = get_spot_price("NIFTY")
                if df1.empty or not spot:
                    time.sleep(TICK_SECONDS)
                    continue

                signal = None
                if self.regime == "TREND":
                    signal = self._scan_trend_setup(df1, spot)
                else:
                    signal = self._scan_range_setup(df1)

                if not signal:
                    rsi_val = _rsi(df1["Close"], 14) if len(df1) >= 15 else 50.0
                    self._update_status(
                        signal=(
                            f"Scanning [{self.regime}]"
                            f"  {now.strftime('%H:%M')}"
                            f"  spot={spot:,.0f}"
                            f"  RSI={rsi_val:.0f}"
                            f"  orb={self.orb_high:.0f}/{self.orb_low:.0f}"
                            f"  day_pnl=Rs.{self.day_pnl:+,.0f}"
                            f"  trades={self._trade_count}"
                        )
                    )
                    print(
                        f"  [INTRADAY] {now.strftime('%H:%M')}"
                        f"  spot={spot:,.1f}"
                        f"  regime={self.regime}"
                        f"  RSI={rsi_val:.0f}"
                        f"  day_pnl=Rs.{self.day_pnl:+,.0f}"
                        f"  scanning...",
                        end="\r",
                    )
                    time.sleep(TICK_SECONDS)
                    continue

                # ── Valid signal — compute entry ──────────────────────────────
                direction = signal["direction"]
                opt_type  = "CE" if direction == "BULLISH" else "PE"
                strike    = self._pick_strike(spot, direction)
                ltp       = self._get_ltp(spot, expiry, strike, opt_type)

                entry_prem = ltp * (1 + self.slippage)
                qty, stop_prem, target_prem, be_prem = self._compute_qty(entry_prem, half_size)

                if qty == 0:
                    self._update_status(
                        signal=(
                            f"Signal {direction} {opt_type}{strike}"
                            f"  LTP=Rs.{ltp:.2f} — qty=0 (budget/stop rule) → skip"
                        ),
                        notable=True,
                    )
                    log.info(f"Signal skipped: qty=0 for {opt_type}{strike} @ Rs.{ltp:.2f}")
                    time.sleep(TICK_SECONDS)
                    continue

                budget_used   = entry_prem * qty
                expected_loss = (entry_prem - stop_prem) * qty
                expected_gain = (target_prem - entry_prem) * qty

                print(
                    f"\n  ▶ [INTRADAY] ENTRY  {direction}  {opt_type}{strike}"
                    f"  setup={signal['setup']}"
                    f"\n    qty={qty} ({qty//self.lot_size} lot{'s' if qty//self.lot_size>1 else ''})"
                    f"  premium=Rs.{entry_prem:.2f}"
                    f"  budget=Rs.{budget_used:,.0f}"
                    f"\n    stop=Rs.{stop_prem:.2f} (max_loss=Rs.{expected_loss:,.0f})"
                    f"  target=Rs.{target_prem:.2f} (gain=Rs.{expected_gain:,.0f})"
                    f"\n    note: {signal['note']}"
                )

                trade = IntradayTrade(
                    direction=direction,
                    option_type=opt_type,
                    strike=strike,
                    expiry_str=exp_str,
                    entry_price=round(entry_prem, 2),
                    stop_price=round(stop_prem, 2),
                    target_price=round(target_prem, 2),
                    be_price=round(be_prem, 2),
                    quantity=qty,
                    entry_time=now.strftime("%H:%M:%S"),
                    setup_type=signal["setup"],
                    is_paper=(self.mode == "paper"),
                )

                if self.mode == "live":
                    self._place_buy(trade, expiry)
                    if not trade.order_id:
                        self._update_status(
                            signal=f"ORDER FAILED — NOT tracking {opt_type}{strike}. Place manually on Kite.",
                            notable=True,
                        )
                        time.sleep(TICK_SECONDS)
                        continue
                    order_status = f"ORDER PLACED #{trade.order_id}"
                else:
                    trade.order_id = "PAPER"
                    order_status   = "PAPER trade recorded"

                self._update_status(
                    direction=direction,
                    signal=(
                        f"ENTRY {direction} {opt_type}{strike}"
                        f"  {signal['setup']}"
                        f"  entry=Rs.{entry_prem:.2f}"
                        f"  stop=Rs.{stop_prem:.2f}"
                        f"  tgt=Rs.{target_prem:.2f}"
                        f"  qty={qty}"
                        f"  {order_status}"
                    ),
                    notable=True,
                )
                self._update_status(
                    direction=direction,
                    trade_event={
                        "event":       "ENTRY",
                        "instrument":  "NIFTY",
                        "direction":   direction,
                        "option_type": opt_type,
                        "strike":      strike,
                        "price":       round(entry_prem, 2),
                        "quantity":    qty,
                        "pnl":         "",
                        "exit_reason": "",
                        "window":      signal["setup"],
                    },
                )

                self.open_trade = trade
                self.state      = "IN_TRADE"
                time.sleep(TICK_SECONDS)

        except KeyboardInterrupt:
            print("\n\n  Ctrl+C — closing position...")
            if self.open_trade:
                self._close_trade("FORCE_CLOSE", expiry)
            self._print_eod()

    def _print_eod(self) -> None:
        print(f"\n  {'─'*60}")
        print(f"  Nifty Intraday EOD  |  {date.today()}")
        print(f"  {'─'*60}")
        wins = sum(1 for t in self.trades if t.pnl > 0)
        if not self.trades:
            print("  No trades fired today.")
        else:
            for t in self.trades:
                sign = "+" if t.pnl >= 0 else ""
                print(
                    f"  {t.setup_type:<14}  {t.direction:<8}"
                    f"  {t.option_type}{t.strike:<6}"
                    f"  {t.entry_time}→{t.exit_time}"
                    f"  P&L: {sign}Rs.{t.pnl:,.0f}"
                    f"  ({t.exit_reason})"
                )
            print(f"  {'─'*60}")
            print(f"  Win rate: {wins}/{len(self.trades)}"
                  f"  ({wins/len(self.trades)*100:.0f}%)")
        sign = "+" if self.day_pnl >= 0 else ""
        print(f"  Total day P&L: {sign}Rs.{self.day_pnl:,.0f}")
        print(f"  {'─'*60}\n")
