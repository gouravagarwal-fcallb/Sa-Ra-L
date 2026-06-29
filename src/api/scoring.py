"""
Analysis-vs-Actual scoring engine (redesign Part 4)
═══════════════════════════════════════════════════
The closure report can say a strategy "stood aside 361×" but not whether standing
aside was RIGHT. This engine closes that gap: for every analysis/no-trade cycle it
replays what the index actually did over the next horizon and grades the decision.

Inputs already persisted by the platform:
  • per-strategy cycle trail   logs/dashboard/<name>_<date>.jsonl  ({ts, category, message})
  • actual index path          one Kite fetch per index per session (NOT per cycle)

No look-ahead: a cycle at time T is scored ONLY on bars at/after T.

Verdict taxonomy (Part 4): Correct stand-aside · Correct caution · Missed opportunity
· Over-filtered · Premature rejection · Correct invalidation · Inconclusive ·
Blind / unverifiable.
"""
from __future__ import annotations

import os
import json
import glob
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from src.api.closure_report import classify_no_trade

IST = timezone(timedelta(hours=5, minutes=30))
_DASH_DIR = "logs/dashboard"

# Thresholds (auditable, configurable). Index intraday, 30-min horizon.
NOISE_PCT = 0.12      # below this the market "did nothing" → standing aside is right
OPP_PCT = 0.30        # a clean move of this size is a real opportunity
HORIZON_MIN = 30

# reason buckets that are FILTERS (a missed move through a filter = over-filtering)
_FILTER_REASONS = {"Volatility filter", "Trend filter", "Liquidity filter",
                   "Regime mismatch / no edge"}

_SCORE = {
    "Correct stand-aside": 1.0, "Correct caution": 0.6, "Correct invalidation": 0.8,
    "Missed opportunity": -1.0, "Over-filtered": -1.0, "Premature rejection": -0.6,
    "Inconclusive": 0.0, "Blind / unverifiable": 0.0,
}


def _mod(tstr: str) -> int | None:
    """minute-of-day from 'HH:MM:SS' or 'YYYY-MM-DD HH:MM[:SS]'."""
    try:
        s = tstr.strip()
        if " " in s:
            s = s.split(" ")[1]
        h, m = s.split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _default_bars(index: str, day: str) -> list:
    """One Kite fetch of the whole session for `index` on `day` (graceful []→ scoring
    degrades to 'unverifiable')."""
    try:
        from src.data import kite_historical
        if not kite_historical.is_enabled():
            return []
        d = datetime.strptime(day, "%Y-%m-%d")
        frm = d.replace(hour=9, minute=10, tzinfo=IST)
        to = d.replace(hour=15, minute=35, tzinfo=IST)
        return kite_historical.fetch_range(index, frm, to, "1m") or []
    except Exception:
        return []


class _Path:
    """Indexed session price path for fast at/after lookups + window extrema."""

    def __init__(self, bars: list):
        self.rows = []
        for b in bars:
            mo = _mod(b.get("t", ""))
            c = b.get("c")
            if mo is not None and c is not None:
                self.rows.append((mo, float(c), float(b.get("h", c)), float(b.get("l", c))))
        self.rows.sort(key=lambda r: r[0])

    def ok(self) -> bool:
        return len(self.rows) > 20

    def _at(self, mo: int):
        for r in self.rows:
            if r[0] >= mo:
                return r
        return None

    def forward(self, mo: int, horizon: int):
        ref = self._at(mo)
        if not ref:
            return None
        end = mo + horizon
        seg = [r for r in self.rows if mo <= r[0] <= end]
        if len(seg) < 2:
            return None
        ref_c = ref[1]
        last_c = seg[-1][1]
        hi = max(r[2] for r in seg)
        lo = min(r[3] for r in seg)
        return {
            "fwd_move_pct": round((last_c - ref_c) / ref_c * 100, 3),
            "mfe_pct": round((hi - ref_c) / ref_c * 100, 3),
            "mae_pct": round((ref_c - lo) / ref_c * 100, 3),
        }


def _label(reason: str, fwd: dict | None) -> tuple[str, float]:
    if fwd is None:
        return "Blind / unverifiable", 0.0
    move = abs(fwd["fwd_move_pct"])
    mfe, mae = fwd["mfe_pct"], fwd["mae_pct"]
    is_filter = reason in _FILTER_REASONS
    is_warming = "Warming" in (reason or "")
    clean_move = mfe >= OPP_PCT and mae <= OPP_PCT * 0.6
    if move < NOISE_PCT and mfe < OPP_PCT:
        lab = "Correct stand-aside"
    elif clean_move and mfe >= OPP_PCT:
        lab = ("Premature rejection" if is_warming
               else "Over-filtered" if is_filter else "Missed opportunity")
    elif mae >= OPP_PCT and move < NOISE_PCT:
        lab = "Correct caution"      # would have drawn down, ended flat
    elif mae >= OPP_PCT and move < 0 and not is_filter:
        lab = "Correct invalidation"
    else:
        lab = "Inconclusive"
    return lab, _SCORE.get(lab, 0.0)


def _index_for(cfg: dict) -> str:
    insts = [str(i).upper() for i in (cfg.get("instruments") or [])]
    if "SENSEX" in insts and "NIFTY" not in insts:
        return "SENSEX"
    if any("USD" in i or "INR" in i for i in insts):
        return ""        # FX — not scored against an index here
    return "NIFTY"


def _read_logs(name: str, day: str) -> list:
    p = os.path.join(_DASH_DIR, f"{name}_{day}.jsonl")
    out = []
    try:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        out.append(json.loads(ln))
    except Exception:
        pass
    return out


def score_strategy(name: str, cfg: dict, day: str, paths: dict, bars_provider=None) -> dict:
    """Grade one strategy's cycles for `day`. `paths` caches _Path per index."""
    index = _index_for(cfg)
    logs = _read_logs(name, day)
    cycles = [e for e in logs if (e.get("category") or "").upper() in ("ANALYSIS", "SIGNAL", "ANALYSE")]
    counts = defaultdict(int)
    reason_labels = defaultdict(lambda: defaultdict(int))   # reason -> label -> n
    scored, total_score = 0, 0.0

    path = None
    if index:
        if index not in paths:
            bars = (bars_provider or _default_bars)(index, day)
            paths[index] = _Path(bars)
        path = paths[index]

    for e in cycles:
        mo = _mod(e.get("ts", ""))
        rc = classify_no_trade(e.get("message", ""))
        reason = rc[1] if rc else "No setup found"
        fwd = path.forward(mo, HORIZON_MIN) if (path and path.ok() and mo is not None) else None
        lab, sc = _label(reason, fwd)
        counts[lab] += 1
        reason_labels[reason][lab] += 1
        if lab != "Blind / unverifiable":
            scored += 1
            total_score += sc

    correct = counts["Correct stand-aside"] + counts["Correct caution"] + counts["Correct invalidation"]
    missed = counts["Missed opportunity"] + counts["Premature rejection"]
    over = counts["Over-filtered"]
    denom = scored or 1
    return {
        "strategy": name, "index": index or "—", "cycles": len(cycles), "scored": scored,
        "labels": dict(counts),
        "no_trade_correctness": round(correct / denom, 3) if scored else None,
        "missed_opportunity_rate": round(missed / denom, 3) if scored else None,
        "over_filtered_rate": round(over / denom, 3) if scored else None,
        "avg_score": round(total_score / denom, 3) if scored else None,
        "reason_labels": {k: dict(v) for k, v in reason_labels.items()},
        "verifiable": bool(path and path.ok()),
    }


def score_session(registry: dict, day: str, bars_provider=None) -> dict:
    """Grade every strategy for `day`. Returns per-strategy + a filter-audit roll-up."""
    strategies_cfg = (registry or {}).get("strategies", registry or {})
    paths: dict = {}
    per = []
    for name, cfg in strategies_cfg.items():
        if isinstance(cfg, dict):
            per.append(score_strategy(name, cfg, day, paths, bars_provider))

    # over-filtering diagnostics (Part 6): per reason, justified vs unjustified
    filt = defaultdict(lambda: {"fires": 0, "justified": 0, "unjustified": 0})
    for s in per:
        for reason, labs in s["reason_labels"].items():
            for lab, n in labs.items():
                if lab == "Blind / unverifiable":
                    continue
                filt[reason]["fires"] += n
                if lab in ("Correct stand-aside", "Correct caution", "Correct invalidation"):
                    filt[reason]["justified"] += n
                elif lab in ("Over-filtered", "Missed opportunity", "Premature rejection"):
                    filt[reason]["unjustified"] += n
    filter_audit = []
    for reason, d in sorted(filt.items(), key=lambda kv: -kv[1]["unjustified"]):
        tot = d["justified"] + d["unjustified"]
        filter_audit.append({
            "reason": reason, "fires": d["fires"], "justified": d["justified"],
            "unjustified": d["unjustified"],
            "justification_rate": round(d["justified"] / tot, 3) if tot else None,
        })

    verifiable = any(s["verifiable"] for s in per)
    return {
        "date": day, "verifiable": verifiable,
        "thresholds": {"noise_pct": NOISE_PCT, "opp_pct": OPP_PCT, "horizon_min": HORIZON_MIN},
        "per_strategy": {s["strategy"]: s for s in per},
        "filter_audit": filter_audit,
        "note": ("Graded against replayed index bars." if verifiable else
                 "Index actuals unavailable (no Kite/history for this day) — cycles marked unverifiable."),
    }
