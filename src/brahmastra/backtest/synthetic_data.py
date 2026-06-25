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

Functions:
  generate_nifty_bars()     — daily OHLCV bars
  generate_nifty_5m_bars()  — 5-minute OHLCV bars (intraday from daily)
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Optional

IST = timezone(timedelta(hours=5, minutes=30))


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


# ─────────────────────────────────────────────────────────────────────────────
# 5-minute bar generation — Brownian bridge intraday expansion
# ─────────────────────────────────────────────────────────────────────────────

def _make_intraday_path(
    open_p:  float,
    high_p:  float,
    low_p:   float,
    close_p: float,
    n:       int = 75,
    rng:     Optional[random.Random] = None,
) -> List[float]:
    """
    Brownian bridge price path from open_p to close_p over n intervals.
    Returns n+1 prices: path[0]=open_p, path[n]=close_p, all in [low_p, high_p].
    """
    if rng is None:
        rng = random.Random()

    log_o = math.log(open_p)
    log_c = math.log(close_p)
    log_h = math.log(max(high_p, open_p, close_p))
    log_l = math.log(min(low_p,  open_p, close_p))

    # Per-step vol from Parkinson high-low range estimator
    step_vol = (log_h - log_l) / (2.0 * math.sqrt(math.log(4)) * math.sqrt(n))

    # Brownian bridge W_i - (i/n)*W_n : zero at both endpoints
    W = [0.0] * (n + 1)
    for i in range(1, n + 1):
        W[i] = W[i - 1] + rng.gauss(0, 1)
    bridge = [W[i] - (i / n) * W[n] for i in range(n + 1)]

    linear = [log_o + (log_c - log_o) * i / n for i in range(n + 1)]
    path   = [linear[i] + bridge[i] * step_vol for i in range(n + 1)]

    # Squish positive excursions so path <= log_h everywhere
    bind_pos = [
        (log_h - linear[i]) / (path[i] - linear[i])
        for i in range(n + 1)
        if path[i] - linear[i] > 1e-10 and path[i] > log_h
    ]
    if bind_pos:
        sp   = min(bind_pos)
        path = [
            linear[i] + (path[i] - linear[i]) * sp if path[i] > linear[i] else path[i]
            for i in range(n + 1)
        ]

    # Squish negative excursions so path >= log_l everywhere
    bind_neg = [
        (linear[i] - log_l) / (linear[i] - path[i])
        for i in range(n + 1)
        if linear[i] - path[i] > 1e-10 and path[i] < log_l
    ]
    if bind_neg:
        sn   = min(bind_neg)
        path = [
            linear[i] - (linear[i] - path[i]) * sn if path[i] < linear[i] else path[i]
            for i in range(n + 1)
        ]

    return [round(math.exp(p), 2) for p in path]


def _path_to_bars(
    instrument:   str,
    date_obj:     date,
    price_path:   List[float],
    daily_high:   float,
    daily_low:    float,
    daily_volume: float,
    rng:          random.Random,
) -> List[Dict]:
    """
    Convert n+1 price-path points into n 5-minute OHLCV bar dicts.
    Timestamps start at 09:15 IST; volume is U-shaped (heavier at open/close).
    Wicks are clamped so no 5m bar exceeds [daily_low, daily_high].
    """
    n = len(price_path) - 1       # 75 bars
    market_open = datetime(date_obj.year, date_obj.month, date_obj.day, 9, 15, tzinfo=IST)

    # U-shaped volume: Gaussian bumps at session open and close
    wts = [
        0.5 + 1.5 * (
            math.exp(-20 * (i / (n - 1)) ** 2) +
            math.exp(-20 * (1 - i / (n - 1)) ** 2)
        )
        for i in range(n)
    ]
    wt_sum = sum(wts)

    bars: List[Dict] = []
    for i in range(n):
        bar_open  = price_path[i]
        bar_close = price_path[i + 1]

        body = abs(bar_close - bar_open)
        wick = body * rng.uniform(0.05, 0.35) + 0.05
        bar_high = max(bar_open, bar_close) + abs(rng.gauss(0, wick * 0.5))
        bar_low  = min(bar_open, bar_close) - abs(rng.gauss(0, wick * 0.5))
        # Clamp wicks to daily H/L bounds
        bar_high = min(max(bar_high, bar_open, bar_close), daily_high)
        bar_low  = max(min(bar_low,  bar_open, bar_close), daily_low)

        bar_vol = round(daily_volume * (wts[i] / wt_sum) * rng.uniform(0.7, 1.3))

        bars.append({
            "datetime":   market_open + timedelta(minutes=i * 5),
            "instrument": instrument,
            "open":       round(bar_open,  2),
            "high":       round(bar_high,  2),
            "low":        round(bar_low,   2),
            "close":      round(bar_close, 2),
            "volume":     max(100, bar_vol),
        })

    return bars


def generate_nifty_5m_bars(
    start_year: int = 2023,
    end_year:   int = 2024,
    seed:       int = 42,
    instrument: str = "NIFTY",
) -> List[Dict]:
    """
    Generate synthetic 5-minute NIFTY bars calibrated to daily OHLCV anchors.
    Each trading day expands into 75 5m bars via Brownian bridge,
    constrained within the daily [low, high] range.
    Returns dicts: datetime (IST-aware), instrument, open, high, low, close, volume.
    """
    rng   = random.Random(seed)
    daily = generate_nifty_bars(start_year, end_year, seed)

    intraday: List[Dict] = []
    for day in daily:
        path = _make_intraday_path(
            open_p  = day["open"],
            high_p  = day["high"],
            low_p   = day["low"],
            close_p = day["close"],
            n       = 75,
            rng     = rng,
        )
        intraday.extend(_path_to_bars(
            instrument, day["date"], path,
            day["high"], day["low"], day["volume"], rng,
        ))

    return intraday
