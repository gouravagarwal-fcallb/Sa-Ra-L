"""
Scoreboard (Phase 2, additive)
══════════════════════════════
Makes the trust memory + benchmark history visible: rolling trust per strategy
(↑/↓ over recent sessions) and a book-vs-benchmark series. Read-only — it only
reads what the EOD commit job persisted (logs/trust/*.jsonl, logs/eod/*.json).
Empty until at least one EOD commit has run.
"""
from __future__ import annotations

import os
import glob
import json


def _read_jsonl(path: str) -> list:
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


def build_scoreboard(window: int = 20) -> dict:
    # ── per-strategy rolling trust ───────────────────────────────────────────
    strategies = []
    for path in sorted(glob.glob("logs/trust/*.jsonl")):
        name = os.path.basename(path)[:-len(".jsonl")]
        recs = _read_jsonl(path)
        if not recs:
            continue
        recs = recs[-window:]
        latest = recs[-1]
        strategies.append({
            "name": name,
            "trust_score": latest.get("trust_score"),
            "trust_delta": latest.get("trust_delta"),
            "trust_weight": latest.get("trust_weight"),
            "classification": latest.get("classification"),
            "sessions": len(recs),
            "history": [{"date": r.get("date"), "trust_score": r.get("trust_score")} for r in recs],
        })
    strategies.sort(key=lambda s: -(s["trust_score"] or 0))

    # ── book vs benchmark series from EOD snapshots ──────────────────────────
    book = []
    for path in sorted(glob.glob("logs/eod/*.json")):
        snap = None
        try:
            snap = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        summ = snap.get("summary") or {}
        bm = ((snap.get("benchmarks") or {}).get("indices") or {}).get("NIFTY") or {}
        book.append({
            "date": snap.get("date"),
            "net_pnl": summ.get("net_pnl"),
            "win_rate": summ.get("win_rate"),
            "index_buy_hold_pct": bm.get("buy_hold_pct") if bm.get("available") else None,
            "verifiable": bool((snap.get("scoring") or {}).get("verifiable")),
        })
    book.sort(key=lambda b: b["date"] or "")

    return {
        "window": window,
        "strategies": strategies,
        "book": book,
        "note": ("No committed sessions yet — run the EOD commit (Closure Report → "
                 "'Commit trust (EOD)') on a trading day to populate this."
                 if not strategies and not book else None),
    }
