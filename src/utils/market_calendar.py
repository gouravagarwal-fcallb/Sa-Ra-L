"""
Market calendar for NSE (Nifty) and BSE (Sensex) weekly options.

Current weekly expiry schedule (post-SEBI rationalisation):
  NSE Nifty 50   : Tuesday
  BSE Sensex     : Thursday

Trading schedule:
  Monday    → Nifty 50
  Tuesday   → Nifty 50 (EXPIRY DAY)
  Wednesday → Skip (avoided by design)
  Thursday  → Sensex (EXPIRY DAY)
  Friday    → Nifty 50 (next week)
"""

from datetime import date, timedelta
from typing import Optional, Tuple
import holidays


NSE_EXTRA_HOLIDAYS_2024 = {
    date(2024, 1, 22),   # Ram Mandir consecration
}

NSE_EXTRA_HOLIDAYS_2025 = {
    date(2025, 2, 26),   # Maha Shivratri
}


def _nse_holidays(year: int) -> set:
    india = holidays.India(years=year, state="MH")
    nse_dates = set(india.keys())
    if year == 2024:
        nse_dates |= NSE_EXTRA_HOLIDAYS_2024
    if year == 2025:
        nse_dates |= NSE_EXTRA_HOLIDAYS_2025
    return nse_dates


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in _nse_holidays(d.year)


# ── Nifty weekly expiry: Tuesday ─────────────────────────────────────────────

def get_nifty_weekly_expiry(d: date) -> date:
    """Return Nifty weekly expiry (Tuesday) for the week containing d."""
    days_ahead = (1 - d.weekday()) % 7   # Tuesday = weekday 1
    expiry = d + timedelta(days=days_ahead)
    while not is_trading_day(expiry):
        expiry -= timedelta(days=1)
    return expiry


def is_nifty_expiry_day(d: Optional[date] = None) -> bool:
    if d is None:
        d = date.today()
    return d == get_nifty_weekly_expiry(d)


# ── Sensex weekly expiry: Thursday ───────────────────────────────────────────

def get_sensex_weekly_expiry(d: date) -> date:
    """Return Sensex weekly expiry (Thursday) for the week containing d."""
    days_ahead = (3 - d.weekday()) % 7   # Thursday = weekday 3
    expiry = d + timedelta(days=days_ahead)
    while not is_trading_day(expiry):
        expiry -= timedelta(days=1)
    return expiry


def is_sensex_expiry_day(d: Optional[date] = None) -> bool:
    if d is None:
        d = date.today()
    return d == get_sensex_weekly_expiry(d)


# ── Day classification ────────────────────────────────────────────────────────

def get_day_instrument(d: Optional[date] = None) -> Optional[str]:
    """
    Returns which instrument to trade on a given day:
      Monday    → "NIFTY"
      Tuesday   → "NIFTY"
      Wednesday → "NIFTY"   (Nifty weekly expiry is Thursday — Wed is pre-expiry)
      Thursday  → "SENSEX"  (expiry day)
      Friday    → "NIFTY"
      Weekend / Holiday → None
    """
    if d is None:
        d = date.today()
    if not is_trading_day(d):
        return None
    weekday = d.weekday()  # 0=Mon,1=Tue,2=Wed,3=Thu,4=Fri
    if weekday == 3:        # Thursday → Sensex expiry
        return "SENSEX"
    return "NIFTY"


def should_trade_today(d: Optional[date] = None) -> Tuple[bool, Optional[str]]:
    """Returns (should_trade, instrument_or_None)."""
    instrument = get_day_instrument(d)
    return (instrument is not None), instrument


# ── Legacy compatibility (kept for existing callers) ──────────────────────────

def get_weekly_expiry(d: date) -> date:
    """Legacy: returns Nifty weekly expiry (Tuesday). Use get_nifty_weekly_expiry() for clarity."""
    return get_nifty_weekly_expiry(d)


def is_expiry_day(d: Optional[date] = None) -> bool:
    """Legacy: returns True if d is Nifty expiry day (Tuesday)."""
    return is_nifty_expiry_day(d)


def get_next_expiry(from_date: Optional[date] = None) -> date:
    if from_date is None:
        from_date = date.today()
    candidate = get_nifty_weekly_expiry(from_date)
    if candidate <= from_date:
        candidate = get_nifty_weekly_expiry(from_date + timedelta(days=7))
    return candidate


def days_to_expiry(from_date: Optional[date] = None) -> int:
    if from_date is None:
        from_date = date.today()
    expiry = get_nifty_weekly_expiry(from_date)
    if expiry < from_date:
        expiry = get_next_expiry(from_date)
    return (expiry - from_date).days


def get_all_expiry_dates(start: date, end: date) -> list:
    """Return all Nifty weekly expiry dates between start and end (inclusive)."""
    expiries = []
    current = start
    while current <= end:
        expiry = get_nifty_weekly_expiry(current)
        if start <= expiry <= end and (not expiries or expiry != expiries[-1]):
            expiries.append(expiry)
        current = expiry + timedelta(days=1)
    return expiries
