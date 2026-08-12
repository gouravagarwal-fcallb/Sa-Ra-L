"""
BT-1 — High-fidelity backtest for TRAP_CMCD_v1  (audit control BT-1).
────────────────────────────────────────────────────────────────────
Runs the ACTUAL trap logic (the same detect/zone/VWAP code the live engine uses)
on REAL Nifty *futures* 3-minute bars — futures, because the spot index carries no
volume and the Blue/Black/Yellow candle model needs it. Options are Black-Scholes
priced off the futures with that day's India VIX, and every fill pays realistic
frictions:

    * slippage        0.5% of premium on each side (buy up / sell down)
    * brokerage       ₹20 per order (₹40 round trip)
    * STT             0.1% of premium turnover on the SELL leg
    * exchange txn    0.03503% of total premium turnover
    * GST             18% on (brokerage + txn)

Theta is captured by re-pricing the option every bar as time-to-expiry shrinks.

Honesty caveats (MODEL-1): BS with VIX-as-IV ignores intraday IV moves (vol
crush/spike); futures≈forward is a small basis approximation; one near-month
contract limits the sample. This is an optimistic-but-honest estimate, not a
promise. Writes strategies/TRAP_CMCD_v1/results/{summary.json,backtest_trades.csv}.

Run:
    python -m src.backtest.trap_backtest --from 2026-06-15 --to 2026-08-11
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, time as dtime

import numpy as np
import pandas as pd

from src.research.gti.validate import _authenticated_kite
from src.research.gti.kite_data import fetch_history
from src.research.gti.gti_zones import detect_zones, active_zones, ZoneConfig
from src.live.trap_cmcd_live import TrapCMCDLive, TrapParams, _classify, _vwap, _squeeze_on, _atr
from src.backtest.option_pricer import OptionPricer
from src.utils.market_calendar import (
    get_nifty_weekly_expiry_historical, get_sensex_weekly_expiry)

IST = timezone(timedelta(hours=5, minutes=30))
NIFTY_AUG_FUT = 14866434          # near-month; pass --token to override
INDIA_VIX_TOKEN = 264969
HARD_CLOSE = dtime(15, 10)
NO_NEW_AFTER = dtime(15, 0)


@dataclass
class Instrument:
    """What differs between Nifty (Tue expiry) and Sensex (Thu expiry)."""
    name: str
    spot_token: int
    lot_size: int
    strike_step: int
    expiry_fn: object          # day -> weekly expiry date

# NIFTY spot 256265; SENSEX spot 265. Sensex weekly = Thursday (verified from
# 2025-09-04; pre-that the Sensex expiry weekday is inconsistent across sources,
# so restrict Sensex backtests to the Thursday era). Lot/step: verify per expiry.
NIFTY = Instrument("NIFTY", 256265, 65, 50, get_nifty_weekly_expiry_historical)
SENSEX = Instrument("SENSEX", 265, 20, 100, get_sensex_weekly_expiry)


@dataclass
class Costs:
    slippage_pct: float = 0.005
    brokerage_per_order: float = 20.0
    stt_sell_pct: float = 0.001
    txn_pct: float = 0.0003503
    gst_pct: float = 0.18

    def total(self, entry_prem: float, exit_prem: float, qty: int) -> float:
        buy_to, sell_to = entry_prem * qty, exit_prem * qty
        brokerage = 2 * self.brokerage_per_order
        stt = self.stt_sell_pct * sell_to
        txn = self.txn_pct * (buy_to + sell_to)
        gst = self.gst_pct * (brokerage + txn)
        return brokerage + stt + txn + gst


def _t_hours(bar_ts: pd.Timestamp, expiry_date) -> float:
    exp_dt = pd.Timestamp(datetime.combine(expiry_date, dtime(15, 30)))
    ts = bar_ts.tz_localize(None) if bar_ts.tzinfo else bar_ts
    return max((exp_dt - ts).total_seconds() / 3600.0, 0.5)


def _simulate_day(dday: pd.DataFrame, day, vix: float, params: TrapParams,
                  pricer: OptionPricer, costs: Costs, capital: float,
                  max_trade_rs: float, engine, expiry_only: bool = False,
                  non_expiry_only: bool = False, inst: Instrument = NIFTY) -> list[dict]:
    trades: list[dict] = []
    expiry = inst.expiry_fn(day)
    is_expiry = (day == expiry)
    if expiry_only and not is_expiry:
        return trades
    if non_expiry_only and is_expiry:
        return trades
    n = len(dday)
    if n < 45:
        return trades

    in_trade = False
    tr: dict = {}
    zones_cache = None
    day_pnl = 0.0
    cooldown_until_i = -1

    for i in range(40, n):
        ts = dday.index[i]
        row = dday.iloc[i]
        spot = float(row["close"])
        tod = ts.time()

        # ── manage an open position ───────────────────────────────────────
        if in_trade:
            th = _t_hours(ts, expiry)
            prem = pricer.price(spot, tr["strike"], vix, th, tr["opt"]).price
            tr["peak"] = max(tr["peak"], prem)
            exit_reason = None
            if tod >= HARD_CLOSE:
                exit_reason = "TIME_EXIT"
            elif prem >= tr["target"]:
                tr["activated"] = True
                tr["stop"] = max(tr["stop"], tr["entry_prem"] + params.breakeven_lock_points)
            if tr["activated"]:
                tr["stop"] = max(tr["stop"], tr["peak"] - params.trail_giveback_points)
                at_zone = (spot >= tr["opp"]) if tr["opt"] == "CE" else (spot <= tr["opp"])
                if exit_reason is None and at_zone:
                    exit_reason = "ZONE_TO_ZONE"
            # no-progress time-stop: bail if +min_target isn't reached in N bars
            if exit_reason is None and params.no_progress_bars > 0 and not tr["activated"] \
                    and (i - tr["entry_i"]) >= params.no_progress_bars:
                exit_reason = "NO_PROGRESS"
            if exit_reason is None and prem <= tr["stop"]:
                exit_reason = "TRAIL_SL" if tr["activated"] else "HARD_SL"

            if exit_reason:
                exit_fill = prem * (1 - costs.slippage_pct)
                c = costs.total(tr["entry_fill"], exit_fill, tr["qty"])
                pnl = (exit_fill - tr["entry_fill"]) * tr["qty"] - c
                tr.update(exit_time=str(ts), exit_prem=round(prem, 2),
                          exit_reason=exit_reason, cost=round(c, 1), pnl=round(pnl, 1))
                trades.append(tr)
                day_pnl += pnl
                in_trade = False
                cooldown_until_i = i + max(1, params.cooldown_min // 3)
                if day_pnl <= -abs(params.daily_loss_lock_rs):
                    break
            continue

        # ── look for a new trap (flat) ────────────────────────────────────
        if tod >= NO_NEW_AFTER or i <= cooldown_until_i:
            continue
        if len([t for t in trades]) >= params.max_trades_per_day:
            break

        if i % 3 == 0 or zones_cache is None:
            win = dday.iloc[max(0, i - 199): i + 1]
            try:
                zones_cache = detect_zones(win, ZoneConfig(max_active_zones=0))
            except Exception:
                zones_cache = []
        near = active_zones(zones_cache, spot,
                            max_distance_pct=params.zone_max_distance_pct,
                            include_mitigated=False) if zones_cache else []
        wdf = dday.iloc[: i + 1]
        vwap = _vwap(wdf)
        squeeze = _squeeze_on(wdf)
        sig = engine._detect(wdf, near, vwap, squeeze)
        if not sig:
            continue

        direction, trap_level, opp_zone, reasons = sig
        opt = "CE" if direction == "BULLISH" else "PE"
        strike = int(round(spot / inst.strike_step) * inst.strike_step)
        th = _t_hours(ts, expiry)
        entry_prem = pricer.price(spot, strike, vix, th, opt).price
        if entry_prem <= 0.5:
            continue
        budget = min(capital * params.capital_per_trade_pct, max_trade_rs)
        lots = int(budget / (entry_prem * inst.lot_size))
        if lots < 1:
            continue
        qty = lots * inst.lot_size
        entry_fill = entry_prem * (1 + costs.slippage_pct)
        tr = dict(date=str(day), is_expiry=is_expiry, entry_i=i, entry_time=str(ts),
                  direction=direction, opt=opt,
                  strike=strike, entry_prem=round(entry_prem, 2),
                  entry_fill=round(entry_fill, 2), qty=qty, opp=opp_zone,
                  stop=max(0.05, entry_prem - params.hard_sl_points),
                  target=entry_prem + params.min_target_points,
                  peak=entry_prem, activated=False, reason=reasons[0])
        in_trade = True

    # force-close any runner at EOD
    if in_trade:
        ts = dday.index[-1]
        prem = pricer.price(float(dday.iloc[-1]["close"]), tr["strike"], vix,
                            _t_hours(ts, expiry), tr["opt"]).price
        exit_fill = prem * (1 - costs.slippage_pct)
        c = costs.total(tr["entry_fill"], exit_fill, tr["qty"])
        tr.update(exit_time=str(ts), exit_prem=round(prem, 2), exit_reason="EOD",
                  cost=round(c, 1), pnl=round((exit_fill - tr["entry_fill"]) * tr["qty"] - c, 1))
        trades.append(tr)
    return trades


def run_trap_backtest(frm: str, to: str, token: int = None,
                      capital: float = 50000.0, max_trade_rs: float = 10000.0,
                      costs: Costs = Costs(), params: TrapParams = None,
                      variant: str = "strict-spec", expiry_only: bool = False,
                      non_expiry_only: bool = False, inst: Instrument = NIFTY,
                      results_dir: str = "strategies/TRAP_CMCD_v1/results",
                      write: bool = True, verbose: bool = True) -> dict:
    token = token if token is not None else inst.spot_token
    kite = _authenticated_kite()
    df = fetch_history(kite, token, frm, to, interval="3minute", continuous=False,
                       cache_dir="cache/gti")
    if df is None or df.empty:
        raise SystemExit("no futures data returned — check token / dates / Kite subscription")
    try:
        vixd = fetch_history(kite, INDIA_VIX_TOKEN, frm, to, interval="day", cache_dir="cache/gti")
        vix_by_date = {d.date(): float(v) for d, v in vixd["close"].items()}
    except Exception:
        vix_by_date = {}

    params = params or TrapParams()
    pricer = OptionPricer()
    engine = TrapCMCDLive.__new__(TrapCMCDLive)
    engine.p = params

    all_trades: list[dict] = []
    daily_pnl: dict[str, float] = {}
    for day, dday in df.groupby(df.index.date):
        vix = vix_by_date.get(day, 12.0)
        t = _simulate_day(dday.sort_index(), day, vix, params, pricer, costs,
                          capital, max_trade_rs, engine, expiry_only=expiry_only,
                          non_expiry_only=non_expiry_only, inst=inst)
        all_trades.extend(t)
        if t:
            daily_pnl[str(day)] = round(sum(x["pnl"] for x in t), 1)
        if verbose and t:
            print(f"  {day}  trades={len(t):2d}  pnl=Rs.{sum(x['pnl'] for x in t):>9,.0f}  VIX={vix:.1f}")

    metrics = _metrics(all_trades, daily_pnl, df, capital, frm, to)
    metrics["variant"] = variant
    metrics["instrument"] = inst.name
    if metrics.get("by_instrument"):
        metrics["by_instrument"][0]["instrument"] = inst.name
    if write:
        _write_outputs(all_trades, metrics, results_dir)
    if verbose:
        _print_report(metrics)
    return metrics


def _metrics(trades, daily_pnl, df, capital, frm, to) -> dict:
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gp, gl = sum(wins), abs(sum(losses))
    daily = pd.Series(daily_pnl, dtype=float).sort_index()
    equity = daily.cumsum()
    dd = float((equity - equity.cummax()).min()) if len(equity) else 0.0
    sharpe = float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() else 0.0
    by_reason: dict[str, list] = {}
    for t in trades:
        by_reason.setdefault(t["exit_reason"], []).append(t["pnl"])
    worst = sorted(daily_pnl.items(), key=lambda kv: kv[1])[:8]

    def _split(subset):
        pl = [t["pnl"] for t in subset]
        w = [p for p in pl if p > 0]
        g_p, g_l = sum(w), abs(sum(p for p in pl if p <= 0))
        return {"trades": len(subset), "pnl": round(sum(pl), 1),
                "win_pct": round(100 * len(w) / len(subset), 1) if subset else 0.0,
                "profit_factor": round(g_p / g_l, 2) if g_l else 0.0,
                "avg_pnl": round(np.mean(pl), 1) if pl else 0.0}
    by_expiry = {"expiry_day": _split([t for t in trades if t.get("is_expiry")]),
                 "non_expiry": _split([t for t in trades if not t.get("is_expiry")])}
    return {
        "strategy_name": "TRAP_CMCD_v1", "run_kind": "backtest",
        "generated_at": datetime.now(IST).isoformat(),
        "data_basis": "REAL Nifty AUG futures 3m; BS-priced options w/ daily VIX; "
                      "slippage 0.5% + brokerage + STT + txn + GST",
        "caveat": "MODEL-1: BS/VIX IV, futures≈forward, single near-month contract "
                  "(small sample). Optimistic-but-honest; not a promise.",
        "period": {"start": frm, "end": to},
        "trading_days": int(df.index.normalize().nunique()),
        "initial_capital": capital,
        "total_trades": len(trades),
        "total_pnl": round(sum(pnls), 1),
        "max_trade_pnl": round(max(pnls), 1) if pnls else 0.0,
        "pnl_ex_top1": round(sum(pnls) - max(pnls), 1) if pnls else 0.0,   # jackknife
        "pnl_ex_top3": round(sum(pnls) - sum(sorted(pnls)[-3:]), 1) if len(pnls) >= 3 else 0.0,
        "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gp / gl, 2) if gl else 0.0,
        "avg_win": round(np.mean(wins), 1) if wins else 0.0,
        "avg_loss": round(np.mean(losses), 1) if losses else 0.0,
        "max_drawdown": round(dd, 1),
        "sharpe": round(sharpe, 2),
        "by_instrument": [{"instrument": "NIFTY", "trades": len(trades),
                           "win_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
                           "pnl": round(sum(pnls), 1)}],
        "by_exit_reason": [{"reason": r, "count": len(v), "pnl": round(sum(v), 1)}
                           for r, v in sorted(by_reason.items())],
        "by_expiry": by_expiry,
        "worst_days": [{"date": d, "pnl": p} for d, p in worst],
        "wfv": None,
    }


def _write_outputs(trades, metrics, d="strategies/TRAP_CMCD_v1/results"):
    import os
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    pd.DataFrame(trades).to_csv(os.path.join(d, "backtest_trades.csv"), index=False)
    print(f"\n  wrote {d}/summary.json + backtest_trades.csv")


def _print_report(m):
    print("\n" + "=" * 60)
    print(f"  BT-1 HIGH-FIDELITY TRAP BACKTEST — {m['period']['start']} → {m['period']['end']}")
    print("=" * 60)
    print(f"  Trades         : {m['total_trades']}")
    print(f"  Win rate       : {m['win_rate']}%")
    print(f"  Net P&L        : Rs. {m['total_pnl']:,.0f}   (after all costs)")
    print(f"  Profit factor  : {m['profit_factor']}")
    print(f"  Avg win/loss   : Rs.{m['avg_win']:,.0f} / Rs.{m['avg_loss']:,.0f}")
    print(f"  Max drawdown   : Rs. {m['max_drawdown']:,.0f}")
    print(f"  Sharpe         : {m['sharpe']}")
    print("  By exit reason :")
    for r in m["by_exit_reason"]:
        print(f"     {r['reason']:14} n={r['count']:3d}  pnl=Rs.{r['pnl']:>9,.0f}")
    print("=" * 60)
    print(f"  {m['caveat']}")


def main():
    ap = argparse.ArgumentParser(description="BT-1 high-fidelity trap backtest (real futures 3m)")
    ap.add_argument("--from", dest="frm", default="2026-06-15")
    ap.add_argument("--to", dest="to", default="2026-08-11")
    ap.add_argument("--token", type=int, default=NIFTY_AUG_FUT)
    ap.add_argument("--capital", type=float, default=50000.0)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    run_trap_backtest(args.frm, args.to, token=args.token, capital=args.capital,
                      write=not args.no_write)


if __name__ == "__main__":
    main()
