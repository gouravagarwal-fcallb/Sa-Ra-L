"""
BRAHMASTRA Economic Calendar Checker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reads config/economic_calendar.yaml and returns how many minutes until the
next high-impact event.  Used by entry gate G12.

Usage:
    events = load_calendar()
    mins   = minutes_to_next_event(datetime.now(IST), events)
    # mins < 30 → block new entries
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

_CALENDAR_PATH = os.path.join(
    os.path.dirname(__file__),          # .../fetchers/
    "..", "..", "..", "..", "config",    # → project root/config
    "economic_calendar.yaml",
)


def load_calendar(path: str = None) -> list[datetime]:
    """
    Load events from YAML and return a sorted list of IST-aware datetimes.
    Returns empty list on any error (fail-safe: gate never blocks).
    """
    try:
        import yaml
    except ImportError:
        return []

    try:
        fpath = path or os.path.normpath(_CALENDAR_PATH)
        with open(fpath, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not raw:
            return []

        events: list[datetime] = []
        for item in raw:
            try:
                date_str = str(item["date"])
                time_str = str(item.get("time", "09:15"))
                dt_str   = f"{date_str} {time_str}"
                dt       = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                dt_ist   = dt.replace(tzinfo=IST)
                events.append(dt_ist)
            except Exception:
                continue
        return sorted(events)
    except Exception:
        return []


def minutes_to_next_event(
    now: datetime,
    events: list[datetime],
    window_hours: float = 2.0,
) -> int:
    """
    Return minutes until the next event that falls within the next window_hours.
    Returns 999 if no event is due within that window.
    """
    if not events:
        return 999

    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)

    cutoff = now + timedelta(hours=window_hours)
    upcoming = [e for e in events if now <= e <= cutoff]
    if not upcoming:
        return 999

    nearest = min(upcoming)
    delta_minutes = int((nearest - now).total_seconds() / 60)
    return max(0, delta_minutes)
