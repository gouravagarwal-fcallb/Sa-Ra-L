"""
Preflight session-timing must key off the APP START time, not the view/check time.

Bug: opening the dashboard at 09:18 after starting the app at 09:10 falsely warned
"started after the 09:15 open" because the check used datetime.now(). It must use
the recorded boot time so a pre-open start reads as a full session all day.
"""
from datetime import datetime, timezone, timedelta

import src.api.preflight as pf

IST = timezone(timedelta(hours=5, minutes=30))


class _FrozenDT(datetime):
    """datetime subclass whose now() returns a fixed weekday-after-open moment."""
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 7, 27, 9, 18, tzinfo=tz or IST)   # Mon 09:18, after open


def _at(h, m, day=27):
    return datetime(2026, 7, day, h, m, tzinfo=IST)


def test_started_before_open_reads_full_session_even_when_viewed_after(monkeypatch):
    monkeypatch.setattr(pf, "datetime", _FrozenDT)
    before, sess = pf._check_session_timing(started_at=_at(9, 10))   # started 09:10
    assert before is True, "a 09:10 start must read as before-open even when viewed at 09:18"
    assert sess["started_at"] == "09:10"


def test_started_after_open_warns(monkeypatch):
    monkeypatch.setattr(pf, "datetime", _FrozenDT)
    before, sess = pf._check_session_timing(started_at=_at(9, 30))   # started 09:30
    assert before is False
    assert sess["started_at"] == "09:30"


def test_overnight_start_reads_full_session(monkeypatch):
    monkeypatch.setattr(pf, "datetime", _FrozenDT)
    before, _ = pf._check_session_timing(started_at=_at(20, 0, day=26))  # prev evening
    assert before is True, "a server left running overnight was up before today's open"


def test_no_started_at_falls_back_to_now(monkeypatch):
    # CLI path: no boot time → uses now() (09:18, after open) → not before-open.
    monkeypatch.setattr(pf, "datetime", _FrozenDT)
    before, sess = pf._check_session_timing()
    assert before is False and sess["started_at"] == "09:18"
