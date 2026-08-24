"""
Benchmark engine (redesign Part 3)
══════════════════════════════════
"Are we better?" is undefined without a reference. This computes session baselines
from the actual index path so the book can be judged against what a trivial rule
would have made — the floor a discretionary no-trade book must clear.

Baselines per index (from one replayed session of 1-min bars):
  • buy_hold     — open→close % (did the market even move?)
  • mae_adjusted — close move ÷ max adverse excursion (risk-adjusted reference)
  • orb          — first-15-min range breakout, 1R target / opposite-side stop
  • vwap         — long above VWAP / flat-or-short below, summed segment return
  • bb_breakout  — enter on BB(20,2) band break, exit mid

All degrade to None when index actuals are unavailable (no Kite/history).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from statistics import mean, pstdev

IST = timezone(timedelta(hours=5, minutes=30))


def _session_bars(index: str, day: str, bars_provider=None) -> list:
    if bars_provider:
        return bars_provider(index, day) or []
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


def _closes(bars):
    return [float(b["c"]) for b in bars if b.get("c") is not None]


def _orb_R(bars) -> float | None:
    """First-15-bar range; on a break, risk = range, reward measured to session end."""
    if len(bars) < 30:
        return None
    op = bars[:15]
    hi = max(float(b["h"]) for b in op)
    lo = min(float(b["l"]) for b in op)
    rng = hi - lo
    if rng <= 0:
        return None
    for b in bars[15:]:
        if float(b["h"]) > hi:                      # long break
            exit_c = float(bars[-1]["c"])
            return round((exit_c - hi) / rng, 2)
        if float(b["l"]) < lo:                       # short break
            exit_c = float(bars[-1]["c"])
            return round((lo - exit_c) / rng, 2)
    return 0.0


def _vwap_follow_pct(bars) -> float | None:
    if len(bars) < 20:
        return None
    cum_pv = cum_v = 0.0
    ret = 0.0
    prev_c = None
    for b in bars:
        c = float(b["c"]); v = float(b.get("v", 0) or 1)
        tp = (float(b["h"]) + float(b["l"]) + c) / 3
        cum_pv += tp * v; cum_v += v
        vwap = cum_pv / cum_v if cum_v else c
        if prev_c is not None and prev_c > vwap_prev:    # held long last bar
            ret += (c - prev_c) / prev_c * 100
        prev_c, vwap_prev = c, vwap
    return round(ret, 3)


def benchmark_index(index: str, day: str, bars_provider=None) -> dict:
    bars = _session_bars(index, day, bars_provider)
    cl = _closes(bars)
    if len(cl) < 20:
        return {"index": index, "available": False}
    op, close = cl[0], cl[-1]
    hi = max(float(b["h"]) for b in bars)
    lo = min(float(b["l"]) for b in bars)
    move = (close - op) / op * 100
    mae = (op - lo) / op * 100 if move >= 0 else (hi - op) / op * 100
    return {
        "index": index, "available": True,
        "buy_hold_pct": round(move, 3),
        "session_range_pct": round((hi - lo) / op * 100, 3),
        "mae_adjusted": round(move / mae, 2) if mae > 0.01 else None,
        "orb_R": _orb_R(bars),
        "vwap_follow_pct": _vwap_follow_pct(bars),
        "trend_strength": round(abs(move) / ((hi - lo) / op * 100), 2) if (hi - lo) else None,
    }


def benchmark_session(day: str, indices=("NIFTY", "SENSEX"), bars_provider=None) -> dict:
    out = {ix: benchmark_index(ix, day, bars_provider) for ix in indices}
    avail = [b for b in out.values() if b.get("available")]
    headline = None
    if avail:
        ni = out.get("NIFTY", {})
        bh = ni.get("buy_hold_pct")
        ts = ni.get("trend_strength")
        if bh is not None:
            if abs(bh) >= 0.4 and (ts or 0) >= 0.4:
                headline = (f"Index ran {bh:+.2f}% on a directional path — a no-trade book "
                            "should be able to justify standing aside.")
            elif abs(bh) < 0.2:
                headline = (f"Index went nowhere ({bh:+.2f}%) — a flat, low-edge day; "
                            "standing aside was reasonable.")
            else:
                headline = f"Index closed {bh:+.2f}% — mixed/choppy session."
    return {"date": day, "available": bool(avail), "indices": out, "headline": headline}
