"""
Multi-Timeframe Charts + Bollinger Bands
────────────────────────────────────────
Serves OHLC candles + Bollinger Bands (20, 2σ) per instrument per timeframe for
the always-on chart panel. Timeframes: 1m, 3m, 5m, 15m, 1h, 1d, 1w.

Two data paths:
  1. Live: the MarketFeed writes rolling windows into the shared market state slot
     (multi.market().chart_bars) on each bar close — read those first.
  2. Fallback: compute on demand from the BackfillManager so charts render even
     when no feed/engine is running (e.g. reviewing during off-hours).
"""
from __future__ import annotations

import time
import threading
from statistics import mean, pstdev

# tf -> yfinance interval used by BackfillManager.
# 3m has no native source on Kite or yfinance — we pull 1m and resample to 3-minute.
_TF_INTERVAL = {"1m": "1m", "3m": "1m", "5m": "5m", "15m": "15m",
                "1h": "60m", "1d": "1d", "1w": "1wk"}
_INTRADAY = {"1m", "3m", "5m", "15m", "1h"}
_DAILY = {"1d", "1w"}
# How many days of history to pull from Kite per intraday timeframe.
_KITE_LOOKBACK = {"1m": 2, "3m": 2, "5m": 4, "15m": 7, "1h": 20}
# Daily/weekly also come from Kite (its `day` interval is reliable, unlike
# yfinance for Indian indices). Weekly is built by resampling daily candles.
# NOTE: Kite's `day` interval allows at most ~2000 days per request — asking for
# more makes the whole call fail and return nothing (this is why 1W showed
# "0 bars"). Keep 1w well under 2000; ~1800 days ≈ 257 weekly candles, ample for
# a Bollinger(20) weekly.
_KITE_DAILY_LOOKBACK = {"1d": 500, "1w": 1800}
BB_PERIOD = 20
BB_MULT = 2.0

# Short server-side cache so the 6-timeframe × N-instrument polling doesn't hammer
# Kite (which is throttled to ~3 req/s).
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 30.0  # seconds


def _kite_intraday(instrument: str, tf: str):
    """Pull intraday bars from Kite (reliable real-time, unlike yfinance intraday
    during market hours). Returns a bar list or None if Kite is off/empty."""
    try:
        from datetime import datetime, timedelta, timezone
        from src.data import kite_historical
        if not kite_historical.is_enabled():
            return None
        IST = timezone(timedelta(hours=5, minutes=30))
        to = datetime.now(IST)
        frm = to - timedelta(days=_KITE_LOOKBACK.get(tf, 5))
        bars = kite_historical.fetch_range(instrument, frm, to, tf)
        return bars or None
    except Exception:
        return None


def _resample_daily_to_weekly(bars: list) -> list:
    """Aggregate daily OHLCV dicts into weekly candles (bucket = ISO week; label =
    the Monday of that week). Kite has no native weekly interval, so 1w is built
    from `day` bars. Assumes chronological input."""
    from datetime import datetime as _dt, timedelta as _td

    def _parse(t):
        if isinstance(t, (int, float)):
            return _dt.fromtimestamp(t)
        s = str(t)
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return _dt.strptime(s[:19], fmt)
            except Exception:
                pass
        try:
            return _dt.fromisoformat(s.replace("Z", "+00:00").split("+")[0])
        except Exception:
            return None

    buckets, order = {}, []
    for b in bars or []:
        dt = _parse(b.get("t"))
        if dt is None:
            continue
        monday = (dt - _td(days=dt.weekday())).strftime("%Y-%m-%d")
        h, l, c, v = b.get("h"), b.get("l"), b.get("c"), b.get("v", 0) or 0
        if monday not in buckets:
            buckets[monday] = {"t": monday, "o": b.get("o"), "h": h, "l": l, "c": c, "v": v}
            order.append(monday)
        else:
            agg = buckets[monday]
            if h is not None:
                agg["h"] = h if agg["h"] is None else max(agg["h"], h)
            if l is not None:
                agg["l"] = l if agg["l"] is None else min(agg["l"], l)
            agg["c"] = c
            agg["v"] = (agg["v"] or 0) + (v or 0)
    return [buckets[k] for k in order]


def _kite_daily(instrument: str, tf: str):
    """Pull daily bars from Kite (its `day` interval is reliable for Indian
    indices, where yfinance is patchy). For 1w, fetch daily and resample to
    weekly. Returns a bar list or None if Kite is off/empty."""
    try:
        from datetime import datetime, timedelta, timezone
        from src.data import kite_historical
        if not kite_historical.is_enabled():
            return None
        IST = timezone(timedelta(hours=5, minutes=30))
        to = datetime.now(IST)
        frm = to - timedelta(days=_KITE_DAILY_LOOKBACK.get(tf, 500))
        bars = kite_historical.fetch_range(instrument, frm, to, "1d")
        if not bars:
            return None
        if tf == "1w":
            bars = _resample_daily_to_weekly(bars)
        return bars or None
    except Exception:
        return None


def bollinger(closes: list[float], period: int = BB_PERIOD,
              mult: float = BB_MULT) -> dict:
    """Rolling Bollinger Bands; arrays align 1:1 with `closes` (None before warmup)."""
    upper, mid, lower = [], [], []
    for i in range(len(closes)):
        if i + 1 < period:
            upper.append(None); mid.append(None); lower.append(None); continue
        window = closes[i + 1 - period:i + 1]
        m = mean(window); sd = pstdev(window)
        mid.append(round(m, 2))
        upper.append(round(m + mult * sd, 2))
        lower.append(round(m - mult * sd, 2))
    return {"upper": upper, "mid": mid, "lower": lower,
            "period": period, "mult": mult}


def _compute_from_backfill(instrument: str, tf: str) -> dict:
    interval = _TF_INTERVAL.get(tf)
    if interval is None:
        return {"instrument": instrument, "tf": tf, "bars": [], "bb": {},
                "source": "error", "reason": f"unsupported tf {tf}"}
    try:
        from src.data.backfill import BackfillManager
        bf = BackfillManager()
        if tf in _INTRADAY:
            bars = bf.get_today_bars(instrument, interval=interval)
            if not bars:
                bars = bf.get_bars(instrument, interval=interval, days_back=5)
        else:
            days = 400 if tf == "1d" else 1500
            bars = bf.get_bars(instrument, interval=interval, days_back=days)
    except Exception as e:
        return {"instrument": instrument, "tf": tf, "bars": [], "bb": {},
                "source": "error", "reason": str(e)[:140]}
    bars = bars or []
    # 3m comes back as 1-minute bars from yfinance — resample to 3-minute candles.
    if tf == "3m" and bars:
        try:
            from src.data.kite_historical import _resample_minute_bars
            bars = _resample_minute_bars(bars, 3)
        except Exception:
            pass
    closes = [b.get("c") for b in bars if b.get("c") is not None]
    return {"instrument": instrument, "tf": tf,
            "bars": bars[-250:], "bb": _slice_bb(bollinger(closes), len(bars), 250),
            "source": "backfill", "n": len(bars)}


def _slice_bb(bb: dict, total: int, keep: int) -> dict:
    if total <= keep:
        return bb
    start = total - keep
    return {"upper": bb["upper"][start:], "mid": bb["mid"][start:],
            "lower": bb["lower"][start:], "period": bb["period"], "mult": bb["mult"]}


def get_chart(multi, instrument: str, tf: str) -> dict:
    instrument = instrument.upper()
    if tf not in _TF_INTERVAL:
        return {"instrument": instrument, "tf": tf, "bars": [], "bb": {},
                "source": "error", "reason": f"unsupported tf {tf}",
                "supported": list(_TF_INTERVAL.keys())}
    key = (instrument, tf)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and (now - hit[0]) < _CACHE_TTL:
            return hit[1]
    result = _build_chart(multi, instrument, tf)
    # only cache non-empty results so a transient miss doesn't stick for 30s
    if result.get("bars"):
        with _CACHE_LOCK:
            _CACHE[key] = (now, result)
    return result


def fresh_chart(instrument: str, tf: str) -> dict:
    """Kite-first / backfill chart WITHOUT reading the live slot — used by the market
    feed to (re)populate the live slot. Returns {bars, bb, source}."""
    instrument = instrument.upper()
    if tf not in _TF_INTERVAL:
        return {"bars": [], "bb": {}, "source": "error"}
    kb = _kite_intraday(instrument, tf) if tf in _INTRADAY else \
         _kite_daily(instrument, tf) if tf in _DAILY else None
    if kb:
        closes = [b.get("c") for b in kb if b.get("c") is not None]
        return {"bars": kb[-250:], "bb": _slice_bb(bollinger(closes), len(kb), 250),
                "source": "kite"}
    bf = _compute_from_backfill(instrument, tf)
    return {"bars": bf.get("bars", []), "bb": bf.get("bb", {}), "source": bf.get("source")}


def _build_chart(multi, instrument: str, tf: str) -> dict:
    # 1) live market slot (if a feed ever populates it)
    try:
        if multi is not None and multi.has("_market"):
            cb = multi.market().chart_bars.get(instrument, {}).get(tf)
            if cb and cb.get("bars"):
                return {"instrument": instrument, "tf": tf,
                        "bars": cb["bars"][-250:], "bb": cb.get("bb", {}),
                        "source": "live", "updated": cb.get("updated")}
    except Exception:
        pass
    # 2) Kite — reliable for both intraday and daily/weekly (yfinance is flaky
    #    intraday in-session and patchy on Indian-index daily/weekly history).
    kb = _kite_intraday(instrument, tf) if tf in _INTRADAY else \
         _kite_daily(instrument, tf) if tf in _DAILY else None
    if kb:
        closes = [b.get("c") for b in kb if b.get("c") is not None]
        return {"instrument": instrument, "tf": tf, "bars": kb[-250:],
                "bb": _slice_bb(bollinger(closes), len(kb), 250),
                "source": "kite", "n": len(kb)}
    # 3) yfinance backfill (used when Kite is off)
    return _compute_from_backfill(instrument, tf)
