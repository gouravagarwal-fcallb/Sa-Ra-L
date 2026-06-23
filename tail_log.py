#!/usr/bin/env python3
"""
Sa-Ra-L  |  Live Log Viewer
────────────────────────────
Run this in a SEPARATE terminal window while the portfolio is trading.
Opens the log file with shared (non-exclusive) access so it never
conflicts with the running trading session.

Usage:
    python tail_log.py                         # tail trading.log (last 30 lines)
    python tail_log.py --lines 50              # show last 50 lines on start
    python tail_log.py --file logs/custom.log  # watch a different file
    python tail_log.py --no-color              # plain text output
"""

from __future__ import annotations

import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

# ── Optional color support (falls back gracefully if colorama not installed) ──

try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

    class Fore:
        RED = GREEN = YELLOW = CYAN = MAGENTA = WHITE = RESET = ""

    class Style:
        BRIGHT = DIM = RESET_ALL = ""


def _colorize(line: str, use_color: bool) -> str:
    if not use_color or not HAS_COLOR:
        return line.rstrip()

    ll = line.lower()

    # Errors / stops / losses
    if any(k in ll for k in ("error", "stop_loss", "daily stop", "range_broken", "invalidated")):
        return Fore.RED + Style.BRIGHT + line.rstrip() + Style.RESET_ALL

    # Targets / wins / validated
    if any(k in ll for k in ("target_hit", "validated", "✓")):
        return Fore.GREEN + Style.BRIGHT + line.rstrip() + Style.RESET_ALL

    # Entries
    if "entry" in ll and "no" not in ll:
        return Fore.CYAN + Style.BRIGHT + line.rstrip() + Style.RESET_ALL

    # Pre-market / startup events
    if any(k in ll for k in ("pre-mkt", "pre-market", "starting", "running backtest")):
        return Fore.WHITE + Style.BRIGHT + line.rstrip() + Style.RESET_ALL

    # Warnings / skips
    if any(k in ll for k in ("warning", "skip", "outside", "insufficient", "breakout")):
        return Fore.YELLOW + line.rstrip() + Style.RESET_ALL

    # Force-close / info
    if "force_close" in ll:
        return Fore.MAGENTA + line.rstrip() + Style.RESET_ALL

    # Normal INFO lines
    if "| info" in ll:
        return Style.DIM + line.rstrip() + Style.RESET_ALL

    return line.rstrip()


def tail(filepath: Path, n_lines: int, use_color: bool) -> None:
    print(f"\n  Sa-Ra-L  |  Live Log  —  {filepath}")
    print(f"  Started  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  Ctrl+C to stop\n" + "─" * 70)

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            # Print last n_lines on startup
            all_lines = f.readlines()
            if all_lines:
                for line in all_lines[-n_lines:]:
                    print(_colorize(line, use_color))
            else:
                print("  (log file is empty — waiting for first event…)")

            # Tail new content as it arrives
            while True:
                line = f.readline()
                if line:
                    print(_colorize(line, use_color), flush=True)
                else:
                    time.sleep(0.3)   # poll every 300 ms

    except FileNotFoundError:
        print(f"\n  Log file not found: {filepath}")
        print("  Is the portfolio runner active?  (python main.py --mode portfolio)")
        sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n  Stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sa-Ra-L live log viewer — run alongside the portfolio runner"
    )
    parser.add_argument(
        "--file", default="logs/trading.log",
        help="Path to log file (default: logs/trading.log)",
    )
    parser.add_argument(
        "--lines", type=int, default=30,
        help="Number of historical lines to show on startup (default: 30)",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable color output",
    )
    args = parser.parse_args()

    use_color = not args.no_color
    tail(Path(args.file), args.lines, use_color)


if __name__ == "__main__":
    main()
