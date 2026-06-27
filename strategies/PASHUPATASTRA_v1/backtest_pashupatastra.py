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
    # exit ladder
    scale1_mult: float = 3.0
    scale1_size: float = 0.40
    scale2_mult: float = 6.0
    scale2_size: float = 0.30
    runner_trail: float = 0.40          # runner exits 40% below peak (only after scale1 hits)
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
    # portfolio rails
    max_bullets_week: int = 3
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
        cand = detect_candidate(c, day, ipath, recent_ranges, iv_pctl)
        recent_ranges.append((day["high"] - day["low"]) / day["open"])
        if cand is None:
            continue
        b = simulate_bullet(c, day, ipath, cand)
        if b is None:
            continue
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


if __name__ == "__main__":
    main()
