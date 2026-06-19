"""
Gift Nifty (NSE IFSC Nifty 50 Futures) data fetcher.

Live data is scraped from publicly available sources.
Returns the premium/discount vs Nifty 50 previous close in points.
"""

from __future__ import annotations
import requests
from bs4 import BeautifulSoup
from src.utils.logger import setup_logger
from src.data.market_data import get_previous_close

log = setup_logger("gift_nifty")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _fetch_from_nseindia() -> float | None:
    """Attempt to fetch Gift Nifty price from NSE India website."""
    try:
        url = "https://www.nseindia.com/api/overview-derivatives?index=NIFTY"
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
        resp = session.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            gift = data.get("marketStatus", {}).get("giftNifty")
            if gift:
                return float(gift)
    except Exception as e:
        log.debug(f"NSE Gift Nifty fetch failed: {e}")
    return None


def _fetch_from_moneycontrol() -> float | None:
    """Fallback: scrape moneycontrol for SGX/Gift Nifty."""
    try:
        url = "https://www.moneycontrol.com/stocks/fututrade/futures/sgxnifty.html"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        price_tag = soup.find("span", {"id": "nsespot"}) or soup.find(
            "div", class_="Bggry"
        )
        if price_tag:
            text = price_tag.get_text(strip=True).replace(",", "")
            return float(text)
    except Exception as e:
        log.debug(f"MoneyControl Gift Nifty fetch failed: {e}")
    return None


def get_gift_nifty_price() -> float:
    """Return the current Gift Nifty price, 0.0 if unavailable."""
    price = _fetch_from_nseindia()
    if price:
        log.info(f"Gift Nifty price: {price}")
        return price

    price = _fetch_from_moneycontrol()
    if price:
        log.info(f"Gift Nifty price (MC fallback): {price}")
        return price

    log.warning("Gift Nifty price unavailable from all sources.")
    return 0.0


def get_gift_nifty_premium() -> float:
    """
    Return Gift Nifty premium/discount vs Nifty 50 previous close.
    Positive = Gift Nifty trading above → bullish signal.
    Negative = Gift Nifty trading below → bearish signal.
    """
    gift_price = get_gift_nifty_price()
    if gift_price == 0.0:
        return 0.0
    nifty_prev_close = get_previous_close("NIFTY")
    if nifty_prev_close == 0.0:
        return 0.0
    return gift_price - nifty_prev_close


def get_gift_nifty_premium_manual(gift_price: float) -> float:
    """Use this if you want to enter Gift Nifty price manually."""
    nifty_prev_close = get_previous_close("NIFTY")
    if nifty_prev_close == 0.0:
        return 0.0
    return gift_price - nifty_prev_close
