"""
Kite Historical Data Source (for deep intraday backtests)
─────────────────────────────────────────────────────────
yfinance only serves intraday bars for the last ~30–60 days. Zerodha Kite's
historical API serves intraday candles back to ~2015, so this module lets the
backtest engine pull real 1-/5-/15-min/hourly bars from Kite instead.

Enable it from the CLI:  python main.py --mode backtest --strategy X --source kite
(That calls `enable(settings)`; the loader then routes intraday fetches here and
falls back to yfinance on any failure.)

Notes / honest limits:
  • Kite minute history starts ~2015 (≈10 years), not 20.
  • Index spot candles (NIFTY 50 / SENSEX) carry volume = 0, so volume-surge
    filters won't trigger on them. For volume-sensitive strategies a futures
    underlying is more faithful — a future enhancement.
  • One request per trading day; throttled to respect Kite's ~3 req/s limit, so
    a multi-year run takes several minutes.
"""
from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta, timezone, date

import pandas as pd

from src.utils.logger import setup_logger

log = setup_logger("kite_historical")
IST = timezone(timedelta(hours=5, minutes=30))

_INTERVAL = {"1m": "minute", "5m": "5minute", "15m": "15minute",
             "1h": "60minute", "60m": "60minute", "1d": "day"}

_state = {"enabled": False, "broker": None, "last_call": 0.0}
_lock = threading.Lock()
_MIN_GAP = 0.34          # seconds between historical calls (~3 req/s)


def enable(settings: dict) -> bool:
    """Create a Kite broker from settings and route intraday loads through it."""
    try:
        from src.broker.kite_broker import create_kite_broker
        broker = create_kite_broker(settings)
        with _lock:
            _state.update(enabled=True, broker=broker)
        log.info("Kite historical data source ENABLED for backtests.")
        return True
    except Exception as e:
        log.error(f"Could not enable Kite historical source: {e}")
        with _lock:
            _state.update(enabled=False, broker=None)
        return False


def disable() -> None:
    with _lock:
        _state.update(enabled=False, broker=None)


def is_enabled() -> bool:
    return bool(_state.get("enabled") and _state.get("broker"))


def _throttle() -> None:
    with _lock:
        gap = time.time() - _state["last_call"]
        if gap < _MIN_GAP:
            time.sleep(_MIN_GAP - gap)
        _state["last_call"] = time.time()


def load_intraday_kite(symbol_key: str, trade_date: date,
                       interval: str = "5m") -> pd.DataFrame:
    """
    Fetch one day's OHLCV from Kite, formatted like load_intraday (yfinance):
    DatetimeIndex + Open/High/Low/Close/Volume columns. Empty df on failure.
    """
    if not is_enabled():
        return pd.DataFrame()
    broker = _state["broker"]
    kite_interval = _INTERVAL.get(interval)
    if kite_interval is None:
        return pd.DataFrame()
    try:
        token = broker.get_index_token(symbol_key.upper()
                                       if symbol_key.upper() in ("NIFTY", "SENSEX")
                                       else "NIFTY")
        frm = datetime(trade_date.year, trade_date.month, trade_date.day, 9, 15, tzinfo=IST)
        to  = datetime(trade_date.year, trade_date.month, trade_date.day, 15, 30, tzinfo=IST)
        _throttle()
        raw = broker._kite.historical_data(token, frm, to, kite_interval, continuous=False)
        if not raw:
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                "close": "Close", "volume": "Volume"})
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        log.warning(f"Kite historical fetch failed for {symbol_key} {trade_date} "
                    f"{interval}: {type(e).__name__} {str(e)[:80]} — falling back to yfinance.")
        return pd.DataFrame()
