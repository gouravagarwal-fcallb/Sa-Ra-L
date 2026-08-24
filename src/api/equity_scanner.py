"""
Intraday Equity Scanner — ranks a universe of liquid NSE stocks by today's
intraday momentum setup (Opening-Range Breakout + VWAP + volume + RSI + % move).

Design principles (same doctrine as the rest of Sa-Ra-L):
  • REAL signals only. Every field is computed from actual intraday OHLCV bars.
  • Honest empty-state. If live data is unavailable (no Kite, no network, market
    closed), the scan returns an explicit status="no_data" with an empty watchlist —
    it NEVER fabricates prices or signals.
  • The scoring is pure (score_symbol operates on a bar list), so it is unit-testable
    offline without any network.

Data source: yfinance intraday bars (SYMBOL.NS), reusing the backfill fetcher. A
Kite-quote source can be swapped into `_today_bars` later for lower latency.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# Where each day's scan snapshot is persisted so we can grade it against the
# next trading day's real move. One file per date, overwritten by the latest
# scan of that day (the most-informed read of the session).
_SNAP_DIR = os.path.join("logs", "equity_scans")

# A curated, liquid NSE intraday universe (large-caps + active F&O names). Editable.
DEFAULT_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "AXISBANK",
    "KOTAKBANK", "LT", "ITC", "HINDUNILVR", "BHARTIARTL", "BAJFINANCE", "MARUTI",
    "TATAMOTORS", "TATASTEEL", "SUNPHARMA", "WIPRO", "ADANIENT", "HCLTECH",
]

_MIN_BARS = 6          # need at least the opening range + a few bars to score


# ── pure indicators ───────────────────────────────────────────────────────────
def _rsi(closes: list[float], n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[-n:]) / n
    al = sum(losses[-n:]) / n
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 1)


def _vwap(bars: list[dict]) -> float:
    num = den = 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        num += tp * b["v"]
        den += b["v"]
    return round(num / den, 2) if den else round(bars[-1]["c"], 2)


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


# ── scoring (pure — operates on today's bars) ─────────────────────────────────
def score_symbol(symbol: str, bars: list[dict], prev_close: float | None = None) -> dict | None:
    """Compute intraday signals + a composite momentum score from today's bars.
    Returns None when there aren't enough bars to judge. Score is signed:
    positive = long bias, negative = short bias; magnitude = conviction (0-100)."""
    if not bars or len(bars) < _MIN_BARS:
        return None
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    vols = [b["v"] for b in bars]
    ltp = closes[-1]
    day_open = bars[0]["o"]
    ref = prev_close or day_open
    pct = round((ltp - ref) / ref * 100, 2) if ref else 0.0

    # Opening range = first 3 five-min bars (~15 min)
    orb_hi = max(highs[:3])
    orb_lo = min(lows[:3])
    if ltp > orb_hi:
        orb = "BREAKOUT_UP"
    elif ltp < orb_lo:
        orb = "BREAKDOWN"
    else:
        orb = "INSIDE"

    vwap = _vwap(bars)
    vwap_pos = "ABOVE" if ltp >= vwap else "BELOW"
    avg_v = sum(vols[:-1]) / max(len(vols) - 1, 1)
    vol_ratio = round(vols[-1] / avg_v, 2) if avg_v else 1.0
    rsi = _rsi(closes)

    # ── volume shocker: this bar's volume vs the session's average bar volume ──
    # A genuine "shocker" is volume running well ABOVE its own norm (≥2×), graded
    # into tiers. Observe-only signal — describes participation, triggers nothing.
    vol_tier = ("EXTREME" if vol_ratio >= 3.0 else "HIGH" if vol_ratio >= 2.0
                else "ELEVATED" if vol_ratio >= 1.5 else "NORMAL")
    vol_shocker = vol_ratio >= 2.0

    # ── composite score ──
    score = 0.0
    if orb == "BREAKOUT_UP":
        score += 30
    elif orb == "BREAKDOWN":
        score -= 30
    score += 15 if vwap_pos == "ABOVE" else -15
    score += max(-25, min(25, pct * 10))          # intraday momentum
    if vol_ratio >= 1.5 and score != 0:           # volume confirms the prevailing side
        score += 15 * _sign(score)
    if rsi >= 60:
        score += 10
    elif rsi <= 40:
        score -= 10
    score = round(max(-100, min(100, score)), 1)

    bias = "LONG" if score >= 20 else "SHORT" if score <= -20 else "NEUTRAL"
    parts = [f"{pct:+.2f}%", orb.replace("_", " ").title(), f"{vwap_pos} VWAP",
             f"vol {vol_ratio:.1f}x", f"RSI {rsi:.0f}"]
    if vol_shocker:                                   # surface it up front in the read
        parts.insert(0, f"🔥 VOL SHOCKER {vol_ratio:.1f}x ({vol_tier.lower()})")
    return {
        "symbol": symbol, "ltp": ltp, "pct_change": pct,
        "orb": orb, "vwap": vwap, "vwap_pos": vwap_pos,
        "vol_ratio": vol_ratio, "vol_shocker": vol_shocker, "vol_tier": vol_tier,
        "rsi": rsi,
        "score": score, "bias": bias, "reason": " · ".join(parts),
    }


# ── data fetch + full scan ────────────────────────────────────────────────────
def _today_bars(symbol: str) -> list[dict]:
    """Today's 5-min intraday bars for an NSE symbol via yfinance (SYMBOL.NS).
    yfinance is ~15 min DELAYED and often sparse early in the session — the Kite
    path below is preferred when a live session exists."""
    from src.data.backfill import _fetch_yfinance
    return _fetch_yfinance(f"{symbol}.NS", interval="5m", period="1d")


def kite_then_yf_fetch(broker):
    """Build a per-symbol fetcher that reads REAL-TIME intraday bars from the live
    Kite session when available, and falls back to (delayed) yfinance only if Kite
    returns nothing. `broker` is a KiteBroker (or None) — anything exposing
    get_equity_intraday_bars(symbol)."""
    def fetch(symbol: str) -> list[dict]:
        bars = []
        if broker is not None and hasattr(broker, "get_equity_intraday_bars"):
            try:
                bars = broker.get_equity_intraday_bars(symbol) or []
            except Exception:
                bars = []
        if not bars:
            try:
                bars = _today_bars(symbol)
            except Exception:
                bars = []
        return bars
    return fetch


def scan_equities(universe: list[str] | None = None, limit: int = 15,
                  fetch=None, source_label: str | None = None,
                  rank_by: str = "score") -> dict:
    """Scan the universe and return a ranked intraday watchlist.
    `fetch` is injectable (symbol -> bars) so tests run without network.
    `source_label` names the data source shown in the UI.
    `rank_by`: "score" (conviction, default) or "volume_shocker" (rank by how far
    today's volume is above its own average — the biggest volume shockers first)."""
    universe = universe or DEFAULT_UNIVERSE
    fetch = fetch or _today_bars
    data_source = source_label or "yfinance intraday (SYMBOL.NS)"
    rows, errors = [], 0
    for sym in universe:
        try:
            bars = fetch(sym)
            r = score_symbol(sym, bars)
            if r:
                rows.append(r)
        except Exception:
            errors += 1
    if rank_by in ("volume_shocker", "shocker", "volume"):
        rows.sort(key=lambda r: r.get("vol_ratio", 0), reverse=True)
    else:
        rows.sort(key=lambda r: abs(r["score"]), reverse=True)
    status = "ok" if rows else "no_data"
    note = ("" if rows else
            "No live intraday equity data (market closed, or no data feed/network). "
            "Connect a feed during market hours to populate the scan.")
    scan = {
        "market_basis": "NSE",
        "generated_at": datetime.now(IST).isoformat(),
        "data_source": data_source,
        "status": status, "note": note, "rank_by": rank_by,
        "scanned": len(universe), "returned": len(rows), "errors": errors,
        "shockers": sum(1 for r in rows if r.get("vol_shocker")),
        "watchlist": rows[:limit],
    }
    if status == "ok":
        try:
            save_scan_snapshot(scan)
        except Exception:
            pass          # snapshotting is best-effort; never break a live scan
    return scan


# ── next-day follow-up: snapshot each scan, grade it against the real move ─────
def save_scan_snapshot(scan: dict, snap_dir: str | None = None) -> str | None:
    """Persist a scan's ranked picks under logs/equity_scans/<date>.json so it can
    later be graded against the next trading day's actual close. Overwrites the
    day's file with the latest scan (the most-informed read of the session)."""
    if scan.get("status") != "ok" or not scan.get("watchlist"):
        return None
    snap_dir = snap_dir or _SNAP_DIR
    os.makedirs(snap_dir, exist_ok=True)
    gen = scan.get("generated_at") or datetime.now(IST).isoformat()
    day = gen[:10]
    picks = [{
        "symbol": r["symbol"], "bias": r["bias"], "score": r["score"],
        "ltp": r["ltp"], "orb": r.get("orb"), "vwap_pos": r.get("vwap_pos"),
        "reason": r.get("reason"),
    } for r in scan["watchlist"]]
    payload = {"date": day, "generated_at": gen, "picks": picks}
    path = os.path.join(snap_dir, f"{day}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def list_snapshots(snap_dir: str | None = None) -> list[str]:
    """Dates (YYYY-MM-DD) that have a saved scan snapshot, newest first."""
    snap_dir = snap_dir or _SNAP_DIR
    if not os.path.isdir(snap_dir):
        return []
    days = [f[:-5] for f in os.listdir(snap_dir) if f.endswith(".json")]
    return sorted(days, reverse=True)


def _load_snapshot(day: str, snap_dir: str | None = None) -> dict | None:
    snap_dir = snap_dir or _SNAP_DIR
    path = os.path.join(snap_dir, f"{day}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def grade_followup(picks: list[dict], next_closes: dict[str, float]) -> dict:
    """Pure grader: did each pick's *directional* call pay off on the next day?

    entry = the pick's snapshot LTP; exit = the next trading day's close.
      • LONG  is "hit" when the stock closed HIGHER than entry.
      • SHORT is "hit" when the stock closed LOWER than entry.
    `dir_return` is the return in the *direction of the call* (positive = the
    call was right). NEUTRAL picks are graded for reference but excluded from
    the directional hit-rate. Picks with no next-day close are skipped."""
    graded, hits, dir_rets = [], 0, []
    directional = 0
    for p in picks:
        sym = p["symbol"]
        entry = p.get("ltp")
        nxt = next_closes.get(sym)
        if entry in (None, 0) or nxt is None:
            continue
        raw = (nxt - entry) / entry * 100.0
        bias = p.get("bias", "NEUTRAL")
        dir_ret = raw if bias == "LONG" else -raw if bias == "SHORT" else raw
        hit = dir_ret > 0
        if bias in ("LONG", "SHORT"):
            directional += 1
            if hit:
                hits += 1
            dir_rets.append(dir_ret)
        graded.append({
            "symbol": sym, "bias": bias, "score": p.get("score"),
            "entry": round(entry, 2), "next_close": round(nxt, 2),
            "raw_change_pct": round(raw, 2), "dir_return_pct": round(dir_ret, 2),
            "hit": hit,
        })
    hit_rate = round(hits / directional * 100, 1) if directional else None
    avg_dir = round(sum(dir_rets) / len(dir_rets), 2) if dir_rets else None
    verdict, verdict_note = _carry_verdict(directional, hit_rate, avg_dir)
    return {
        "graded": len(graded), "directional": directional,
        "hits": hits, "hit_rate_pct": hit_rate, "avg_dir_return_pct": avg_dir,
        "verdict": verdict, "verdict_note": verdict_note,
        "picks": graded,
    }


def _carry_verdict(directional: int, hit_rate, avg_dir):
    """Plain-English read of whether intraday picks CARRIED OVER or REVERSED the
    next day. Deliberately conservative and honest about sample size — a single
    day of a handful of names is an anecdote, not evidence."""
    if not directional:
        return None, ""
    hr = hit_rate or 0
    ad = avg_dir or 0
    if hr <= 40 or ad < 0:
        label = "REVERSAL"
        msg = ("Picks tended to FADE the next day — the strongest intraday momentum "
               "reversed. If this persists across many days it points to a contrarian "
               "(fade) read, not a follow read.")
    elif hr >= 60 and ad > 0:
        label = "CARRY_OVER"
        msg = ("Momentum FOLLOWED THROUGH into the next day — the calls kept paying "
               "in their own direction.")
    else:
        label = "MIXED"
        msg = "No clear carry-over either way on this sample."
    if directional < 5:
        msg += (f" ⚠ Only {directional} directional pick(s) on one day — treat this as "
                "an anecdote; read the trend once many days have accumulated.")
    return label, msg


def _next_day_closes(symbols: list[str], after_day: str) -> dict[str, float]:
    """Real next-trading-day close per symbol via yfinance daily bars.
    Returns {symbol: close} for the first session strictly after `after_day`."""
    from src.data.backfill import _fetch_yfinance
    out: dict[str, float] = {}
    for sym in symbols:
        try:
            daily = _fetch_yfinance(f"{sym}.NS", interval="1d", period="1mo")
            future = [b for b in daily if (b.get("t") or "")[:10] > after_day]
            if future:
                out[sym] = future[0]["c"]
        except Exception:
            continue
    return out


def followup_analysis(day: str | None = None, snap_dir: str | None = None,
                      fetch_next=None) -> dict:
    """Grade a saved day's scan against the next trading day's real move.
    `day` defaults to the most recent snapshot. `fetch_next` is injectable
    (symbols, after_day) -> {symbol: close} so tests run without network."""
    days = list_snapshots(snap_dir)
    if not days:
        return {"status": "no_snapshots", "note":
                "No scan has been saved yet. Run a live scan during market hours "
                "first; each day's picks are stored automatically, then graded here "
                "against the very next day's real close.", "picks": []}
    day = day or days[0]
    snap = _load_snapshot(day, snap_dir)
    if not snap:
        return {"status": "not_found", "note": f"No saved scan for {day}.",
                "available": days, "picks": []}
    picks = snap.get("picks", [])
    symbols = [p["symbol"] for p in picks]
    fetch_next = fetch_next or _next_day_closes
    next_closes = fetch_next(symbols, day)
    if not next_closes:
        return {"status": "pending", "snapshot_date": day, "available": days,
                "note": ("Next trading day's close isn't available yet (the market "
                         "hasn't closed after this scan, or no data feed). It will "
                         "grade once the next session's data exists."),
                "picks": []}
    result = grade_followup(picks, next_closes)
    result.update({"status": "ok", "snapshot_date": day, "available": days,
                   "note": ("Intraday picks graded against the NEXT day's close — "
                            "this measures momentum carry-over (follow-through), a "
                            "different edge than same-day intraday.")})
    return result
