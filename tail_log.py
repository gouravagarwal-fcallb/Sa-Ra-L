#!/usr/bin/env python3
"""
Sa-Ra-L  |  Live Strategy Log Viewer
──────────────────────────────────────
Run in a SEPARATE terminal window while the portfolio is trading.

Usage:
    python tail_log.py                              # combined trading.log
    python tail_log.py --strategy NIFTY_INTRADAY_v1 # per-strategy detailed log
    python tail_log.py --strategy BLACK_SWAN_v1
    python tail_log.py --strategy EXPIRY_SCALPER_v1
    python tail_log.py --strategy RANGE_SCALPER_v1
    python tail_log.py --list                       # show all log files for today
    python tail_log.py --analysis                   # include ANALYSIS-level detail
    python tail_log.py --no-color                   # plain text (for piping/reports)
    python tail_log.py --lines 100                  # show last 100 lines on start
    python tail_log.py --export report.txt          # also write output to a file

Per-strategy logs:  logs/YYYY-MM-DD/<STRATEGY_NAME>.log
Combined log:       logs/trading.log
"""

from __future__ import annotations

import sys
import time
import argparse
import os
from datetime import datetime, date
from pathlib import Path

# ── Optional color support ────────────────────────────────────────────────────

try:
    from colorama import init as colorama_init, Fore, Style, Back
    colorama_init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

    class Fore:
        RED = GREEN = YELLOW = CYAN = MAGENTA = WHITE = BLUE = RESET = ""

    class Style:
        BRIGHT = DIM = RESET_ALL = ""

    class Back:
        RED = GREEN = YELLOW = BLUE = RESET = ""


# ── Strategy → display color mapping ─────────────────────────────────────────

STRATEGY_COLORS = {
    "NIFTY_INTRADAY": Fore.CYAN,
    "BLACK_SWAN":     Fore.MAGENTA,
    "EXPIRY_SCALPER": Fore.YELLOW,
    "RANGE_SCALPER":  Fore.GREEN,
    "RAMS":           Fore.BLUE,
    "LIVE_ENGINE":    Fore.WHITE,
    "portfolio":      Fore.WHITE,
}


def _strategy_color(line: str) -> str:
    for key, color in STRATEGY_COLORS.items():
        if key.lower() in line.lower():
            return color
    return ""


def _colorize(line: str, use_color: bool, show_analysis: bool) -> str | None:
    """
    Returns colorized line string, or None if the line should be filtered out.
    """
    stripped = line.rstrip()
    if not stripped:
        return stripped

    # Filter ANALYSIS lines unless requested
    if "| ANALYSIS  |" in stripped and not show_analysis:
        return None

    if not use_color or not HAS_COLOR:
        return stripped

    ll = stripped.lower()

    # ── Critical / errors ────────────────────────────────────────────────
    if "critical" in ll:
        return Back.RED + Fore.WHITE + Style.BRIGHT + stripped + Style.RESET_ALL
    if any(k in ll for k in ("error", "stop_loss", "daily stop", "range_broken",
                              "invalidated", "locked_loss", "sell failed")):
        return Fore.RED + Style.BRIGHT + stripped + Style.RESET_ALL

    # ── Wins / targets ───────────────────────────────────────────────────
    if any(k in ll for k in ("target_hit", "target hit", "validated", "profit lock")):
        return Fore.GREEN + Style.BRIGHT + stripped + Style.RESET_ALL

    # ── Entries ──────────────────────────────────────────────────────────
    if "entry" in ll and "no " not in ll and "skip" not in ll:
        return Fore.CYAN + Style.BRIGHT + stripped + Style.RESET_ALL

    # ── Breakeven / trail ────────────────────────────────────────────────
    if "breakeven" in ll or "be trail" in ll:
        return Fore.GREEN + stripped + Style.RESET_ALL

    # ── Regime classification ────────────────────────────────────────────
    if "regime:" in ll:
        return Style.BRIGHT + stripped + Style.RESET_ALL

    # ── Cooldown / locks ─────────────────────────────────────────────────
    if any(k in ll for k in ("cooldown", "locked_profit", "daily profit lock")):
        return Fore.YELLOW + stripped + Style.RESET_ALL

    # ── Skips / rejections ───────────────────────────────────────────────
    if any(k in ll for k in ("skip", "outside", "insufficient", "breakout",
                              "chase limit", "low volume", "no reversal")):
        return Fore.YELLOW + Style.DIM + stripped + Style.RESET_ALL

    # ── Analysis detail lines ────────────────────────────────────────────
    if "| analysis  |" in ll:
        return Style.DIM + stripped + Style.RESET_ALL

    # ── Force close / warnings ───────────────────────────────────────────
    if any(k in ll for k in ("force_close", "time_exit", "warning")):
        return Fore.MAGENTA + stripped + Style.RESET_ALL

    # ── Monitor tick lines ───────────────────────────────────────────────
    if "monitor" in ll and "pnl" in ll:
        return Style.DIM + stripped + Style.RESET_ALL

    # ── Pre-market / startup ─────────────────────────────────────────────
    if any(k in ll for k in ("pre-mkt", "pre-market", "starting", "vix=")):
        return Fore.WHITE + Style.BRIGHT + stripped + Style.RESET_ALL

    # ── Normal INFO lines ────────────────────────────────────────────────
    if "| info" in ll:
        return stripped

    return stripped


def _strategy_log_path(strategy_name: str, log_date: str = None) -> Path:
    d = log_date or date.today().strftime("%Y-%m-%d")
    return Path("logs") / d / f"{strategy_name}.log"


def list_logs() -> None:
    """Print all available log files for today and recent days."""
    log_root = Path("logs")
    print(f"\n  Sa-Ra-L  |  Available Log Files\n  {'─'*50}")

    if not log_root.exists():
        print("  No logs/ directory found.")
        return

    # Per-strategy per-day logs
    for day_dir in sorted(log_root.iterdir(), reverse=True)[:5]:
        if day_dir.is_dir():
            print(f"\n  {day_dir.name}/")
            for lf in sorted(day_dir.glob("*.log")):
                size = lf.stat().st_size
                mtime = datetime.fromtimestamp(lf.stat().st_mtime).strftime("%H:%M:%S")
                print(f"    {lf.name:<35}  {size:>8,} bytes  last updated {mtime}")

    # Combined log
    combined = log_root / "trading.log"
    if combined.exists():
        size = combined.stat().st_size
        print(f"\n  trading.log  {size:>8,} bytes  (combined all strategies)")

    print("\n  Usage:  python tail_log.py --strategy NIFTY_INTRADAY_v1")
    print("          python tail_log.py  (combined log)\n")


def _open_shared(filepath: Path):
    """Open log file for reading, allowing concurrent writes (Windows + Linux)."""
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes
        import msvcrt

        GENERIC_READ          = 0x80000000
        FILE_SHARE_READ       = 0x00000001
        FILE_SHARE_WRITE      = 0x00000002
        OPEN_EXISTING         = 3
        FILE_ATTRIBUTE_NORMAL = 0x80

        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype  = ctypes.wintypes.HANDLE
        k32.CreateFileW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
            ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
            ctypes.wintypes.HANDLE,
        ]
        handle = k32.CreateFileW(
            str(filepath), GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle == ctypes.wintypes.HANDLE(-1).value:
            raise FileNotFoundError(f"Cannot open: {filepath}")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        return os.fdopen(fd, "r", encoding="utf-8", errors="replace")
    else:
        return open(filepath, "r", encoding="utf-8", errors="replace")


def tail(filepath: Path, n_lines: int, use_color: bool,
         show_analysis: bool, export_path: Path | None) -> None:

    strategy = filepath.stem   # filename without extension
    title    = f"Sa-Ra-L  |  {strategy}  |  {filepath}"
    sep      = "─" * 72

    print(f"\n  {title}")
    print(f"  Started  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
          f"  |  {'ANALYSIS included' if show_analysis else 'INFO level (use --analysis for full detail)'}")
    print(f"  Ctrl+C to stop\n{sep}")

    export_fh = None
    if export_path:
        export_fh = open(export_path, "w", encoding="utf-8")
        export_fh.write(f"Sa-Ra-L Strategy Log Export — {strategy}\n")
        export_fh.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        export_fh.write(f"{sep}\n\n")

    def _emit(line: str) -> None:
        colorized = _colorize(line, use_color, show_analysis)
        if colorized is None:
            return
        print(colorized, flush=True)
        if export_fh:
            export_fh.write(line.rstrip() + "\n")
            export_fh.flush()

    try:
        with _open_shared(filepath) as f:
            all_lines = f.readlines()
            if all_lines:
                for line in all_lines[-n_lines:]:
                    _emit(line)
            else:
                print("  (log file is empty — waiting for first event…)")

            while True:
                line = f.readline()
                if line:
                    _emit(line)
                else:
                    time.sleep(0.3)

    except FileNotFoundError:
        print(f"\n  Log file not found: {filepath}")
        if "NIFTY_INTRADAY" in str(filepath) or "BLACK_SWAN" in str(filepath):
            print("  Make sure the portfolio runner is active and the strategy has started.")
            print(f"  Expected path: {filepath}")
        else:
            print("  Is the portfolio runner active?  (python main.py --mode portfolio)")
        sys.exit(1)

    except KeyboardInterrupt:
        print(f"\n\n{sep}")
        print(f"  Session ended  {datetime.now().strftime('%H:%M:%S')}")
        if export_fh:
            export_fh.write(f"\n{sep}\nSession ended {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            export_fh.close()
            print(f"  Report saved to: {export_path}")
        print()

    finally:
        if export_fh and not export_fh.closed:
            export_fh.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sa-Ra-L live strategy log viewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--strategy", "-s", default=None,
        metavar="NAME",
        help="Strategy name to tail (e.g. NIFTY_INTRADAY_v1). Omit for combined log.",
    )
    parser.add_argument(
        "--date", "-d", default=None,
        metavar="YYYY-MM-DD",
        help="Date of log to view (default: today). Useful for reviewing yesterday's trades.",
    )
    parser.add_argument(
        "--lines", "-n", type=int, default=50,
        help="Historical lines to show on startup (default: 50)",
    )
    parser.add_argument(
        "--analysis", "-a", action="store_true",
        help="Show ANALYSIS-level detail (tick-by-tick indicator values and rejection reasons)",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable color output (useful for piping to files)",
    )
    parser.add_argument(
        "--export", "-e", default=None, metavar="FILE",
        help="Also write output to this file (for report presentation)",
    )
    parser.add_argument(
        "--list", "-l", action="store_true",
        help="List all available log files and exit",
    )
    parser.add_argument(
        "--file", "-f", default=None,
        help="Tail an arbitrary log file path (overrides --strategy)",
    )
    args = parser.parse_args()

    if args.list:
        list_logs()
        return

    if args.file:
        filepath = Path(args.file)
    elif args.strategy:
        filepath = _strategy_log_path(args.strategy, args.date)
    else:
        filepath = Path("logs/trading.log")

    tail(
        filepath,
        n_lines=args.lines,
        use_color=not args.no_color,
        show_analysis=args.analysis,
        export_path=Path(args.export) if args.export else None,
    )


if __name__ == "__main__":
    main()
