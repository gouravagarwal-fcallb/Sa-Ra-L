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

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

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
    return {
        "symbol": symbol, "ltp": ltp, "pct_change": pct,
        "orb": orb, "vwap": vwap, "vwap_pos": vwap_pos,
        "vol_ratio": vol_ratio, "rsi": rsi,
        "score": score, "bias": bias, "reason": " · ".join(parts),
    }


# ── data fetch + full scan ────────────────────────────────────────────────────
def _today_bars(symbol: str) -> list[dict]:
    """Today's 5-min intraday bars for an NSE symbol via yfinance (SYMBOL.NS)."""
    from src.data.backfill import _fetch_yfinance
    return _fetch_yfinance(f"{symbol}.NS", interval="5m", period="1d")


def scan_equities(universe: list[str] | None = None, limit: int = 15,
                  fetch=None) -> dict:
    """Scan the universe and return a ranked intraday watchlist.
    `fetch` is injectable (symbol -> bars) so tests run without network."""
    universe = universe or DEFAULT_UNIVERSE
    fetch = fetch or _today_bars
    rows, errors = [], 0
    for sym in universe:
        try:
            bars = fetch(sym)
            r = score_symbol(sym, bars)
            if r:
                rows.append(r)
        except Exception:
            errors += 1
    rows.sort(key=lambda r: abs(r["score"]), reverse=True)
    status = "ok" if rows else "no_data"
    note = ("" if rows else
            "No live intraday equity data (market closed, or no data feed/network). "
            "Connect a feed during market hours to populate the scan.")
    return {
        "market_basis": "NSE",
        "generated_at": datetime.now(IST).isoformat(),
        "data_source": "yfinance intraday (SYMBOL.NS)",
        "status": status, "note": note,
        "scanned": len(universe), "returned": len(rows), "errors": errors,
        "watchlist": rows[:limit],
    }
