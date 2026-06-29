"""
Multi-Strategy State Coordinator
────────────────────────────────
The unified dashboard runs many strategies at once. Each strategy gets its own
`BrahmastraState` instance (the same thread-safe store the BRAHMASTRA engine
already writes to). `MultiStrategyState` is a thin registry over those instances
plus a multiplexed WebSocket drain that tags every event with its `strategy`
name so the frontend can route it to the right view.

Design notes
  - We deliberately do NOT try to key the single global BrahmastraState by
    strategy (its read/write methods are keyed by *instrument*, and strategies
    overlap on instruments). One state per strategy keeps `snapshot()` and every
    `update_*` method reusable verbatim.
  - A separate "market" pseudo-strategy slot holds shared chart/indicator data
    that is instrument-wide (used by the multi-timeframe chart panel) so charts
    are available even when no single strategy is running.
"""
from __future__ import annotations

import threading
from typing import Optional

from src.brahmastra.api.state import BrahmastraState

# Shared market-data slot (charts/ticks) not owned by any single strategy.
MARKET_SLOT = "_market"


class MultiStrategyState:
    """Registry of per-strategy `BrahmastraState` instances."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[str, BrahmastraState] = {}

    # ── Access ────────────────────────────────────────────────────────────────

    def get(self, name: str) -> BrahmastraState:
        """Return the state for `name`, lazily creating it on first use."""
        with self._lock:
            st = self._states.get(name)
            if st is None:
                # Persist per-strategy logs (skip the shared market slot) so a restart
                # reloads the trail instead of starting from zero.
                st = BrahmastraState(persist_name=None if name == MARKET_SLOT else name)
                self._states[name] = st
            return st

    def market(self) -> BrahmastraState:
        """Shared instrument-wide market/chart state slot."""
        return self.get(MARKET_SLOT)

    def register(self, name: str, state: BrahmastraState) -> BrahmastraState:
        """
        Bind an EXISTING BrahmastraState as the slot for `name`.
        Used for BRAHMASTRA_v1, whose engine writes to the module-level
        `get_state()` singleton — we register that singleton so its rich
        updates flow into the coordinator with no engine changes.
        """
        with self._lock:
            self._states[name] = state
            return state

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._states

    def names(self, include_market: bool = False) -> list[str]:
        with self._lock:
            return [n for n in self._states
                    if include_market or n != MARKET_SLOT]

    def remove(self, name: str) -> None:
        with self._lock:
            self._states.pop(name, None)

    # ── Reads ─────────────────────────────────────────────────────────────────

    def snapshot(self, name: str) -> Optional[dict]:
        with self._lock:
            st = self._states.get(name)
        return st.snapshot() if st else None

    def snapshot_all(self) -> dict:
        """Full snapshot of every slot, keyed by strategy name."""
        with self._lock:
            states = dict(self._states)
        return {name: st.snapshot() for name, st in states.items()}

    def pop_ws_events(self, max_per_strategy: int = 200) -> list[dict]:
        """
        Drain every per-strategy queue and tag each event with its strategy.
        Returns a flat list ready to broadcast to all WebSocket clients.
        """
        with self._lock:
            states = list(self._states.items())
        events: list[dict] = []
        for name, st in states:
            for ev in st.pop_ws_events(max_per_strategy):
                ev = dict(ev)
                ev["strategy"] = name
                events.append(ev)
        return events


# ── Module-level singleton (mirrors brahmastra's get_state pattern) ───────────

_multi: Optional[MultiStrategyState] = None
_multi_lock = threading.Lock()


def get_multi_state() -> MultiStrategyState:
    global _multi
    if _multi is None:
        with _multi_lock:
            if _multi is None:
                _multi = MultiStrategyState()
    return _multi


def reset_multi_state() -> MultiStrategyState:
    global _multi
    with _multi_lock:
        _multi = MultiStrategyState()
    return _multi
