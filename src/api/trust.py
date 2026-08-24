"""
Strategy trust engine with memory (redesign Part 5)
═══════════════════════════════════════════════════
Turns per-session scores into a slow-moving trust level per strategy, so the
platform remembers which strategies have earned attention and which are blind or
over-filtering — without whipsawing on a single day (EWMA, α=0.3).

Persisted to logs/trust/<strategy>.jsonl (one record per scored session). Advisory
only: trust never auto-arms live; it informs ordering, capital-attention and the
demote-only governance in the closure report.
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
_DIR = "logs/trust"
ALPHA = 0.3


def _path(strategy: str) -> str:
    return os.path.join(_DIR, f"{strategy}.jsonl")


def _read(strategy: str) -> list:
    out = []
    try:
        p = _path(strategy)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        out.append(json.loads(ln))
    except Exception:
        pass
    return out


def _prior(strategy: str, day: str):
    recs = [r for r in _read(strategy) if r.get("date", "") < day]
    return recs[-1] if recs else None


def session_quality(m: dict):
    """0–100 quality for ONE session, or None to freeze trust (nothing to learn).

    Freeze (don't move trust) whenever there is no scoreable evidence — an idle or
    unverifiable strategy must NOT be punished. Telemetry/blind handling lives in
    the readiness gate + demotion, not in this trust math."""
    if (m.get("scored", 0) == 0) and (m.get("trades", 0) == 0):
        return None
    q = 50.0
    if m.get("no_trade_correctness") is not None:
        q = 30 + 60 * m["no_trade_correctness"]      # 30..90 from stand-aside correctness
    q -= 25 * (m.get("over_filtered_rate") or 0)
    q -= 20 * (m.get("missed_opportunity_rate") or 0)
    if m.get("trades", 0) > 0:
        q += 10 if (m.get("expectancy") or 0) > 0 else -10
    return max(0.0, min(100.0, q))


def _classify(trust: float, m: dict) -> str:
    if not m.get("telemetry_ok", True):
        return "Blind — logging failure"
    of = m.get("over_filtered_rate") or 0
    mr = m.get("missed_opportunity_rate") or 0
    if trust >= 70:
        return "Trusted"
    if of >= 0.4:
        return "Useful but over-filtered"
    if mr >= 0.4:
        return "Needs recalibration"
    if trust >= 45:
        return "Useful — conservative"
    if trust < 28:
        return "Disable pending review"
    return "Neutral / inconclusive"


def compute_session(strategy: str, day: str, m: dict) -> dict:
    """Provisional trust for a session WITHOUT persisting — safe to call on every
    report view (the report polls frequently; we must not fold EWMA each time)."""
    prior = _prior(strategy, day)
    prior_trust = prior.get("trust_score", 50.0) if prior else 50.0
    sq = session_quality(m)
    new_trust = prior_trust if sq is None else round(ALPHA * sq + (1 - ALPHA) * prior_trust, 1)
    return {
        "strategy": strategy, "date": day,
        "session_quality": sq, "trust_score": new_trust,
        "trust_delta": round(new_trust - prior_trust, 1),
        "classification": _classify(new_trust, m),
        "metrics": m, "generated_at": datetime.now(IST).isoformat(),
    }


def commit_session(strategy: str, day: str, m: dict) -> dict:
    """Persist one session's trust (EOD batch). Idempotent per (strategy, day):
    if a record for `day` already exists it is returned, not duplicated."""
    existing = [r for r in _read(strategy) if r.get("date") == day]
    if existing:
        return existing[-1]
    rec = compute_session(strategy, day, m)
    try:
        os.makedirs(_DIR, exist_ok=True)
        with open(_path(strategy), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass
    return rec


def trust_weight(trust_score: float) -> float:
    """Capital-attention multiplier 0..1 from trust (advisory; human still arms live)."""
    return round(max(0.0, min(1.0, (trust_score - 20) / 60)), 2)


def history(strategy: str, n: int = 20) -> list:
    return _read(strategy)[-n:]
