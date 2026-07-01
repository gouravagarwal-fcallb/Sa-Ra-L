"""
gti_zones.py
============
GTI-style Demand / Supply Zone Detector — core foundation module.

Built for intraday index trading (NIFTY / SENSEX) on Zerodha Kite Connect,
tuned for 3-min and 5-min candles. This is a clean, from-scratch implementation
of standard institutional order-flow / Smart-Money-Concept logic (Sam Seiden
style Rally-Base-Rally / Drop-Base-Drop zones + order-block departure imbalance).
It does NOT copy any proprietary vendor code.

Design notes for indices
------------------------
* SPOT NIFTY/SENSEX have NO volume on Kite. So volume is OPTIONAL:
    - If a real volume column is present and non-zero (e.g. you feed FUT candles),
      the departure move is validated with a volume spike.
    - If volume is absent / all-zero (spot index), the detector falls back to
      RANGE EXPANSION (ATR-multiple of the departure leg) as the imbalance proxy.
* Every zone carries a proximal line (entry edge), a distal line (SL edge),
  a type, a strength score (0-100) and a freshness/test count so the rest of
  the Brahmastra stack (SL-hunt, engulfing, kill-switch) can act on it.

Public API
----------
    ZoneConfig                       -> tunable parameters
    Zone                             -> a detected zone (dataclass)
    detect_zones(df, config)         -> list[Zone]
    update_freshness(zones, df)      -> marks tests / mitigation on live data
    active_zones(zones, ltp, ...)    -> zones near the current price, ranked
    fetch_kite_candles(...)          -> pull + resample candles from Kite
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Literal

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class ZoneConfig:
    # --- candle classification ---
    base_body_ratio: float = 0.50      # body/range BELOW this = "basing" candle
    explosive_body_ratio: float = 0.50 # body/range ABOVE this = "explosive" candle
    atr_period: int = 14
    explosive_atr_mult: float = 1.20   # departure candle range must exceed ATR*this
    max_base_candles: int = 4          # a base is 1..max_base_candles tight candles

    # --- departure / imbalance strength ---
    min_departure_atr: float = 1.50    # leg-out must travel >= this many ATRs
    leg_out_lookahead: int = 3         # candles after base to measure the leg-out

    # --- volume validation (only used if real volume exists) ---
    use_volume: bool = True
    vol_avg_period: int = 20
    vol_spike_mult: float = 1.30       # departure volume > avg*this

    # --- freshness ---
    fresh_only_bonus: float = 20.0     # score bonus for an untested zone
    penalty_per_test: float = 12.0     # score penalty per prior test

    # --- housekeeping ---
    max_active_zones: int = 20         # keep only the N strongest live zones
    merge_overlap: bool = True         # merge near-duplicate overlapping zones


ZoneType = Literal["RBR", "DBR", "DBD", "RBD"]  # Rally/Drop - Base - Rally/Drop
ZoneSide = Literal["demand", "supply"]


# --------------------------------------------------------------------------- #
# Zone object
# --------------------------------------------------------------------------- #
@dataclass
class Zone:
    side: ZoneSide                 # "demand" or "supply"
    ztype: ZoneType                # RBR / DBR / DBD / RBD
    proximal: float                # entry edge (nearest to price)
    distal: float                  # stop-loss edge (farthest from price)
    created_index: int             # bar index where the base ended
    created_time: Optional[datetime] = None
    departure_atr: float = 0.0     # leg-out strength in ATR multiples
    volume_score: float = 0.0      # 0..1 (0 if volume unavailable)
    base_candles: int = 1
    tests: int = 0                 # times price has re-entered the zone
    mitigated: bool = False        # fully traded through (distal broken)
    strength: float = 0.0          # 0..100 composite score

    @property
    def height(self) -> float:
        return abs(self.proximal - self.distal)

    @property
    def mid(self) -> float:
        return (self.proximal + self.distal) / 2.0

    def contains(self, price: float) -> bool:
        lo, hi = sorted((self.proximal, self.distal))
        return lo <= price <= hi

    def as_dict(self) -> dict:
        return {
            "side": self.side,
            "type": self.ztype,
            "proximal": round(self.proximal, 2),
            "distal": round(self.distal, 2),
            "height": round(self.height, 2),
            "created_time": self.created_time.isoformat() if self.created_time else None,
            "departure_atr": round(self.departure_atr, 2),
            "volume_score": round(self.volume_score, 2),
            "base_candles": self.base_candles,
            "tests": self.tests,
            "fresh": self.tests == 0,
            "mitigated": self.mitigated,
            "strength": round(self.strength, 1),
        }


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder-style ATR."""
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat(
        [(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _has_real_volume(df: pd.DataFrame) -> bool:
    return "volume" in df.columns and df["volume"].fillna(0).abs().sum() > 0


# --------------------------------------------------------------------------- #
# Candle classification
# --------------------------------------------------------------------------- #
def _classify(df: pd.DataFrame, cfg: ZoneConfig) -> pd.DataFrame:
    """Tag each candle as basing / explosive and add helper columns."""
    d = df.copy()
    d["range"] = (d["high"] - d["low"]).replace(0, np.nan)
    d["body"] = (d["close"] - d["open"]).abs()
    d["body_ratio"] = (d["body"] / d["range"]).fillna(0)
    d["bull"] = d["close"] >= d["open"]
    d["atr"] = atr(d, cfg.atr_period)

    d["basing"] = d["body_ratio"] < cfg.base_body_ratio
    d["explosive"] = (
        (d["body_ratio"] > cfg.explosive_body_ratio)
        & (d["range"] > d["atr"] * cfg.explosive_atr_mult)
    )
    if _has_real_volume(df):
        d["vol_avg"] = d["volume"].rolling(cfg.vol_avg_period, min_periods=1).mean()
    return d


# --------------------------------------------------------------------------- #
# Core zone detection
# --------------------------------------------------------------------------- #
def detect_zones(df: pd.DataFrame, cfg: ZoneConfig = ZoneConfig()) -> List[Zone]:
    """
    Detect demand & supply zones on an OHLC(V) DataFrame.

    Expected columns: open, high, low, close, [volume], and either a DatetimeIndex
    or a 'date' column. Returns a list of Zone objects (already freshness-scored
    against the full frame).
    """
    if len(df) < cfg.atr_period + cfg.max_base_candles + cfg.leg_out_lookahead + 2:
        return []

    d = _classify(df, cfg)
    has_vol = _has_real_volume(df)
    times = _time_series(df)
    zones: List[Zone] = []

    n = len(d)
    i = cfg.atr_period + 1
    while i < n - cfg.leg_out_lookahead - 1:
        if not d["basing"].iloc[i]:
            i += 1
            continue

        # ---- grow the base (consecutive basing candles) ----
        start = i
        end = i
        while (
            end + 1 < n
            and d["basing"].iloc[end + 1]
            and (end - start + 1) < cfg.max_base_candles
        ):
            end += 1

        base = d.iloc[start:end + 1]
        after_idx = end + 1
        if after_idx >= n:
            break

        # ---- measure the leg-out (departure) ----
        look = min(cfg.leg_out_lookahead, n - after_idx)
        leg = d.iloc[after_idx:after_idx + look]
        if leg.empty or not leg["explosive"].any():
            i = end + 1
            continue

        atr_here = d["atr"].iloc[after_idx]
        if not np.isfinite(atr_here) or atr_here <= 0:
            i = end + 1
            continue

        # direction of the departure = sign of net move across the leg-out
        leg_move = leg["close"].iloc[-1] - base["close"].iloc[-1]
        departure_atr = abs(leg_move) / atr_here
        if departure_atr < cfg.min_departure_atr:
            i = end + 1
            continue

        bullish_departure = leg_move > 0

        # ---- volume score for the departure ----
        vol_score = 0.0
        if has_vol and cfg.use_volume:
            dep_vol = leg["volume"].max()
            vavg = d["vol_avg"].iloc[after_idx]
            if vavg and vavg > 0:
                ratio = dep_vol / vavg
                vol_score = float(np.clip((ratio - 1.0) / (cfg.vol_spike_mult), 0, 1))

        # ---- build the zone geometry ----
        base_hi = base[["open", "close"]].max().max()   # top of bodies
        base_lo = base[["open", "close"]].min().min()   # bottom of bodies
        wick_hi = base["high"].max()
        wick_lo = base["low"].min()

        # leg-in direction (was price rallying or dropping INTO the base?)
        pre = d.iloc[max(0, start - cfg.leg_out_lookahead):start]
        rally_in = (not pre.empty) and (base["close"].iloc[0] > pre["close"].iloc[0])

        if bullish_departure:
            side: ZoneSide = "demand"
            ztype: ZoneType = "RBR" if rally_in else "DBR"
            proximal = base_hi            # buy edge = top of base bodies
            distal = wick_lo              # SL edge  = base low
        else:
            side = "supply"
            ztype = "DBD" if not rally_in else "RBD"
            proximal = base_lo            # sell edge = bottom of base bodies
            distal = wick_hi              # SL edge   = base high

        z = Zone(
            side=side,
            ztype=ztype,
            proximal=float(proximal),
            distal=float(distal),
            created_index=int(end),
            created_time=times[end] if times is not None else None,
            departure_atr=float(departure_atr),
            volume_score=float(vol_score),
            base_candles=int(end - start + 1),
        )
        zones.append(z)

        # jump past the departure leg to avoid overlapping detections
        i = after_idx + look

    # ---- freshness pass + scoring + housekeeping ----
    update_freshness(zones, df)
    if cfg.merge_overlap:
        zones = _merge_overlapping(zones)
    for z in zones:
        z.strength = _score(z, cfg)

    zones.sort(key=lambda z: z.strength, reverse=True)
    return zones[: cfg.max_active_zones] if cfg.max_active_zones else zones


# --------------------------------------------------------------------------- #
# Freshness / mitigation tracking
# --------------------------------------------------------------------------- #
def update_freshness(zones: List[Zone], df: pd.DataFrame) -> None:
    """
    Walk price forward AFTER each zone's creation and count how many times price
    re-enters the zone (a 'test'). If price closes fully beyond the distal line,
    the zone is marked mitigated (consumed).
    """
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    n = len(df)

    for z in zones:
        z.tests = 0
        z.mitigated = False
        lo, hi = sorted((z.proximal, z.distal))
        was_outside = True
        for k in range(z.created_index + 1, n):
            touched = (lows[k] <= hi) and (highs[k] >= lo)
            if touched and was_outside:
                z.tests += 1
                was_outside = False
            elif not touched:
                was_outside = True

            # mitigation: a close beyond the distal edge kills the zone
            if z.side == "demand" and closes[k] < z.distal:
                z.mitigated = True
                break
            if z.side == "supply" and closes[k] > z.distal:
                z.mitigated = True
                break


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _score(z: Zone, cfg: ZoneConfig) -> float:
    """Composite 0-100 zone quality score."""
    # departure imbalance (cap at ~4 ATR -> 40 pts)
    dep = min(z.departure_atr, 4.0) / 4.0 * 40.0
    # volume confirmation (0-20 pts). If no volume, this stays 0 and the model
    # leans on departure strength instead.
    vol = z.volume_score * 20.0
    # freshness (0-20 pts) minus per-test penalty
    fresh = cfg.fresh_only_bonus if z.tests == 0 else 0.0
    fresh -= cfg.penalty_per_test * z.tests
    # tight base bonus (fewer base candles = cleaner imbalance, up to 20 pts)
    tight = max(0.0, (cfg.max_base_candles - z.base_candles + 1)) / cfg.max_base_candles * 20.0

    score = dep + vol + fresh + tight
    if z.mitigated:
        score *= 0.25  # heavily discount consumed zones (kept for context)
    return float(np.clip(score, 0, 100))


# --------------------------------------------------------------------------- #
# Overlap merge
# --------------------------------------------------------------------------- #
def _merge_overlapping(zones: List[Zone]) -> List[Zone]:
    """Merge zones of the same side whose ranges overlap; keep the stronger one."""
    merged: List[Zone] = []
    for z in sorted(zones, key=lambda x: x.created_index):
        dup = None
        for m in merged:
            if m.side != z.side:
                continue
            lo1, hi1 = sorted((m.proximal, m.distal))
            lo2, hi2 = sorted((z.proximal, z.distal))
            if lo1 <= hi2 and lo2 <= hi1:  # overlap
                dup = m
                break
        if dup is None:
            merged.append(z)
        else:
            # keep whichever has the stronger departure
            if z.departure_atr > dup.departure_atr:
                merged[merged.index(dup)] = z
    return merged


# --------------------------------------------------------------------------- #
# Active-zone selection (what to actually trade right now)
# --------------------------------------------------------------------------- #
def active_zones(
    zones: List[Zone],
    ltp: float,
    max_distance_pct: float = 1.0,
    include_mitigated: bool = False,
    min_strength: float = 0.0,
) -> List[Zone]:
    """
    Return live, un-mitigated zones within `max_distance_pct` of the last price,
    sorted by proximity then strength. This is what your entry logic consumes:
      - nearest demand BELOW price  -> potential long trigger
      - nearest supply ABOVE price  -> potential short trigger
    """
    out = []
    for z in zones:
        if z.mitigated and not include_mitigated:
            continue
        if z.strength < min_strength:
            continue
        dist_pct = abs(z.proximal - ltp) / ltp * 100.0
        if dist_pct <= max_distance_pct:
            out.append((dist_pct, z))
    out.sort(key=lambda t: (t[0], -t[1].strength))
    return [z for _, z in out]


# --------------------------------------------------------------------------- #
# Kite Connect helpers
# --------------------------------------------------------------------------- #
def _time_series(df: pd.DataFrame):
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index.to_pydatetime()
    if "date" in df.columns:
        return pd.to_datetime(df["date"]).dt.to_pydatetime()
    return None


def candles_to_df(records: list) -> pd.DataFrame:
    """Convert kite.historical_data() output (list of dicts) to a clean DataFrame."""
    df = pd.DataFrame(records)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep].astype(float)


def fetch_kite_candles(
    kite,
    instrument_token: int,
    from_dt: datetime,
    to_dt: datetime,
    interval: str = "5minute",
) -> pd.DataFrame:
    """
    Pull historical candles from an authenticated KiteConnect instance.
    interval: '3minute' or '5minute' for this detector.
    For NIFTY/SENSEX use the FUT token if you want real volume; use the spot
    token if you only need price (detector auto-handles the no-volume case).
    """
    records = kite.historical_data(instrument_token, from_dt, to_dt, interval)
    return candles_to_df(records)


# --------------------------------------------------------------------------- #
# Self-test / demo with synthetic data
# --------------------------------------------------------------------------- #
def _demo():
    """Generate a synthetic index-like series with a clear base+departure and test."""
    rng = np.random.default_rng(42)
    price = 22000.0
    rows = []
    t = pd.Timestamp("2026-06-30 09:15")
    for k in range(200):
        # inject a tight base then a strong rally around bar 60-70
        if 60 <= k < 66:
            drift = rng.normal(0, 3)          # base (tight)
        elif 66 <= k < 72:
            drift = rng.normal(35, 5)         # explosive departure up
        else:
            drift = rng.normal(0, 12)
        o = price
        c = price + drift
        h = max(o, c) + abs(rng.normal(0, 6))
        l = min(o, c) - abs(rng.normal(0, 6))
        rows.append({"date": t, "open": o, "high": h, "low": l, "close": c, "volume": 0})
        price = c
        t += pd.Timedelta(minutes=5)

    df = candles_to_df(rows)
    cfg = ZoneConfig()
    zones = detect_zones(df, cfg)

    print(f"Volume present: {_has_real_volume(df)} (spot-index mode)")
    print(f"Detected {len(zones)} zones on {len(df)} candles\n")
    for z in zones[:8]:
        d = z.as_dict()
        print(
            f"  {d['side']:6s} {d['type']:3s}  "
            f"prox={d['proximal']:>9.2f}  distal={d['distal']:>9.2f}  "
            f"depATR={d['departure_atr']:.2f}  tests={d['tests']}  "
            f"fresh={d['fresh']}  strength={d['strength']:.1f}"
        )

    ltp = float(df["close"].iloc[-1])
    live = active_zones(zones, ltp, max_distance_pct=100)
    print(f"\nLTP={ltp:.2f} | {len(live)} actionable zone(s) within range")


if __name__ == "__main__":
    _demo()
