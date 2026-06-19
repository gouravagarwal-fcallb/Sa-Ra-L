"""
Determines expiry days and trading holidays for NSE.

Nifty 50 weekly options expire every Thursday.
If Thursday is a holiday, expiry shifts to Wednesday.
Monthly expiry = last Thursday of the month.
"""

from datetime import date, timedelta
from typing import Optional
import holidays


NSE_EXTRA_HOLIDAYS_2024 = {
    date(2024, 1, 22),   # Ram Mandir consecration (special holiday)
}

NSE_EXTRA_HOLIDAYS_2025 = {
    date(2025, 2, 26),   # Maha Shivratri
}


def _nse_holidays(year: int) -> set:
    india = holidays.India(years=year, state="MH")  # Maharashtra covers NSE
    nse_dates = set(india.keys())
    if year == 2024:
        nse_dates |= NSE_EXTRA_HOLIDAYS_2024
    if year == 2025:
        nse_dates |= NSE_EXTRA_HOLIDAYS_2025
    return nse_dates


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return d not in _nse_holidays(d.year)


def get_weekly_expiry(d: date) -> date:
    """Return the weekly expiry date for the week containing date d."""
    days_ahead = 3 - d.weekday()  # Thursday is weekday 3
    if days_ahead < 0:
        days_ahead += 7
    expiry = d + timedelta(days=days_ahead)

    # If Thursday is a holiday, shift to Wednesday
    while not is_trading_day(expiry):
        expiry -= timedelta(days=1)

    return expiry


def is_expiry_day(d: Optional[date] = None) -> bool:
    if d is None:
        d = date.today()
    return d == get_weekly_expiry(d)


def get_next_expiry(from_date: Optional[date] = None) -> date:
    if from_date is None:
        from_date = date.today()
    candidate = get_weekly_expiry(from_date)
    if candidate <= from_date:
        candidate = get_weekly_expiry(from_date + timedelta(days=7))
    return candidate


def days_to_expiry(from_date: Optional[date] = None) -> int:
    if from_date is None:
        from_date = date.today()
    expiry = get_weekly_expiry(from_date)
    if expiry < from_date:
        expiry = get_next_expiry(from_date)
    return (expiry - from_date).days


def get_all_expiry_dates(start: date, end: date) -> list[date]:
    """Return all weekly expiry dates between start and end (inclusive)."""
    expiries = []
    current = start
    while current <= end:
        expiry = get_weekly_expiry(current)
        if start <= expiry <= end and (not expiries or expiry != expiries[-1]):
            expiries.append(expiry)
        # Move to next week
        current = expiry + timedelta(days=1)
    return expiries
