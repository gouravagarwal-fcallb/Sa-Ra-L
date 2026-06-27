"""
OI Analyzer — PASHUPATASTRA measurement loop, step 2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reads the recorder's JSONL (oi_recorder.py) and MEASURES the thing the backtest
could only assume: when the Seller-Trap Score fires, how often does the option we
would have bought actually reach 2× (the bread-and-butter) before end of day?

Output = the EDGE TABLE: 2×-hit-rate (and avg peak multiple) by score bucket.
That hit-rate is the real, measured `filter_skill` — feed it into the sizing
engine (V2_REENGINEERED.md §3). This is how the strategy becomes self-calibrating.

    python -m src.brahmastra.options.oi_analyze data/oi_recordings/NIFTY
    python -m src.brahmastra.options.oi_analyze data/oi_recordings/NIFTY/2026-06-30.jsonl --target 2.0
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime


def _ltp_at(rec, strike, side):
    for r in rec.get("strikes", []):
        if r["k"] == strike:
            return r["ce_ltp"] if side == "CE" else r["pe_ltp"]
    return None


def load_jsonl(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    recs.sort(key=lambda r: r["ts"])
    return recs


def measure_day(recs, target_mult=2.0, min_score=75.0, cooldown_min=10):
    """Evaluate every trap fire: did the candidate option reach target_mult before EOD?"""
    fires = []
    last_fire_ts = {}                       # (strike,side) -> datetime of last counted fire
    for i, rec in enumerate(recs):
        t = rec.get("trap") or {}
        side, cand, score = t.get("side"), t.get("candidate"), t.get("score", 0)
        if not side or cand is None or score < min_score:
            continue
        ts = datetime.fromisoformat(rec["ts"])
        key = (cand, side)
        if key in last_fire_ts and (ts - last_fire_ts[key]).total_seconds() < cooldown_min * 60:
            continue                        # same squeeze — don't double-count
        entry = _ltp_at(rec, cand, side)
        if not entry or entry <= 0:
            continue
        last_fire_ts[key] = ts
        peak = entry
        hit = False
        for rec2 in recs[i + 1:]:
            ltp = _ltp_at(rec2, cand, side)
            if ltp is None:
                continue
            peak = max(peak, ltp)
            if ltp >= target_mult * entry:
                hit = True
                break
        fires.append({"ts": rec["ts"], "side": side, "strike": cand, "score": score,
                      "entry_ltp": entry, "peak_mult": round(peak / entry, 2), "hit_2x": hit})
    return fires


def summarize(fires, target_mult=2.0):
    # v1 trap score is uncalibrated; buckets span its real range so the edge table is
    # useful NOW (production threshold ~75 is a starting point, to be set from this data).
    buckets = [(45, 60), (60, 70), (70, 80), (80, 101)]
    lines = [f"  score bucket | fires | {target_mult:.0f}x-hit-rate | avg peak mult"]
    lines.append("  " + "-" * 52)
    for lo, hi in buckets:
        b = [f for f in fires if lo <= f["score"] < hi]
        if not b:
            lines.append(f"  {lo:>3}-{hi-1:<3}     |   0   |     n/a       |   n/a")
            continue
        hr = 100 * sum(1 for f in b if f["hit_2x"]) / len(b)
        ap = sum(f["peak_mult"] for f in b) / len(b)
        lines.append(f"  {lo:>3}-{hi-1:<3}     |  {len(b):>3}  |    {hr:5.1f}%     |   {ap:.2f}x")
    if fires:
        hr_all = 100 * sum(1 for f in fires if f["hit_2x"]) / len(fires)
        lines.append("  " + "-" * 52)
        lines.append(f"  ALL fires    |  {len(fires):>3}  |    {hr_all:5.1f}%     | "
                     f"  {sum(f['peak_mult'] for f in fires)/len(fires):.2f}x")
        lines.append("")
        lines.append(f"  => MEASURED 2x-hit-rate = the real 'filter_skill' for sizing.")
    else:
        lines.append("  (no fires at/above min_score — record more expiries or lower threshold)")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a .jsonl file OR a directory of them")
    ap.add_argument("--target", type=float, default=2.0)
    ap.add_argument("--min-score", type=float, default=75.0)
    ap.add_argument("--cooldown-min", type=int, default=10)
    args = ap.parse_args(argv)

    files = ([args.path] if args.path.endswith(".jsonl")
             else sorted(glob.glob(os.path.join(args.path, "*.jsonl"))))
    if not files:
        print(f"no .jsonl files at {args.path}")
        return 1

    all_fires = []
    for fp in files:
        recs = load_jsonl(fp)
        fires = measure_day(recs, args.target, args.min_score, args.cooldown_min)
        all_fires.extend(fires)
        print(f"  {os.path.basename(fp)}: {len(recs)} snapshots, {len(fires)} trap fires")
    print()
    print(f"EDGE TABLE — {len(files)} day(s), target {args.target:.0f}x, min_score {args.min_score}")
    print(summarize(all_fires, args.target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
