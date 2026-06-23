"""
Gift Nifty (NSE IFSC Nifty 50 Futures) data fetcher.

Tries multiple NSE India endpoints in order. Each one requires a browser-like
session (cookies from the main page) to get past basic bot detection.

Returns the premium/discount vs Nifty 50 previous close in points.
Returns 0.0 silently when all sources fail — caller falls back to DOW proxy.
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
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection":       "keep-alive",
}


def _nse_session() -> requests.Session:
    """Open a cookie-seeded NSE session (required to bypass their bot check)."""
    s = requests.Session()
    try:
        s.get("https://www.nseindia.com", headers=_HEADERS, timeout=8)
    except Exception:
        pass
    return s


def _try_market_status(session: requests.Session) -> float | None:
    """
    /api/marketStatus — NSE returns a 'giftnifty' key with lastPrice.
    Example: {"giftnifty": {"lastPrice": 24115.0, "previousClose": 24073.5}}
    """
    try:
        r = session.get(
            "https://www.nseindia.com/api/marketStatus",
            headers=_HEADERS, timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            gn = data.get("giftnifty") or data.get("giftNifty") or {}
            price = gn.get("lastPrice") or gn.get("last_price") or gn.get("ltp")
            if price:
                return float(price)
    except Exception as e:
        log.debug(f"NSE marketStatus Gift Nifty: {e}")
    return None


def _try_all_indices(session: requests.Session) -> float | None:
    """
    /api/allIndices — returns a list of indices including "GIFT NIFTY".
    """
    try:
        r = session.get(
            "https://www.nseindia.com/api/allIndices",
            headers=_HEADERS, timeout=8,
        )
        if r.status_code == 200:
            for idx in r.json().get("data", []):
                name = idx.get("indexSymbol", "").upper()
                if "GIFT" in name or "IFSC" in name:
                    price = idx.get("last") or idx.get("lastPrice")
                    if price:
                        return float(price)
    except Exception as e:
        log.debug(f"NSE allIndices Gift Nifty: {e}")
    return None


def _try_derivatives_overview(session: requests.Session) -> float | None:
    """
    /api/overview-derivatives — alternate NSE endpoint (original source).
    """
    try:
        r = session.get(
            "https://www.nseindia.com/api/overview-derivatives?index=NIFTY",
            headers=_HEADERS, timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            gift = (
                data.get("marketStatus", {}).get("giftNifty")
                or data.get("giftNifty")
                or data.get("giftnifty")
            )
            if gift:
                return float(gift)
    except Exception as e:
        log.debug(f"NSE derivatives overview Gift Nifty: {e}")
    return None


def _try_chart_index(session: requests.Session) -> float | None:
    """
    /api/chart-databyindex — Gift Nifty chart data endpoint.
    """
    try:
        r = session.get(
            "https://www.nseindia.com/api/chart-databyindex?index=NIFTYIFSC50&indices=true",
            headers=_HEADERS, timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            # Response: {"grapthData": [[ts, value], ...], "closePrice": 24102.0}
            price = data.get("closePrice") or data.get("last")
            if price:
                return float(price)
            series = data.get("grapthData") or data.get("graphData") or []
            if series:
                return float(series[-1][-1])
    except Exception as e:
        log.debug(f"NSE chart-databyindex Gift Nifty: {e}")
    return None


def get_gift_nifty_price() -> float:
    """
    Return the current Gift Nifty price (absolute level), 0.0 if unavailable.
    Tries four NSE India endpoints with a shared cookie session.
    """
    session = _nse_session()

    for fn in (_try_market_status, _try_all_indices,
               _try_derivatives_overview, _try_chart_index):
        price = fn(session)
        if price and price > 1000:   # sanity: Nifty is always >10000
            log.info(f"Gift Nifty price: {price:.1f} (via {fn.__name__})")
            return price

    log.warning("Gift Nifty price unavailable from all NSE sources.")
    return 0.0


def get_gift_nifty_premium() -> float:
    """
    Return Gift Nifty premium/discount vs Nifty 50 previous close.
    Positive = Gift Nifty above prev close → bullish.
    Negative = Gift Nifty below prev close → bearish.
    Returns 0.0 when data is unavailable (caller uses DOW proxy).
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
