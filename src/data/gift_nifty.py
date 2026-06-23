"""
Gift Nifty (NSE IFSC Nifty 50 Futures) data fetcher.

Tries multiple endpoints in order. NSE India requires a browser-like
session (cookies from the main page) to pass basic bot detection.

Returns the premium/discount vs Nifty 50 previous close in points.
Returns 0.0 when all sources fail — caller falls back to DOW proxy.
"""

from __future__ import annotations
import requests
from src.utils.logger import setup_logger
from src.data.market_data import get_previous_close

log = setup_logger("gift_nifty")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "Referer":          "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection":       "keep-alive",
}

# Headers for sites other than NSE
_GENERIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}


def _nse_session() -> requests.Session:
    """Open a cookie-seeded NSE session (required to bypass their bot check)."""
    s = requests.Session()
    for attempt in range(2):
        try:
            r = s.get("https://www.nseindia.com", headers=_HEADERS, timeout=10)
            if r.status_code == 200 and len(s.cookies) > 0:
                log.info(f"NSE session seeded OK (cookies={len(s.cookies)}, attempt={attempt+1})")
                return s
            log.info(
                f"NSE session attempt {attempt+1}: status={r.status_code} "
                f"cookies={len(s.cookies)} — {'retrying' if attempt == 0 else 'proceeding anyway'}"
            )
        except Exception as e:
            log.info(f"NSE session attempt {attempt+1} failed: {type(e).__name__}: {e}")
    return s


def _try_market_status(session: requests.Session) -> float | None:
    """
    /api/marketStatus — NSE returns a 'giftnifty' key with lastPrice.
    Example: {"giftnifty": {"lastPrice": 24115.0, "previousClose": 24073.5}}
    """
    url = "https://www.nseindia.com/api/marketStatus"
    try:
        r = session.get(url, headers=_HEADERS, timeout=8)
        log.info(f"NSE marketStatus: HTTP {r.status_code} ({len(r.content)} bytes)")
        if r.status_code == 200:
            data = r.json()
            gn = data.get("giftnifty") or data.get("giftNifty") or {}
            price = gn.get("lastPrice") or gn.get("last_price") or gn.get("ltp")
            if price:
                return float(price)
            log.info(f"NSE marketStatus: no giftnifty key — keys present: {list(data.keys())[:8]}")
        else:
            log.info(f"NSE marketStatus: non-200, body prefix: {r.text[:120]!r}")
    except Exception as e:
        log.info(f"NSE marketStatus exception: {type(e).__name__}: {e}")
    return None


def _try_all_indices(session: requests.Session) -> float | None:
    """/api/allIndices — looks for 'GIFT' in indexSymbol."""
    url = "https://www.nseindia.com/api/allIndices"
    try:
        r = session.get(url, headers=_HEADERS, timeout=8)
        log.info(f"NSE allIndices: HTTP {r.status_code} ({len(r.content)} bytes)")
        if r.status_code == 200:
            entries = r.json().get("data", [])
            for idx in entries:
                name = idx.get("indexSymbol", "").upper()
                if "GIFT" in name or "IFSC" in name:
                    price = idx.get("last") or idx.get("lastPrice")
                    if price:
                        return float(price)
            names = [x.get("indexSymbol", "") for x in entries[:10]]
            log.info(f"NSE allIndices: no GIFT entry — sample names: {names}")
        else:
            log.info(f"NSE allIndices: non-200, body prefix: {r.text[:120]!r}")
    except Exception as e:
        log.info(f"NSE allIndices exception: {type(e).__name__}: {e}")
    return None


def _try_derivatives_overview(session: requests.Session) -> float | None:
    """/api/overview-derivatives (original source)."""
    url = "https://www.nseindia.com/api/overview-derivatives?index=NIFTY"
    try:
        r = session.get(url, headers=_HEADERS, timeout=8)
        log.info(f"NSE overview-derivatives: HTTP {r.status_code} ({len(r.content)} bytes)")
        if r.status_code == 200:
            data = r.json()
            gift = (
                data.get("marketStatus", {}).get("giftNifty")
                or data.get("giftNifty")
                or data.get("giftnifty")
            )
            if gift:
                return float(gift)
            log.info(f"NSE overview-derivatives: no giftNifty key — top keys: {list(data.keys())[:8]}")
        else:
            log.info(f"NSE overview-derivatives: non-200, body prefix: {r.text[:120]!r}")
    except Exception as e:
        log.info(f"NSE overview-derivatives exception: {type(e).__name__}: {e}")
    return None


def _try_chart_index(session: requests.Session) -> float | None:
    """/api/chart-databyindex — Gift Nifty chart data."""
    url = "https://www.nseindia.com/api/chart-databyindex?index=NIFTYIFSC50&indices=true"
    try:
        r = session.get(url, headers=_HEADERS, timeout=8)
        log.info(f"NSE chart-databyindex: HTTP {r.status_code} ({len(r.content)} bytes)")
        if r.status_code == 200:
            data = r.json()
            price = data.get("closePrice") or data.get("last")
            if price:
                return float(price)
            series = data.get("grapthData") or data.get("graphData") or []
            if series:
                return float(series[-1][-1])
            log.info(f"NSE chart-databyindex: no price — keys: {list(data.keys())[:8]}")
        else:
            log.info(f"NSE chart-databyindex: non-200, body prefix: {r.text[:120]!r}")
    except Exception as e:
        log.info(f"NSE chart-databyindex exception: {type(e).__name__}: {e}")
    return None


def _try_groww(session: requests.Session) -> float | None:
    """
    Groww public indices API — has Gift Nifty as 'GIFT NIFTY'.
    No cookie seeding required (public endpoint).
    """
    url = "https://groww.in/v1/api/stocks_data/v1/tr_live_prices/segment/INDICES/GIFT_NIFTY50/latest"
    try:
        r = requests.get(url, headers=_GENERIC_HEADERS, timeout=8)
        log.info(f"Groww Gift Nifty: HTTP {r.status_code} ({len(r.content)} bytes)")
        if r.status_code == 200:
            data = r.json()
            price = (
                data.get("ltp") or data.get("lastPrice") or
                data.get("close") or data.get("price")
            )
            if price:
                return float(price)
            log.info(f"Groww: no price key — keys: {list(data.keys())[:8]}")
    except Exception as e:
        log.info(f"Groww exception: {type(e).__name__}: {e}")
    return None


def _try_moneycontrol(session: requests.Session) -> float | None:
    """
    MoneyControl API for GIFT Nifty (SGX/NSE IFSC).
    """
    url = "https://priceapi.moneycontrol.com/pricefeed/nseifsc/inidices/NIFTYIFSC50"
    try:
        r = requests.get(url, headers=_GENERIC_HEADERS, timeout=8)
        log.info(f"MoneyControl Gift Nifty: HTTP {r.status_code} ({len(r.content)} bytes)")
        if r.status_code == 200:
            data = r.json()
            inner = data.get("data") or {}
            price = (
                inner.get("pricecurrent")
                or inner.get("lastprice")
                or data.get("lastprice")
            )
            if price:
                return float(str(price).replace(",", ""))
            log.info(f"MoneyControl: no price — top keys: {list(data.keys())[:8]}")
    except Exception as e:
        log.info(f"MoneyControl exception: {type(e).__name__}: {e}")
    return None


def get_gift_nifty_price() -> float:
    """
    Return the current Gift Nifty price (absolute level), 0.0 if unavailable.
    Tries six sources. Logs the HTTP status of each attempt at INFO level.
    """
    session = _nse_session()

    sources = [
        ("NSE_marketStatus",     _try_market_status),
        ("NSE_allIndices",       _try_all_indices),
        ("NSE_derivatives",      _try_derivatives_overview),
        ("NSE_chart",            _try_chart_index),
        ("Groww",                _try_groww),
        ("MoneyControl",         _try_moneycontrol),
    ]

    for name, fn in sources:
        price = fn(session)
        if price and price > 1000:
            log.info(f"Gift Nifty price: {price:.1f}  (source: {name})")
            return price

    log.warning("Gift Nifty price unavailable from all 6 sources — caller will use DOW proxy.")
    return 0.0


def get_gift_nifty_premium() -> float:
    """
    Return Gift Nifty premium/discount vs Nifty 50 previous close.
    Positive = Gift Nifty above prev close → bullish.
    Negative = Gift Nifty below prev close → bearish.
    Returns 0.0 when data is unavailable.
    """
    gift_price = get_gift_nifty_price()
    if gift_price == 0.0:
        return 0.0
    nifty_prev_close = get_previous_close("NIFTY")
    if nifty_prev_close == 0.0:
        return 0.0
    return gift_price - nifty_prev_close


def get_gift_nifty_premium_manual(gift_price: float) -> float:
    """Use when you want to enter Gift Nifty price manually."""
    nifty_prev_close = get_previous_close("NIFTY")
    if nifty_prev_close == 0.0:
        return 0.0
    return gift_price - nifty_prev_close
