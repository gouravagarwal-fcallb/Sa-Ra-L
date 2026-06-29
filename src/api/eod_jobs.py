"""
End-of-day commit job (Phase 2)
═══════════════════════════════
The closure report computes trust *provisionally* on every view (read-only). This
job COMMITS it once per day so trust gains real cross-session memory (EWMA), and
snapshots the graded session to disk for the future scoreboard / weekly review.

It is intentionally conservative and demote-SAFE:
  • commits one trust record per (strategy, day) — idempotent, never double-folds;
  • snapshots scoring + benchmark + telemetry to logs/eod/<day>.json;
  • RECOMMENDS demotion for blind strategies but does NOT auto-demote (a strategy
    that simply wasn't started must not be punished — demotion stays an explicit
    operator action via the telemetry endpoints).

Trigger manually (`POST /api/eod/run`) or wire to a 15:50 IST scheduler later.
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
_EOD_DIR = "logs/eod"


def run_eod(registry: dict, multi=None, runner=None, day: str | None = None) -> dict:
    from src.api.closure_report import build_closure_report
    from src.api.trust import commit_session, trust_weight

    day = day or datetime.now(IST).date().isoformat()
    rep = build_closure_report(registry, multi, runner, day)

    committed, frozen = [], []
    for b in rep.get("per_strategy", []):
        sc = b.get("scored") or {}
        metrics = {
            "telemetry_ok": b.get("telemetry_ok", True),
            "scored": sc.get("scored", 0),
            "trades": b.get("trades", 0),
            "expectancy": (b["pnl"] / b["trades"]) if b.get("trades") else 0,
            "no_trade_correctness": sc.get("no_trade_correctness"),
            "over_filtered_rate": sc.get("over_filtered_rate"),
            "missed_opportunity_rate": sc.get("missed_opportunity_rate"),
        }
        rec = commit_session(b["name"], day, metrics)
        rec["trust_weight"] = trust_weight(rec["trust_score"])
        (frozen if rec.get("session_quality") is None else committed).append(
            {"strategy": b["name"], "trust_score": rec["trust_score"],
             "trust_delta": rec["trust_delta"], "classification": rec["classification"],
             "trust_weight": rec["trust_weight"]})

    blind = rep.get("telemetry", {}).get("blind", [])
    snapshot = {
        "date": day, "committed_at": datetime.now(IST).isoformat(),
        "summary": rep.get("summary"), "benchmarks": rep.get("benchmarks"),
        "scoring": rep.get("scoring"), "telemetry": rep.get("telemetry"),
        "trust_committed": committed, "trust_frozen": [f["strategy"] for f in frozen],
        "blind_recommend_demote": blind,
    }
    try:
        os.makedirs(_EOD_DIR, exist_ok=True)
        with open(os.path.join(_EOD_DIR, f"{day}.json"), "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, default=str)
    except Exception:
        pass

    return {
        "date": day,
        "verifiable": bool(rep.get("scoring", {}).get("verifiable")),
        "trust_committed": len(committed), "trust_frozen": len(frozen),
        "blind_recommend_demote": blind,
        "note": ("Trust committed for verifiable strategies; blind strategies are "
                 "RECOMMENDED for demotion, not auto-demoted. Use the telemetry "
                 "demote endpoint to enforce."),
        "detail": committed,
    }


def load_eod(day: str) -> dict | None:
    p = os.path.join(_EOD_DIR, f"{day}.json")
    try:
        if os.path.isfile(p):
            return json.load(open(p, encoding="utf-8"))
    except Exception:
        pass
    return None
