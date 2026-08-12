"""
TRAP_CMCD_v1 — Trap Trading + CMCD intraday Nifty option buyer (3-minute).
─────────────────────────────────────────────────────────────────────────
Reversal-only. Fades a failed stop-hunt (the "trap") at a FRESH demand/supply
zone, back toward the Golden Line (VWAP), and rides zone-to-zone.

Reuses platform building blocks — nothing here reinvents indicators:
  - Zones   : src.research.gti.gti_zones.detect_zones / active_zones (freshness)
  - 3m bars : src.api.charts.fresh_chart("NIFTY","3m")  (Kite + resample)
  - Golden Line / squeeze / candle colors : computed on those bars
  - Orders  : self.broker.place_order(Order(...))  (paper or live, injected)

Mirrors the NiftyIntradayLive engine contract:
  __init__(strategy_config, broker, mode="paper", status_callback=None)
  run()  — blocking loop, checks self._stop_event, hard-closes 15:10.

SAFETY: registry ships this paper + live_blocked. No auto-live path here beyond
the shared broker; live still needs the platform arm+confirm. UNVALIDATED — see
RULEBOOK.md and the GTI-zone verdict in CLAUDE.md.
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
from src.data.market_data import get_spot_price, get_india_vix, get_day_open_spot
from src.utils.market_calendar import get_nifty_weekly_expiry
from src.utils.helpers import round_to_strike
from src.utils.logger import get_strategy_logger

log = get_strategy_logger("trap_cmcd_live", "TRAP_CMCD_v1")
IST = timezone(timedelta(hours=5, minutes=30))
TICK_SECONDS = 60


# ── Indicator helpers (computed on the 3m OHLCV df, lowercase columns) ──────────

def _vwap(df: pd.DataFrame) -> float:
    """Session VWAP = Golden Line. df is today's bars only."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (tp * df["volume"]).cumsum()
    vv = df["volume"].cumsum().replace(0, pd.NA)
    s = (pv / vv)
    return float(s.iloc[-1]) if not s.empty and pd.notna(s.iloc[-1]) else float(df["close"].iloc[-1])


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
    return float(a.iloc[-1]) if not a.empty else 0.0


def _squeeze_on(df: pd.DataFrame, period: int = 20) -> bool:
    """TTM squeeze: Bollinger band inside Keltner channel = Compression."""
    if len(df) < period:
        return False
    c = df["close"]
    basis = c.rolling(period).mean()
    dev = 2.0 * c.rolling(period).std(ddof=0)
    bb_u, bb_l = basis + dev, basis - dev
    kc_mid = c.ewm(span=period, adjust=False).mean()
    h, l, pc = df["high"], df["low"], c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    kc_dev = 1.5 * tr.rolling(period).mean()
    try:
        return bool(bb_u.iloc[-1] < (kc_mid.iloc[-1] + kc_dev.iloc[-1]) and
                    bb_l.iloc[-1] > (kc_mid.iloc[-1] - kc_dev.iloc[-1]))
    except Exception:
        return False


def _classify(df: pd.DataFrame, cfg: "TrapParams") -> list[str]:
    """Return Blue/Black/Yellow/Neutral per bar. Yellow (reversal) wins ties."""
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    rng = (h - l).replace(0, pd.NA)
    body_ratio = (c - o).abs() / rng
    # "activity" = the imbalance proxy. On volume-bearing data (futures) use the
    # volume z; on no-volume data (spot index) fall back to RANGE-expansion vs its
    # rolling average — the same fallback detect_zones uses. Selected via candle_source.
    if getattr(cfg, "candle_source", "volume") == "range":
        avg_r = rng.rolling(cfg.vol_avg_lookback, min_periods=3).mean()
        vmult = rng / avg_r
    else:
        avg_v = v.rolling(cfg.vol_avg_lookback, min_periods=3).mean()
        vmult = v / avg_v
    close_pos = (c - l) / rng
    up_wick = (h - o.combine(c, max)) / rng
    dn_wick = (o.combine(c, min) - l) / rng
    out = []
    for i in range(len(df)):
        br = body_ratio.iloc[i]; vm = vmult.iloc[i]; cp = close_pos.iloc[i]
        uw = up_wick.iloc[i]; dw = dn_wick.iloc[i]; body = c.iloc[i] - o.iloc[i]
        if pd.isna(br) or pd.isna(vm):
            out.append("NEUTRAL"); continue
        big = vm >= cfg.yellow_vol_mult
        yellow = big and ((uw >= cfg.yellow_wick_ratio or dw >= cfg.yellow_wick_ratio)
                          or br <= (1 - cfg.strong_body_ratio))
        strong = br >= cfg.strong_body_ratio and vm >= cfg.blue_black_vol_mult
        if yellow:
            out.append("YELLOW")
        elif body > 0 and strong and cp >= 0.6:
            out.append("BLUE")
        elif body < 0 and strong and cp <= 0.4:
            out.append("BLACK")
        else:
            out.append("NEUTRAL")
    return out


# ── Params ──────────────────────────────────────────────────────────────────────

@dataclass
class TrapParams:
    capital_per_trade_pct: float = 0.10
    max_trade_rs: int = 10000
    hard_sl_points: float = 35.0
    min_target_points: float = 25.0
    trail_giveback_points: float = 20.0
    breakeven_lock_points: float = 2.0
    zone_to_zone: bool = True
    daily_loss_lock_rs: float = 3500
    max_trades_per_day: int = 6
    cooldown_min: int = 10
    vol_avg_lookback: int = 20
    blue_black_vol_mult: float = 1.3
    strong_body_ratio: float = 0.55
    yellow_vol_mult: float = 1.8
    yellow_wick_ratio: float = 0.55
    candle_source: str = "volume"      # "volume" (futures) | "range" (spot, no volume)
    zone_max_distance_pct: float = 0.6
    require_fresh_zone: bool = True
    min_trap_candles: int = 2
    trap_scan_window: int = 6
    require_compression: bool = False
    require_mean_reversion: bool = True
    expiry_only: bool = False           # only trade on weekly-expiry days (Tuesdays)
    # ── non-expiry thesis knobs (0 = off) ──────────────────────────────────────
    min_opp_distance_atr: float = 0.0   # reject unless the opposite zone is this
                                        # many ATRs away (the ride must clear theta)
    no_progress_bars: int = 0           # exit if +min_target not reached within N bars


@dataclass
class TrapTrade:
    direction: str            # BULLISH / BEARISH
    option_type: str          # CE / PE
    strike: int
    expiry_str: str
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float
    opposite_zone: float
    reasons: list[str]
    peak: float = 0.0
    activated: bool = False
    order_id: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0


# ── Engine ──────────────────────────────────────────────────────────────────────

class TrapCMCDLive:
    STATES = ("IDLE", "IN_TRADE", "COOLDOWN", "LOCKED_LOSS")

    def __init__(self, strategy_config: dict, broker: BaseBroker,
                 mode: str = "paper", status_callback=None):
        self.sc = strategy_config
        self.broker = broker
        self.mode = mode
        self._status_callback = status_callback
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065))

        p = strategy_config.get("trap_cmcd", {})
        self.p = TrapParams(**{k: p[k] for k in p if k in TrapParams.__dataclass_fields__})
        self.capital = float(strategy_config.get("capital_allocated_rs", 50000))

        inst = strategy_config.get("instruments", {}).get("nifty", {})
        self.lot_size = inst.get("lot_size", 65)
        self.step = inst.get("strike_step", 50)

        cs = p.get("hard_close_time", "15:10")
        self.close_h, self.close_m = int(cs[:2]), int(cs[3:])
        na = p.get("no_new_trades_after", "15:00")
        self.no_new_h, self.no_new_m = int(na[:2]), int(na[3:])

        self.state = "IDLE"
        self.day_pnl = 0.0
        self.trades: list[TrapTrade] = []
        self.open_trade: Optional[TrapTrade] = None
        self.cooldown_until: Optional[datetime] = None
        self.vix = 15.0

    # ── timing ──────────────────────────────────────────────────────────────
    def _now(self) -> datetime:
        return datetime.now(IST)

    def _t_hours_to_close(self) -> float:
        now = self._now()
        close = now.replace(hour=self.close_h, minute=self.close_m, second=0, microsecond=0)
        return max((close - now).total_seconds(), 60) / 3600

    # ── bars: reuse the platform's 3m Kite chart + zone converter ────────────
    def _bars_3m(self) -> Optional[pd.DataFrame]:
        from src.api.charts import fresh_chart
        from src.api.gti_zones_live import _bars_to_df
        try:
            chart = fresh_chart("NIFTY", "3m")
            df = _bars_to_df(chart.get("bars") or [])
        except Exception as e:
            log.warning(f"3m bars fetch failed: {e}")
            return None
        if df is None or len(df) < 40:
            return None
        try:
            df = df[df.index.date == date.today()]
        except Exception:
            pass
        return df if len(df) >= 20 else None

    def _zones_near(self, df: pd.DataFrame, ltp: float):
        from src.research.gti.gti_zones import detect_zones, active_zones, ZoneConfig
        try:
            zones = detect_zones(df, ZoneConfig(max_active_zones=0))
            return active_zones(zones, ltp, max_distance_pct=self.p.zone_max_distance_pct,
                                include_mitigated=False)
        except Exception as e:
            log.warning(f"zone detect failed: {e}")
            return []

    # ── option pricing (real quote in paper too; model fallback) ─────────────
    def _get_ltp(self, spot: float, expiry: date, strike: int, opt_type: str) -> float:
        try:
            ts, exch = self.broker.get_tradingsymbol("NIFTY", expiry, strike, opt_type)
            px = self.broker.get_ltp(ts, exch, strike, opt_type, expiry.strftime("%Y%m%d"))
            if px and px > 0:
                return float(px)
        except Exception:
            pass
        return float(self.pricer.price(spot, strike, self.vix, self._t_hours_to_close(), opt_type).price)

    def _resolve_sym(self, expiry: date, strike: int, opt_type: str) -> tuple[str, str]:
        if self.mode == "live":
            from src.broker.kite_broker import KiteBroker
            if isinstance(self.broker, KiteBroker):
                try:
                    return self.broker.get_tradingsymbol("NIFTY", expiry, strike, opt_type)
                except Exception as e:
                    log.warning(f"get_tradingsymbol failed ({e}) — fallback")
        return "NIFTY", "NFO"

    # ── sizing: 10% of capital, capped at max_trade_rs ───────────────────────
    def _compute_qty(self, entry_prem: float) -> int:
        if entry_prem <= 0:
            return 0
        budget = min(self.capital * self.p.capital_per_trade_pct, self.p.max_trade_rs)
        lots = int(budget / (entry_prem * self.lot_size))
        return lots * self.lot_size if lots >= 1 else 0

    # ── the trap ─────────────────────────────────────────────────────────────
    def _detect(self, df: pd.DataFrame, near_zones, vwap: float, squeeze: bool):
        p = self.p
        colors = _classify(df, p)
        if len(colors) < p.trap_scan_window + 1:
            return None
        cur_color = colors[-1]
        prev = colors[-(p.trap_scan_window + 1):-1]
        seg = df.iloc[-(p.trap_scan_window + 1):]
        cur = df.iloc[-1]
        close = float(cur["close"])
        avg_v = float(df["volume"].tail(p.vol_avg_lookback).mean() or 0)
        whale = float(cur["volume"]) >= p.yellow_vol_mult * avg_v if avg_v else False

        def fresh(z):
            return (not p.require_fresh_zone) or getattr(z, "tests", 1) == 0

        demand = [z for z in near_zones if z.side == "demand" and fresh(z)]
        supply = [z for z in near_zones if z.side == "supply" and fresh(z)]

        # LONG — Whale 'W' in a demand zone
        if demand and (not p.require_compression or squeeze) and \
                (not p.require_mean_reversion or close < vwap):
            n_black = sum(1 for c in prev if c == "BLACK")
            if n_black >= p.min_trap_candles:
                trap = seg.iloc[:-1]
                trap_low = float(trap["low"].min())
                trap_high = float(trap["high"].max())
                holding = float(cur["low"]) >= trap_low - 1e-6
                confirm = cur_color == "YELLOW" or (cur_color == "BLUE" and close > trap_high)
                clean_break = float(cur["close"]) < float(demand[0].distal) and \
                    float(df["close"].iloc[-2]) < float(demand[0].distal) and float(cur["volume"]) > avg_v
                if holding and confirm and not clean_break:
                    reasons = [f"Manipulation detected: {n_black} Black candles trapped in Buying Zone"]
                    if whale:
                        reasons.append("Whale Bubble (volume-spike proxy)")
                    reasons.append(f"Fresh demand zone {demand[0].distal:.0f}-{demand[0].proximal:.0f}")
                    reasons.append(f"Below Golden Line (VWAP {vwap:.0f}) -> magnet up")
                    supply_above = [z.proximal for z in supply if z.proximal > close]
                    opp = min(supply_above) if supply_above else close + 8 * _atr(df)
                    if p.min_opp_distance_atr > 0:
                        a = _atr(df)
                        if a <= 0 or abs(opp - close) < p.min_opp_distance_atr * a:
                            return None
                    return ("BULLISH", trap_high, opp, reasons)

        # SHORT — Whale 'M' in a supply zone
        if supply and (not p.require_compression or squeeze) and \
                (not p.require_mean_reversion or close > vwap):
            n_blue = sum(1 for c in prev if c == "BLUE")
            if n_blue >= p.min_trap_candles:
                trap = seg.iloc[:-1]
                trap_high = float(trap["high"].max())
                trap_low = float(trap["low"].min())
                holding = float(cur["high"]) <= trap_high + 1e-6
                confirm = cur_color == "YELLOW" or (cur_color == "BLACK" and close < trap_low)
                clean_break = float(cur["close"]) > float(supply[0].distal) and \
                    float(df["close"].iloc[-2]) > float(supply[0].distal) and float(cur["volume"]) > avg_v
                if holding and confirm and not clean_break:
                    reasons = [f"Manipulation detected: {n_blue} Blue candles trapped in Selling Zone"]
                    if whale:
                        reasons.append("Whale Bubble (volume-spike proxy)")
                    reasons.append(f"Fresh supply zone {supply[0].proximal:.0f}-{supply[0].distal:.0f}")
                    reasons.append(f"Above Golden Line (VWAP {vwap:.0f}) -> magnet down")
                    demand_below = [z.proximal for z in demand if z.proximal < close]
                    opp = max(demand_below) if demand_below else close - 8 * _atr(df)
                    if p.min_opp_distance_atr > 0:
                        a = _atr(df)
                        if a <= 0 or abs(opp - close) < p.min_opp_distance_atr * a:
                            return None
                    return ("BEARISH", trap_low, opp, reasons)
        return None

    # ── orders ───────────────────────────────────────────────────────────────
    def _enter(self, signal, spot: float, expiry: date):
        direction, trap_level, opp_zone, reasons = signal
        opt = "CE" if direction == "BULLISH" else "PE"
        atm = round_to_strike(spot, self.step)
        strike = int(atm)
        prem = self._get_ltp(spot, expiry, strike, opt)
        qty = self._compute_qty(prem)
        if qty < 1 or prem <= 0:
            log.info(f"skip: cannot size (prem={prem}, qty={qty})")
            return
        stop = max(0.05, prem - self.p.hard_sl_points)
        target = prem + self.p.min_target_points
        t = TrapTrade(direction=direction, option_type=opt, strike=strike,
                      expiry_str=expiry.strftime("%Y%m%d"), quantity=qty,
                      entry_price=prem, stop_price=round(stop, 2), target_price=round(target, 2),
                      opposite_zone=opp_zone, reasons=reasons, peak=prem)
        sym, exch = self._resolve_sym(expiry, strike, opt)
        try:
            t.order_id = self.broker.place_order(Order(
                symbol=sym, exchange=exch, option_type=opt, strike=strike,
                expiry=t.expiry_str, transaction="BUY", quantity=qty))
        except Exception as e:
            log.error(f"BUY failed: {e}")
            return
        self.open_trade = t
        self.state = "IN_TRADE"
        why = " | ".join(reasons)
        log.info(f"[{self.mode.upper()}] BUY {opt}{strike} qty={qty} @ Rs.{prem:.1f} — {why}")
        self._update_status(
            direction=direction,
            signal=(f"BUY {opt} {strike} @ Rs.{prem:.1f} | Whale "
                    f"'{'W' if direction=='BULLISH' else 'M'}' | {why} | "
                    f"SL {self.p.hard_sl_points:.0f}pts Tgt {self.p.min_target_points:.0f}+ "
                    f"then zone-to-zone ({opp_zone:.0f}) | 10% capital (Brain-Freeze guard)"),
            notable=True,
            trade_event={"event": "ENTRY", "instrument": "NIFTY", "strike": strike,
                         "option_type": opt, "direction": direction,
                         "price": round(prem, 1), "quantity": qty, "mode": self.mode,
                         "reason": why, "time": self._now().strftime("%H:%M:%S")})

    def _exit(self, reason: str, exit_prem: float):
        t = self.open_trade
        if t is None:
            return
        sym, exch = self._resolve_sym(
            datetime.strptime(t.expiry_str, "%Y%m%d").date(), t.strike, t.option_type)
        for attempt in range(1, 4):
            try:
                self.broker.place_order(Order(
                    symbol=sym, exchange=exch, option_type=t.option_type, strike=t.strike,
                    expiry=t.expiry_str, transaction="SELL", quantity=t.quantity))
                break
            except Exception as e:
                log.error(f"SELL {attempt}/3 failed: {e}")
                if attempt < 3:
                    time.sleep(2 * attempt)
        t.exit_price = exit_prem
        t.exit_reason = reason
        t.pnl = (exit_prem - t.entry_price) * t.quantity
        self.day_pnl += t.pnl
        self.trades.append(t)
        self.open_trade = None
        sign = "+" if t.pnl >= 0 else ""
        log.info(f"EXIT {reason} {t.option_type}{t.strike} @ Rs.{exit_prem:.1f} "
                 f"pnl={sign}Rs.{t.pnl:,.0f}")
        self._update_status(
            signal=f"EXIT {reason} {t.option_type}{t.strike} @ Rs.{exit_prem:.1f} "
                   f"pnl={sign}Rs.{t.pnl:,.0f}",
            notable=True,
            trade_event={"event": "EXIT", "instrument": "NIFTY", "strike": t.strike,
                         "option_type": t.option_type, "direction": t.direction,
                         "price": round(exit_prem, 1), "quantity": t.quantity,
                         "pnl": round(t.pnl, 0), "mode": self.mode, "reason": reason,
                         "time": self._now().strftime("%H:%M:%S")})
        if self.day_pnl <= -abs(self.p.daily_loss_lock_rs):
            self.state = "LOCKED_LOSS"
            log.warning("daily loss lock hit — no more trades today")
        else:
            self.state = "COOLDOWN"
            self.cooldown_until = self._now() + timedelta(minutes=self.p.cooldown_min)

    def _monitor(self, spot: float, expiry: date):
        t = self.open_trade
        prem = self._get_ltp(spot, expiry, t.strike, t.option_type)
        t.peak = max(t.peak, prem)
        now = self._now()
        if _dt.time(now.hour, now.minute) >= _dt.time(self.close_h, self.close_m):
            return self._exit("TIME_EXIT", prem)
        # min target -> lock + zone-to-zone ratchet
        if prem >= t.target_price:
            t.activated = True
            t.stop_price = max(t.stop_price, t.entry_price + self.p.breakeven_lock_points)
        if t.activated and self.p.zone_to_zone:
            t.stop_price = max(t.stop_price, t.peak - self.p.trail_giveback_points)
            at_zone = (spot >= t.opposite_zone) if t.option_type == "CE" else (spot <= t.opposite_zone)
            if at_zone:
                return self._exit("ZONE_TO_ZONE", prem)
        if prem <= t.stop_price:
            return self._exit("TRAIL_SL" if t.activated else "HARD_SL", prem)
        self._update_status(
            direction=t.direction,
            signal=(f"POS {t.option_type}{t.strike} LTP=Rs.{prem:.1f} "
                    f"pnl={'+' if (prem-t.entry_price)>=0 else ''}Rs.{(prem-t.entry_price)*t.quantity:,.0f} "
                    f"stop=Rs.{t.stop_price:.1f} tgt=Rs.{t.target_price:.1f} "
                    f"{'[trailing]' if t.activated else ''}"))
        return None

    # ── status ───────────────────────────────────────────────────────────────
    def _update_status(self, direction: str = "NEUTRAL", trade_event: dict = None,
                       signal: str = None, notable: bool = False):
        if not self._status_callback:
            return
        wins = sum(1 for t in self.trades if t.pnl > 0)
        kwargs = dict(direction=direction, score=0,
                      budget=int(min(self.capital * self.p.capital_per_trade_pct, self.p.max_trade_rs)),
                      real_pnl=self.day_pnl if self.mode == "live" else 0.0,
                      paper_pnl=self.day_pnl if self.mode == "paper" else 0.0,
                      open_positions=1 if self.open_trade else 0,
                      trades_today=len(self.trades), wins_today=wins)
        if trade_event:
            kwargs["trade_event"] = trade_event
        if signal:
            kwargs["signal"] = signal
            kwargs["notable"] = notable
        self._status_callback(**kwargs)

    # ── main loop ────────────────────────────────────────────────────────────
    def run(self) -> None:
        today = date.today()
        expiry = get_nifty_weekly_expiry(today)
        self._is_expiry_today = (today == expiry)
        if self.p.expiry_only and not self._is_expiry_today:
            log.info(f"TRAP_CMCD expiry_only=ON and {today} is not an expiry day "
                     f"(next expiry {expiry}) — standing down, no trades today")
        try:
            self.vix = get_india_vix() or 15.0
        except Exception:
            self.vix = 15.0
        log.info(f"TRAP_CMCD {today} {'LIVE' if self.mode=='live' else 'PAPER'} "
                 f"expiry={expiry} VIX={self.vix:.1f}")
        self._update_status(signal=f"Pre-mkt ✓ VIX={self.vix:.1f} expiry={expiry} "
                            f"SL={self.p.hard_sl_points:.0f}pts Tgt={self.p.min_target_points:.0f}+pts "
                            f"(then zone-to-zone) | 10% capital", notable=True)
        try:
            while True:
                if getattr(self, "_stop_event", None) is not None and self._stop_event.is_set():
                    break
                now = self._now()
                now_hm = _dt.time(now.hour, now.minute)

                if now_hm >= _dt.time(self.close_h, self.close_m):
                    if self.open_trade:
                        spot = get_spot_price("NIFTY") or 0.0
                        self._monitor(spot, expiry)
                    break

                if self.state == "IN_TRADE" and self.open_trade:
                    spot = get_spot_price("NIFTY") or 0.0
                    if spot:
                        self._monitor(spot, expiry)
                    time.sleep(TICK_SECONDS)
                    continue

                if self.state == "COOLDOWN":
                    if self.cooldown_until and now >= self.cooldown_until:
                        self.state = "IDLE"
                    else:
                        time.sleep(TICK_SECONDS)
                        continue

                if self.state == "LOCKED_LOSS":
                    time.sleep(TICK_SECONDS)
                    continue

                # IDLE — scan for a trap
                if self.p.expiry_only and not self._is_expiry_today:
                    time.sleep(TICK_SECONDS)
                    continue
                if now_hm >= _dt.time(self.no_new_h, self.no_new_m):
                    time.sleep(TICK_SECONDS)
                    continue
                if len(self.trades) >= self.p.max_trades_per_day:
                    time.sleep(TICK_SECONDS)
                    continue

                df = self._bars_3m()
                spot = get_spot_price("NIFTY") or (float(df["close"].iloc[-1]) if df is not None else 0.0)
                if df is not None and spot:
                    near = self._zones_near(df, spot)
                    vwap = _vwap(df)
                    squeeze = _squeeze_on(df)
                    sig = self._detect(df, near, vwap, squeeze)
                    if sig:
                        self._enter(sig, spot, expiry)
                time.sleep(TICK_SECONDS)
        finally:
            self._print_eod()

    def _print_eod(self):
        wins = sum(1 for t in self.trades if t.pnl > 0)
        log.info(f"EOD TRAP_CMCD — trades={len(self.trades)} wins={wins} "
                 f"day_pnl=Rs.{self.day_pnl:,.0f}")
