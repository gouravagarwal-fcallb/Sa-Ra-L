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


def _entry_datetime(trades: pd.DataFrame) -> pd.Series:
    """Reconstruct each trade's FULL entry timestamp.

    The platform export stores the trading DATE ('date') and the time-of-day
    ('entry_time' = 'HH:MM:SS') in SEPARATE columns — parsing entry_time alone
    collapses every trade onto today, which silently breaks the zone alignment
    (and made the first A/B run veto nothing). Combine date + time when the time
    column carries no date of its own."""
    tcol = _find_col(trades, _TIME_COLS)
    dcol = _find_col(trades, ["date", "trade_date", "day", "session_date"])
    tvals = trades[tcol].astype(str).str.strip()

    def _has_date(s: str) -> bool:
        return ("-" in s or "/" in s) and len(s) >= 8

    sample = next((s for s in tvals if s and s.lower() != "nan"), "")
    if dcol and dcol != tcol and not _has_date(sample):
        combined = trades[dcol].astype(str).str.strip() + " " + tvals
        return pd.to_datetime(combined, errors="coerce")
    return pd.to_datetime(tvals, errors="coerce")


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
# Point-in-time zone check (opposing = veto, or supporting = aligned)
# --------------------------------------------------------------------------- #
def _zone_hit(spot: pd.DataFrame, pos: int, direction: int,
              cfg: ConfluenceConfig, supporting: bool) -> dict | None:
    """Detect zones from ONLY spot[:pos+1] (trailing window) — no look-ahead —
    and return a matching fresh zone or None.

    supporting=False (VETO): the trade fires INTO an opposing zone —
        long into overhead supply / short into support below (a wall).
    supporting=True (ALIGNED): the trade fires WITH a supporting zone —
        long leaning on fresh demand below / short under fresh supply above.
    """
    lo = max(0, pos - cfg.zone_lookback_bars + 1)
    window = spot.iloc[lo:pos + 1]
    if len(window) < 40:
        return None
    p = float(window["close"].iloc[-1])
    zones = detect_zones(window, ZoneConfig(max_active_zones=0))
    # Search at least as wide as the band, else a raised band would be silently
    # capped by the default proximity filter.
    radius = max(cfg.max_distance_pct, cfg.veto_pct + 0.1)
    near = active_zones(zones, p, max_distance_pct=radius, include_mitigated=False)
    for z in near:
        if cfg.require_fresh and z.tests != 0:
            continue
        if not supporting:
            # opposing wall in the trade's path
            if direction > 0 and z.side == "supply":       # long into overhead supply
                gap = (z.proximal - p) / p * 100
                if 0 < gap < cfg.veto_pct:
                    return {"side": z.side, "proximal": z.proximal, "gap_pct": round(gap, 3)}
            if direction < 0 and z.side == "demand":        # short into support below
                gap = (p - z.proximal) / p * 100
                if 0 < gap < cfg.veto_pct:
                    return {"side": z.side, "proximal": z.proximal, "gap_pct": round(gap, 3)}
        else:
            # supporting zone the trade leans on (price sitting AT it)
            if direction > 0 and z.side == "demand":        # long off fresh demand support
                gap = abs(z.proximal - p) / p * 100
                if gap < cfg.veto_pct:
                    return {"side": z.side, "proximal": z.proximal, "gap_pct": round(gap, 3)}
            if direction < 0 and z.side == "supply":        # short off fresh supply resistance
                gap = abs(z.proximal - p) / p * 100
                if gap < cfg.veto_pct:
                    return {"side": z.side, "proximal": z.proximal, "gap_pct": round(gap, 3)}
    return None


# --------------------------------------------------------------------------- #
# Shared prep + tagging
# --------------------------------------------------------------------------- #
def _prepare(trades: pd.DataFrame, spot: pd.DataFrame, pcol: str):
    """Clean trades (real entry datetime + numeric pnl) and normalise spot to a
    sorted tz-naive DatetimeIndex. Returns (trades, spot, spot_times)."""
    trades = trades.copy()
    trades["_t"] = _entry_datetime(trades)
    if getattr(trades["_t"].dt, "tz", None) is not None:
        trades["_t"] = trades["_t"].dt.tz_localize(None)
    trades["_pnl"] = pd.to_numeric(trades[pcol], errors="coerce")
    trades = trades.dropna(subset=["_t", "_pnl"]).sort_values("_t").reset_index(drop=True)

    if not isinstance(spot.index, pd.DatetimeIndex):
        spot = spot.set_index(pd.to_datetime(spot.index))
    if getattr(spot.index, "tz", None) is not None:
        spot.index = spot.index.tz_localize(None)
    spot = spot.sort_index()
    return trades, spot, spot.index


def _tag(trades, spot, spot_times, cfg, supporting):
    """Return (hit_mask, undetermined): hit_mask[i] True if trade i hits a zone
    of the requested kind (opposing if supporting=False, supporting if True)."""
    hit_mask, undetermined = [], 0
    for _, row in trades.iterrows():
        d = _trade_direction(row, trades.columns)
        if d is None:
            undetermined += 1; hit_mask.append(False); continue
        pos = spot_times.searchsorted(row["_t"], side="right") - 1
        if pos < 0:
            hit_mask.append(False); continue
        hit_mask.append(_zone_hit(spot, pos, d, cfg, supporting) is not None)
    return hit_mask, undetermined


# --------------------------------------------------------------------------- #
# Core A/B  (VETO: remove trades into an opposing fresh zone)
# --------------------------------------------------------------------------- #
def run_confluence_ab(trades: pd.DataFrame, spot: pd.DataFrame,
                      cfg: ConfluenceConfig = ConfluenceConfig()) -> dict:
    """Tag each trade kept/vetoed by the opposing-fresh-zone rule; return A/B stats."""
    tcol = _find_col(trades, _TIME_COLS)
    pcol = _find_col(trades, _PNL_COLS)
    if tcol is None or pcol is None:
        return {"error": f"need a time column ({_TIME_COLS}) and a pnl/R column "
                         f"({_PNL_COLS}); found {list(trades.columns)}"}

    trades, spot, spot_times = _prepare(trades, spot, pcol)
    hit, undetermined = _tag(trades, spot, spot_times, cfg, supporting=False)
    trades["_kept"] = [not h for h in hit]

    A = trades["_pnl"].to_numpy()
    B = trades.loc[trades["_kept"], "_pnl"].to_numpy()
    V = trades.loc[~trades["_kept"], "_pnl"].to_numpy()
    return {
        "n_total": len(trades), "undetermined_direction": undetermined,
        "A_all": _stats(A), "B_kept": _stats(B), "vetoed": _stats(V),
        "n_vetoed": int((~trades["_kept"]).sum()),
        "pnl_col": pcol, "time_col": tcol, "trades": trades,
    }


# --------------------------------------------------------------------------- #
# Aligned selector  (do trades WITH a supporting fresh zone outperform?)
# --------------------------------------------------------------------------- #
def run_confluence_select(trades: pd.DataFrame, spot: pd.DataFrame,
                          cfg: ConfluenceConfig = ConfluenceConfig()) -> dict:
    """Partition trades into those firing AT a supporting fresh zone vs the rest,
    and compare. If ALIGNED >> REST, the zones are a positive trade-quality
    selector (candidate for concentration / up-sizing)."""
    tcol = _find_col(trades, _TIME_COLS)
    pcol = _find_col(trades, _PNL_COLS)
    if tcol is None or pcol is None:
        return {"error": f"need a time column ({_TIME_COLS}) and a pnl/R column "
                         f"({_PNL_COLS}); found {list(trades.columns)}"}

    trades, spot, spot_times = _prepare(trades, spot, pcol)
    hit, undetermined = _tag(trades, spot, spot_times, cfg, supporting=True)
    trades["_aligned"] = hit

    A = trades["_pnl"].to_numpy()
    ALG = trades.loc[trades["_aligned"], "_pnl"].to_numpy()
    REST = trades.loc[~trades["_aligned"], "_pnl"].to_numpy()
    return {
        "n_total": len(trades), "undetermined_direction": undetermined,
        "A_all": _stats(A), "aligned": _stats(ALG), "rest": _stats(REST),
        "n_aligned": int(trades["_aligned"].sum()),
        "pnl_col": pcol, "time_col": tcol, "trades": trades,
    }


# --------------------------------------------------------------------------- #
# Band sweep  (is the veto effect robust across bands, or a lucky pick?)
# --------------------------------------------------------------------------- #
def run_sweep(trades: pd.DataFrame, spot: pd.DataFrame, bands: list,
              base_cfg: ConfluenceConfig = ConfluenceConfig()) -> dict:
    """Run the veto A/B across several bands; return a per-band trend."""
    tcol = _find_col(trades, _TIME_COLS)
    pcol = _find_col(trades, _PNL_COLS)
    if tcol is None or pcol is None:
        return {"error": f"need a time column ({_TIME_COLS}) and a pnl/R column "
                         f"({_PNL_COLS}); found {list(trades.columns)}"}
    trades0, spot, spot_times = _prepare(trades, spot, pcol)
    A = _stats(trades0["_pnl"].to_numpy())
    rows = []
    for band in bands:
        cfg = ConfluenceConfig(veto_pct=band, zone_lookback_bars=base_cfg.zone_lookback_bars,
                               require_fresh=base_cfg.require_fresh,
                               max_distance_pct=base_cfg.max_distance_pct)
        hit, _ = _tag(trades0, spot, spot_times, cfg, supporting=False)
        kept = [not h for h in hit]
        B = _stats(trades0.loc[kept, "_pnl"].to_numpy())
        V = _stats(trades0.loc[[not k for k in kept], "_pnl"].to_numpy())
        rows.append({"band": band, "n_vetoed": int(sum(hit)),
                     "vetoed_total": V["total"], "vetoed_win": V["win_rate"],
                     "B_total": B["total"], "B_pf": B["pf"],
                     "delta_vs_A": round(B["total"] - A["total"], 2)})
    return {"A_all": A, "bands": rows, "pnl_col": pcol}


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


def print_select(res: dict) -> None:
    if "error" in res:
        print(f"  ERROR: {res['error']}"); return
    A, ALG, REST = res["A_all"], res["aligned"], res["rest"]
    print("=" * 68)
    print("  GTI ALIGNED SELECTOR — do trades AT a supporting FRESH 15m zone win more?")
    print("=" * 68)
    print(f"  trades={res['n_total']}  aligned={res['n_aligned']}  "
          f"(pnl col='{res['pnl_col']}', time col='{res['time_col']}')")
    if res["undetermined_direction"]:
        print(f"  ! {res['undetermined_direction']} trades had no readable direction.")
    print(f"\n  {'':10}{'n':>6}{'total':>13}{'win%':>8}{'avg':>11}{'PF':>8}")
    for lab, s in [("all", A), ("aligned", ALG), ("rest", REST)]:
        wr = "  -  " if s["win_rate"] is None else f"{s['win_rate']:>7.1f}"
        av = "   -   " if s["avg"] is None else f"{s['avg']:>10.2f}"
        pf = "  -  " if s["pf"] is None else f"{s['pf']:>7.2f}"
        print(f"  {lab:10}{s['n']:>6}{s['total']:>13.2f}{wr}{av}{pf}")
    print("\n  READ: if ALIGNED beats REST on win% / avg / PF, the zones are a")
    print("  positive trade-quality selector — a case to concentrate or up-size on")
    print("  trades that fire at a fresh supporting zone.")
    print("=" * 68)


def print_sweep(res: dict) -> None:
    if "error" in res:
        print(f"  ERROR: {res['error']}"); return
    A = res["A_all"]
    print("=" * 74)
    print("  GTI VETO BAND SWEEP — is the effect robust, or a lucky single band?")
    print("=" * 74)
    print(f"  baseline A: n={A['n']}  total={A['total']:.2f}  win%={A['win_rate']}  PF={A['pf']}")
    print(f"\n  {'band%':>7}{'vetoed':>8}{'veto_total':>13}{'veto_win%':>11}"
          f"{'B_total':>13}{'B_PF':>7}{'ΔvsA':>12}")
    for r in res["bands"]:
        vw = "  -  " if r["vetoed_win"] is None else f"{r['vetoed_win']:>10.1f}"
        print(f"  {r['band']:>7.2f}{r['n_vetoed']:>8}{r['vetoed_total']:>13.2f}{vw}"
              f"{r['B_total']:>13.2f}{r['B_pf']:>7.2f}{r['delta_vs_A']:>12.2f}")
    print("\n  READ: a REAL filter shows a consistent trend — vetoed trades stay net")
    print("  LOSERS (low veto_win%) and ΔvsA stays POSITIVE across bands. If only one")
    print("  band helps and neighbours don't, it's noise / overfit.")
    print("=" * 74)


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
    ap.add_argument("--mode", default="veto", choices=["veto", "aligned"],
                    help="'veto' = remove trades into an opposing zone; "
                         "'aligned' = do trades AT a supporting zone win more?")
    ap.add_argument("--veto-pct", type=float, default=0.40, help="zone-proximity band, in percent")
    ap.add_argument("--sweep", default=None,
                    help="comma bands to sweep the veto across, e.g. '0.4,0.6,0.8,1.0'")
    ap.add_argument("--lookback", type=int, default=200, help="trailing bars for point-in-time zones")
    ap.add_argument("--allow-tested", action="store_true", help="also count tested (non-fresh) zones")
    ap.add_argument("--from", dest="frm", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="to", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--cache-dir", default="cache/gti")
    ap.add_argument("--out", default=None, help="optional: write tagged trades CSV here")
    args = ap.parse_args()

    trades = pd.read_csv(args.trades)
    tcol = _find_col(trades, _TIME_COLS)
    if tcol is None:
        print(f"  Could not find a time column in {list(trades.columns)}"); return 1
    t = _entry_datetime(trades).dropna()
    frm = args.frm or (t.min() - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    to = args.to or (t.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")

    mode_lbl = "SWEEP" if args.sweep else args.mode.upper()
    print(f"\n  Confluence {mode_lbl} · {args.instrument} · spot {args.interval} · zones over {frm} → {to}")
    print(f"  strategy trades: {args.trades}  (n={len(trades)})")
    print(f"  band={args.veto_pct}  lookback={args.lookback}  fresh_only={not args.allow_tested}\n")

    spot = _load_spot(args.instrument, frm, to, args.interval, args.cache_dir)
    if spot is None or spot.empty:
        print("  No spot data — check Kite token / dates."); return 1

    base = ConfluenceConfig(veto_pct=args.veto_pct, zone_lookback_bars=args.lookback,
                            require_fresh=not args.allow_tested)

    if args.sweep:
        bands = [float(b) for b in args.sweep.split(",") if b.strip()]
        print_sweep(run_sweep(trades, spot, bands, base))
        return 0

    if args.mode == "aligned":
        res = run_confluence_select(trades, spot, base)
        print_select(res)
        flag = "_aligned"
    else:
        res = run_confluence_ab(trades, spot, base)
        print_ab(res)
        flag = "_kept"

    if args.out and "trades" in res:
        res["trades"].drop(columns=[c for c in ("_t",) if c in res["trades"]]).to_csv(args.out, index=False)
        print(f"\n  Tagged trades (with {flag} flag) → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
