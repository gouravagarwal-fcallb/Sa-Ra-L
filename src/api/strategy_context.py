"""
Strategy context adjustments — the ONLY channel from News Desk to the engine
════════════════════════════════════════════════════════════════════════════
News can influence the trading tool, but it must NEVER place or trigger a trade
(bot spec Part 6). This module is the controlled, auditable, expiring bridge:

  • The News Desk writes ContextAdjustment objects here (macro bias, high-risk
    flag, require-confirm, widen-no-trade, disable-strategy, watch-condition).
  • The engine/dashboard READS the current effective context — advisory only.
  • Every write is audited; every adjustment EXPIRES (stale news stops mattering);
    conflicting items are resolved by most-recent + highest-confidence.

There is deliberately NO method here that places an order, changes capital, or
arms live trading. The surface is read/advise only.
"""
from __future__ import annotations

import os
import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta

from src.api.bot_core import audit, Cat

IST = timezone(timedelta(hours=5, minutes=30))
_PATH_DIR = "logs/bots"

# Allowed effects (Part 6) — anything not in this set is rejected.
ALLOWED_EFFECTS = {
    "macro_bias",          # value: BULLISH | BEARISH | NEUTRAL
    "confidence_delta",    # value: -1.0 .. +1.0 (scales strategy context confidence)
    "high_risk_session",   # value: True
    "require_confirmation",# value: True (extra confirm before any trade)
    "widen_no_trade",      # value: True (widen the no-trade zone)
    "watch_condition",     # value: free text
    "disable_strategy",    # value: strategy name
}


def _now():
    return datetime.now(IST)


@dataclass
class ContextAdjustment:
    adj_id: str
    effect: str
    value: object
    reason: str
    source_impact_id: str = ""
    confidence: float = 0.5
    created_at: str = field(default_factory=lambda: _now().isoformat())
    expires_at: str = ""           # ISO; empty = default TTL applied on add
    active: bool = True


class StrategyContext:
    """Thread-safe, expiring store of advisory context adjustments."""

    def __init__(self, default_ttl_min: int = 120):
        self._lock = threading.Lock()
        self._items: list[ContextAdjustment] = []
        self._ttl = default_ttl_min
        self._seq = 0
        self._load()

    def _path(self):
        return os.path.join(_PATH_DIR, f"context_{_now().date().isoformat()}.jsonl")

    def _load(self):
        try:
            if os.path.isfile(self._path()):
                with open(self._path(), encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if ln:
                            row = json.loads(ln)
                            self._items.append(ContextAdjustment(
                                **{k: row.get(k) for k in ContextAdjustment.__dataclass_fields__}))
        except Exception:
            pass

    def _persist(self, adj: ContextAdjustment):
        try:
            os.makedirs(_PATH_DIR, exist_ok=True)
            with open(self._path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(adj), default=str) + "\n")
        except Exception:
            pass

    def add(self, effect: str, value, reason: str, *, source_impact_id: str = "",
            confidence: float = 0.5, ttl_min: int | None = None) -> dict:
        """Add an advisory adjustment. Rejects unknown effects. Never trades."""
        if effect not in ALLOWED_EFFECTS:
            audit(Cat.CONTEXT, "news_desk", "rejected", "error",
                  f"disallowed effect {effect}", ref=source_impact_id)
            return {"ok": False, "reason": f"effect '{effect}' not allowed"}
        self._seq += 1
        exp = (_now() + timedelta(minutes=ttl_min or self._ttl)).isoformat()
        adj = ContextAdjustment(
            adj_id=f"ctx-{int(_now().timestamp())}-{self._seq}", effect=effect, value=value,
            reason=reason, source_impact_id=source_impact_id, confidence=confidence,
            expires_at=exp)
        with self._lock:
            self._items.append(adj)
            self._persist(adj)
        audit(Cat.CONTEXT, "news_desk", "apply", "ok",
              f"{effect}={value} ({reason[:80]})", ref=adj.adj_id,
              meta={"confidence": confidence, "expires_at": exp})
        return {"ok": True, "adj_id": adj.adj_id, "expires_at": exp}

    def _prune(self):
        now = _now().isoformat()
        for a in self._items:
            if a.active and a.expires_at and a.expires_at < now:
                a.active = False
                audit(Cat.CONTEXT, "news_desk", "expire", "ok", a.effect, ref=a.adj_id)

    def effective(self) -> dict:
        """Current advisory context after expiry + conflict resolution (latest +
        highest-confidence wins per effect). Read-only; the engine consults this."""
        with self._lock:
            self._prune()
            active = [a for a in self._items if a.active]
        resolved: dict[str, ContextAdjustment] = {}
        disabled, watches = [], []
        for a in sorted(active, key=lambda x: (x.created_at, x.confidence)):
            if a.effect == "disable_strategy":
                disabled.append(a.value)
            elif a.effect == "watch_condition":
                watches.append(a.value)
            else:
                resolved[a.effect] = a   # later/higher-conf overwrites
        out = {k: {"value": v.value, "reason": v.reason, "confidence": v.confidence,
                   "expires_at": v.expires_at, "source": v.source_impact_id}
               for k, v in resolved.items()}
        out["disabled_strategies"] = disabled
        out["watch_conditions"] = watches
        out["active_count"] = len(active)
        return out

    def all(self) -> list:
        with self._lock:
            self._prune()
            return [asdict(a) for a in self._items]

    def clear(self, adj_id: str) -> bool:
        with self._lock:
            for a in self._items:
                if a.adj_id == adj_id and a.active:
                    a.active = False
                    audit(Cat.CONTEXT, "operator", "clear", "ok", a.effect, ref=adj_id)
                    return True
        return False


_ctx: StrategyContext | None = None
_ctx_lock = threading.Lock()


def get_context() -> StrategyContext:
    global _ctx
    with _ctx_lock:
        if _ctx is None:
            _ctx = StrategyContext()
        return _ctx
