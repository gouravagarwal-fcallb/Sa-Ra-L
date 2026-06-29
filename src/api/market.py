"""
Live market summary — NIFTY / SENSEX / India VIX with change.
─────────────────────────────────────────────────────────────
Powers the dashboard header ticker ("where is the market right now"). Pulls live
quotes from Kite (reliable) with a yfinance fallback, behind an 8s cache so the
header polling doesn't hammer the data source.
"""
from __future__ import annotations

import time
import threading

_cache = {"t": 0.0, "data": {}}
_intern_cache = {"t": 0.0, "data": {}}
_lock = threading.Lock()
_TTL = 8.0
_INTERN_TTL = 45.0   # option-chain OI is heavier — refresh less often than quotes


def _from_kite() -> dict:
    from src.data import kite_historical
    q = kite_historical.get_quotes()
    if not q:
        return {}
    out = {"source": "kite"}
    for k_low, k_up in (("nifty", "NIFTY"), ("sensex", "SENSEX")):
        if q.get(k_up):
            out[k_low] = q[k_up]
    vix = q.get("VIX")
    if vix:
        out["vix"] = vix.get("ltp")
        out["vix_change"] = vix.get("change")
    return out if (out.get("nifty") or out.get("sensex") or out.get("vix")) else {}


def _from_yfinance() -> dict:
    """Fallback: last close vs previous close from yfinance daily bars."""
    try:
        import yfinance as yf
    except Exception:
        return {}
    out = {"source": "yfinance"}
    syms = {"nifty": "^NSEI", "sensex": "^BSESN", "vix": "^INDIAVIX"}
    for key, sym in syms.items():
        try:
            h = yf.Ticker(sym).history(period="5d", interval="1d")
            if h is None or h.empty or len(h) < 1:
                continue
            ltp = float(h["Close"].iloc[-1])
            prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else ltp
            chg = ltp - prev
            if key == "vix":
                out["vix"] = round(ltp, 2)
                out["vix_change"] = round(chg, 2)
            else:
                out[key] = {"ltp": round(ltp, 2), "prev_close": round(prev, 2),
                            "change": round(chg, 2),
                            "change_pct": round(chg / prev * 100, 2) if prev else 0.0}
        except Exception:
            continue
    return out if (out.get("nifty") or out.get("sensex") or out.get("vix")) else {}


def _build() -> dict:
    data = _from_kite()
    if not data:
        data = _from_yfinance()
    return data


def get_market_summary() -> dict:
    with _lock:
        if (time.time() - _cache["t"]) < _TTL and _cache["data"]:
            return _cache["data"]
    data = _build()
    with _lock:
        if data:
            _cache.update(t=time.time(), data=data)
        return data or _cache["data"] or {"source": "none"}


def get_live_internals() -> dict:
    """Live NIFTY option-chain internals (PCR + Max Pain) for the Pre-Market page's
    'Live Market' section — distinct from the frozen pre-market briefing. Reads from
    Kite OI (NSE blocks bots intraday), behind a 45s cache."""
    with _lock:
        if (time.time() - _intern_cache["t"]) < _INTERN_TTL and _intern_cache["data"]:
            return _intern_cache["data"]
    out = {"pcr": None, "max_pain": None, "source": "none"}
    try:
        from src.data import kite_historical
        pcr, mp = kite_historical.get_option_chain_metrics("NIFTY")
        if pcr is not None or mp is not None:
            out = {"pcr": pcr, "max_pain": mp, "source": "kite"}
    except Exception:
        pass
    with _lock:
        if out.get("source") != "none":
            _intern_cache.update(t=time.time(), data=out)
        return out
