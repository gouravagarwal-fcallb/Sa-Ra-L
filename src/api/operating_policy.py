"""
Default-live operating policy + precise status taxonomy (operator policy)
═════════════════════════════════════════════════════════════════════════
New operating rule: every strategy is ACTIVE by default (auto-started, continuously
analysing) — the operator never hand-pushes a strategy to make it run, and "Blind"
is never an acceptable steady state. Real-money order placement remains gated.

Two independent switches (config/live_policy.yaml, gitignored):
  • auto_start        (default TRUE)  — bring every non-paused/archived strategy up
                                        on launch and keep it analysing.
  • auto_live_orders  (default FALSE) — let status=live strategies place REAL orders
                                        automatically. OFF until the operator flips it.

Operator overrides (per strategy) always win: paper | paused | stopped | archived.

Live-eligibility is risk-gated even when auto_live_orders is on: capital>0 AND not
telemetry-demoted. A live strategy that fails a gate is started in PAPER and an alert
is raised — it is never left blind, and it never places a bad real order.
"""
from __future__ import annotations

import os

POLICY_PATH = "config/live_policy.yaml"
_DEFAULT = {"auto_start": True, "auto_live_orders": False, "overrides": {}}
_NO_START = {"archived", "planned", "paused", "stopped"}


def load_policy() -> dict:
    pol = dict(_DEFAULT)
    try:
        import yaml
        if os.path.isfile(POLICY_PATH):
            d = yaml.safe_load(open(POLICY_PATH, encoding="utf-8")) or {}
            for k in ("auto_start", "auto_live_orders"):
                if k in d:
                    pol[k] = bool(d[k])
            pol["overrides"] = {str(k): str(v).lower() for k, v in (d.get("overrides") or {}).items()}
    except Exception:
        pass
    return pol


def _effective_status(name: str, cfg: dict, policy: dict) -> str:
    ov = (policy.get("overrides") or {}).get(name)
    return (ov or cfg.get("status") or "paper").lower()


def _live_gate(name: str, cfg: dict) -> str | None:
    """None if a status=live strategy may place real orders, else the blocking reason."""
    try:
        from src.api import telemetry
        if telemetry.is_demoted(name):
            return "telemetry-demoted"
    except Exception:
        pass
    cap = cfg.get("capital_allocated_rs", 0)
    if not cap or cap <= 0:
        return "no capital allocated"
    return None


def decide(name: str, cfg: dict, policy: dict) -> dict:
    """How a strategy should come up on auto-start.
    Returns {action: 'start'|'skip', mode: 'live'|'paper'|None, reason, degraded}."""
    status = _effective_status(name, cfg, policy)
    if status in _NO_START:
        return {"action": "skip", "mode": None, "reason": f"operator/status={status}", "degraded": False}
    # Wants real orders only if explicitly live AND the global switch is on.
    if status == "live" and policy.get("auto_live_orders"):
        gate = _live_gate(name, cfg)
        if gate is None:
            return {"action": "start", "mode": "live", "reason": "live (risk gates passed)", "degraded": False}
        return {"action": "start", "mode": "paper",
                "reason": f"live blocked ({gate}) → started PAPER + alert", "degraded": True}
    # Default active mode = paper (analyses continuously, no real orders).
    degraded = (status == "live" and not policy.get("auto_live_orders"))
    return {"action": "start", "mode": "paper",
            "reason": ("live status but auto_live_orders OFF → PAPER" if degraded
                       else f"active (paper) status={status}"),
            "degraded": False}


# ── Precise status taxonomy — replaces the ambiguous single "Blind" ──────────
def classify_status(name: str, cfg: dict, runtime: dict, cycles: int,
                    policy: dict, dominant_reason: str = "") -> tuple[str, str]:
    """Return (CLASS, human label). Never returns a bare 'Blind' — it pinpoints
    whether a strategy is intentionally paused, warming, data-starved, telemetry-
    broken, crashed, inactive, or genuinely active-and-standing-aside."""
    status = _effective_status(name, cfg, policy)
    running = bool(runtime.get("running"))
    state = (runtime.get("state") or "").upper()
    # Operator-intentional states first (never a failure).
    if status == "archived":
        return "ARCHIVED", "Archived (intentional)"
    if status == "paused":
        return "PAUSED_BY_OPERATOR", "Paused by operator (intentional)"
    if status == "stopped":
        return "STOPPED_BY_OPERATOR", "Stopped by operator (intentional)"
    # If it emitted cycles, it WAS active that session (true for live or historical).
    if cycles > 0:
        dr = (dominant_reason or "").lower()
        if "warming" in dr or "insufficient" in dr:
            return "WARMING_UP", "Warming up — building enough bars to analyse"
        if "stale" in dr or "data" in dr or "feed" in dr:
            return "DATA_UNAVAILABLE", "Data unavailable / stale feed this window"
        return "ACTIVE_NO_TRADE", "Active — analysing, no qualifying setup"
    # No cycles at all — pinpoint why (never a bare 'Blind').
    if state in ("ERROR", "CRASHED"):
        return "RUNTIME_FAILURE", (runtime.get("error") or "Runtime failure")[:120]
    if running:
        return "TELEMETRY_BROKEN", "Running but emitting NO analysis — telemetry broken (self-recovery will restart)"
    return "INACTIVE_NOT_STARTED", "Inactive — was not started this session"
