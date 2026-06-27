"""
PASHUPATASTRA — OI Recorder runner
==================================
Logs the live NSE option chain + Seller-Trap Score to disk during market hours,
so the strategy's edge can be MEASURED (not assumed). See
strategies/PASHUPATASTRA_v1/RECORDER.md and V2_REENGINEERED.md §2.

Usage (after `python main.py --mode login` for a fresh session):
    python record_oi.py                                  # NIFTY, every 120s, market hours
    python record_oi.py --instruments NIFTY SENSEX --interval 60
    python record_oi.py --once                           # single snapshot (smoke test)

Then analyse accrued recordings:
    python -m src.brahmastra.options.oi_analyze data/oi_recordings/NIFTY
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.brahmastra.options.oi_recorder import OIRecorder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", nargs="+", default=["NIFTY"])
    ap.add_argument("--interval", type=int, default=120, help="seconds between fetches")
    ap.add_argument("--out", default="data/oi_recordings")
    ap.add_argument("--trap-threshold", type=float, default=75.0)
    ap.add_argument("--once", action="store_true", help="record one snapshot and exit")
    ap.add_argument("--all-hours", action="store_true", help="ignore market-hours guard")
    args = ap.parse_args()

    rec = OIRecorder(
        instruments=args.instruments,
        out_dir=args.out,
        interval_sec=args.interval,
        trap_threshold=args.trap_threshold,
    )
    if args.once:
        out = rec.record_once()
        print(f"recorded {len(out)} snapshot(s) -> {args.out}")
    else:
        rec.run(market_hours_only=not args.all_hours)


if __name__ == "__main__":
    main()
