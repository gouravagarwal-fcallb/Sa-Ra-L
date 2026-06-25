"""
Synthetic NIFTY historical data generator.
Calibrated to actual NIFTY 50 price levels and volatility regimes (2008–2024).
Used when live market data download is unavailable.

Data anchors (actual NIFTY 50 year-end closes, approximate):
  2007-end : 6,138   2008-end : 2,959   2009-end : 5,201   2010-end : 6,134
  2011-end : 4,624   2012-end : 5,905   2013-end : 6,304   2014-end : 8,282
  2015-end : 7,946   2016-end : 8,186   2017-end : 10,530  2018-end : 10,779
  2019-end : 12,168  2020-end : 13,982  2021-end : 17,354  2022-end : 18,105
  2023-end : 21,731  2024-end : 23,644
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import List, Dict


_YEAR_END_PRICES = {
    2007: 6138, 2008: 2959, 2009: 5201, 2010: 6134,
    2011: 4624, 2012: 5905, 2013: 6304, 2014: 8282,
    2015: 7946, 2016: 8186, 2017: 10530, 2018: 10779,
    2019: 12168, 2020: 13982, 2021: 17354, 2022: 18105,
    2023: 21731, 2024: 23644,
}

# Annual volatility (σ) by year — calibrated to historical India VIX
_ANNUAL_VOL = {
    2008: 0.65, 2009: 0.45, 2010: 0.22, 2011: 0.28,
    2012: 0.18, 2013: 0.20, 2014: 0.18, 2015: 0.22,
    2016: 0.20, 2017: 0.14, 2018: 0.22, 2019: 0.18,
    2020: 0.48, 2021: 0.22, 2022: 0.20, 2023: 0.14,
    2024: 0.14,
}

# Special intra-year events that cause sharp moves
_EVENTS = [
    # (date_str, move_pct)  — one-day shock move (+ = up, - = down)
    ("2008-09-15", -6.0),   # Lehman Brothers collapse
    ("2008-10-24", -5.5),   # GFC bottom panic
    ("2009-05-18",  8.0),   # Indian election result rally
    ("2016-11-08", -6.0),   # Demonetisation shock
    ("2020-03-23", -9.0),   # COVID circuit breaker
    ("2020-05-04",  5.0),   # COVID recovery start
    ("2024-06-04", -6.0),   # 2024 election surprise
]
_EVENT_MAP = {e[0]: e[1] for e in _EVENTS}


def _is_trading_day(d: date) -> bool:
    """Approximate trading days: Mon–Fri, skip major Indian holidays."""
    if d.weekday() >= 5:
        return False
    # Skip a handful of fixed NSE holidays (approximate)
    _HOLIDAYS = {
        "01-26", "08-15", "10-02",  # Republic, Independence, Gandhi
        "12-25",                    # Christmas
    }
    return d.strftime("%m-%d") not in _HOLIDAYS


def generate_nifty_bars(
    start_year: int = 2008,
    end_year: int = 2024,
    seed: int = 42,
) -> List[Dict]:
    """
    Generate synthetic NIFTY daily OHLCV bars calibrated to actual price anchors.
    Returns list of dicts with keys: date, open, high, low, close, volume.
    """
    rng = random.Random(seed)

    bars: List[Dict] = []
    price = float(_YEAR_END_PRICES.get(start_year - 1, 6138))

    start_date = date(start_year, 1, 1)
    end_date   = date(end_year, 12, 31)
    cur = start_date

    # Pre-compute daily drift to hit each year-end anchor
    # We recalculate on Jan 1 of each year
    current_year = None
    daily_drift  = 0.0
    daily_vol    = 0.0
    year_start_price = price
    remaining_trading_days = 0

    while cur <= end_date:
        if not _is_trading_day(cur):
            cur += timedelta(days=1)
            continue

        # Recalibrate at start of each year
        if cur.year != current_year:
            current_year = cur.year
            year_start_price = price
            target_year_end  = float(_YEAR_END_PRICES.get(current_year, price))
            annual_vol       = _ANNUAL_VOL.get(current_year, 0.20)
            daily_vol        = annual_vol / math.sqrt(252)

            # Count remaining trading days in this year
            temp = cur
            remaining_trading_days = sum(
                1 for _ in range(366)
                if _is_trading_day(temp + timedelta(days=_))
                and (temp + timedelta(days=_)).year == current_year
            )
            if remaining_trading_days > 0:
                log_return_needed = math.log(target_year_end / price)
                daily_drift = log_return_needed / remaining_trading_days

        # One-day shock events
        event_key = cur.strftime("%Y-%m-%d")
        shock = _EVENT_MAP.get(event_key, 0.0) / 100.0

        # Geometric Brownian Motion step
        z = rng.gauss(0, 1)
        log_return = daily_drift + daily_vol * z + shock
        close = price * math.exp(log_return)

        # Realistic intraday range
        intraday_vol = daily_vol * price * rng.uniform(0.6, 1.8)
        open_  = price * math.exp(rng.gauss(0, daily_vol * 0.3))
        high   = max(open_, close) + abs(rng.gauss(0, intraday_vol * 0.5))
        low    = min(open_, close) - abs(rng.gauss(0, intraday_vol * 0.5))
        high   = max(high, open_, close)
        low    = min(low,  open_, close)

        # Volume: roughly proportional to price level, higher on volatile days
        base_vol   = price * 500_000 / 10000
        vol_factor = 1 + abs(log_return) * 10
        volume     = max(1_000_000, rng.gauss(base_vol * vol_factor, base_vol * 0.3))

        bars.append({
            "date":   cur,
            "open":   round(open_, 2),
            "high":   round(high, 2),
            "low":    round(low, 2),
            "close":  round(close, 2),
            "volume": round(volume),
        })

        price = close
        remaining_trading_days -= 1
        cur += timedelta(days=1)

    return bars
