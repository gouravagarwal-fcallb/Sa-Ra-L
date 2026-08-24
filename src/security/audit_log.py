"""
Structured audit trail — audit control LOG-1.
────────────────────────────────────────────
Three explicitly-separated channels so an auditor can filter by failure class:

    SYSTEM     — infrastructure faults: API timeouts, auth failures, crashes.
    DATA       — data-integrity faults: stale/late feed, gaps, NaN bars, zero volume.
    EXECUTION  — order lifecycle: intent, order_id, fill, slippage, exit reason.

Every record is a single structured line (key=value) routed through the platform
logger, so it lands in the same rotating files the dashboard tails. Secret values
must never be passed in — pass masked refs only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from src.utils.logger import get_strategy_logger

_IST = timezone(timedelta(hours=5, minutes=30))
_sys = get_strategy_logger("audit_system", "AUDIT.SYSTEM")
_dat = get_strategy_logger("audit_data", "AUDIT.DATA")
_exe = get_strategy_logger("audit_exec", "AUDIT.EXEC")


def _emit(logger, channel: str, event: str, **fields) -> dict:
    rec = {"ts": datetime.now(_IST).isoformat(), "chan": channel, "event": event}
    rec.update(fields)
    logger.info(json.dumps(rec, default=str, separators=(",", ":")))
    return rec


def log_system(event: str, **f) -> dict:
    """Infra fault (API timeout, auth fail, restart)."""
    return _emit(_sys, "SYSTEM", event, **f)


def log_data(event: str, **f) -> dict:
    """Data-integrity fault (stale feed, gap, zero-volume bar)."""
    return _emit(_dat, "DATA", event, **f)


def log_execution(event: str, **f) -> dict:
    """Order lifecycle (intent / placed / filled / slippage / exit)."""
    return _emit(_exe, "EXECUTION", event, **f)
