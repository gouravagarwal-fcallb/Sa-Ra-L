import logging
import os
import sys
from datetime import datetime


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
            GENERIC_WRITE        = 0x40000000
            FILE_SHARE_READ      = 0x00000001
            FILE_SHARE_WRITE     = 0x00000002
            OPEN_ALWAYS          = 4
            FILE_ATTRIBUTE_NORMAL = 0x80
            FILE_END             = 2

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

            k32.SetFilePointer(handle, 0, None, FILE_END)  # seek to end (append)
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_APPEND)
            return os.fdopen(fd, "a", encoding=self.encoding or "utf-8")

    _FileHandler = _SharedFileHandler
else:
    _FileHandler = logging.FileHandler


def setup_logger(name: str, log_file: str = "logs/trading.log", level: str = "INFO") -> logging.Logger:
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        fh = _FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        # Force UTF-8 on Windows console so special characters don't crash
        if sys.platform == "win32":
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except AttributeError:
                pass
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    return logger


def get_trade_logger() -> logging.Logger:
    today = datetime.now().strftime("%Y%m%d")
    return setup_logger("trade", f"logs/trades_{today}.log")
