"""
INRUSD Data Fetcher
━━━━━━━━━━━━━━━━━━━
Downloads USDINR spot and related currency/macro data from yfinance.

For backtesting we use USDINR=X (spot rate) as a proxy for the front-month
futures.  The futures premium/discount vs spot is typically very small (< 0.2%)
and for trend-following purposes the two series move in lockstep.

For live trading, futures quotes come from NSE via Kite Connect tick stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class DailyBar:
    date:   date
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def change_pct(self) -> float:
        return (self.close - self.open) / self.open * 100 if self.open else 0.0


@dataclass
class PreSessionSnapshot:
    """Global data captured at 08:45 IST before trading begins."""
    date:              date
    dxy_change_pct:    Optional[float] = None   # DXY overnight % change
    crude_change_pct:  Optional[float] = None   # WTI crude % change
    us_10y_change_bps: Optional[float] = None   # US 10Y yield change in bps
    eurusd_change_pct: Optional[float] = None   # EUR/USD % change
    india_vix:         Optional[float] = None   # India VIX current level
    usdinr_prev_close: Optional[float] = None   # Yesterday's USDINR close
    errors:            list[str] = field(default_factory=list)
    fetched_at:        datetime = field(default_factory=lambda: datetime.now(IST))


def fetch_daily_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    verbose: bool = False,
) -> list[DailyBar]:
    """
    Download daily OHLCV bars for a yfinance symbol.
    Returns an empty list on failure (caller handles gracefully).
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(start=start_date, end=end_date, interval="1d", auto_adjust=True)
        if hist.empty:
            if verbose:
                print(f"  [data] no data for {symbol} ({start_date} – {end_date})")
            return []
        bars = []
        for ts, row in hist.iterrows():
            dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            d  = dt.date() if hasattr(dt, "date") else dt
            bars.append(DailyBar(
                date=d,
                open=float(row.get("Open",  0) or 0),
                high=float(row.get("High",  0) or 0),
                low=float(row.get("Low",   0) or 0),
                close=float(row.get("Close", 0) or 0),
                volume=float(row.get("Volume", 0) or 0),
            ))
        if verbose:
            print(f"  [data] {symbol}: {len(bars)} bars ({bars[0].date} – {bars[-1].date})")
        return bars
    except Exception as e:
        if verbose:
            print(f"  [data] fetch error for {symbol}: {e}")
        return []


def fetch_pre_session_snapshot(verbose: bool = False) -> PreSessionSnapshot:
    """
    Fetch live pre-session data at 08:45 IST.
    Returns a snapshot with whatever data is available; errors are logged
    inside the object so the caller can still proceed.
    """
    from datetime import date as date_cls
    snap = PreSessionSnapshot(date=date_cls.today())

    SOURCES = [
        ("dxy",     "DX-Y.NYB",  "dxy_change_pct",    "pct"),
        ("crude",   "CL=F",       "crude_change_pct",   "pct"),
        ("us10y",   "^TNX",       "us_10y_change_bps",  "bps"),  # yield in %; bps = change × 100
        ("eurusd",  "EURUSD=X",   "eurusd_change_pct",  "pct"),
        ("indiavix","^INDIAVIX",  "india_vix",          "level"),
        ("usdinr",  "USDINR=X",   "usdinr_prev_close",  "level"),
    ]

    try:
        import yfinance as yf
    except ImportError:
        snap.errors.append("yfinance not installed")
        return snap

    for name, symbol, field_name, mode in SOURCES:
        try:
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(period="2d", interval="1d", auto_adjust=True)
            if hist.empty or len(hist) < 1:
                snap.errors.append(f"no data for {symbol}")
                continue

            closes = hist["Close"].dropna().tolist()
            if mode == "level":
                setattr(snap, field_name, float(closes[-1]))
            elif mode == "pct" and len(closes) >= 2:
                chg = (closes[-1] - closes[-2]) / closes[-2] * 100
                setattr(snap, field_name, round(chg, 4))
            elif mode == "bps" and len(closes) >= 2:
                chg_bps = (closes[-1] - closes[-2]) * 100   # yield already in %
                setattr(snap, field_name, round(chg_bps, 2))
            else:
                snap.errors.append(f"insufficient history for {symbol}")

            if verbose:
                print(f"  [presession] {name}: {getattr(snap, field_name, 'N/A')}")
        except Exception as e:
            snap.errors.append(f"{symbol}: {e}")

    return snap
