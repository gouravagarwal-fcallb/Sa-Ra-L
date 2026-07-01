"""
GTI zone-quality validation runner  (RESEARCH STAGE — no live orders)
─────────────────────────────────────────────────────────────────────
Fetches real NIFTY/SENSEX 5-minute history through the platform's own
authenticated Kite connection, runs the deliberately-dumb zone backtest, and
prints the strength-bucket report.

This answers the ONE question that must be settled before any live wiring:
    "Do high-strength, FRESH demand/supply zones actually produce edge on real
     NIFTY/SENSEX 5-min — i.e. does expectancy RISE with the zone strength score?"

If yes → the detector has edge and Phase 2 (native strategy + kill-switch) is
justified. If the buckets are flat/noisy → we fix the detector (or drop it)
before writing another line. Nothing here touches funds.

Run (from repo root, on a machine with a live Kite token — `python main.py --mode login`):
    python -m src.research.gti.validate --from 2026-01-01 --to 2026-06-30
    python -m src.research.gti.validate --instrument SENSEX
    python -m src.research.gti.validate --min-strength 60         # only strong zones
    python -m src.research.gti.validate --token <FUT_TOKEN> --futures   # real-volume path
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from src.research.gti.kite_data import fetch_history
from src.research.gti.gti_zones import ZoneConfig
from src.research.gti.gti_backtest import run_backtest, print_report, BacktestConfig, trades_to_df

# Kite SPOT instrument tokens. For the real-volume (futures) path, pass the
# current-month FUT token explicitly via --token with --futures.
_SPOT_TOKENS = {"NIFTY": 256265, "SENSEX": 265}


def _authenticated_kite():
    """Reuse the platform's Kite auth (same env / cached-token / settings ladder
    as the live app), so the operator doesn't re-enter API keys."""
    from main import load_configs
    from src.broker.kite_broker import create_kite_broker
    settings, _ = load_configs(None)
    return create_kite_broker(settings)._kite


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate GTI zone quality on real Kite history.")
    ap.add_argument("--instrument", default="NIFTY", choices=["NIFTY", "SENSEX"])
    ap.add_argument("--from", dest="frm", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="to", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--interval", default="5minute")
    ap.add_argument("--min-strength", type=float, default=0.0,
                    help="only trade zones at/above this strength (0-100)")
    ap.add_argument("--rr", type=float, default=2.0, help="reward:risk target multiple")
    ap.add_argument("--entry-mode", default="limit", choices=["limit", "confirm"],
                    help="'limit' = dumb touch-at-proximal; 'confirm' = SL-hunt sweep "
                         "+ engulfing reclaim (smart entry)")
    ap.add_argument("--no-sl-hunt", action="store_true",
                    help="confirm mode: don't require a stop-hunt sweep of the zone")
    ap.add_argument("--no-engulfing", action="store_true",
                    help="confirm mode: don't require an engulfing reversal candle")
    ap.add_argument("--futures", action="store_true",
                    help="stitch continuous FUT (real volume); pass --token for the FUT token")
    ap.add_argument("--token", type=int, default=None, help="explicit instrument token")
    ap.add_argument("--cache-dir", default="cache/gti")
    args = ap.parse_args()

    to = args.to or datetime.now().strftime("%Y-%m-%d")
    frm = args.frm or (datetime.strptime(to, "%Y-%m-%d") - timedelta(days=180)).strftime("%Y-%m-%d")
    token = args.token or _SPOT_TOKENS[args.instrument]

    print(f"\n  GTI zone validation · {args.instrument} · {args.interval} · {frm} → {to}")
    print(f"  token={token}  futures={args.futures}  min_strength={args.min_strength}\n")

    kite = _authenticated_kite()
    df = fetch_history(kite, token, frm, to, interval=args.interval,
                       continuous=args.futures, cache_dir=args.cache_dir)
    if df is None or df.empty:
        print("  No data returned — check the Kite token / historical-data subscription / dates.")
        return 1
    print(f"  Fetched {len(df)} bars ({df.index[0]} → {df.index[-1]})\n")

    result = run_backtest(df, ZoneConfig(),
                          BacktestConfig(min_strength=args.min_strength, reward_risk=args.rr,
                                         entry_mode=args.entry_mode,
                                         require_sl_hunt=not args.no_sl_hunt,
                                         require_engulfing=not args.no_engulfing))
    print(f"  entry_mode={args.entry_mode}  sl_hunt={not args.no_sl_hunt}  "
          f"engulfing={not args.no_engulfing}\n")
    print_report(result)

    # Save the trade list next to the module for later trade-by-trade analysis.
    import os
    out = trades_to_df(result)
    if not out.empty:
        dest = os.path.join(os.path.dirname(__file__),
                            f"gti_trades_{args.instrument}_{frm}_{to}.csv")
        out.to_csv(dest, index=False)
        print(f"\n  Trade list saved → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
