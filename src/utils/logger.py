import logging
import os
import sys
import threading
from datetime import datetime

_setup_lock = threading.Lock()

# ── Windows-safe file handler ─────────────────────────────────────────────────
# Python's default FileHandler on Windows holds an exclusive write lock that
# blocks Notepad and CMD's shell-open from reading the file.
# This subclass opens with FILE_SHARE_READ | FILE_SHARE_WRITE so tail_log.py
# (and PowerShell Get-Content -Wait) can read the file while trading runs.

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes
    import msvcrt

    class _SharedFileHandler(logging.FileHandler):
        def _open(self):
            GENERIC_WRITE         = 0x40000000
            FILE_SHARE_READ       = 0x00000001
            FILE_SHARE_WRITE      = 0x00000002
            OPEN_ALWAYS           = 4
            FILE_ATTRIBUTE_NORMAL = 0x80
            FILE_END              = 2

            k32 = ctypes.windll.kernel32
            k32.CreateFileW.restype  = ctypes.wintypes.HANDLE
            k32.CreateFileW.argtypes = [
                ctypes.wintypes.LPCWSTR,
                ctypes.wintypes.DWORD,
                ctypes.wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.wintypes.DWORD,
                ctypes.wintypes.DWORD,
                ctypes.wintypes.HANDLE,
            ]

            handle = k32.CreateFileW(
                str(self.baseFilename),
                GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_ALWAYS,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
            if handle == ctypes.wintypes.HANDLE(-1).value:
                raise OSError(f"Cannot open log file: {self.baseFilename}")

            k32.SetFilePointer(handle, 0, None, FILE_END)   # seek to end (append)
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_APPEND)
            return os.fdopen(fd, "a", encoding=self.encoding or "utf-8")

    _FileHandler = _SharedFileHandler
else:
    _FileHandler = logging.FileHandler


# ── ANALYSIS log level (between DEBUG=10 and INFO=20) ────────────────────────
# Used for tick-by-tick indicator values and signal rejection reasons.
# Visible in per-strategy log files; suppressed in the combined trading.log
# (which only shows INFO+) so the master log stays readable.
ANALYSIS = 15
logging.addLevelName(ANALYSIS, "ANALYSIS")


def _analysis(self, message, *args, **kwargs):
    if self.isEnabledFor(ANALYSIS):
        self._log(ANALYSIS, message, args, **kwargs)


logging.Logger.analysis = _analysis


# ── Master combined log that all strategies also write to ─────────────────────
_MASTER_LOG = "logs/trading.log"
_master_handler: logging.FileHandler | None = None


def _get_master_handler() -> logging.FileHandler:
    global _master_handler
    if _master_handler is None:
        os.makedirs("logs", exist_ok=True)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        h = _FileHandler(_MASTER_LOG, encoding="utf-8")
        h.setFormatter(fmt)
        h.setLevel(logging.INFO)   # master log: INFO+ only (no ANALYSIS noise)
        _master_handler = h
    return _master_handler


def setup_logger(
    name: str,
    log_file: str = "logs/trading.log",
    level: str = "INFO",
    strategy_name: str = None,
) -> logging.Logger:
    """
    Create (or retrieve) a named logger.

    When `strategy_name` is provided the logger writes to TWO files:
      1. logs/YYYY-MM-DD/<strategy_name>.log  — full ANALYSIS-level detail
      2. logs/trading.log                     — master combined log (INFO+ only)

    Without `strategy_name` the old single-file behaviour is preserved.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if strategy_name:
        per_strat_dir  = os.path.join("logs", today)
        per_strat_file = os.path.join(per_strat_dir, f"{strategy_name}.log")
    else:
        per_strat_file = None

    os.makedirs(os.path.dirname(log_file) if os.path.dirname(log_file) else "logs", exist_ok=True)

    logger = logging.getLogger(name)

    # ANALYSIS level so per-strategy file gets everything; master handler
    # is filtered to INFO+ separately.
    logger.setLevel(ANALYSIS)

    with _setup_lock:
        if not logger.handlers:
            fmt = logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            if per_strat_file:
                # ── Per-strategy, per-day file (ANALYSIS+ = everything) ──────
                os.makedirs(per_strat_dir, exist_ok=True)
                fh_strat = _FileHandler(per_strat_file, encoding="utf-8")
                fh_strat.setLevel(ANALYSIS)
                fh_strat.setFormatter(fmt)
                logger.addHandler(fh_strat)

                # ── Master combined log (INFO+ only) ─────────────────────────
                logger.addHandler(_get_master_handler())

            else:
                # Legacy path: single log file
                fh = _FileHandler(log_file, encoding="utf-8")
                fh.setLevel(getattr(logging, level.upper(), logging.INFO))
                fh.setFormatter(fmt)
                logger.addHandler(fh)

            # ── Console output (INFO+ to avoid ANALYSIS spam in terminal) ────
            if sys.platform == "win32":
                try:
                    sys.stdout.reconfigure(encoding="utf-8")
                except AttributeError:
                    pass
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(logging.INFO)
            ch.setFormatter(fmt)
            logger.addHandler(ch)

    return logger


def get_strategy_logger(name: str, strategy_name: str) -> logging.Logger:
    """Convenience wrapper for live strategy engines."""
    return setup_logger(name, strategy_name=strategy_name)


def get_trade_logger() -> logging.Logger:
    today = datetime.now().strftime("%Y%m%d")
    return setup_logger("trade", f"logs/trades_{today}.log")
