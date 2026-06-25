"""
BRAHMASTRA Verbose Logger — 13 Categories
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every second BRAHMASTRA is alive, it is working.
Every action is logged. Nothing is hidden.

Categories:
  TICK     — raw price data received
  BAR      — new bar completed / updated
  ANALYSE  — indicator computed + meaning
  SCORE    — confluence score update
  SCENARIO — scenario confidence update
  DECISION — system making a choice
  GATE     — entry gate check result
  ORDER    — order placement / fill / cancel
  TRADE    — trade lifecycle event
  RISK     — risk management check
  DATA     — data fetch / backfill / cache
  ALERT    — notification sent
  SYSTEM   — startup / shutdown / mode change
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Optional

IST = timezone(timedelta(hours=5, minutes=30))

# ── Custom level numbers ───────────────────────────────────────────
# Standard: DEBUG=10  INFO=20  WARNING=30  ERROR=40  CRITICAL=50
# We insert category levels below INFO so they appear in verbose log
# but can be filtered separately.

_CATEGORY_LEVELS: dict[str, int] = {
    "TICK":     11,
    "BAR":      12,
    "ANALYSE":  13,
    "SCORE":    14,
    "SCENARIO": 15,
    "DECISION": 16,
    "GATE":     17,
    "ORDER":    18,
    "TRADE":    19,
    "RISK":     20,
    "DATA":     21,
    "ALERT":    22,
    "SYSTEM":   23,
}

# Register custom levels with the logging module
for _name, _num in _CATEGORY_LEVELS.items():
    logging.addLevelName(_num, _name)


class _CategoryFormatter(logging.Formatter):
    """Formats every log line as: [HH:MM:SS.mmm] [CATEGORY  ] message"""

    def format(self, record: logging.LogRecord) -> str:
        now = datetime.now(IST)
        ts  = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        cat = record.levelname.ljust(8)
        return f"[{ts}] [{cat}] {record.getMessage()}"


class BrahmastraLogger:
    """
    Central logger for BRAHMASTRA_v1.

    Usage:
        log = BrahmastraLogger(date="2026-06-26", config=cfg)
        log.system("BRAHMASTRA_v1 starting — Phase 0 Foundation")
        log.bar("1m bar closed | NIFTY O=24218 H=24241 L=24215 C=24235 V=1842000")
        log.analyse("1m RSI=62.3 (prev=61.1) rising → crossed 60 → +3pts BULL")
        log.decision("ARMED — Scenario A confidence=82% → waiting for 2nd confirm bar")

    Every line goes to:
        1. Console (stdout) — configurable verbosity
        2. Full verbose log file (all 13 categories)
        3. Trades-only log  (TRADE + ORDER + DECISION + GATE)
        4. Error log        (WARNING + ERROR + CRITICAL)
        5. Live UI queue    (last N lines, streamed via WebSocket)
    """

    def __init__(
        self,
        log_dir: str = "logs/BRAHMASTRA_v1",
        date_str: Optional[str] = None,
        enabled_categories: Optional[dict[str, bool]] = None,
        console_categories: Optional[list[str]] = None,
        ui_queue_size: int = 200,
        ui_callback: Optional[Callable[[str], None]] = None,
    ):
        self._date = date_str or datetime.now(IST).strftime("%Y-%m-%d")
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._ui_queue: queue.Queue = queue.Queue(maxsize=ui_queue_size)
        self._ui_callback = ui_callback
        self._lock = threading.Lock()

        # Which categories are enabled (default: all on)
        self._enabled: dict[str, bool] = {k: True for k in _CATEGORY_LEVELS}
        if enabled_categories:
            self._enabled.update(enabled_categories)

        # Which categories print to console (default: important ones)
        self._console_cats: set[str] = set(
            console_categories or
            ["BAR", "ANALYSE", "SCORE", "SCENARIO", "DECISION", "GATE",
             "ORDER", "TRADE", "RISK", "ALERT", "SYSTEM"]
        )

        # Build file handlers
        fmt = _CategoryFormatter()

        # 1. Full verbose log — everything
        self._verbose_handler = self._make_file_handler(
            self._log_dir / f"BRAHMASTRA_{self._date}.log", level=1
        )

        # 2. Trades-only log — TRADE + ORDER + DECISION + GATE
        self._trades_handler = self._make_file_handler(
            self._log_dir / f"BRAHMASTRA_{self._date}_trades.log", level=1
        )
        self._trades_cats = {"TRADE", "ORDER", "DECISION", "GATE", "RISK", "ALERT", "SYSTEM"}

        # 3. Error log — WARNING and above
        self._error_handler = self._make_file_handler(
            self._log_dir / f"BRAHMASTRA_{self._date}_errors.log",
            level=logging.WARNING
        )

        # 4. Console handler
        self._console_handler = logging.StreamHandler()
        self._console_handler.setLevel(1)
        self._console_handler.setFormatter(fmt)

        # Internal python logger (used only for error log + console)
        self._py_log = logging.getLogger(f"brahmastra.{self._date}")
        self._py_log.setLevel(1)
        self._py_log.propagate = False
        for h in (self._verbose_handler, self._error_handler, self._console_handler):
            h.setFormatter(fmt)
            self._py_log.addHandler(h)

    @staticmethod
    def _make_file_handler(path: Path, level: int) -> logging.FileHandler:
        h = logging.FileHandler(path, encoding="utf-8")
        h.setLevel(level)
        h.setFormatter(_CategoryFormatter())
        return h

    def _write(self, category: str, message: str) -> None:
        if not self._enabled.get(category, True):
            return
        level = _CATEGORY_LEVELS[category]
        now   = datetime.now(IST)
        ts    = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        line  = f"[{ts}] [{category.ljust(8)}] {message}"

        with self._lock:
            # Write to verbose log
            try:
                self._verbose_handler.stream.write(line + "\n")
                self._verbose_handler.stream.flush()
            except Exception:
                pass

            # Write to trades log if category qualifies
            if category in self._trades_cats:
                try:
                    self._trades_handler.stream.write(line + "\n")
                    self._trades_handler.stream.flush()
                except Exception:
                    pass

            # Console output for important categories
            if category in self._console_cats:
                print(line)

            # UI queue (non-blocking — drop oldest if full)
            try:
                if self._ui_queue.full():
                    try:
                        self._ui_queue.get_nowait()
                    except queue.Empty:
                        pass
                self._ui_queue.put_nowait(line)
            except Exception:
                pass

            # UI callback (e.g. WebSocket emit)
            if self._ui_callback:
                try:
                    self._ui_callback(line)
                except Exception:
                    pass

    # ── Error/warning via standard Python logger ─────────────────
    def warning(self, msg: str) -> None:
        self._py_log.warning(msg)

    def error(self, msg: str) -> None:
        self._py_log.error(msg)

    def critical(self, msg: str) -> None:
        self._py_log.critical(msg)

    # ── 13 category methods ───────────────────────────────────────

    def tick(self, msg: str) -> None:
        self._write("TICK", msg)

    def bar(self, msg: str) -> None:
        self._write("BAR", msg)

    def analyse(self, msg: str) -> None:
        self._write("ANALYSE", msg)

    def score(self, msg: str) -> None:
        self._write("SCORE", msg)

    def scenario(self, msg: str) -> None:
        self._write("SCENARIO", msg)

    def decision(self, msg: str) -> None:
        self._write("DECISION", msg)

    def gate(self, msg: str) -> None:
        self._write("GATE", msg)

    def order(self, msg: str) -> None:
        self._write("ORDER", msg)

    def trade(self, msg: str) -> None:
        self._write("TRADE", msg)

    def risk(self, msg: str) -> None:
        self._write("RISK", msg)

    def data(self, msg: str) -> None:
        self._write("DATA", msg)

    def alert(self, msg: str) -> None:
        self._write("ALERT", msg)

    def system(self, msg: str) -> None:
        self._write("SYSTEM", msg)

    # ── UI queue access ───────────────────────────────────────────
    def get_recent_lines(self, n: int = 100) -> list[str]:
        """Return last n log lines for UI display."""
        lines = list(self._ui_queue.queue)
        return lines[-n:] if len(lines) > n else lines

    def set_ui_callback(self, callback: Callable[[str], None]) -> None:
        self._ui_callback = callback

    def close(self) -> None:
        for h in (self._verbose_handler, self._trades_handler, self._error_handler):
            try:
                h.close()
            except Exception:
                pass


# ── Module-level singleton ────────────────────────────────────────

_instance: Optional[BrahmastraLogger] = None


def get_brahmastra_logger(
    log_dir: str = "logs/BRAHMASTRA_v1",
    date_str: Optional[str] = None,
    config: Optional[dict] = None,
) -> BrahmastraLogger:
    """
    Return the module-level BRAHMASTRA logger singleton.
    Creates it on first call; subsequent calls return the same instance.
    """
    global _instance
    if _instance is None:
        enabled = None
        console_cats = None
        ui_size = 200
        if config:
            log_cfg = config.get("logging", {})
            cats_cfg = log_cfg.get("categories", {})
            enabled = {k: v for k, v in cats_cfg.items()} if cats_cfg else None
            ui_size  = log_cfg.get("ui_live_lines", 200)
            console_cats = log_cfg.get("ui_default_filter", None)
            if log_dir == "logs/BRAHMASTRA_v1":
                log_dir = log_cfg.get("log_dir", log_dir)
        _instance = BrahmastraLogger(
            log_dir=log_dir,
            date_str=date_str,
            enabled_categories=enabled,
            console_categories=console_cats,
            ui_queue_size=ui_size,
        )
    return _instance
