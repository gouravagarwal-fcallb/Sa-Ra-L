from datetime import datetime, time
from typing import Union
import pytz

IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return IST.localize(dt)
    return dt.astimezone(IST)


def current_time_ist() -> time:
    return now_ist().time()


def is_market_open() -> bool:
    t = current_time_ist()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def time_in_window(window_start: str, window_end: str) -> bool:
    """Check if current IST time falls within HH:MM – HH:MM window."""
    t = current_time_ist()
    start = _parse_time(window_start)
    end = _parse_time(window_end)
    return start <= t <= end


def _parse_time(t: Union[str, time]) -> time:
    if isinstance(t, time):
        return t
    h, m = t.split(":")
    return time(int(h), int(m))


def round_to_strike(price: float, step: int = 50) -> int:
    """Round spot price to nearest option strike."""
    return int(round(price / step) * step)


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / abs(old)) * 100


def format_inr(amount: float) -> str:
    """Format number as Indian rupees with lakh/crore notation."""
    abs_amt = abs(amount)
    sign = "-" if amount < 0 else ""
    if abs_amt >= 1e7:
        return f"{sign}₹{abs_amt/1e7:.2f} Cr"
    if abs_amt >= 1e5:
        return f"{sign}₹{abs_amt/1e5:.2f} L"
    return f"{sign}₹{abs_amt:,.0f}"


def lots_from_qty(qty: int, lot_size: int) -> int:
    return qty // lot_size
