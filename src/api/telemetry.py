"""
Telemetry contract + liveness + demote-only safety (redesign Phase 1, Part 7)
══════════════════════════════════════════════════════════════════════════════
A strategy that logs no analysis trail is unverifiable and must NOT be treated as
healthy. This module defines the telemetry contract and the gate:

  • TELEMETRY CONTRACT: while running, a strategy must produce at least one
    structured cycle line (category ANALYSIS / SIGNAL / HEARTBEAT, parseable ts)
    every HEARTBEAT_MAX_GAP_S. The MarketFeed emits a central HEARTBEAT to GUARANTEE
    liveness for every running strategy without touching strategy logic (so the
    four blind strategies — ATM_PULSE_BURST, BRAHMASTRA, INRUSD, PASHUPATASTRA —
    get a verifiable trail immediately).

  • telemetry_ok(name): True if the strategy emitted ≥1 cycle line today (and, when
    running, a line within the liveness window).

  • DEMOTE-ONLY: a strategy flagged blind can be added to config/telemetry_overlay.json
    (gitignored). The runner forces it to paper and arm-live refuses it until fixed.
    This only ever RESTRICTS (live→paper) — it never promotes. No auto-write here;
    population is an explicit operator/EOD action.
"""
from __future__ import annotations

import os
import json
import threading
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
_DASH_DIR = "logs/dashboard"
_OVERLAY = "config/telemetry_overlay.json"

HEARTBEAT_MAX_GAP_S = 90          # contract: a cycle line at least this often while running
CYCLE_CATEGORIES = ("ANALYSIS", "SIGNAL", "ANALYSE", "HEARTBEAT")

_lock = threading.Lock()


def _today() -> str:
    return datetime.now(IST).date().isoformat()


def _secs_of_day(ts: str) -> int | None:
    try:
        s = ts.strip().split(" ")[-1]
        h, m, *rest = s.split(":")
        sec = int(rest[0]) if rest else 0
        return int(h) * 3600 + int(m) * 60 + sec
    except Exception:
        return None


def cycles_today(name: str) -> int:
    """Count of cycle lines in today's persisted trail for `name`."""
    p = os.path.join(_DASH_DIR, f"{name}_{_today()}.jsonl")
    n = 0
    try:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        e = json.loads(ln)
                    except Exception:
                        continue
                    if (e.get("category") or "").upper() in CYCLE_CATEGORIES:
                        n += 1
    except Exception:
        pass
    return n


def last_cycle_age_s(state) -> float | None:
    """Seconds since the strategy's last cycle line, from its in-memory log deque."""
    try:
        lines = list(getattr(state, "log_lines", []))
        for e in reversed(lines):
            if (e.get("category") or "").upper() in CYCLE_CATEGORIES:
                sd = _secs_of_day(e.get("ts", ""))
                if sd is None:
                    return None
                now = datetime.now(IST)
                return max(0.0, (now.hour * 3600 + now.minute * 60 + now.second) - sd)
    except Exception:
        return None
    return None


def telemetry_ok(name: str, *, state=None, running: bool = False) -> bool:
    """Healthy telemetry = emitted ≥1 cycle today, and (if running) within the window."""
    if cycles_today(name) <= 0 and not (state and list(getattr(state, "log_lines", []))):
        return False
    if running and state is not None:
        age = last_cycle_age_s(state)
        if age is not None and age > HEARTBEAT_MAX_GAP_S * 3:
            return False
    return True


# ── Demote-only overlay (live→paper restriction) ─────────────────────────────
def load_demoted() -> dict:
    try:
        if os.path.isfile(_OVERLAY):
            return json.load(open(_OVERLAY, encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def is_demoted(name: str) -> bool:
    return name in load_demoted()


def demote(name: str, reason: str) -> None:
    """Force a strategy to paper until telemetry is fixed (restrict only)."""
    with _lock:
        d = load_demoted()
        d[name] = {"reason": reason, "at": datetime.now(IST).isoformat()}
        try:
            os.makedirs(os.path.dirname(_OVERLAY), exist_ok=True)
            json.dump(d, open(_OVERLAY, "w", encoding="utf-8"), indent=2)
        except Exception:
            pass


def clear_demoted(name: str) -> None:
    with _lock:
        d = load_demoted()
        if name in d:
            d.pop(name)
            try:
                json.dump(d, open(_OVERLAY, "w", encoding="utf-8"), indent=2)
            except Exception:
                pass
