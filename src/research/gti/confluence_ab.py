"""
GTI confluence A/B test  (RESEARCH STAGE — no live orders, no funds)
────────────────────────────────────────────────────────────────────
Question this answers:
    "Do the 15-minute demand/supply zones actually IMPROVE a strategy I already
     trust — i.e. if I veto trades that fire straight INTO an opposing fresh
     zone, does the strategy get better?"

The zones' own standalone edge is thin (research verdict), but their most
promising use is as a *confluence filter* on strategies that already have edge.
This harness measures that directly and honestly:

    A) the strategy's trades exactly as they were (baseline), vs
    B) the same trades with "trading into an opposing fresh zone" removed.

For every trade, the opposing-zone check is **point-in-time**: zones are detected
using ONLY spot bars up to that trade's entry (a trailing window), and freshness
is counted only up to then. No look-ahead — that is the whole discipline that
made the earlier GTI numbers trustworthy, applied here too.

Veto rule (spot p at the trade's entry time):
    * bullish trade (CE-buy / long): veto if a FRESH supply zone sits just
      OVERHEAD  (0 < (supply.proximal - p)/p < veto_pct)  → capped upside.
    * bearish trade (PE-buy / short): veto if a FRESH demand zone sits just
      UNDER    (0 < (p - demand.proximal)/p < veto_pct)   → capped downside.

Output is an A-vs-B table plus the stats of the VETOED subset — the filter is
only worth it if the trades it removed were, on balance, losers.

Run (repo root, machine with a Kite token):
    python -m src.research.gti.confluence_ab --trades results/nifty_intraday/backtest_trades.csv
    python -m src.research.gti.confluence_ab --trades <csv> --instrument SENSEX --veto-pct 0.5
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.research.gti.gti_zones import detect_zones, ZoneConfig, active_zones


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class ConfluenceConfig:
    veto_pct: float = 0.40        # opposing fresh zone within this % of spot = veto
    zone_lookback_bars: int = 200  # trailing 15m window for point-in-time zones
    require_fresh: bool = True     # only fresh (untested) opposing zones veto
    max_distance_pct: float = 1.0  # zones beyond this from price are irrelevant


# --------------------------------------------------------------------------- #
# Flexible column resolution — strategy CSVs differ
# --------------------------------------------------------------------------- #
_TIME_COLS = ["entry_time", "entry_dt", "entry", "time", "timestamp", "datetime", "date"]
# Net rupee P&L preferred (pnl_rupees is after costs in this platform's export),
# then gross, then percentage, then the generic/R fallbacks.
_PNL_COLS = ["pnl_rupees", "net_pnl", "pnl_net", "pnl_rs", "gross_pnl", "pnl",
             "pnl_pct", "points", "r_multiple", "R", "return", "profit"]


def _find_col(df: pd.DataFrame, candidates) -> str | None:
    low = {c.lower(): c for c in df.columns}
    for name in candidates:
        if name.lower() in low:
            return low[name.lower()]
    return None


def _trade_direction(row, df_cols) -> int | None:
    """+1 bullish / -1 bearish / None if undeterminable.
    Reads side (long/short/buy/sell) or option type (CE/PE) — CE-buy is bullish,
    PE-buy bearish. If an explicit sell action is present, the option sign flips."""
    cols = {c.lower(): c for c in df_cols}

    def val(*names):
        for n in names:
            if n in cols:
                v = row[cols[n]]
                if pd.notna(v):
                    return str(v).strip().lower()
        return None

    side = val("side", "direction", "dir", "position", "bias")
    if side is not None:
        if side in ("long", "buy", "bull", "bullish", "+1", "1", "up") or "bull" in side:
            return 1
        if side in ("short", "sell", "bear", "bearish", "-1", "down") or "bear" in side:
            return -1
        # NEUTRAL / anything else -> fall through to the CE/PE backup below

    opt = val("option_type", "opt_type", "type", "instrument_type", "ce_pe")
    action = val("action", "txn", "transaction", "side")
    is_sell = action in ("sell", "short", "sl")
    if opt in ("ce", "call"):
        return -1 if is_sell else 1
    if opt in ("pe", "put"):
        return 1 if is_sell else -1
    return None


# --------------------------------------------------------------------------- #
# Point-in-time opposing-zone check
# --------------------------------------------------------------------------- #
def _opposing_fresh_zone(spot: pd.DataFrame, pos: int, direction: int,
                         cfg: ConfluenceConfig) -> dict | None:
    """Detect zones from ONLY spot[:pos+1] (trailing window) and return the
    opposing fresh zone blocking this trade, or None. No look-ahead."""
    lo = max(0, pos - cfg.zone_lookback_bars + 1)
    window = spot.iloc[lo:pos + 1]
    if len(window) < 40:
        return None
    p = float(window["close"].iloc[-1])
    zones = detect_zones(window, ZoneConfig(max_active_zones=0))
    near = active_zones(zones, p, max_distance_pct=cfg.max_distance_pct,
                        include_mitigated=False)
    for z in near:
        if cfg.require_fresh and z.tests != 0:
            continue
        if direction > 0 and z.side == "supply":          # bullish into overhead supply
            gap = (z.proximal - p) / p * 100
            if 0 < gap < cfg.veto_pct:
                return {"side": z.side, "proximal": z.proximal, "gap_pct": round(gap, 3)}
        if direction < 0 and z.side == "demand":           # bearish into support below
            gap = (p - z.proximal) / p * 100
            if 0 < gap < cfg.veto_pct:
                return {"side": z.side, "proximal": z.proximal, "gap_pct": round(gap, 3)}
    return None


# --------------------------------------------------------------------------- #
# Core A/B
# --------------------------------------------------------------------------- #
def run_confluence_ab(trades: pd.DataFrame, spot: pd.DataFrame,
                      cfg: ConfluenceConfig = ConfluenceConfig()) -> dict:
    """Tag each trade kept/vetoed by the opposing-fresh-zone rule; return A/B stats."""
    tcol = _find_col(trades, _TIME_COLS)
    pcol = _find_col(trades, _PNL_COLS)
    if tcol is None or pcol is None:
        return {"error": f"need a time column ({_TIME_COLS}) and a pnl/R column "
                         f"({_PNL_COLS}); found {list(trades.columns)}"}

    trades = trades.copy()
    trades["_t"] = pd.to_datetime(trades[tcol], errors="coerce")
    if getattr(trades["_t"].dt, "tz", None) is not None:
        trades["_t"] = trades["_t"].dt.tz_localize(None)
    trades["_pnl"] = pd.to_numeric(trades[pcol], errors="coerce")
    trades = trades.dropna(subset=["_t", "_pnl"]).sort_values("_t").reset_index(drop=True)

    if not isinstance(spot.index, pd.DatetimeIndex):
        spot = spot.set_index(pd.to_datetime(spot.index))
    if getattr(spot.index, "tz", None) is not None:
        spot.index = spot.index.tz_localize(None)
    spot = spot.sort_index()
    spot_times = spot.index

    kept_mask, veto_info, undetermined = [], [], 0
    for _, row in trades.iterrows():
        d = _trade_direction(row, trades.columns)
        if d is None:
            undetermined += 1
            kept_mask.append(True)          # can't judge → keep (baseline-safe)
            veto_info.append(None)
            continue
        pos = spot_times.searchsorted(row["_t"], side="right") - 1
        if pos < 0:
            kept_mask.append(True); veto_info.append(None); continue
        opp = _opposing_fresh_zone(spot, pos, d, cfg)
        kept_mask.append(opp is None)
        veto_info.append(opp)

    trades["_kept"] = kept_mask
    A = trades["_pnl"].to_numpy()
    B = trades.loc[trades["_kept"], "_pnl"].to_numpy()
    V = trades.loc[~trades["_kept"], "_pnl"].to_numpy()

    return {
        "n_total": len(trades),
        "undetermined_direction": undetermined,
        "A_all": _stats(A),
        "B_kept": _stats(B),
        "vetoed": _stats(V),
        "n_vetoed": int((~trades["_kept"]).sum()),
        "pnl_col": pcol, "time_col": tcol,
        "trades": trades,   # for optional CSV dump
    }


def _stats(x: np.ndarray) -> dict:
    if x is None or len(x) == 0:
        return {"n": 0, "total": 0.0, "win_rate": None, "avg": None, "pf": None}
    wins = x[x > 0]; losses = x[x <= 0]
    gl = -losses.sum()
    return {
        "n": len(x),
        "total": round(float(x.sum()), 2),
        "win_rate": round(len(wins) / len(x) * 100, 1),
        "avg": round(float(x.mean()), 4),
        "pf": round(float(wins.sum() / gl), 2) if gl > 0 else float("inf"),
    }


def print_ab(res: dict) -> None:
    if "error" in res:
        print(f"  ERROR: {res['error']}"); return
    A, B, V = res["A_all"], res["B_kept"], res["vetoed"]
    print("=" * 68)
    print("  GTI CONFLUENCE A/B — veto trades into an opposing FRESH 15m zone")
    print("=" * 68)
    print(f"  trades={res['n_total']}  (pnl col='{res['pnl_col']}', time col='{res['time_col']}')")
    if res["undetermined_direction"]:
        print(f"  ! {res['undetermined_direction']} trades had no readable direction "
              f"— kept in both (couldn't judge).")
    print(f"\n  {'':10}{'n':>6}{'total':>12}{'win%':>8}{'avg':>10}{'PF':>8}")
    for lab, s in [("A all", A), ("B kept", B), ("vetoed", V)]:
        wr = "  -  " if s["win_rate"] is None else f"{s['win_rate']:>7.1f}"
        av = "   -   " if s["avg"] is None else f"{s['avg']:>9.4f}"
        pf = "  -  " if s["pf"] is None else f"{s['pf']:>7.2f}"
        print(f"  {lab:10}{s['n']:>6}{s['total']:>12.2f}{wr}{av}{pf}")
    print("\n  READ:")
    print("   • If B (kept) total & PF BEAT A (all), and the vetoed subset is a")
    print("     net LOSER, the zones add value as a confluence filter — keep going.")
    print("   • If B ≈ A, or the vetoed subset was net POSITIVE, the filter is")
    print("     throwing away good trades — the zones don't help this strategy.")
    print("=" * 68)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
_SPOT_TOKENS = {"NIFTY": 256265, "SENSEX": 265}


def _load_spot(instrument: str, frm: str, to: str, interval: str, cache_dir: str) -> pd.DataFrame:
    from main import load_configs
    from src.broker.kite_broker import create_kite_broker
    from src.research.gti.kite_data import fetch_history
    settings, _ = load_configs(None)
    kite = create_kite_broker(settings)._kite
    token = _SPOT_TOKENS[instrument]
    return fetch_history(kite, token, frm, to, interval=interval, cache_dir=cache_dir)


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B test GTI zones as a confluence filter on a strategy's trades.")
    ap.add_argument("--trades", required=True, help="strategy backtest trade CSV (needs entry time + pnl/R)")
    ap.add_argument("--instrument", default="NIFTY", choices=["NIFTY", "SENSEX"])
    ap.add_argument("--interval", default="15minute", help="spot timeframe for zones (validated: 15minute)")
    ap.add_argument("--veto-pct", type=float, default=0.40)
    ap.add_argument("--lookback", type=int, default=200, help="trailing bars for point-in-time zones")
    ap.add_argument("--allow-tested", action="store_true", help="also veto on tested (non-fresh) zones")
    ap.add_argument("--from", dest="frm", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="to", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--cache-dir", default="cache/gti")
    ap.add_argument("--out", default=None, help="optional: write tagged trades CSV here")
    args = ap.parse_args()

    trades = pd.read_csv(args.trades)
    tcol = _find_col(trades, _TIME_COLS)
    if tcol is None:
        print(f"  Could not find a time column in {list(trades.columns)}"); return 1
    t = pd.to_datetime(trades[tcol], errors="coerce").dropna()
    frm = args.frm or (t.min() - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    to = args.to or (t.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")

    print(f"\n  Confluence A/B · {args.instrument} · spot {args.interval} · zones over {frm} → {to}")
    print(f"  strategy trades: {args.trades}  (n={len(trades)})")
    print(f"  veto_pct={args.veto_pct}  lookback={args.lookback}  fresh_only={not args.allow_tested}\n")

    spot = _load_spot(args.instrument, frm, to, args.interval, args.cache_dir)
    if spot is None or spot.empty:
        print("  No spot data — check Kite token / dates."); return 1

    cfg = ConfluenceConfig(veto_pct=args.veto_pct, zone_lookback_bars=args.lookback,
                           require_fresh=not args.allow_tested)
    res = run_confluence_ab(trades, spot, cfg)
    print_ab(res)

    if args.out and "trades" in res:
        res["trades"].drop(columns=[c for c in ("_t",) if c in res["trades"]]).to_csv(args.out, index=False)
        print(f"\n  Tagged trades (with _kept flag) → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
