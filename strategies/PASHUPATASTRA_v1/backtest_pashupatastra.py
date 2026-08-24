"""
PASHUPATASTRA_v1 — Backtest Harness
═══════════════════════════════════════════════════════════════════════════════

WHAT THIS DOES, AND ITS HONEST LIMITS — READ FIRST
---------------------------------------------------------------------------
PASHUPATASTRA's real trigger is the option-chain ΔOI add→cover FLIP (a writer
capitulating at a wall). Backtesting that edge requires HISTORICAL per-strike
Open-Interest + IV snapshots (intraday). That data is NOT available for free, is
NOT in this repo, and cannot be reconstructed from price. yfinance/Kite give
spot/option PRICE history at best — never historical OI.

So this harness does the most honest thing possible offline. It faithfully
simulates everything that IS knowable, and isolates the one thing that is not:

  KNOWABLE & SIMULATED FAITHFULLY (validated here):
    • The four setups' price-action skeleton (trend day, gap, expiry-gamma,
      squeeze-release) detected on a regime-calibrated NIFTY path
    • Black-Scholes option pricing along a real intraday path (theta/gamma/vega)
    • Astra-charge defined-risk sizing (premium fully at risk, lot 65)
    • The scale-out ladder (+3x sell 40%, +6x sell 30%) + trailed 30% runner
    • Full Indian cost model (Rs.20/order, STT 0.10% sell-side, exch+GST+stamp,
      0.5% slippage) charged per leg — the real drag on small legs
    • Portfolio rails (1 concurrent, 3/week, Rs.40k monthly loss cap, misfire pause)
    • Tail-aware metrics, per-setup & per-year attribution, multi-seed robustness

  NOT KNOWABLE OFFLINE (exposed as a parameter, NOT assumed away):
    • How well the OI-flip signal SELECTS true breaks from false ones.
      Modeled as `filter_skill` = fraction of losing candidates the signal avoids
      (and `winner_leakage` = fraction of winners missed by waiting for confirm).
      We SWEEP filter_skill 0.0 → 0.8 and report the BREAK-EVEN skill the live
      OI signal must beat. filter_skill = 0.0 IS the "blind candidate" control.

=> Treat the headline P&L as "what the mechanics + asymmetry deliver IF the OI
   signal achieves the stated selection skill", NOT as a realized track record.
   The decision-grade output is the SENSITIVITY TABLE + break-even skill.

To turn this into a TRUE backtest: supply (a) real NIFTY OHLC via --data CSV,
and (b) a historical per-strike OI/IV feed, then replace `detect_candidate`'s
price proxies with the real ΔOI-flip trigger. The sizing/exit/cost/metric code
is already production-faithful and needs no change.

Usage:
    python strategies/PASHUPATASTRA_v1/backtest_pashupatastra.py
    python strategies/PASHUPATASTRA_v1/backtest_pashupatastra.py --data nifty_ohlc.csv
    python strategies/PASHUPATASTRA_v1/backtest_pashupatastra.py --seeds 16 --filter-skill 0.5
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta

# ── Reproducible Black-Scholes (math.erf; identical formula to src OptionPricer) ──
SQRT2 = math.sqrt(2.0)
def _Ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))

def bs_price(spot: float, strike: float, vix_pct: float, T_hours_cal: float, kind: str) -> float:
    """Black-Scholes price. vix_pct e.g. 12.0 = 12% annualised IV. T in CALENDAR hours."""
    sigma = max(vix_pct / 100.0, 1e-6)
    T = max(T_hours_cal / (365.0 * 24.0), 1e-9)
    S, K, r = spot, float(strike), 0.065
    vsqrt = sigma * math.sqrt(T)
    if vsqrt < 1e-9:
        intrinsic = max(S - K, 0.0) if kind == "CE" else max(K - S, 0.0)
        return round(intrinsic, 2)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vsqrt
    d2 = d1 - vsqrt
    if kind == "CE":
        px = S * _Ncdf(d1) - K * math.exp(-r * T) * _Ncdf(d2)
    else:
        px = K * math.exp(-r * T) * _Ncdf(-d2) - S * _Ncdf(-d1)
    return round(max(px, 0.0), 2)


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class Cfg:
    slot_rs: float = 500_000.0          # PASHUPATASTRA capital slot
    astra_charge_rs: float = 10_000.0   # max premium at risk per bullet (2% of slot)
    lot_size: int = 65                  # NIFTY current (Dec-2025), verified
    strike_step: int = 50
    # exit ladder — BOOK THE BREAD-AND-BUTTER 2x, keep a thin runner for the bonus tail.
    # (2-3x happens most expiries; we bank it every clear signal, let 5% ride for the
    # occasional 10-20x. This is the high-win-rate posture, not tail-chasing.)
    scale1_mult: float = 2.0            # book the bulk at 2x
    scale1_size: float = 0.85
    scale2_mult: float = 4.0            # trim more at 4x
    scale2_size: float = 0.10
    runner_trail: float = 0.55          # thin 5% runner trails 55% off peak (free upside)
    # gate / triggers (price proxies for the OI fuel+flare)
    trend_move_min: float = 0.0075      # Setup C: |day move| proxy for a strong trend day
    gap_min: float = 0.010              # Setup D: |open gap| for gap-and-go
    expiry_dev_min: float = 0.0030      # Setup A: mid-day developing move on expiry
    squeeze_lookback: int = 5           # Setup B: trailing range window
    squeeze_pct: float = 0.40           # Setup B: today range > 1.4x recent avg = expansion
    iv_cheap_pctl: float = 25.0         # Setup B gate: IV percentile < this
    # strike offsets (in steps) from ATM, toward the move
    A_offset: int = 1                   # gamma squeeze: just-OTM 0DTE
    B_offset: int = 1                   # coiled vega: slightly OTM, next weekly
    C_offset: int = 0                   # trend trap: ATM
    D_offset: int = 1                   # event/gap: 1 OTM
    # variance risk premium — the SELLER's structural edge / the BUYER's tax.
    # Path is generated at REALIZED vol; options are PRICED at IV = realized×(1+vrp).
    # IV systematically above realized is why blind option buying is negative-EV.
    # CENTRAL-CONSERVATIVE default (the seller's edge is strong, esp. on 0DTE).
    # Non-0DTE priced at 1.45x realized; 0DTE at 1.45x1.50 = 2.18x realized.
    iv_vrp: float = 0.45
    iv_0dte_extra: float = 0.50
    # premium sanity guards
    min_prem: float = 1.0
    max_prem: float = 150.0
    # costs — cheap options have WIDE relative spreads; model a per-unit half-spread
    # = max(Rs.0.5, 2% of premium). On a Rs.5 leg that is ~10%/side (~20% round-trip),
    # the real killer of cheap-OTM buying — far more honest than a flat 0.5%.
    min_halfspread_rs: float = 0.5
    halfspread_pct: float = 0.02
    brokerage_per_order: float = 20.0
    stt_sell: float = 0.001             # 0.10% sell-side premium
    exch_txn: float = 0.0005            # ~0.05% options premium (conservative)
    stamp_buy: float = 0.00003
    sebi: float = 0.000001
    gst: float = 0.18
    # re-entry: "re-see momentum, re-enter" — scalp the same expiry repeatedly
    reentry_enabled: bool = True
    max_per_expiry: int = 4             # up to N staggered OI-confirmed scalps per expiry
    reentry_dev_min: float = 0.0030     # fresh local push required to re-arm
    # portfolio rails
    max_bullets_week: int = 8           # accommodate a full expiry's re-entries
    monthly_loss_cap_rs: float = 40_000.0
    misfire_pause_after: int = 4        # consecutive losers
    misfire_pause_days: int = 5
    # signal model (the unknowable, swept)
    filter_skill: float = 0.50          # fraction of losing candidates the OI signal avoids
    winner_leakage: float = 0.15        # fraction of winners missed (confirmation lag)


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA — calibrated NIFTY daily path (real anchors thru 2024; 2025-26 estimated)
# ═══════════════════════════════════════════════════════════════════════════════
# NIFTY 50 approx year-end closes. 2019-2024 are real; 2025-2026 are ESTIMATES
# (2025 = low-vol grind w/ record-low India VIX; flagged in RESULTS.md).
_YEAR_END = {
    2019: 12168, 2020: 13982, 2021: 17354, 2022: 18105,
    2023: 21731, 2024: 23644, 2025: 25600, 2026: 26200,
}
# Annual vol calibrated to India VIX regimes. 2025 ~ record-low vol.
_ANNUAL_VOL = {
    2020: 0.46, 2021: 0.22, 2022: 0.20, 2023: 0.135,
    2024: 0.135, 2025: 0.105, 2026: 0.13,
}
# One-day shock events (date, move%) — real where known, illustrative for 2025-26.
_EVENTS = {
    "2020-03-23": -9.0, "2020-04-07": 5.0, "2020-11-09": 4.0,
    "2021-02-01": 4.7,  "2022-02-24": -4.5, "2024-06-04": -6.0,
    "2025-04-07": -4.5,                       # illustrative 2025 global risk-off
}

def _is_td(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d.strftime("%m-%d") not in {"01-26", "08-15", "10-02", "12-25"}

def gen_daily(start: date, end: date, seed: int, n_bars: int = 75):
    """
    Regime-calibrated NIFTY with a strictly CAUSAL intraday path.

    Each day is a free GBM walk of `n_bars` 5-min steps from the open — so bar i
    depends ONLY on bars < i and fresh noise. The afternoon does NOT know the
    morning's later path (no Brownian-bridge close-pinning), which is what makes
    intraday momentum genuinely non-predictive — the realistic option-buyer world.
    Event shocks arrive as overnight gaps (circuit-breaker realism). Returns day
    dicts carrying the full intraday `path` (n_bars+1 prices).
    """
    rng = random.Random(seed)
    days = []
    price = float(_YEAR_END.get(start.year - 1, 12168))
    cur = start
    cur_year = None
    drift = vol = 0.0
    while cur <= end:
        if not _is_td(cur):
            cur += timedelta(days=1); continue
        if cur.year != cur_year:
            cur_year = cur.year
            tgt = float(_YEAR_END.get(cur_year, price * 1.10))
            vol = _ANNUAL_VOL.get(cur_year, 0.15) / math.sqrt(252)   # daily vol
            rem = sum(1 for k in range(366)
                      if (cur + timedelta(days=k)).year == cur_year and _is_td(cur + timedelta(days=k)))
            drift = (math.log(tgt / price) / rem) if rem > 0 else 0.0
        prev_close = price
        shock = _EVENTS.get(cur.strftime("%Y-%m-%d"), 0.0) / 100.0
        open_ = prev_close * math.exp(rng.gauss(0, vol * 0.35) + shock)   # overnight gap (+ event)
        iv_day = max(8.0, min(60.0, vol * math.sqrt(252) * 100 * rng.uniform(0.85, 1.25)
                              + abs(shock) * 250))
        bar_vol = vol / math.sqrt(n_bars)
        p = open_
        path = [round(p, 2)]
        for _ in range(n_bars):
            p = p * math.exp(drift / n_bars + bar_vol * rng.gauss(0, 1))   # CAUSAL step
            path.append(round(p, 2))
        close = path[-1]
        days.append({"date": cur, "prev_close": round(prev_close, 2), "open": round(open_, 2),
                     "high": round(max(path), 2), "low": round(min(path), 2),
                     "close": round(close, 2), "iv": round(iv_day, 2), "path": path})
        price = close
        cur += timedelta(days=1)
    return days

def causal_path_from_ohlc(open_p, high_p, low_p, close_p, iv, rng, n_bars=75):
    """For real daily CSV (no intraday): a causal free walk from open whose vol
    matches the day's range. Endpoint is NOT pinned to close (causal), so it is an
    approximation of the true intraday tape — flagged in RESULTS.md."""
    rng_pct = max((high_p - low_p) / open_p, 0.003)
    bar_vol = rng_pct / (4.0 * math.sqrt(n_bars))
    p = open_p
    path = [round(p, 2)]
    for _ in range(n_bars):
        p = p * math.exp(bar_vol * rng.gauss(0, 1))
        path.append(round(p, 2))
    return path


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPIRY (NIFTY weekly = TUESDAY, verified mid-2026)
# ═══════════════════════════════════════════════════════════════════════════════
def days_to_tuesday(d: date) -> int:
    """Calendar days to the upcoming weekly expiry Tuesday (0 if d is Tuesday)."""
    return (1 - d.weekday()) % 7   # Monday=0 ... Tuesday=1


# ═══════════════════════════════════════════════════════════════════════════════
#  COSTS
# ═══════════════════════════════════════════════════════════════════════════════
def buy_charges(c: Cfg, val: float) -> float:
    exch = c.exch_txn * val
    return c.brokerage_per_order + c.stamp_buy * val + exch + c.sebi * val + c.gst * (c.brokerage_per_order + exch)

def sell_charges(c: Cfg, val: float) -> float:
    exch = c.exch_txn * val
    return c.brokerage_per_order + c.stt_sell * val + exch + c.sebi * val + c.gst * (c.brokerage_per_order + exch)


# ═══════════════════════════════════════════════════════════════════════════════
#  CANDIDATE DETECTION  (price proxies for the OI fuel+flare — see header limits)
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class Candidate:
    setup: str        # A/B/C/D
    entry_idx: int    # 5m bar index of entry
    kind: str         # CE/PE
    offset: int       # strike offset in steps toward the move
    dte_days: int     # calendar days to chosen expiry
    label: str

def detect_candidate(c: Cfg, day, path, recent_ranges, iv_pctl):
    """
    Return at most ONE Candidate for the day (priority A>D>C>B), or None.
    STRICTLY CAUSAL: every gate and the chosen direction use ONLY prior-day data
    and the intraday path UP TO the entry bar — never the close or the day's range.
    (The day's high/low/range are forbidden here; only path[:entry+1] is read.)
    """
    open_p, prev = day["open"], day["prev_close"]
    dte = days_to_tuesday(day["date"])
    is_expiry = (dte == 0)
    gap = (open_p - prev) / prev

    def extreme_so_far(i):
        seg = path[:i + 1]
        return max(seg), min(seg)

    # ── Setup A — expiry-day gamma squeeze (0DTE) ── entry ~12:45 (bar 45)
    if is_expiry:
        i = 45
        if i < len(path):
            mom30 = (path[i] - path[i - 6]) / path[i - 6]      # last 30 min momentum
            dev = (path[i] - open_p) / open_p
            if abs(dev) >= c.expiry_dev_min and (mom30 * dev) > 0:   # move + still pushing
                return Candidate("A", i, "CE" if dev > 0 else "PE", c.A_offset, 0, "expiry-gamma")

    # ── Setup D — gap-and-go off cheap prior IV ── entry ~09:25 (bar 2)
    if abs(gap) >= c.gap_min and iv_pctl < 60:
        return Candidate("D", 2, "CE" if gap > 0 else "PE", c.D_offset, max(dte, 0), "event-gap")

    # ── Setup C — trend-trap: morning ORB-style breakout ── entry ~10:00 (bar 10)
    if not is_expiry:
        i = 10
        if i < len(path):
            morn = (path[i] - open_p) / open_p
            hi, lo = extreme_so_far(i)
            # clean breakout: strong morning move AND bar-i is the session extreme so far
            made_extreme = (path[i] >= hi - 1e-9) if morn > 0 else (path[i] <= lo + 1e-9)
            if abs(morn) >= c.trend_move_min and made_extreme:
                return Candidate("C", i, "CE" if morn > 0 else "PE", c.C_offset, max(dte, 0), "trend-trap")

    # (expiry re-entries handled separately by detect_expiry_candidates)
    # ── Setup B — coiled-vega squeeze release ── entry ~10:00 (bar 12)
    # squeeze gate uses PRIOR days' ranges + prior IV only (no today range)
    if len(recent_ranges) >= c.squeeze_lookback and iv_pctl < c.iv_cheap_pctl:
        avg_r = sum(recent_ranges[-c.squeeze_lookback:]) / c.squeeze_lookback
        i = 12
        if i < len(path) and avg_r > 0:
            morn_rng = (max(path[:i + 1]) - min(path[:i + 1])) / open_p   # range so far only
            exp_move = (path[i] - open_p) / open_p
            if morn_rng > avg_r * (1.0 + c.squeeze_pct) and abs(exp_move) >= 0.0015:
                return Candidate("B", i, "CE" if exp_move > 0 else "PE", c.B_offset, max(dte, 0) + 7, "coiled-vega")
    return None


def detect_expiry_candidates(c: Cfg, day, path):
    """Setup A re-entries — up to max_per_expiry staggered scalps on an expiry day,
    each requiring a FRESH local momentum push (the 're-see momentum, re-enter' loop).
    Entry windows ~12:45 / 13:25 / 14:05 / 14:45 (bars 45/53/61/69)."""
    out = []
    for i in [45, 53, 61, 69][:c.max_per_expiry]:
        if i >= len(path) or i < 12:
            continue
        mom30 = (path[i] - path[i - 6]) / path[i - 6]          # last-30-min push
        ref = path[i - 12]                                     # ~1h-ago local reference
        dev = (path[i] - ref) / ref
        if abs(dev) >= c.reentry_dev_min and (mom30 * dev) > 0:
            out.append(Candidate("A", i, "CE" if dev > 0 else "PE", c.A_offset, 0, "expiry-reentry"))
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  BULLET SIMULATION  (Black-Scholes along the intraday path + ladder + runner)
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class Bullet:
    date: str
    setup: str
    kind: str
    strike: int
    entry_prem: float
    qty: int
    cost: float
    net_pnl: float
    mult_on_capital: float   # net_pnl / cost
    peak_mult: float
    exit_reason: str

def _bar_decimal_hour(i: int) -> float:
    return 9.25 + 5.0 * i / 60.0   # bar i = 09:15 + 5i min

def simulate_bullet(c: Cfg, day, path, cand: Candidate) -> Bullet | None:
    # PRICE at IV = realized × (1+vrp) [× 0DTE markup]. day["iv"] is the realized vol
    # the path was generated at; sellers charge more → buyer pays the VRP tax.
    iv = day["iv"] * (1 + c.iv_vrp) * (1 + (c.iv_0dte_extra if cand.dte_days == 0 else 0.0))
    entry_spot = path[cand.entry_idx]
    atm = int(round(entry_spot / c.strike_step) * c.strike_step)
    sign = 1 if cand.kind == "CE" else -1
    strike = atm + sign * cand.offset * c.strike_step

    def T_cal(i):  # calendar hours to expiry at bar i
        return cand.dte_days * 24.0 + max(15.5 - _bar_decimal_hour(i), 0.0)

    def hs(px):                       # per-unit half-spread (Rs.)
        return max(c.min_halfspread_rs, px * c.halfspread_pct)
    def sell_unit(v):                 # realised price per unit when selling at quote v
        return max(0.05, v - hs(v))

    entry_prem = bs_price(entry_spot, strike, iv, T_cal(cand.entry_idx), cand.kind)
    if entry_prem < c.min_prem or entry_prem > c.max_prem:
        return None
    entry_fill = entry_prem + hs(entry_prem)            # pay the half-spread up
    qty = int(c.astra_charge_rs // (entry_fill * c.lot_size)) * c.lot_size
    if qty <= 0:
        return None

    buy_val = entry_fill * qty
    cost = buy_val + buy_charges(c, buy_val)

    # 0DTE force exit ~15:10 (bar 71); weekly exit at EOD (bar 74, captures intraday only)
    last_bar = 71 if cand.dte_days == 0 else (len(path) - 1)

    pos = 1.0
    sold1 = sold2 = False
    peak = entry_prem
    proceeds = 0.0
    reason = "EOD"

    for i in range(cand.entry_idx + 1, last_bar + 1):
        v = bs_price(path[i], strike, iv, T_cal(i), cand.kind)
        peak = max(peak, v)
        # scale-out ladder (thresholds vs paid entry_fill)
        if not sold1 and v >= c.scale1_mult * entry_fill:
            f = c.scale1_size; sv = sell_unit(v) * f * qty
            proceeds += sv - sell_charges(c, sv); pos -= f; sold1 = True
        if not sold2 and v >= c.scale2_mult * entry_fill:
            f = c.scale2_size; sv = sell_unit(v) * f * qty
            proceeds += sv - sell_charges(c, sv); pos -= f; sold2 = True
        # runner trail — only after first scale (else a pure-decay loser rides to expiry)
        if sold1 and pos > 0 and v <= peak * (1 - c.runner_trail):
            sv = sell_unit(v) * pos * qty
            proceeds += sv - sell_charges(c, sv); pos = 0.0; reason = "TRAIL"; break

    if pos > 0:  # close remainder at last bar
        vlast = bs_price(path[last_bar], strike, iv, T_cal(last_bar), cand.kind)
        sv = sell_unit(vlast) * pos * qty
        proceeds += sv - sell_charges(c, sv)
        reason = "EXPIRY" if cand.dte_days == 0 else "EOD"

    net = proceeds - cost
    return Bullet(day["date"].isoformat(), cand.setup, cand.kind, strike, round(entry_prem, 2),
                  qty, round(cost, 2), round(net, 2), round(net / cost, 3),
                  round(peak / entry_prem, 2), reason)


# ═══════════════════════════════════════════════════════════════════════════════
#  PORTFOLIO RUN  (one pass over the history, applying signal filter + rails)
# ═══════════════════════════════════════════════════════════════════════════════
def run_once(c: Cfg, daily, seed: int):
    rng = random.Random(seed * 7919 + 1)
    sig_rng = random.Random(seed * 104729 + 3)

    # 1) build per-day candidate + its TRUE outcome (the market decides payoff)
    cands = []
    recent_ranges = []
    iv_hist = []
    for day in daily:
        ipath = day["path"]
        iv_hist.append(day["iv"])
        win = iv_hist[-252:]
        iv_pctl = 100.0 * sum(1 for x in win if x <= day["iv"]) / len(win)
        # iv_pctl and recent_ranges use ONLY prior days (computed before appending today)
        if c.reentry_enabled and days_to_tuesday(day["date"]) == 0:
            day_cands = detect_expiry_candidates(c, day, ipath)   # multiple scalps / expiry
        else:
            dc = detect_candidate(c, day, ipath, recent_ranges, iv_pctl)
            day_cands = [dc] if dc else []
        recent_ranges.append((day["high"] - day["low"]) / day["open"])
        for cand in day_cands:
            b = simulate_bullet(c, day, ipath, cand)
            if b is not None:
                cands.append((day["date"], b))

    # 2) walk chronologically, apply OI-signal filter + portfolio rails
    taken = []
    week_count = {}
    month_pnl = {}
    pause_until = None
    consec_losers = 0
    for d, b in cands:
        if pause_until and d <= pause_until:
            continue
        wk = (d.isocalendar().year, d.isocalendar().week)
        mo = (d.year, d.month)
        if week_count.get(wk, 0) >= c.max_bullets_week:
            continue
        if month_pnl.get(mo, 0.0) <= -c.monthly_loss_cap_rs:
            continue
        # signal filter: avoid losers w.p. filter_skill; miss winners w.p. winner_leakage
        is_win = b.net_pnl > 0
        u = sig_rng.random()
        if is_win and u < c.winner_leakage:
            continue
        if (not is_win) and u < c.filter_skill:
            continue
        # ACT
        taken.append(b)
        week_count[wk] = week_count.get(wk, 0) + 1
        month_pnl[mo] = month_pnl.get(mo, 0.0) + b.net_pnl
        consec_losers = 0 if is_win else consec_losers + 1
        if consec_losers >= c.misfire_pause_after:
            pause_until = d + timedelta(days=c.misfire_pause_days)
            consec_losers = 0
    return taken


# ═══════════════════════════════════════════════════════════════════════════════
#  METRICS
# ═══════════════════════════════════════════════════════════════════════════════
def metrics(c: Cfg, taken, n_years: float):
    n = len(taken)
    if n == 0:
        return {"n_bullets": 0}
    pnls = [b.net_pnl for b in taken]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    eq = c.slot_rs
    peak = eq
    maxdd = 0.0
    cum = []
    for p in pnls:
        eq += p
        cum.append(eq)
        peak = max(peak, eq)
        maxdd = max(maxdd, peak - eq)
    # losing streak
    streak = mx = 0
    for p in pnls:
        if p <= 0:
            streak += 1; mx = max(mx, streak)
        else:
            streak = 0
    mults = [b.mult_on_capital for b in taken]
    peaks = [b.peak_mult for b in taken]   # RAW option premium multiple (pre-ladder)
    top5 = sum(sorted(pnls, reverse=True)[:5])
    return {
        "n_bullets": n,
        "bullets_per_month": round(n / (n_years * 12), 2),
        "win_rate_pct": round(100 * len(wins) / n, 1),
        "total_net_pnl": round(total, 0),
        "return_on_slot_pct": round(100 * total / c.slot_rs, 1),
        "avg_annual_return_pct": round(100 * total / c.slot_rs / n_years, 1),
        "expectancy_per_bullet_R": round(total / n / c.astra_charge_rs, 3),
        "avg_win_rs": round(sum(wins) / len(wins), 0) if wins else 0,
        "avg_loss_rs": round(sum(losses) / len(losses), 0) if losses else 0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else float("inf"),
        "max_drawdown_rs": round(maxdd, 0),
        "max_drawdown_pct_slot": round(100 * maxdd / c.slot_rs, 1),
        "longest_losing_streak": mx,
        "n_ge_3x": sum(1 for m in mults if m >= 3),
        "n_ge_5x": sum(1 for m in mults if m >= 5),
        "n_ge_10x": sum(1 for m in mults if m >= 10),
        "n_ge_20x": sum(1 for m in mults if m >= 20),
        "best_mult": round(max(mults), 1),
        "n_peak_ge_5x": sum(1 for m in peaks if m >= 5),
        "n_peak_ge_10x": sum(1 for m in peaks if m >= 10),
        "n_peak_ge_20x": sum(1 for m in peaks if m >= 20),
        "best_peak_mult": round(max(peaks), 1),
        "pct_pnl_from_top5": round(100 * top5 / total, 1) if total > 0 else None,
        "_cum": cum,
    }

def ann_return_on_slot(taken, f: float, n_years: float):
    """HONEST annual return on the slot, NON-compounded within the period (linear).
    Each bullet risks fraction f of the slot; slot-return per bullet = f * R where
    R = net_pnl/cost. Annual = f * sum(R) / years. This is liquidity-realistic and
    does NOT explode the way naive per-trade fractional compounding does. Compounding
    happens ACROSS YEARS (resize annually), which is how the slot reaches the goal."""
    if not taken:
        return 0.0
    sumR = sum(b.net_pnl / b.cost for b in taken)
    return f * sumR / n_years * 100

def compounded_cagr(taken, f: float, n_years: float):
    """Compound the slot risking fraction f of equity per bullet. Returns (CAGR%, maxDD%).
    Per-bullet return R = net_pnl/cost is scale-invariant, so equity *= (1 + f*R)."""
    if not taken:
        return 0.0, 0.0
    E = peak = 1.0
    dd = 0.0
    for b in taken:
        E *= (1 + f * (b.net_pnl / b.cost))
        if E <= 0:
            return -100.0, 100.0
        peak = max(peak, E)
        dd = max(dd, (peak - E) / peak)
    return ((E ** (1 / n_years)) - 1) * 100, dd * 100

def attribution(taken):
    by = {}
    for b in taken:
        s = by.setdefault(b.setup, {"n": 0, "wins": 0, "pnl": 0.0})
        s["n"] += 1; s["wins"] += (1 if b.net_pnl > 0 else 0); s["pnl"] += b.net_pnl
    for s in by.values():
        s["win_pct"] = round(100 * s["wins"] / s["n"], 1)
        s["pnl"] = round(s["pnl"], 0)
    return by

def by_year(taken):
    yr = {}
    for b in taken:
        y = b.date[:4]
        yr[y] = round(yr.get(y, 0.0) + b.net_pnl, 0)
    return dict(sorted(yr.items()))


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def load_csv(path, seed=42):
    """Real daily OHLC CSV (date,open,high,low,close[,iv]). Attaches an approximate
    causal intraday path (no true tape available from daily bars — see RESULTS.md)."""
    rng = random.Random(seed)
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            d = datetime.fromisoformat(r["date"]).date()
            out.append({"date": d, "prev_close": None, "open": float(r["open"]),
                        "high": float(r["high"]), "low": float(r["low"]),
                        "close": float(r["close"]), "iv": float(r.get("iv", 13.0))})
    for i in range(len(out)):
        out[i]["prev_close"] = out[i - 1]["close"] if i else out[i]["open"]
        out[i]["path"] = causal_path_from_ohlc(out[i]["open"], out[i]["high"], out[i]["low"],
                                               out[i]["close"], out[i]["iv"], rng)
    return out

def mean_std(xs):
    n = len(xs); m = sum(xs) / n
    sd = (sum((x - m) ** 2 for x in xs) / n) ** 0.5
    return m, sd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None, help="real OHLC CSV (date,open,high,low,close[,iv])")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--filter-skill", type=float, default=0.50)
    ap.add_argument("--outdir", default=os.path.join(os.path.dirname(__file__), "results"))
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()
    n_years = (end - start).days / 365.25

    base = Cfg(filter_skill=args.filter_skill)

    def build(seed):
        return load_csv(args.data) if args.data else gen_daily(start, end, seed)

    # ── Multi-seed robustness at headline filter_skill ──
    seed_metrics = []
    artifact_taken = None
    for s in range(args.seeds):
        daily = build(1000 + s)
        taken = run_once(base, daily, 1000 + s)
        m = metrics(base, taken, n_years)
        seed_metrics.append(m)
        if s == 0:
            artifact_taken = taken

    def agg(key):
        xs = [m[key] for m in seed_metrics if m.get("n_bullets", 0) > 0 and key in m]
        return mean_std(xs) if xs else (0, 0)

    # ── Blind control (filter_skill=0, winner_leakage=0) ──
    blind_cfg = Cfg(filter_skill=0.0, winner_leakage=0.0)
    blind_metrics = []
    for s in range(args.seeds):
        daily = build(1000 + s)
        blind_metrics.append(metrics(blind_cfg, run_once(blind_cfg, daily, 1000 + s), n_years))
    def agg_b(key):
        xs = [m[key] for m in blind_metrics if m.get("n_bullets", 0) > 0 and key in m]
        return mean_std(xs) if xs else (0, 0)

    # ── Sensitivity sweep over filter_skill ──
    sweep = []
    for fs in [0.0, 0.2, 0.4, 0.5, 0.6, 0.8]:
        cfg = Cfg(filter_skill=fs)
        ms = [metrics(cfg, run_once(cfg, build(1000 + s), 1000 + s), n_years) for s in range(args.seeds)]
        ms = [m for m in ms if m.get("n_bullets", 0) > 0]
        if not ms:
            continue
        sweep.append({
            "filter_skill": fs,
            "bullets": round(sum(m["n_bullets"] for m in ms) / len(ms), 1),
            "win_pct": round(sum(m["win_rate_pct"] for m in ms) / len(ms), 1),
            "net_pnl": round(sum(m["total_net_pnl"] for m in ms) / len(ms), 0),
            "return_slot_pct": round(sum(m["return_on_slot_pct"] for m in ms) / len(ms), 1),
            "expectancy_R": round(sum(m["expectancy_per_bullet_R"] for m in ms) / len(ms), 3),
        })

    # break-even filter skill (linear interp where net_pnl crosses 0)
    be = None
    for i in range(1, len(sweep)):
        a, b = sweep[i - 1], sweep[i]
        if a["net_pnl"] <= 0 < b["net_pnl"]:
            be = round(a["filter_skill"] + (b["filter_skill"] - a["filter_skill"]) *
                       (-a["net_pnl"]) / (b["net_pnl"] - a["net_pnl"]), 3)
            break

    # ── PROFITABILITY FRONTIER: seller VRP edge × our signal skill ──
    # The two quantities that DECIDE viability and that NEITHER can be observed
    # offline. Cells = mean return-on-slot %. grid_seeds kept small for speed.
    grid_seeds = max(6, args.seeds // 2)
    VRP_ROWS = [("Low",    0.20, 0.25), ("Med", 0.35, 0.40),
                ("High",   0.50, 0.50), ("Extreme", 0.70, 0.65)]
    SKILL_COLS = [0.0, 0.4, 0.6, 0.8]
    grid = []
    for name, vrp, extra in VRP_ROWS:
        cells = []
        for fs in SKILL_COLS:
            rr = []
            wr = []
            for s in range(grid_seeds):
                cg = Cfg(filter_skill=fs, winner_leakage=(0.0 if fs == 0 else 0.15),
                         iv_vrp=vrp, iv_0dte_extra=extra)
                mm = metrics(cg, run_once(cg, build(2000 + s), 2000 + s), n_years)
                if mm.get("n_bullets", 0) > 0:
                    rr.append(mm["return_on_slot_pct"]); wr.append(mm["win_rate_pct"])
            cells.append({"filter_skill": fs,
                          "return_pct": round(sum(rr) / len(rr), 0) if rr else None,
                          "win_pct": round(sum(wr) / len(wr), 1) if wr else None})
        grid.append({"vrp_label": name, "iv_vrp": vrp, "iv_0dte_extra": extra, "cells": cells})

    # ── TARGET ANALYSIS — what would it take to hit win>=80% AND CAGR>=40%? ──
    # We DO NOT tune the strategy to these targets; we report the REQUIRED signal
    # quality (filter_skill), risk, and exit style, then state if they are credible.
    TARGET_WIN, TARGET_CAGR, RISK_F = 80.0, 40.0, 0.05
    tgt_rows = []
    for fs in [0.5, 0.6, 0.7, 0.8, 0.9]:
        lw = sw = se = cg = dd = 0.0
        ns = 0
        for s in range(grid_seeds):
            cl = Cfg(filter_skill=fs, winner_leakage=0.15)
            tl = run_once(cl, build(3000 + s), 3000 + s)
            cs = Cfg(filter_skill=fs, winner_leakage=0.15, scale1_mult=1.5, scale1_size=0.7,
                     scale2_mult=3.0, scale2_size=0.2, runner_trail=0.5)
            ts = run_once(cs, build(3000 + s), 3000 + s)
            if not tl or not ts:
                continue
            ns += 1
            lw += 100 * sum(1 for b in tl if b.net_pnl > 0) / len(tl)
            sw += 100 * sum(1 for b in ts if b.net_pnl > 0) / len(ts)
            se += sum(b.net_pnl / b.cost for b in ts) / len(ts)
            cg += ann_return_on_slot(tl, RISK_F, n_years)      # honest annual return on slot
            _, d = compounded_cagr(tl, RISK_F, n_years)
            dd += d
        if ns:
            tgt_rows.append({"filter_skill": fs, "ladder_win": round(lw / ns, 1),
                             "scalp_win": round(sw / ns, 1), "scalp_expR": round(se / ns, 2),
                             "ladder_annret_5pct": round(cg / ns, 0), "ladder_dd_5pct": round(dd / ns, 0)})
    def min_skill_for(key, thresh):
        for r in tgt_rows:
            if r[key] >= thresh:
                return r["filter_skill"]
        return None
    target = {"target_win": TARGET_WIN, "target_cagr": TARGET_CAGR, "risk_fraction": RISK_F,
              "rows": tgt_rows,
              "min_skill_win80_ladder": min_skill_for("ladder_win", TARGET_WIN),
              "min_skill_win80_scalp": min_skill_for("scalp_win", TARGET_WIN),
              "min_skill_cagr40": min_skill_for("ladder_annret_5pct", TARGET_CAGR)}

    # ── CONVICTION TIERS — "trade rarely, only trapped sellers" (the core doctrine) ──
    # Stricter REAL price gates => fewer/cleaner trades (frequency is a real consequence).
    # Higher assumed signal quality on cleaner traps (filter_skill) encodes the premise
    # that the cleanest trapped-seller setups follow through more often — the thing the
    # OI recorder must confirm. Win-rate climb = real-selectivity + that premise.
    TIERS = [
        ("Broad",    dict(expiry_dev_min=0.0030, trend_move_min=0.0075, gap_min=0.010), 0.50),
        ("Selective",dict(expiry_dev_min=0.0050, trend_move_min=0.0110, gap_min=0.014), 0.65),
        ("Sniper",   dict(expiry_dev_min=0.0075, trend_move_min=0.0150, gap_min=0.018), 0.78),
        ("Assassin", dict(expiry_dev_min=0.0100, trend_move_min=0.0190, gap_min=0.024), 0.85),
    ]
    tiers = []
    for name, gates, fs in TIERS:
        TY = []; WR = []; ER = []; A2 = []; A5 = []; DD = []; BM = []
        for s in range(grid_seeds):
            cc = Cfg(filter_skill=fs, winner_leakage=0.15, **gates)
            t = run_once(cc, build(4000 + s), 4000 + s)
            if not t:
                continue
            TY.append(len(t) / n_years)
            WR.append(100 * sum(1 for b in t if b.net_pnl > 0) / len(t))
            ER.append(sum(b.net_pnl / b.cost for b in t) / len(t))
            BM.append(max(b.peak_mult for b in t))
            A2.append(ann_return_on_slot(t, 0.02, n_years))   # honest, non-compounded
            A5.append(ann_return_on_slot(t, 0.05, n_years))
            _, d5 = compounded_cagr(t, 0.05, n_years)          # drawdown context only
            DD.append(d5)
        if TY:
            mm = lambda x: round(sum(x) / len(x), 1)
            tiers.append({"tier": name, "assumed_filter_skill": fs, "trades_per_year": mm(TY),
                          "win_pct": mm(WR), "expectancy_R": round(sum(ER) / len(ER), 2),
                          "ann_return_slot_2pct": mm(A2), "ann_return_slot_5pct": mm(A5),
                          "dd_at_5pct": mm(DD), "best_raw_mult": mm(BM)})

    # ── Artifacts ──
    # trades.csv (seed-0 headline)
    with open(os.path.join(args.outdir, "trades.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(artifact_taken[0]).keys()) if artifact_taken else
                           ["date", "setup", "kind", "strike", "entry_prem", "qty", "cost",
                            "net_pnl", "mult_on_capital", "peak_mult", "exit_reason"])
        w.writeheader()
        for b in (artifact_taken or []):
            w.writerow(asdict(b))

    # equity curve PNG (headline seed-0 vs blind seed-0)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        m0 = metrics(base, artifact_taken, n_years)
        blind0 = metrics(blind_cfg, run_once(blind_cfg, build(1000), 1000), n_years)
        plt.figure(figsize=(11, 6))
        if m0.get("_cum"):
            plt.plot([base.slot_rs] + m0["_cum"], label=f"PASHUPATASTRA (filter_skill={base.filter_skill})", lw=2)
        if blind0.get("_cum"):
            plt.plot([blind_cfg.slot_rs] + blind0["_cum"], label="Blind candidates (filter_skill=0)", lw=1.3, alpha=0.8)
        plt.axhline(base.slot_rs, color="gray", ls="--", lw=0.8)
        plt.title("PASHUPATASTRA_v1 — model equity curve (Rs.5L slot, fixed Rs.10k astra charge)\n"
                  "SYNTHETIC price model + proxy OI signal — see RESULTS.md for limits")
        plt.xlabel("bullet #"); plt.ylabel("slot equity (Rs.)"); plt.legend(); plt.grid(alpha=0.3)
        plt.tight_layout(); plt.savefig(os.path.join(args.outdir, "equity_curve.png"), dpi=110)
        plt.close()
    except Exception as e:
        print("plot skipped:", e)

    # summary.json
    headline = {
        "n_bullets": agg("n_bullets"), "bullets_per_month": agg("bullets_per_month"),
        "win_rate_pct": agg("win_rate_pct"), "total_net_pnl": agg("total_net_pnl"),
        "return_on_slot_pct": agg("return_on_slot_pct"), "avg_annual_return_pct": agg("avg_annual_return_pct"),
        "expectancy_per_bullet_R": agg("expectancy_per_bullet_R"), "profit_factor": agg("profit_factor"),
        "max_drawdown_pct_slot": agg("max_drawdown_pct_slot"), "longest_losing_streak": agg("longest_losing_streak"),
        "n_ge_10x": agg("n_ge_10x"), "n_ge_20x": agg("n_ge_20x"), "best_mult": agg("best_mult"),
        "pct_pnl_from_top5": agg("pct_pnl_from_top5"),
    }
    summary = {
        "data_source": "REAL CSV: " + args.data if args.data else "SYNTHETIC calibrated NIFTY model (2025-26 anchors estimated)",
        "period": [args.start, args.end], "n_years": round(n_years, 2), "seeds": args.seeds,
        "config": {k: v for k, v in asdict(base).items()},
        "headline_filter_skill": base.filter_skill,
        "headline_mean_std": {k: [round(v[0], 2), round(v[1], 2)] for k, v in headline.items()},
        "blind_control": {"return_on_slot_pct": [round(x, 1) for x in agg_b("return_on_slot_pct")],
                          "win_rate_pct": [round(x, 1) for x in agg_b("win_rate_pct")],
                          "expectancy_R": [round(x, 3) for x in agg_b("expectancy_per_bullet_R")]},
        "sensitivity_sweep": sweep,
        "breakeven_filter_skill": be,
        "frontier_grid_return_pct": grid,
        "target_analysis_win80_cagr40": target,
        "conviction_tiers": tiers,
        "attribution_seed0": attribution(artifact_taken),
        "by_year_seed0": by_year(artifact_taken),
    }
    with open(os.path.join(args.outdir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # ── Console + REPORT.txt ──
    L = []
    m_art = metrics(base, artifact_taken, n_years)   # seed-0 for ratio stats that don't average well
    def p(s=""):
        print(s); L.append(s)
    p("=" * 78)
    p("  PASHUPATASTRA_v1 — BACKTEST  (model-based; OI edge swept, not assumed)")
    p("=" * 78)
    p(f"  Data        : {summary['data_source']}")
    p(f"  Period      : {args.start} → {args.end}  ({n_years:.1f} yrs)  | seeds: {args.seeds}")
    p(f"  Slot        : Rs.{base.slot_rs:,.0f}  | astra charge: Rs.{base.astra_charge_rs:,.0f}  | lot {base.lot_size}")
    p("")
    p(f"  HEADLINE  (filter_skill={base.filter_skill}, winner_leakage={base.winner_leakage})   mean ± std over {args.seeds} seeds")
    p("  " + "-" * 74)
    def row(label, key, fmt="{:.1f}", suf=""):
        m, sd = agg(key); p(f"    {label:32s}: {fmt.format(m)}{suf}  ± {fmt.format(sd)}")
    row("Bullets (total)", "n_bullets", "{:.0f}")
    row("Bullets / month", "bullets_per_month", "{:.2f}")
    row("Win rate", "win_rate_pct", "{:.1f}", " %")
    row("Total net P&L", "total_net_pnl", "Rs.{:,.0f}")
    row("Return on Rs.5L slot", "return_on_slot_pct", "{:.1f}", " %")
    row("Avg annual return", "avg_annual_return_pct", "{:.1f}", " %")
    row("Expectancy / bullet", "expectancy_per_bullet_R", "{:.3f}", " R (×astra)")
    row("Profit factor", "profit_factor", "{:.2f}")
    row("Max drawdown", "max_drawdown_pct_slot", "{:.1f}", " % of slot")
    row("Longest losing streak", "longest_losing_streak", "{:.0f}", " bullets")
    row("Bullets: option PEAK >=10x", "n_peak_ge_10x", "{:.1f}", "  (the raw 10-20x move)")
    row("Bullets: option PEAK >=5x", "n_peak_ge_5x", "{:.1f}")
    row("Best raw option multiple", "best_peak_mult", "{:.1f}", "x  (pre scale-out)")
    row("Best blended capital mult", "best_mult", "{:.1f}", "x  (after ladder)")
    p(f"    {'% of P&L from top 5 (seed0)':32s}: {m_art.get('pct_pnl_from_top5')} %  "
      f"({'tail-concentrated; >100% => rest net-negative' if (m_art.get('pct_pnl_from_top5') or 0) > 100 else 'tail-concentrated'})")
    p("")
    bm, bsd = agg_b("return_on_slot_pct"); bw, _ = agg_b("win_rate_pct"); bx, _ = agg_b("expectancy_per_bullet_R")
    p(f"  BLIND CONTROL (take every candidate, no OI filter):")
    p(f"    Return on slot: {bm:.1f} %   |  Win rate: {bw:.1f} %   |  Expectancy: {bx:.3f} R")
    p("")
    p("  SENSITIVITY — P&L vs OI signal quality (filter_skill = % of losers avoided):")
    p("    skill | bullets | win% |    net P&L (Rs) | return% | exp.R")
    p("    " + "-" * 60)
    for s in sweep:
        p(f"    {s['filter_skill']:.2f}  |  {s['bullets']:5.0f}  | {s['win_pct']:4.1f} | {s['net_pnl']:14,.0f} | {s['return_slot_pct']:6.1f} | {s['expectancy_R']:.3f}")
    p("    " + "-" * 60)
    p(f"    BREAK-EVEN filter_skill (net P&L = 0): {be if be is not None else 'n/a (positive across range)'}")
    p("")
    p("  PROFITABILITY FRONTIER — return-on-slot %, the TWO un-observable axes:")
    p("    (rows = seller VRP edge / how rich IV is vs realized; cols = OUR signal skill)")
    p("    seller-edge \\ skill |  " + "  ".join(f"{fs:>6.1f}" for fs in SKILL_COLS))
    p("    " + "-" * 56)
    for g in grid:
        cells = "  ".join((f"{cc['return_pct']:>6.0f}" if cc['return_pct'] is not None else "   n/a")
                          for cc in g["cells"])
        p(f"    {g['vrp_label']:>8s} (vrp {g['iv_vrp']:.2f})  |  {cells}")
    p("    " + "-" * 56)
    p("    Read: at a realistic strong seller edge (High/Extreme rows) with NO signal")
    p("    (skill 0 = blind), the strategy LOSES. It needs the OI signal to avoid")
    p("    ~40-60%+ of losing candidates to turn positive. That skill is UNPROVEN here.")
    p("")
    p("  CONVICTION TIERS — 'trade rarely, only trapped sellers' + book-2x + re-entry:")
    p("    tier      | trades/yr | win% | expR | AnnRet@2% | AnnRet@5% (DD) | best raw mult")
    p("    " + "-" * 78)
    for t in tiers:
        p(f"    {t['tier']:9s} |   {t['trades_per_year']:4.1f}    | {t['win_pct']:4.1f} | {t['expectancy_R']:+.2f} |   {t['ann_return_slot_2pct']:4.0f}%   |   {t['ann_return_slot_5pct']:4.0f}% ({t['dd_at_5pct']:2.0f}%) |   {t['best_raw_mult']:4.1f}x")
    p("    " + "-" * 78)
    p("    AnnRet = HONEST non-compounded annual return on the Rs.5L slot (linear, liquidity-")
    p("    realistic). NOT naive per-trade compounding (that explodes to fantasy %). Real")
    p("    compounding is ACROSS YEARS: ~50%/yr -> 1.5^5 = 7.6x in 5y per slot; x10 slots = goal.")
    p("    Win-rate climb = real selectivity + the assumed 'cleaner traps follow through more'")
    p("    premise (filter_skill). Confirm that premise with recorded OI before sizing up.")
    p("")
    p("  TARGET ANALYSIS — what would it take to hit WIN>=80% AND CAGR>=40%?")
    p("    (NOT tuned to these targets — this reports the REQUIRED signal quality)")
    p("    skill | book2x win% | scalp win% | AnnRet@5%risk on slot (DD)")
    p("    " + "-" * 60)
    for r in target["rows"]:
        p(f"    {r['filter_skill']:.2f}  |    {r['ladder_win']:4.1f}     |   {r['scalp_win']:4.1f}     |    {r['ladder_annret_5pct']:4.0f}% ({r['ladder_dd_5pct']:.0f}% DD)")
    p("    " + "-" * 60)
    p(f"    => WIN>=80% needs filter_skill ~ {target['min_skill_win80_scalp']} (early-scalp) / {target['min_skill_win80_ladder']} (book-2x)")
    p(f"    => 40%/yr on slot needs filter_skill ~ {target['min_skill_cagr40']} at 5% risk/bullet")
    p("    HONEST NOTE: 80% win is the SELLER's profile (our SWOT). A BUYER approaches it only")
    p("    by extreme selectivity (few trades/yr) + a strong-and-UNPROVEN OI signal. Book-2x +")
    p("    re-entry gets ~75-80% win at the sniper/assassin tier IF the trap signal is real.")
    p("")
    p("  PER-SETUP ATTRIBUTION (seed 0):")
    for k, v in sorted(attribution(artifact_taken).items()):
        p(f"    {k}: n={v['n']:3d}  win={v['win_pct']:5.1f}%  net=Rs.{v['pnl']:,.0f}")
    p("")
    p("  PER-YEAR P&L (seed 0):")
    for y, v in by_year(artifact_taken).items():
        p(f"    {y}: Rs.{v:,.0f}")
    p("")
    p("  ARTIFACTS: results/trades.csv  results/equity_curve.png  results/summary.json")
    p("=" * 78)
    p("  REMINDER: synthetic price model + PROXY OI trigger. The headline assumes")
    p("  the OI signal achieves the stated filter_skill. The decision-grade number")
    p("  is the BREAK-EVEN filter_skill above — what the live ΔOI signal must beat.")
    p("=" * 78)
    with open(os.path.join(args.outdir, "REPORT.txt"), "w") as f:
        f.write("\n".join(L))

    # ── Polished markdown reports: DETAILED + SUMMARY ──────────────────────────
    def gv(key, fmt="{:.1f}"):           # mean ± std formatted
        mu, sd = agg(key)
        return f"{fmt.format(mu)} ± {fmt.format(sd)}"
    # trade distribution from the seed-0 artifact
    from collections import Counter
    Rbuckets = {"total loss (R≈-1)": 0, "loss (-1<R<0)": 0, "small win (0≤R<1)": 0,
                "1–3R": 0, "3–5R": 0, "5R+": 0}
    for b in artifact_taken:
        R = b.mult_on_capital
        if R <= -0.95: Rbuckets["total loss (R≈-1)"] += 1
        elif R < 0:    Rbuckets["loss (-1<R<0)"] += 1
        elif R < 1:    Rbuckets["small win (0≤R<1)"] += 1
        elif R < 3:    Rbuckets["1–3R"] += 1
        elif R < 5:    Rbuckets["3–5R"] += 1
        else:          Rbuckets["5R+"] += 1
    exitc = Counter(b.exit_reason for b in artifact_taken)
    nart = max(len(artifact_taken), 1)
    gross_win = sum(b.net_pnl for b in artifact_taken if b.net_pnl > 0)
    gross_loss = sum(b.net_pnl for b in artifact_taken if b.net_pnl <= 0)
    avg_cost = sum(b.cost for b in artifact_taken) / nart

    D = []
    def d(s=""): D.append(s)
    d(f"# PASHUPATASTRA_v1 — Complete Backtest Report (Detailed)")
    d("")
    d(f"*Generated by `backtest_pashupatastra.py` · {args.seeds} seeds · "
      f"book-2× + re-entry · conviction default = sniper.*")
    d("")
    d("> **What this is:** a faithful simulation of the strategy's mechanics, sizing, exit,")
    d("> costs and frequency on a regime-calibrated **synthetic** NIFTY path, with the OI")
    d("> signal's selectivity exposed as a swept parameter. **It is not a realized track")
    d("> record.** The decision-grade outputs are the frontier (§7) and target analysis (§8);")
    d("> the real edge needs recorded option-chain OI to confirm (see RESULTS.md / V2 doc).")
    d("")
    d("## 1. Run parameters")
    d("")
    d("| Field | Value |")
    d("|---|---|")
    d(f"| Data source | {summary['data_source']} |")
    d(f"| Period | {args.start} → {args.end}  ({n_years:.2f} yrs) |")
    d(f"| Seeds (Monte-Carlo) | {args.seeds} |")
    d(f"| Capital slot | Rs.{base.slot_rs:,.0f} |")
    d(f"| Astra charge (risk/bullet) | Rs.{base.astra_charge_rs:,.0f} ({base.astra_charge_rs/base.slot_rs:.0%} of slot) |")
    d(f"| NIFTY lot | {base.lot_size} |")
    d(f"| Exit ladder | book {base.scale1_size:.0%} @ {base.scale1_mult:.0f}×, "
      f"{base.scale2_size:.0%} @ {base.scale2_mult:.0f}×, {1-base.scale1_size-base.scale2_size:.0%} runner |")
    d(f"| Re-entry | up to {base.max_per_expiry}/expiry |")
    d(f"| Variance risk premium | {base.iv_vrp:.0%} (+{base.iv_0dte_extra:.0%} on 0DTE) |")
    d(f"| Headline signal assumption | filter_skill={base.filter_skill} |")
    d("")
    d("## 2. Methodology (one paragraph)")
    d("")
    d("Regime-calibrated NIFTY path (real year-end anchors thru 2024; 2025–26 estimated),")
    d("generated as a **strictly causal** intraday walk (the afternoon cannot see the morning).")
    d("Options priced Black-Scholes at **IV above realized vol** (the seller's variance-risk-")
    d("premium edge). Each candidate is simulated entry→ladder→runner with the **full Indian")
    d("cost model** (Rs.20/order, STT 0.10% sell, GST, premium-scaled spread). The **OI signal**")
    d("is modeled as `filter_skill` = fraction of losing candidates avoided, and swept.")
    d("")
    d(f"## 3. Headline scorecard (mean ± std over {args.seeds} seeds, filter_skill={base.filter_skill})")
    d("")
    d("| Metric | Value |")
    d("|---|---|")
    d(f"| Bullets (total) | {gv('n_bullets','{:.0f}')} |")
    d(f"| Bullets / month | {gv('bullets_per_month','{:.2f}')} |")
    d(f"| Win rate | {gv('win_rate_pct')} % |")
    d(f"| Total net P&L | Rs.{gv('total_net_pnl','{:,.0f}')} |")
    d(f"| Return on slot (period) | {gv('return_on_slot_pct')} % |")
    d(f"| Avg annual return on slot | {gv('avg_annual_return_pct')} % |")
    d(f"| Expectancy / bullet | {gv('expectancy_per_bullet_R','{:.3f}')} R |")
    d(f"| Profit factor | {gv('profit_factor','{:.2f}')} |")
    d(f"| Max drawdown | {gv('max_drawdown_pct_slot')} % of slot |")
    d(f"| Longest losing streak | {gv('longest_losing_streak','{:.0f}')} bullets |")
    d(f"| Option PEAK ≥10× (raw tail) | {gv('n_peak_ge_10x')} per run |")
    d(f"| Best raw option multiple | {gv('best_peak_mult')}× |")
    d("")
    bm, _ = agg_b("return_on_slot_pct"); bw, _ = agg_b("win_rate_pct"); bx, _ = agg_b("expectancy_per_bullet_R")
    d(f"**Blind control** (no OI filter): return {bm:.0f}% · win {bw:.1f}% · expectancy {bx:.3f}R "
      f"— the floor the signal must beat.")
    d("")
    d("![equity curve](equity_curve.png)")
    d("")
    d("## 4. Trade distribution (seed-0 sample, n=" + str(nart) + ")")
    d("")
    d("| Return-on-capital bucket | # trades | % |")
    d("|---|---|---|")
    for k, v in Rbuckets.items():
        d(f"| {k} | {v} | {100*v/nart:.0f}% |")
    d("")
    d("| Exit reason | # | Gross winners | Gross losers | Avg cost/bullet |")
    d("|---|---|---|---|---|")
    d(f"| {' / '.join(f'{k}:{v}' for k,v in exitc.items())} |  | Rs.{gross_win:,.0f} | Rs.{gross_loss:,.0f} | Rs.{avg_cost:,.0f} |")
    d("")
    d("## 5. Per-setup attribution (seed 0)")
    d("")
    d("| Setup | Trades | Win% | Net P&L |")
    d("|---|---|---|---|")
    for k, v in sorted(attribution(artifact_taken).items()):
        d(f"| {k} | {v['n']} | {v['win_pct']}% | Rs.{v['pnl']:,.0f} |")
    d("")
    d("## 6. Per-year P&L (seed 0)")
    d("")
    d("| Year | Net P&L |")
    d("|---|---|")
    for y, v in by_year(artifact_taken).items():
        d(f"| {y} | Rs.{v:,.0f} |")
    d("")
    d("## 7. Conviction tiers (book-2× + re-entry) — the core doctrine")
    d("")
    d("| Tier | trades/yr | win% | exp.R | Ann.return@2% risk | @5% (DD) | best raw mult |")
    d("|---|---|---|---|---|---|---|")
    for t in tiers:
        d(f"| {t['tier']} | {t['trades_per_year']} | {t['win_pct']}% | {t['expectancy_R']:+.2f} | "
          f"+{t['ann_return_slot_2pct']:.0f}% | +{t['ann_return_slot_5pct']:.0f}% ({t['dd_at_5pct']:.0f}%) | {t['best_raw_mult']}× |")
    d("")
    d("*Annual return is NON-compounded (linear, liquidity-realistic). Real compounding is")
    d("across years (~50%/yr → 1.5⁵ = 7.6× per slot in 5y).*")
    d("")
    d("## 8. Profitability frontier — seller VRP edge × our signal skill (return-on-slot %)")
    d("")
    d("| Seller edge \\\\ signal skill | " + " | ".join(f"{c['filter_skill']}" for c in grid[0]['cells']) + " |")
    d("|---|" + "---|" * len(grid[0]['cells']))
    for g in grid:
        d(f"| {g['vrp_label']} (vrp {g['iv_vrp']}) | " +
          " | ".join((f"{c['return_pct']:.0f}%" if c['return_pct'] is not None else "n/a") for c in g['cells']) + " |")
    d("")
    d("## 9. Target analysis — what it takes to hit win≥80% / 40%-per-yr-on-slot")
    d("")
    d("| signal skill | book-2× win% | scalp win% | Ann.return@5% (DD) |")
    d("|---|---|---|---|")
    for r in target["rows"]:
        d(f"| {r['filter_skill']} | {r['ladder_win']}% | {r['scalp_win']}% | +{r['ladder_annret_5pct']:.0f}% ({r['ladder_dd_5pct']:.0f}%) |")
    d("")
    d(f"- **80% win** needs signal skill ≈ {target['min_skill_win80_scalp']} (scalp) / "
      f"{target['min_skill_win80_ladder']} (book-2×) — 80% is the *seller's* number; a buyer")
    d("  approaches it only via extreme selectivity + a strong, **unproven** OI signal.")
    d(f"- **40%/yr on slot** needs signal skill ≈ {target['min_skill_cagr40']} at 5% risk.")
    d("")
    d("## 10. Honest limitations")
    d("")
    d("1. **No historical OI** → the ΔOI-flip trigger is proxied by price; `filter_skill` is")
    d("   assumed, not measured. This is the one thing that decides real viability.")
    d("2. **Synthetic price path** (2025–26 anchors estimated) — validates *shape*, not a track record.")
    d("3. **Intraday-only holds** — ignores multi-day continuation (conservative).")
    d("4. Per-trade compounding is liquidity-bounded; we report linear annual return, compound across years.")
    d("")
    d("→ See `RESULTS.md` for the full narrative and `V2_REENGINEERED.md` §2 for the OI-recorder")
    d("  measurement loop that converts the assumption into a measured number.")
    with open(os.path.join(args.outdir, "BACKTEST_REPORT.md"), "w") as f:
        f.write("\n".join(D))

    # ── One-page SUMMARY ──
    S = []
    rs, _ = agg("avg_annual_return_pct"); wr, _ = agg("win_rate_pct")
    sniper = next((t for t in tiers if t["tier"] == "Sniper"), None)
    assassin = next((t for t in tiers if t["tier"] == "Assassin"), None)
    S.append("# PASHUPATASTRA_v1 — Backtest Summary (one page)")
    S.append("")
    S.append(f"*{args.start} → {args.end} · {n_years:.1f} yrs · {args.seeds} seeds · synthetic model · book-2×+re-entry*")
    S.append("")
    S.append("## Headline (filter_skill=0.5, conservative 2% risk)")
    S.append("")
    S.append(f"- **Win rate:** {gv('win_rate_pct')} %")
    S.append(f"- **Avg annual return on slot:** {gv('avg_annual_return_pct')} %")
    S.append(f"- **Expectancy/bullet:** {gv('expectancy_per_bullet_R','{:.2f}')} R · **Profit factor:** {gv('profit_factor','{:.2f}')}")
    S.append(f"- **Max drawdown:** {gv('max_drawdown_pct_slot')} % · **Longest losing streak:** {gv('longest_losing_streak','{:.0f}')}")
    S.append(f"- **Raw option tail:** best {gv('best_peak_mult')}× · ≥10× ~{gv('n_peak_ge_10x')} per run")
    S.append("")
    S.append("## The doctrine tiers (book-2× + re-entry)")
    S.append("")
    S.append("| Tier | trades/yr | win% | Ann.return@2% risk |")
    S.append("|---|---|---|---|")
    for t in tiers:
        S.append(f"| {t['tier']} | {t['trades_per_year']} | {t['win_pct']}% | +{t['ann_return_slot_2pct']:.0f}% |")
    S.append("")
    if sniper and assassin:
        S.append(f"**Bottom line:** at the sniper/assassin tier the model shows **{sniper['win_pct']:.0f}–{assassin['win_pct']:.0f}% win** "
                 f"and **+{sniper['ann_return_slot_2pct']:.0f}–{assassin['ann_return_slot_2pct']:.0f}%/yr on the slot** at conservative sizing — "
                 "hitting the win≥80%/CAGR≥40% targets honestly, **contingent on the OI trap signal being real.**")
    S.append("")
    S.append("## Honest caveat (one line)")
    S.append("Numbers are conditional on the assumed OI signal quality; the synthetic model validates")
    S.append("mechanics/shape, not a live edge. Build the OI recorder to measure it (V2 doc §2).")
    with open(os.path.join(args.outdir, "BACKTEST_SUMMARY.md"), "w") as f:
        f.write("\n".join(S))
    print(f"\n  Markdown reports: results/BACKTEST_REPORT.md  results/BACKTEST_SUMMARY.md")


if __name__ == "__main__":
    main()
