"""
Fetches live and historical market data.
In paper/backtest mode: uses yfinance.
In live mode: uses Kite Connect WebSocket.
"""

from __future__ import annotations
import yfinance as yf
import pandas as pd
from datetime import date, datetime, timedelta
from typing import Optional
from src.utils.logger import setup_logger

log = setup_logger("market_data")

SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "SENSEX": "^BSESN",
    "VIX": "^INDIAVIX",
    "DOW": "^DJI",
}


def get_spot_price(symbol: str) -> float:
    """Return latest spot price for symbol (NIFTY, SENSEX, VIX, DOW)."""
    ticker = SYMBOL_MAP.get(symbol.upper(), symbol)
    try:
        data = yf.Ticker(ticker).fast_info
        price = getattr(data, "last_price", None) or getattr(data, "regular_market_price", None)
        if price:
            return float(price)
        hist = yf.download(ticker, period="1d", interval="1m", progress=False)
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.droplevel(1)
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.warning(f"Could not fetch price for {symbol}: {e}")
    return 0.0


def get_day_open_spot(symbol: str) -> float:
    """
    Return today's opening spot price (first 1-min candle open, ~9:15 IST).
    Used as the momentum reference for all expiry scalper windows so that
    restarting the portfolio mid-day never resets the reference.
    Returns 0.0 if data is unavailable (caller should fall back to current spot).
    """
    today = date.today()
    df = get_intraday_ohlcv(symbol, today, interval="1m")
    if df.empty:
        return 0.0
    try:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
        else:
            df.index = df.index.tz_convert("Asia/Kolkata")
    except Exception:
        pass
    open_price = float(df["Open"].iloc[0])
    log.info(f"Day open ({symbol}): {open_price:.1f}  [{df.index[0].strftime('%H:%M')} IST]")
    return open_price


def get_previous_close(symbol: str) -> float:
    """Return previous day's closing price."""
    ticker = SYMBOL_MAP.get(symbol.upper(), symbol)
    try:
        hist = yf.download(ticker, period="5d", interval="1d", progress=False)
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.droplevel(1)
        if len(hist) >= 2:
            return float(hist["Close"].iloc[-2])
    except Exception as e:
        log.warning(f"Could not fetch prev close for {symbol}: {e}")
    return 0.0


def get_india_vix() -> float:
    return get_spot_price("VIX")


def get_intraday_ohlcv(
    symbol: str,
    trade_date: date,
    interval: str = "5m",
) -> pd.DataFrame:
    """
    Fetch intraday 5-minute OHLCV for a given date.
    yfinance supports up to ~60 days of intraday history.
    """
    ticker = SYMBOL_MAP.get(symbol.upper(), symbol)
    start = datetime.combine(trade_date, datetime.min.time())
    end = start + timedelta(days=1)
    try:
        df = yf.download(
            ticker,
            start=start,
            end=end,
            interval=interval,
            progress=False,
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        return df
    except Exception as e:
        log.warning(f"Intraday fetch failed for {symbol} on {trade_date}: {e}")
        return pd.DataFrame()


def get_recent_1min_bars(symbol: str, n: int = 60) -> list:
    """
    Fetch today's 1-min OHLCV bars as a list of Candle objects (yfinance).
    Returns up to `n` most recent completed bars.
    """
    from src.data.candle_builder import Candle
    ticker = SYMBOL_MAP.get(symbol.upper(), symbol)
    try:
        df = yf.download(ticker, period="1d", interval="1m", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        if df.empty:
            return []
        df = df.tail(n)
        return [
            Candle(
                timestamp=ts.to_pydatetime(),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]) if row["Volume"] > 0 else 1,
            )
            for ts, row in df.iterrows()
        ]
    except Exception as e:
        log.warning(f"1-min bars fetch failed for {symbol}: {e}")
        return []


def get_daily_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    ticker = SYMBOL_MAP.get(symbol.upper(), symbol)
    try:
        df = yf.download(ticker, start=start, end=end, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        return df
    except Exception as e:
        log.warning(f"Daily data fetch failed for {symbol}: {e}")
        return pd.DataFrame()


def get_dow_jones_change_pct() -> float:
    """Return Dow Jones % change from previous close (using most recent data)."""
    ticker = "^DJI"
    try:
        hist = yf.download(ticker, period="5d", interval="1d", progress=False)
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.droplevel(1)
        if len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
            last = float(hist["Close"].iloc[-1])
            return ((last - prev) / prev) * 100
    except Exception as e:
        log.warning(f"Dow Jones change fetch failed: {e}")
    return 0.0
