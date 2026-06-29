"""
Weekly / rolling-N review (Phase 2, additive · observe-only)
════════════════════════════════════════════════════════════
Aggregates the daily EOD snapshots (logs/eod/*.json) and the per-strategy trust
history (logs/trust/*.jsonl) into a rolling window review: how trust moved, how the
book did vs the index, which filters keep over-blocking, and which strategies had
recurring telemetry failures. Read-only — it only reads what the EOD commit job
persisted; it never changes any strategy. Empty until EOD commits exist.
"""
from __future__ import annotations

import os
import glob
import json
from collections import defaultdict


def _read_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path):
    out = []
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        out.append(json.loads(ln))
    except Exception:
        pass
    return out


def build_weekly_review(sessions: int = 20) -> dict:
    # ── EOD snapshots over the window ────────────────────────────────────────
    snaps = []
    for p in sorted(glob.glob("logs/eod/*.json")):
        s = _read_json(p)
        if s:
            snaps.append(s)
    snaps = snaps[-sessions:]

    book_pnl = 0.0
    win_rates, beat_index, verifiable_days = [], 0, 0
    filt = defaultdict(lambda: {"fires": 0, "unjustified": 0})
    telem_fail_counts = defaultdict(int)
    for s in snaps:
        summ = s.get("summary") or {}
        npnl = summ.get("net_pnl") or 0
        book_pnl += npnl
        if summ.get("win_rate") is not None:
            win_rates.append(summ["win_rate"])
        bm = ((s.get("benchmarks") or {}).get("indices") or {}).get("NIFTY") or {}
        if bm.get("available"):
            idx = bm.get("buy_hold_pct") or 0
            # crude: book made money on a down/flat index, or beat a small up move
            if npnl > 0 and idx <= 0.2:
                beat_index += 1
        if (s.get("scoring") or {}).get("verifiable"):
            verifiable_days += 1
        for f in (s.get("scoring") or {}).get("filter_audit", []) or []:
            filt[f["reason"]]["fires"] += f.get("fires", 0)
            filt[f["reason"]]["unjustified"] += f.get("unjustified", 0)
        for name in (s.get("telemetry") or {}).get("blind", []) or []:
            telem_fail_counts[name] += 1

    filter_trends = sorted(
        [{"reason": k, **v} for k, v in filt.items()],
        key=lambda x: -x["unjustified"])

    # ── Per-strategy trust trend over the window ─────────────────────────────
    per = []
    for path in sorted(glob.glob("logs/trust/*.jsonl")):
        name = os.path.basename(path)[:-len(".jsonl")]
        recs = _read_jsonl(path)[-sessions:]
        if not recs:
            continue
        ntc = [r["metrics"].get("no_trade_correctness") for r in recs
               if (r.get("metrics") or {}).get("no_trade_correctness") is not None]
        ofr = [r["metrics"].get("over_filtered_rate") for r in recs
               if (r.get("metrics") or {}).get("over_filtered_rate") is not None]
        per.append({
            "name": name,
            "trust_start": recs[0].get("trust_score"),
            "trust_now": recs[-1].get("trust_score"),
            "trust_change": round((recs[-1].get("trust_score") or 0) - (recs[0].get("trust_score") or 0), 1),
            "classification": recs[-1].get("classification"),
            "sessions": len(recs),
            "avg_no_trade_correctness": round(sum(ntc) / len(ntc), 2) if ntc else None,
            "avg_over_filtered_rate": round(sum(ofr) / len(ofr), 2) if ofr else None,
            "telemetry_failures": telem_fail_counts.get(name, 0),
        })
    per.sort(key=lambda s: -(s["trust_now"] or 0))

    # ── Generated lessons ────────────────────────────────────────────────────
    lessons = []
    improving = [p["name"] for p in per if (p["trust_change"] or 0) >= 5]
    declining = [p["name"] for p in per if (p["trust_change"] or 0) <= -5]
    recurring_telem = [n for n, c in telem_fail_counts.items() if c >= 2]
    chronic_filters = [f["reason"] for f in filter_trends
                       if f["unjustified"] >= 10 and f["unjustified"] >= f["fires"] * 0.4]
    if improving:
        lessons.append("Trust rising: " + ", ".join(improving) + ".")
    if declining:
        lessons.append("Trust falling — review: " + ", ".join(declining) + ".")
    if recurring_telem:
        lessons.append("⚠ Recurring telemetry failure (≥2 sessions): " + ", ".join(recurring_telem)
                       + " — fix analysis emission.")
    if chronic_filters:
        lessons.append("Filters over-blocking across the window: " + ", ".join(chronic_filters)
                       + " — candidates for threshold review (operator-approved only).")

    return {
        "sessions_requested": sessions,
        "sessions_found": len(snaps),
        "dates": [s.get("date") for s in snaps],
        "book": {
            "net_pnl": round(book_pnl, 2),
            "avg_win_rate": round(sum(win_rates) / len(win_rates), 1) if win_rates else None,
            "verifiable_days": verifiable_days,
            "days_beat_index": beat_index,
        },
        "per_strategy": per,
        "filter_trends": filter_trends,
        "lessons": lessons,
        "note": ("No EOD snapshots yet — run the EOD commit on trading days to build "
                 "the rolling review." if not snaps else None),
    }
