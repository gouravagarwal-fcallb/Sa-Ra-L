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

import numpy as np
import pandas as pd

from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import get_spot_price, get_india_vix, get_day_open_spot
from src.utils.market_calendar import get_nifty_weekly_expiry, get_sensex_weekly_expiry
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
    vv = df["volume"].cumsum().replace(0, np.nan)
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
    rng = (h - l).replace(0, np.nan)
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


# ── GTI higher-timeframe context (Weekly POC + Daily/Weekly zones) ───────────────
def tpo_poc(df: pd.DataFrame, bins: int = 50) -> Optional[float]:
    """Time-based (TPO) Point of Control: the price level the MOST bars traded
    through. Spot indices carry no volume, so this uses time-at-price (standard
    Market-Profile POC), not volume-at-price. NON-REPAINTING: pass only CLOSED
    bars (e.g. the completed prior week)."""
    if df is None or len(df) < 5:
        return None
    lo, hi = float(df["low"].min()), float(df["high"].max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    counts = np.zeros(bins)
    los = np.searchsorted(edges, df["low"].values, side="right") - 1
    his = np.searchsorted(edges, df["high"].values, side="right") - 1
    for a, b in zip(los, his):
        a = max(0, min(int(a), bins - 1)); b = max(0, min(int(b), bins - 1))
        counts[a:b + 1] += 1
    i = int(counts.argmax())
    return float((edges[i] + edges[i + 1]) / 2)


@dataclass
class HTFContext:
    """Point-in-time macro context handed to the engine (all non-repainting)."""
    weekly_poc: Optional[float] = None
    zones: list = field(default_factory=list)   # Daily/Weekly Zone objects, point-in-time

    def demand(self):
        return [z for z in self.zones if z.side == "demand"]

    def supply(self):
        return [z for z in self.zones if z.side == "supply"]


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
    # ── GTI institutional-context filters (config-gated, non-repainting) ────────
    max_zone_tests: int = 0             # FRESHNESS: skip a zone tested > N times (GTI=3); 0=off
    yellow_trail: bool = False          # a Yellow candle while in profit -> tighten the
                                        #   trailing stop to that candle's extreme (spot level)
    weekly_poc_veto: bool = False       # (optional HARD veto) skip longs far BELOW / shorts far
                                        #   ABOVE the Weekly POC. Superseded by scoring_targets.
    poc_veto_atr: float = 2.0           # "significantly far" = this many ATRs from the Weekly POC
    htf_confluence: bool = False        # (optional HARD veto) require the trap INSIDE a HTF zone
    # ── Scoring module: Macro zones + Weekly POC size the TARGET, they don't block ──
    scoring_targets: bool = False       # ON: HTF/POC/freshness -> a confluence score that picks
                                        #   the target (scalp +25 vs ride zone-to-zone).
    ride_score_threshold: int = 2       # score >= this -> ride zone-to-zone; below -> bank +25


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
    ride: bool = True                     # scoring: True = ride zone-to-zone, False = bank +25
    score: int = 0                        # confluence score at entry
    yellow_stop: Optional[float] = None   # spot-level stop set by a Yellow candle in profit
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

        # Instrument parameterization: NIFTY (Tue expiry, NFO) or SENSEX (Thu, BFO).
        # India VIX is used as the IV proxy for both (Nifty-based; a mild Sensex
        # approximation). Lot/step default per instrument, config may override.
        self.underlying = str(strategy_config.get("underlying", "NIFTY")).upper()
        _IMAP = {
            "NIFTY":  dict(exch="NFO", step=50,  lot=65, expiry=get_nifty_weekly_expiry),
            "SENSEX": dict(exch="BFO", step=100, lot=20, expiry=get_sensex_weekly_expiry),
        }
        im = _IMAP.get(self.underlying, _IMAP["NIFTY"])
        self.symbol = self.underlying
        self.exchange = im["exch"]
        self.expiry_fn = im["expiry"]
        inst = strategy_config.get("instruments", {}).get(self.underlying.lower(), {})
        self.lot_size = inst.get("lot_size", im["lot"])
        self.step = inst.get("strike_step", im["step"])

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
            chart = fresh_chart(self.symbol, "3m")
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
            ts, exch = self.broker.get_tradingsymbol(self.symbol, expiry, strike, opt_type)
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
                    return self.broker.get_tradingsymbol(self.symbol, expiry, strike, opt_type)
                except Exception as e:
                    log.warning(f"get_tradingsymbol failed ({e}) — fallback")
        return self.symbol, self.exchange

    # ── sizing: 10% of capital, capped at max_trade_rs ───────────────────────
    def _compute_qty(self, entry_prem: float) -> int:
        if entry_prem <= 0:
            return 0
        budget = min(self.capital * self.p.capital_per_trade_pct, self.p.max_trade_rs)
        lots = int(budget / (entry_prem * self.lot_size))
        return lots * self.lot_size if lots >= 1 else 0

    # ── GTI higher-timeframe context (best-effort; cached per day) ────────────
    def _htf_context(self):
        """Daily/Weekly context for the POC/confluence filters. Degrades to None
        (filters skip) on any error, so live never breaks. Only computed when a
        filter is enabled."""
        if not (self.p.weekly_poc_veto or self.p.htf_confluence or self.p.scoring_targets):
            return None
        today = date.today()
        if getattr(self, "_htf_day", None) == today:
            return getattr(self, "_htf", None)
        self._htf_day = today
        self._htf = None
        try:
            from src.api.charts import fresh_chart
            from src.api.gti_zones_live import _bars_to_df
            from src.research.gti.gti_zones import detect_zones, ZoneConfig
            ddf = _bars_to_df(fresh_chart(self.symbol, "1d").get("bars") or [])
            zones = detect_zones(ddf, ZoneConfig(max_active_zones=0)) if ddf is not None else []
            poc = None
            if ddf is not None and len(ddf) >= 5:
                ic = ddf.index.isocalendar()
                wk = pd.Series(list(zip(ic["year"], ic["week"])), index=ddf.index)
                cur = (today.isocalendar()[0], today.isocalendar()[1])
                prior = sorted(w for w in set(wk) if w < cur)
                if prior:
                    poc = tpo_poc(ddf[wk == prior[-1]])
            self._htf = HTFContext(weekly_poc=poc, zones=zones)
        except Exception as e:
            log.warning(f"HTF context unavailable: {e}")
        return self._htf

    # ── confluence score: sizes the TARGET, does not block the trade ──────────
    def _trade_score(self, side: str, close: float, zone, htf, whale: bool) -> int:
        """Confluence score 0-4 from the SCORING module (fresh 3m zone + whale +
        HTF-zone location + Weekly-POC alignment). Higher score -> ride
        zone-to-zone; lower -> bank the +25 scalp. Never blocks the entry."""
        s = 0
        if getattr(zone, "tests", 1) == 0:
            s += 1                                   # fresh 3m GTI zone
        if whale:
            s += 1                                   # whale-bubble volume cluster
        if htf is not None:
            hz = htf.demand() if side == "long" else htf.supply()
            if any(z.contains(close) for z in hz):
                s += 1                               # inside a Daily/Weekly zone
            if htf.weekly_poc is not None:
                aligned = (close <= htf.weekly_poc) if side == "long" else (close >= htf.weekly_poc)
                if aligned:
                    s += 1                           # room to run toward the Weekly-POC magnet
        return s

    # ── the trap ─────────────────────────────────────────────────────────────
    def _detect(self, df: pd.DataFrame, near_zones, vwap: float, squeeze: bool, htf=None):
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
            t = getattr(z, "tests", 1)
            ok_fresh = (not p.require_fresh_zone) or t == 0
            ok_cap = (p.max_zone_tests <= 0) or t <= p.max_zone_tests   # FRESHNESS cap
            return ok_fresh and ok_cap

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
                    # Macro-bias veto: don't buy far BELOW the Weekly POC.
                    if p.weekly_poc_veto and htf is not None and htf.weekly_poc is not None:
                        a = _atr(df)
                        if a > 0 and close < htf.weekly_poc - p.poc_veto_atr * a:
                            return None
                        reasons.append(f"Weekly-POC bias OK (POC {htf.weekly_poc:.0f})")
                    # HTF confluence: the 3m trap must sit inside a Daily/Weekly demand zone.
                    if p.htf_confluence and htf is not None:
                        if not any(z.contains(close) for z in htf.demand()):
                            return None
                        reasons.append("Inside HTF demand zone (Daily/Weekly confluence)")
                    score = self._trade_score("long", close, demand[0], htf, whale)
                    if p.scoring_targets:
                        reasons.append(f"Confluence {score}/4 -> "
                                       f"{'RIDE zone-to-zone' if score >= p.ride_score_threshold else 'SCALP +25pt'}")
                    return ("BULLISH", trap_high, opp, reasons, score)

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
                    # Macro-bias veto: don't sell far ABOVE the Weekly POC.
                    if p.weekly_poc_veto and htf is not None and htf.weekly_poc is not None:
                        a = _atr(df)
                        if a > 0 and close > htf.weekly_poc + p.poc_veto_atr * a:
                            return None
                        reasons.append(f"Weekly-POC bias OK (POC {htf.weekly_poc:.0f})")
                    # HTF confluence: the 3m trap must sit inside a Daily/Weekly supply zone.
                    if p.htf_confluence and htf is not None:
                        if not any(z.contains(close) for z in htf.supply()):
                            return None
                        reasons.append("Inside HTF supply zone (Daily/Weekly confluence)")
                    score = self._trade_score("short", close, supply[0], htf, whale)
                    if p.scoring_targets:
                        reasons.append(f"Confluence {score}/4 -> "
                                       f"{'RIDE zone-to-zone' if score >= p.ride_score_threshold else 'SCALP +25pt'}")
                    return ("BEARISH", trap_low, opp, reasons, score)
        return None

    # ── orders ───────────────────────────────────────────────────────────────
    def _enter(self, signal, spot: float, expiry: date):
        direction, trap_level, opp_zone, reasons, score = signal
        ride = (not self.p.scoring_targets) or (score >= self.p.ride_score_threshold)
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
                      opposite_zone=opp_zone, reasons=reasons, peak=prem, ride=ride, score=score)
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
            trade_event={"event": "ENTRY", "instrument": self.symbol, "strike": strike,
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
            trade_event={"event": "EXIT", "instrument": self.symbol, "strike": t.strike,
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
        # min target reached -> the SCORE decides: bank the +25 scalp, or ride zone-to-zone
        if prem >= t.target_price:
            if not t.ride:
                return self._exit("TARGET", prem)
            t.activated = True
            t.stop_price = max(t.stop_price, t.entry_price + self.p.breakeven_lock_points)
        if t.activated and self.p.zone_to_zone:
            t.stop_price = max(t.stop_price, t.peak - self.p.trail_giveback_points)
            at_zone = (spot >= t.opposite_zone) if t.option_type == "CE" else (spot <= t.opposite_zone)
            if at_zone:
                return self._exit("ZONE_TO_ZONE", prem)
        # Yellow-candle volatility trigger: tighten a spot-level trail to the candle's extreme.
        if self.p.yellow_trail and t.activated:
            try:
                df3 = self._bars_3m()
                if df3 is not None and _classify(df3, self.p)[-1] == "YELLOW":
                    lo, hi = float(df3.iloc[-1]["low"]), float(df3.iloc[-1]["high"])
                    if t.option_type == "CE":
                        t.yellow_stop = max(t.yellow_stop if t.yellow_stop is not None else -1e18, lo)
                    else:
                        t.yellow_stop = min(t.yellow_stop if t.yellow_stop is not None else 1e18, hi)
            except Exception:
                pass
        if t.yellow_stop is not None and \
                ((t.option_type == "CE" and spot <= t.yellow_stop) or
                 (t.option_type == "PE" and spot >= t.yellow_stop)):
            return self._exit("YELLOW_TRAIL", prem)
        if prem <= t.stop_price:
            return self._exit("TRAIL_SL" if t.activated else "HARD_SL", prem)
        self._update_status(
            direction=t.direction,
            signal=(f"POS {t.option_type}{t.strike} LTP=Rs.{prem:.1f} "
                    f"pnl={'+' if (prem-t.entry_price)>=0 else ''}Rs.{(prem-t.entry_price)*t.quantity:,.0f} "
                    f"stop=Rs.{t.stop_price:.1f} tgt=Rs.{t.target_price:.1f} "
                    f"{'[trailing]' if t.activated else ''}"))
        return None

    # ── continuous market read (emitted every scan, expiry day or not) ────────
    def _analyse(self, df, spot, near, vwap, squeeze, htf):
        """A plain-language read of the tape: price vs the Golden Line, the nearest
        zones, compression, the recent candle sequence, the Weekly-POC bias, and
        what the engine is waiting for. Returns (text, direction)."""
        atr = _atr(df) or 1.0
        colors = _classify(df, self.p)
        recent = colors[-5:]
        stretch = (spot - vwap) / atr if atr else 0.0
        dem = sorted([z for z in near if z.side == "demand"], key=lambda z: abs(z.proximal - spot))
        sup = sorted([z for z in near if z.side == "supply"], key=lambda z: abs(z.proximal - spot))
        d = dem[0] if dem else None
        s = sup[0] if sup else None
        poc = htf.weekly_poc if htf is not None else None
        band = self.p.zone_max_distance_pct / 100.0 * spot
        in_d = bool(d and abs(spot - d.proximal) <= band)
        in_s = bool(s and abs(spot - s.proximal) <= band)
        n_black = sum(1 for c in recent if c == "BLACK")
        n_blue = sum(1 for c in recent if c == "BLUE")

        fresh_d = d and getattr(d, "tests", 1) == 0
        fresh_s = s and getattr(s, "tests", 1) == 0

        # Plain-English state + what it is waiting for (the headline for a layman).
        direction = "NEUTRAL"
        if in_d and n_black >= 1:
            state = (f"At a {'fresh ' if fresh_d else ''}SUPPORT zone ({d.proximal:,.0f}) and the "
                     f"selling is failing — if price turns up here it will BUY a CALL")
            direction = "BULLISH"
        elif in_s and n_blue >= 1:
            state = (f"At a {'fresh ' if fresh_s else ''}RESISTANCE zone ({s.proximal:,.0f}) and the "
                     f"buying is stalling — if price turns down here it will BUY a PUT")
            direction = "BEARISH"
        elif squeeze:
            state = "The market is coiling tight (low volatility) — a move may be loading; waiting for a trap"
        elif abs(stretch) >= 2.0:
            state = (f"Price has run far {'above' if stretch > 0 else 'below'} fair value — "
                     f"watching for a snap back")
        elif in_d:
            state = f"Resting on a support zone ({d.proximal:,.0f}) — no failed-selling trap yet"
        elif in_s:
            state = f"Resting under a resistance zone ({s.proximal:,.0f}) — no failed-buying trap yet"
        else:
            state = "Calm and range-bound — waiting for price to reach a zone and set a trap"

        # The week's heavy-trade "magnet" level, in plain words.
        magnet = ""
        if poc:
            magnet = (f"  ·  The week's most-traded price is {poc:,.0f} "
                      f"({'above — pulls up' if poc > spot else 'below — pulls down'})")

        return f"{self.symbol} {spot:,.0f}  ·  {state}{magnet}", direction

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
        expiry = self.expiry_fn(today)
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
                        spot = get_spot_price(self.symbol) or 0.0
                        self._monitor(spot, expiry)
                    break

                if self.state == "IN_TRADE" and self.open_trade:
                    spot = get_spot_price(self.symbol) or 0.0
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

                # IDLE — analyse the market EVERY scan (expiry day or not), then act.
                df = self._bars_3m()
                spot = get_spot_price(self.symbol) or (float(df["close"].iloc[-1]) if df is not None else 0.0)
                observing = self.p.expiry_only and not self._is_expiry_today
                late = now_hm >= _dt.time(self.no_new_h, self.no_new_m)
                maxed = len(self.trades) >= self.p.max_trades_per_day
                if df is not None and spot:
                    near = self._zones_near(df, spot)
                    vwap = _vwap(df)
                    squeeze = _squeeze_on(df)
                    htf = self._htf_context()
                    read, direction = self._analyse(df, spot, near, vwap, squeeze, htf)
                    if observing:
                        read += (f"  ·  Watching only today — {self.symbol} trades on its "
                                 f"expiry day ({expiry.strftime('%a %d-%b')})")
                    elif late:
                        read += "  ·  Past the entry cut-off — no new trades today"
                    elif maxed:
                        read += "  ·  Daily trade limit reached — done for today"
                    else:
                        read += "  ·  LIVE (expiry day) — ready to trade a trap"
                    self._update_status(direction=direction, signal=read)
                    if not (observing or late or maxed):
                        sig = self._detect(df, near, vwap, squeeze, htf=htf)
                        if sig:
                            self._enter(sig, spot, expiry)
                time.sleep(TICK_SECONDS)
        finally:
            self._print_eod()

    def _print_eod(self):
        wins = sum(1 for t in self.trades if t.pnl > 0)
        log.info(f"EOD TRAP_CMCD — trades={len(self.trades)} wins={wins} "
                 f"day_pnl=Rs.{self.day_pnl:,.0f}")
