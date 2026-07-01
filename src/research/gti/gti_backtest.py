"""
gti_backtest.py
===============
Backtest harness for the GTI-style demand/supply zone detector (gti_zones.py).

Purpose
-------
Answer ONE question before we build any fancy entry logic:
    "Do high-strength, fresh zones actually produce edge on NIFTY/SENSEX 5-min?"

It does that with a deliberately DUMB baseline entry so the zones themselves are
what's being measured, not a clever overlay:

    Baseline rule
    -------------
    * Demand zone  -> BUY  limit at the proximal line   (long)
    * Supply zone  -> SELL limit at the proximal line   (short)
    * Stop loss    -> just beyond the distal line (+ buffer)
    * Target       -> fixed reward:risk (default 1:2)
    * One position at a time; square off at end of day; zones expire EOD.

Anti-lookahead guarantees
-------------------------
    * A zone is only tradeable AFTER its base+departure has completed
      (bar index > zone.created_index).
    * The strength used for bucketing is computed from CREATION-TIME info only
      (departure ATR, base tightness, volume) with freshness fixed at 0 — never
      the module's full-history freshness, which would peek into the future.
    * Same-bar SL/target ambiguity resolves to SL (pessimistic).
    * Entry fill assumed at the proximal (conservative vs. a gap-through fill).

Primary metric is the R-MULTIPLE  R = (exit-entry)/(entry-SL)  — cost-agnostic
and comparable across price levels. A points/rupee estimate is also produced.

Plug in real data
-----------------
    from gti_zones import fetch_kite_candles
    df = fetch_kite_candles(kite, NIFTY_FUT_TOKEN, from_dt, to_dt, "5minute")
    result = run_backtest(df)
    print_report(result)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import time
from typing import List, Optional

import numpy as np
import pandas as pd

from src.research.gti.gti_zones import Zone, ZoneConfig, detect_zones


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class BacktestConfig:
    # --- trade construction ---
    reward_risk: float = 2.0            # target = this * risk
    sl_buffer_atr: float = 0.10         # extra SL padding beyond distal, in ATR
    allow_longs: bool = True
    allow_shorts: bool = True
    min_strength: float = 0.0           # skip zones below this entry-time strength

    # --- intraday session (IST) ---
    entry_start: time = time(9, 30)     # no entries before this
    entry_end: time = time(14, 45)      # no NEW entries after this
    square_off: time = time(15, 15)     # force-flat at/after this
    carry_overnight: bool = False       # zones die at EOD; no overnight positions

    # --- cost model (for the rupee estimate only; R-multiple ignores this) ---
    cost_points: float = 1.0            # round-trip slippage+cost, in index points
    lot_size: int = 65                  # NIFTY lot (current platform value; SENSEX differs)
    point_value: float = 1.0            # ₹ per index point per unit (fut ~1.0)


# --------------------------------------------------------------------------- #
# Trade record
# --------------------------------------------------------------------------- #
@dataclass
class Trade:
    side: str                     # "long" / "short"
    ztype: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    exit_price: float
    exit_reason: str              # "target" / "stop" / "eod"
    risk: float                   # entry - stop  (abs)
    r_multiple: float
    entry_strength: float         # point-in-time (no lookahead)
    departure_atr: float

    @property
    def points(self) -> float:
        return (self.exit_price - self.entry) if self.side == "long" \
            else (self.entry - self.exit_price)

    def as_dict(self) -> dict:
        return {
            "side": self.side, "type": self.ztype,
            "entry_time": self.entry_time, "exit_time": self.exit_time,
            "entry": round(self.entry, 2), "stop": round(self.stop, 2),
            "target": round(self.target, 2), "exit": round(self.exit_price, 2),
            "reason": self.exit_reason, "R": round(self.r_multiple, 3),
            "points": round(self.points, 2),
            "entry_strength": round(self.entry_strength, 1),
            "dep_atr": round(self.departure_atr, 2),
        }


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    df: Optional[pd.DataFrame] = None
    bt_cfg: BacktestConfig = field(default_factory=BacktestConfig)


# --------------------------------------------------------------------------- #
# Point-in-time strength (no lookahead)
# --------------------------------------------------------------------------- #
def _entry_time_strength(z: Zone, zcfg: ZoneConfig) -> float:
    """
    Recompute the zone's strength as it would have looked AT ENTRY: fresh
    (tests=0), using only creation-time components. Mirrors gti_zones._score
    but never touches future freshness/mitigation.
    """
    dep = min(z.departure_atr, 4.0) / 4.0 * 40.0
    vol = z.volume_score * 20.0
    fresh = zcfg.fresh_only_bonus            # tests == 0 at first touch
    tight = max(0.0, (zcfg.max_base_candles - z.base_candles + 1)) \
        / zcfg.max_base_candles * 20.0
    return float(np.clip(dep + vol + fresh + tight, 0, 100))


# --------------------------------------------------------------------------- #
# Core event-driven backtest
# --------------------------------------------------------------------------- #
def run_backtest(
    df: pd.DataFrame,
    zone_cfg: ZoneConfig = ZoneConfig(),
    bt_cfg: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df = df.set_index(pd.to_datetime(df["date"]))
        else:
            raise ValueError("df needs a DatetimeIndex or a 'date' column")

    df = df.sort_index()
    # CRITICAL (anti-lookahead): a backtest must see EVERY detected zone, never
    # the hindsight-ranked top-N that detect_zones returns for LIVE use.
    # detect_zones ranks by a strength score that folds in FUTURE freshness /
    # mitigation (update_freshness walks the whole series), then truncates to
    # max_active_zones. Feeding that truncated set to the backtest cherry-picks
    # the zones that turned out best — which is exactly what produced the
    # implausible ~10-trade, 100%-win result. Force all zones through here;
    # entry-time strength is still recomputed cleanly per trade below.
    bt_zone_cfg = replace(zone_cfg, max_active_zones=0)
    zones = detect_zones(df, bt_zone_cfg)
    # Index zones by the bar at which they become tradeable. That is the
    # CONFIRMATION bar (departure leg complete) — never the base-end bar, which
    # would let the backtest trade a zone several bars before it could be known
    # to exist (look-ahead). Fall back to created_index for older Zone objects.
    zones_by_creation: dict[int, List[Zone]] = {}
    for z in zones:
        arm_at = z.confirmed_index if getattr(z, "confirmed_index", -1) >= 0 else z.created_index
        arm_at = max(arm_at, z.created_index)
        zones_by_creation.setdefault(arm_at, []).append(z)

    atr_arr = _atr_array(df, zone_cfg.atr_period)
    times = df.index
    highs, lows, closes, opens = (df[c].to_numpy() for c in ("high", "low", "close", "open"))
    n = len(df)

    armed: List[Zone] = []          # zones live and awaiting first touch
    position: Optional[dict] = None
    trades: List[Trade] = []

    def _is_eod(i: int) -> bool:
        if i + 1 >= n:
            return True
        return times[i + 1].date() != times[i].date()

    for i in range(n):
        t = times[i]
        tod = t.time()

        # ---- arm zones created on this bar (tradeable from NEXT bar) ----
        # (handled at end of loop so a zone can't trade on its own creation bar)

        # ---- expire stale zones ----
        if not bt_cfg.carry_overnight:
            armed = [z for z in armed if times[z.created_index].date() == t.date()]

        # ================= manage open position =================
        if position is not None:
            side = position["side"]
            stop, target, entry = position["stop"], position["target"], position["entry"]
            exit_price = exit_reason = None

            if side == "long":
                # pessimistic: check stop before target within the same bar
                if lows[i] <= stop:
                    exit_price, exit_reason = stop, "stop"
                elif highs[i] >= target:
                    exit_price, exit_reason = target, "target"
            else:  # short
                if highs[i] >= stop:
                    exit_price, exit_reason = stop, "stop"
                elif lows[i] <= target:
                    exit_price, exit_reason = target, "target"

            # forced square-off
            if exit_price is None and (tod >= bt_cfg.square_off or _is_eod(i)):
                exit_price, exit_reason = closes[i], "eod"

            if exit_price is not None:
                risk = position["risk"]
                r = ((exit_price - entry) if side == "long" else (entry - exit_price)) / risk
                trades.append(Trade(
                    side=side, ztype=position["ztype"],
                    entry_time=position["entry_time"], exit_time=t,
                    entry=entry, stop=stop, target=target,
                    exit_price=exit_price, exit_reason=exit_reason,
                    risk=risk, r_multiple=r,
                    entry_strength=position["strength"],
                    departure_atr=position["dep_atr"],
                ))
                position = None

        # ================= look for a new entry =================
        if position is None and bt_cfg.entry_start <= tod <= bt_cfg.entry_end:
            # nearest touched zone wins; check demand (long) & supply (short)
            best = None
            for z in armed:
                a = atr_arr[i] if np.isfinite(atr_arr[i]) and atr_arr[i] > 0 else 0.0
                if a == 0:
                    continue
                strength = _entry_time_strength(z, zone_cfg)
                if strength < bt_cfg.min_strength:
                    continue

                if z.side == "demand" and bt_cfg.allow_longs:
                    prox = z.proximal
                    if lows[i] <= prox <= highs[i] or opens[i] <= prox:  # touched
                        risk = (prox - z.distal) + bt_cfg.sl_buffer_atr * a
                        if risk <= 0:
                            continue
                        cand = ("long", z, prox, prox - risk, prox + bt_cfg.reward_risk * risk,
                                risk, strength)
                        best = _closer(best, cand, highs[i], lows[i], prox)

                elif z.side == "supply" and bt_cfg.allow_shorts:
                    prox = z.proximal
                    if lows[i] <= prox <= highs[i] or opens[i] >= prox:  # touched
                        risk = (z.distal - prox) + bt_cfg.sl_buffer_atr * a
                        if risk <= 0:
                            continue
                        cand = ("short", z, prox, prox + risk, prox - bt_cfg.reward_risk * risk,
                                risk, strength)
                        best = _closer(best, cand, highs[i], lows[i], prox)

            if best is not None:
                side, z, entry, stop, target, risk, strength = best
                position = {
                    "side": side, "ztype": z.ztype, "entry": entry,
                    "stop": stop, "target": target, "risk": risk,
                    "entry_time": t, "strength": strength, "dep_atr": z.departure_atr,
                }
                armed.remove(z)  # consumed

                # ---- evaluate the ENTRY BAR itself (pessimistic, stop-first) ----
                # The manage-position block above runs BEFORE this entry block, so
                # without this an entry bar's own adverse excursion is never seen:
                # a trade that should have stopped out on its entry bar instead
                # gets a clean look on the next bar and often books a target — a
                # systematic win-inflating bias. Resolve same-bar stop-before-
                # target (we can't see intrabar order, so assume the worst).
                sb_exit = sb_reason = None
                if side == "long":
                    if lows[i] <= stop:
                        sb_exit, sb_reason = stop, "stop"
                    elif highs[i] >= target:
                        sb_exit, sb_reason = target, "target"
                else:
                    if highs[i] >= stop:
                        sb_exit, sb_reason = stop, "stop"
                    elif lows[i] <= target:
                        sb_exit, sb_reason = target, "target"
                if sb_exit is not None:
                    r = ((sb_exit - entry) if side == "long" else (entry - sb_exit)) / risk
                    trades.append(Trade(
                        side=side, ztype=z.ztype,
                        entry_time=t, exit_time=t,
                        entry=entry, stop=stop, target=target,
                        exit_price=sb_exit, exit_reason=sb_reason,
                        risk=risk, r_multiple=r,
                        entry_strength=strength, departure_atr=z.departure_atr,
                    ))
                    position = None

        # ---- now arm zones whose base ended exactly at THIS bar ----
        for z in zones_by_creation.get(i, []):
            armed.append(z)

    return BacktestResult(trades=trades, df=df, bt_cfg=bt_cfg)


def _closer(best, cand, hi, lo, prox):
    """Keep whichever candidate zone's proximal is nearest the bar's action."""
    if best is None:
        return cand
    return best  # first-touch-wins; armed list is creation-ordered


def _atr_array(df: pd.DataFrame, period: int) -> np.ndarray:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().to_numpy()


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _stats(trades: List[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t.r_multiple for t in trades])
    wins = r[r > 0]
    losses = r[r <= 0]
    gross_win = wins.sum() if len(wins) else 0.0
    gross_loss = -losses.sum() if len(losses) else 0.0
    # max consecutive losses
    mcl = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0
        mcl = max(mcl, cur)
    return {
        "n": len(trades),
        "win_rate": len(wins) / len(r) * 100,
        "expectancy_R": r.mean(),
        "total_R": r.sum(),
        "avg_win_R": wins.mean() if len(wins) else 0.0,
        "avg_loss_R": losses.mean() if len(losses) else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "max_consec_loss": mcl,
    }


def _bucket(trades: List[Trade], key, bands, labels) -> List[dict]:
    rows = []
    for (lo, hi), lab in zip(bands, labels):
        sub = [t for t in trades if lo <= key(t) < hi]
        s = _stats(sub)
        if s["n"]:
            rows.append({"bucket": lab, "n": s["n"],
                         "win%": round(s["win_rate"], 1),
                         "expectancy_R": round(s["expectancy_R"], 3),
                         "total_R": round(s["total_R"], 2)})
        else:
            rows.append({"bucket": lab, "n": 0, "win%": None,
                         "expectancy_R": None, "total_R": 0.0})
    return rows


def print_report(result: BacktestResult) -> None:
    trades = result.trades
    print("=" * 66)
    print("  GTI ZONE BACKTEST — baseline entry (limit@proximal, fixed R:R)")
    print("=" * 66)
    if not trades:
        print("  No trades generated. Check data length / config thresholds.")
        return

    s = _stats(trades)
    span = f"{trades[0].entry_time.date()} -> {trades[-1].exit_time.date()}"
    print(f"  Period            : {span}")
    print(f"  Trades            : {s['n']}")
    print(f"  Win rate          : {s['win_rate']:.1f}%")
    print(f"  Expectancy        : {s['expectancy_R']:+.3f} R / trade")
    print(f"  Total             : {s['total_R']:+.2f} R")
    print(f"  Avg win / avg loss: {s['avg_win_R']:+.2f} R / {s['avg_loss_R']:+.2f} R")
    print(f"  Profit factor     : {s['profit_factor']:.2f}")
    print(f"  Max consec losses : {s['max_consec_loss']}")

    # rupee estimate
    cfg = result.bt_cfg
    net_points = sum(t.points for t in trades) - cfg.cost_points * len(trades)
    rupees = net_points * cfg.point_value * cfg.lot_size
    print(f"  Net points (after {cfg.cost_points} pt cost/trade): {net_points:+.1f}")
    print(f"  ~P&L @ lot {cfg.lot_size}: Rs {rupees:+,.0f}   "
          f"(verify lot size / point value per instrument)")

    # ---- THE KEY TABLE: does strength predict edge? ----
    print("\n  --- Expectancy by ENTRY-TIME STRENGTH (no lookahead) ---")
    rows = _bucket(trades, lambda t: t.entry_strength,
                   [(0, 40), (40, 60), (60, 80), (80, 101)],
                   ["0-40", "40-60", "60-80", "80-100"])
    _print_table(rows)

    print("\n  --- By departure imbalance (ATR multiple) ---")
    rows = _bucket(trades, lambda t: t.departure_atr,
                   [(0, 2), (2, 3), (3, 4), (4, 99)],
                   ["<2", "2-3", "3-4", "4+"])
    _print_table(rows)

    print("\n  --- By zone type ---")
    for zt in ("RBR", "DBR", "DBD", "RBD"):
        sub = [t for t in trades if t.ztype == zt]
        if sub:
            ss = _stats(sub)
            print(f"    {zt}:  n={ss['n']:>3}  win%={ss['win_rate']:>5.1f}  "
                  f"exp={ss['expectancy_R']:+.3f}R")

    print("\n  --- By side ---")
    for sd in ("long", "short"):
        sub = [t for t in trades if t.side == sd]
        if sub:
            ss = _stats(sub)
            print(f"    {sd:5s}: n={ss['n']:>3}  win%={ss['win_rate']:>5.1f}  "
                  f"exp={ss['expectancy_R']:+.3f}R")
    print("=" * 66)
    print("  READ: if expectancy RISES with strength, the detector has edge and")
    print("  we tune from here. If it's flat/noisy, we fix zones before entries.")
    print("=" * 66)


def _print_table(rows: List[dict]) -> None:
    print(f"    {'bucket':<8}{'n':>5}{'win%':>8}{'exp_R':>9}{'total_R':>10}")
    for r in rows:
        win = "  -  " if r["win%"] is None else f"{r['win%']:>7.1f}"
        exp = "   -   " if r["expectancy_R"] is None else f"{r['expectancy_R']:>+8.3f}"
        print(f"    {r['bucket']:<8}{r['n']:>5}{win}{exp}{r['total_R']:>10.2f}")


def trades_to_df(result: BacktestResult) -> pd.DataFrame:
    return pd.DataFrame([t.as_dict() for t in result.trades])


# --------------------------------------------------------------------------- #
# Synthetic multi-day demo (replace with real Kite 5-min data)
# --------------------------------------------------------------------------- #
def _synth_intraday(days: int = 20, seed: int = 11) -> pd.DataFrame:
    """A multi-day 5-min series with occasional base+departure structures."""
    rng = np.random.default_rng(seed)
    rows = []
    price = 22000.0
    day = pd.Timestamp("2026-06-01 09:15")
    while len([r for r in rows]) < days * 75:  # ~75 five-min bars per session
        # skip weekends
        if day.weekday() >= 5:
            day = (day + pd.Timedelta(days=1)).replace(hour=9, minute=15)
            continue
        session_open = price
        for b in range(75):  # 09:15 -> ~15:25
            phase = b % 25
            if phase < 4:                       # tight base
                drift = rng.normal(0, 4)
            elif phase < 7:                      # departure
                drift = rng.normal(rng.choice([28, -28]), 6)
            else:
                drift = rng.normal(0, 11)
            o = price
            c = price + drift
            h = max(o, c) + abs(rng.normal(0, 6))
            l = min(o, c) - abs(rng.normal(0, 6))
            ts = day + pd.Timedelta(minutes=5 * b)
            rows.append({"date": ts, "open": o, "high": h, "low": l,
                         "close": c, "volume": 0})
            price = c
        # small overnight gap
        price += rng.normal(0, 20)
        day = (day + pd.Timedelta(days=1)).replace(hour=9, minute=15)

    df = pd.DataFrame(rows).set_index("date")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


if __name__ == "__main__":
    df = _synth_intraday(days=25)
    print(f"Loaded {len(df)} synthetic 5-min bars "
          f"({df.index[0].date()} -> {df.index[-1].date()})\n")
    result = run_backtest(df, ZoneConfig(), BacktestConfig())
    print_report(result)

    out = trades_to_df(result)
    if not out.empty:
        _dest = os.path.join(os.path.dirname(__file__), "gti_backtest_trades.csv")
        out.to_csv(_dest, index=False)
        print(f"\nSaved {len(out)} trades -> {_dest}")
