"""
Downloads and caches historical data needed for backtesting.

Sources:
- Nifty 50 spot  : yfinance  ^NSEI
- India VIX      : yfinance  ^INDIAVIX
- Dow Jones      : yfinance  ^DJI
- NSE bhavcopy   : NSE website (options EOD data)

Intraday (5m) data is available from yfinance for the last ~60 days.
For older dates we reconstruct option prices via Black-Scholes.
"""

from __future__ import annotations
import os
import pandas as pd
import yfinance as yf
from datetime import date, timedelta
from src.utils.logger import setup_logger

log = setup_logger("historical_loader")

CACHE_DIR = "data/historical"
os.makedirs(CACHE_DIR, exist_ok=True)

SYMBOLS = {
    "nifty": "^NSEI",
    "vix": "^INDIAVIX",
    "dow": "^DJI",
    "sensex": "^BSESN",
}


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"{name}.csv")


def load_daily(symbol_key: str, start: date, end: date, force_refresh: bool = False) -> pd.DataFrame:
    """Load daily OHLCV with local CSV cache (no extra library needed)."""
    path = _cache_path(symbol_key)
    ticker = SYMBOLS.get(symbol_key.lower(), symbol_key)

    if not force_refresh and os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index)
        cached_start = df.index.min().date()
        cached_end = df.index.max().date()
        if cached_start <= start and cached_end >= end:
            log.info(f"Loaded {symbol_key} from cache ({cached_start} to {cached_end})")
            mask = (df.index.date >= start) & (df.index.date <= end)
            return df[mask]

    log.info(f"Downloading {symbol_key} ({ticker}) from {start} to {end}")
    df = yf.download(ticker, start=start, end=end + timedelta(days=1), progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    if not df.empty:
        df.to_csv(path)
    return df


def load_intraday(symbol_key: str, trade_date: date, interval: str = "5m") -> pd.DataFrame:
    """
    Load intraday data for a specific date.
    yfinance supports ~60 days of intraday history.
    Returns empty DataFrame if date is outside that window.
    """
    ticker = SYMBOLS.get(symbol_key.lower(), symbol_key)
    start = pd.Timestamp(trade_date)
    end = start + pd.Timedelta(days=1)
    try:
        df = yf.download(ticker, start=start, end=end, interval=interval, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        return df
    except Exception as e:
        log.warning(f"Intraday load failed for {symbol_key} on {trade_date}: {e}")
        return pd.DataFrame()


def build_backtest_dataset(start: date, end: date) -> pd.DataFrame:
    """
    Build a merged daily dataset with Nifty, VIX, Dow for backtesting.
    Returns DataFrame indexed by date with columns:
    nifty_open, nifty_high, nifty_low, nifty_close,
    vix_close, dow_close, dow_prev_close, dow_change_pct
    """
    nifty = load_daily("nifty", start, end)
    vix = load_daily("vix", start, end)
    dow = load_daily("dow", start - timedelta(days=5), end)

    if nifty.empty:
        log.error("Nifty data unavailable. Check internet connection.")
        return pd.DataFrame()

    df = pd.DataFrame(index=nifty.index)
    df["nifty_open"] = nifty["Open"]
    df["nifty_high"] = nifty["High"]
    df["nifty_low"] = nifty["Low"]
    df["nifty_close"] = nifty["Close"]
    df["nifty_prev_close"] = nifty["Close"].shift(1)

    if not vix.empty:
        df["vix_close"] = vix["Close"].reindex(df.index, method="ffill")
    else:
        df["vix_close"] = 15.0  # default if VIX data unavailable
        log.warning("India VIX data unavailable; using default 15.0")

    if not dow.empty:
        dow_daily = dow["Close"].resample("D").last().ffill()
        df["dow_close"] = dow_daily.reindex(df.index, method="ffill")
        df["dow_prev_close"] = df["dow_close"].shift(1)
        df["dow_change_pct"] = (
            (df["dow_close"] - df["dow_prev_close"]) / df["dow_prev_close"] * 100
        )
    else:
        df["dow_close"] = 0.0
        df["dow_change_pct"] = 0.0
        log.warning("Dow Jones data unavailable")

    df.dropna(subset=["nifty_close", "nifty_prev_close"], inplace=True)
    return df
