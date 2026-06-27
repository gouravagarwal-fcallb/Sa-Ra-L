"""
PASHUPATASTRA — Shadow runner
=============================
Runs the live engine in SHADOW mode (no real orders): detects trapped sellers on
the live chain, manages would-be book-2× + re-entry trades, and journals P&L to
strategies/PASHUPATASTRA_v1/results/shadow_trades.csv. Also records the raw chain.

Usage (during market hours, after `python main.py --mode login`):
    python pashupatastra_shadow.py
    python pashupatastra_shadow.py --instruments NIFTY SENSEX --interval 90

LIVE trading is intentionally NOT enabled here. It requires (1) the recorder's edge
table confirming the trap signal, and (2) broker order wiring + the staged go/no-go
gates in V2_REENGINEERED.md §7. Shadow first. Always.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.brahmastra.live.pashupatastra_live import PashupatastraLive
from src.brahmastra.options.oi_recorder import OIRecorder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", nargs="+", default=["NIFTY"])
    ap.add_argument("--interval", type=int, default=90)
    ap.add_argument("--trap-threshold", type=float, default=None)
    ap.add_argument("--all-hours", action="store_true")
    ap.add_argument("--no-alerts", action="store_true", help="disable real-time trap pings")
    args = ap.parse_args()

    eng = PashupatastraLive(mode="shadow")
    if args.trap_threshold is not None:
        eng.cfg.trap_threshold = args.trap_threshold

    # real-time trap pings (shared notifier feeds both the alerter and the engine's entry/exit)
    alerter = None
    if not args.no_alerts:
        from src.brahmastra.options.trap_alert import build_default_alerter
        alerter = build_default_alerter(threshold=eng.cfg.trap_threshold)
        eng.notifier = alerter.notifier            # engine entry/exit pings via the same channel

    recorder = OIRecorder(instruments=args.instruments, alerter=alerter)
    eng.recorder = recorder
    eng.run_live(instruments=args.instruments, interval_sec=args.interval,
                 market_hours_only=not args.all_hours)


if __name__ == "__main__":
    main()
