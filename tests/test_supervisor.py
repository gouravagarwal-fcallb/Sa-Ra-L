"""
Crash self-heal supervisor — regression tests.

Guards the behaviour that makes strategies "workable": a strategy that crashes
mid-session must be auto-restarted (PAPER only, capped, respecting operator stop),
so a transient data error doesn't silently kill it for the rest of the day (the
RANGE_SCALPER "0 analysis cycles / Market closed" symptom).
"""
import types

import pytest

from src.api.runner import ApiPortfolioRunner


def _runner(monkeypatch):
    r = ApiPortfolioRunner.__new__(ApiPortfolioRunner)   # skip heavy __init__
    import threading
    r._lock = threading.RLock()
    r._statuses = {}
    r._threads = {}
    r._operator_stopped = set()
    r._restart_budget = {}
    r._restart_mode = {}
    # a fake multi-state slot that records add_log calls
    logs = []
    slot = types.SimpleNamespace(add_log=lambda cat, msg: logs.append((cat, msg)))
    r.multi = types.SimpleNamespace(has=lambda n: True, get=lambda n: slot)
    r._logs = logs
    # market is OPEN for these tests
    import src.api.operating_policy as op
    monkeypatch.setattr(op, "_market_closed", lambda: False)
    # record start calls; pretend the restart succeeds
    r._started = []
    def _start(name, mode="paper"):
        r._started.append((name, mode))
        return {"name": name, "started": True}
    monkeypatch.setattr(r, "start_strategy", _start)
    monkeypatch.setattr(r, "is_running", lambda n: False)  # nothing alive
    return r


def _status(state, mode="paper"):
    return types.SimpleNamespace(state=state, mode=mode, error="")


def test_supervisor_restarts_crashed_strategy_in_paper(monkeypatch):
    r = _runner(monkeypatch)
    r._statuses = {"RANGE_SCALPER_v1": _status("ERROR")}
    restarted = r.supervise()
    assert restarted == ["RANGE_SCALPER_v1"]
    assert r._started == [("RANGE_SCALPER_v1", "paper")]      # PAPER, never live


def test_supervisor_never_restarts_operator_stopped(monkeypatch):
    r = _runner(monkeypatch)
    r._statuses = {"X_v1": _status("ERROR")}
    r._operator_stopped.add("X_v1")
    assert r.supervise() == []
    assert r._started == []


def test_supervisor_ignores_cleanly_stopped(monkeypatch):
    r = _runner(monkeypatch)
    r._statuses = {"X_v1": _status("STOPPED")}   # ended normally at close — not a crash
    assert r.supervise() == []


def test_supervisor_restart_is_capped(monkeypatch):
    r = _runner(monkeypatch)
    r._statuses = {"X_v1": _status("ERROR")}
    for _ in range(ApiPortfolioRunner._MAX_RESTARTS):
        r.supervise()
    n = len(r._started)
    assert n == ApiPortfolioRunner._MAX_RESTARTS       # stopped at the cap
    r.supervise()                                      # one more pass — must not restart
    assert len(r._started) == n
    assert any("gave up" in msg for _, msg in r._logs)  # alerted loudly


def test_supervisor_never_auto_restarts_live(monkeypatch):
    # Even if it crashed while LIVE, it must come back PAPER (never auto-arm real money).
    r = _runner(monkeypatch)
    r._statuses = {"EXPIRY_SCALPER_v1": _status("ERROR", mode="live")}
    r.supervise()
    assert r._started == [("EXPIRY_SCALPER_v1", "paper")]
