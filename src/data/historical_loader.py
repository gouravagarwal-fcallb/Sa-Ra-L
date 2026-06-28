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


_INTRADAY_CACHE_DIR = os.path.join("data", "intraday_cache")


def _intraday_cache_path(symbol_key: str, trade_date: date, interval: str, src: str) -> str:
    return os.path.join(_INTRADAY_CACHE_DIR, f"{symbol_key.lower()}_{trade_date}_{interval}_{src}.pkl")


def load_intraday(symbol_key: str, trade_date: date, interval: str = "5m") -> pd.DataFrame:
    """
    Load intraday bars for one date. Prefers Kite (deep history) when enabled,
    else yfinance (~60 days). Past dates are cached to disk so deep / multi-
    strategy backtests fetch each day only once (big speedup, fewer API calls).
    """
    from src.data import kite_historical
    kite_on = False
    try:
        kite_on = kite_historical.is_enabled()
    except Exception:
        pass
    src = "kite" if kite_on else "yf"

    # ── Disk cache (only for completed past days — they never change) ─────────
    is_past = trade_date < date.today()
    cpath = _intraday_cache_path(symbol_key, trade_date, interval, src)
    if is_past:
        try:
            if os.path.exists(cpath):
                return pd.read_pickle(cpath)
        except Exception:
            pass

    df = pd.DataFrame()
    if kite_on:
        try:
            kdf = kite_historical.load_intraday_kite(symbol_key, trade_date, interval)
            if kdf is not None and not kdf.empty:
                df = kdf
        except Exception:
            pass

    if df.empty:
        ticker = SYMBOLS.get(symbol_key.lower(), symbol_key)
        start = pd.Timestamp(trade_date)
        end = start + pd.Timedelta(days=1)
        try:
            ydf = yf.download(ticker, start=start, end=end, interval=interval, progress=False)
            if isinstance(ydf.columns, pd.MultiIndex):
                ydf.columns = ydf.columns.droplevel(1)
            df = ydf
        except Exception as e:
            log.warning(f"Intraday load failed for {symbol_key} on {trade_date}: {e}")
            df = pd.DataFrame()

    if is_past and df is not None and not df.empty:
        try:
            os.makedirs(_INTRADAY_CACHE_DIR, exist_ok=True)
            df.to_pickle(cpath)
        except Exception:
            pass
    return df


def build_backtest_dataset(start: date, end: date) -> pd.DataFrame:
    """
    Build a merged daily dataset for backtesting.

    Columns:
      Nifty : nifty_open, nifty_high, nifty_low, nifty_close, nifty_prev_close
      Sensex: sensex_open, sensex_close, sensex_prev_close
      Other : vix_close, dow_close, dow_change_pct

    Thursday rows use Sensex OHLC; all other rows use Nifty.
    """
    nifty = load_daily("nifty", start, end)
    sensex = load_daily("sensex", start, end)
    vix = load_daily("vix", start, end)
    dow = load_daily("dow", start - timedelta(days=5), end)

    if nifty.empty:
        log.error("Nifty data unavailable. Check internet connection.")
        return pd.DataFrame()

    df = pd.DataFrame(index=nifty.index)

    # ── Nifty OHLC ────────────────────────────────────────────────────────────
    df["nifty_open"] = nifty["Open"]
    df["nifty_high"] = nifty["High"]
    df["nifty_low"] = nifty["Low"]
    df["nifty_close"] = nifty["Close"]
    df["nifty_prev_close"] = nifty["Close"].shift(1)

    # ── Sensex OHLC (used on Thursdays) ──────────────────────────────────────
    if not sensex.empty:
        sensex_r = sensex.reindex(df.index)
        df["sensex_open"] = sensex_r["Open"]
        df["sensex_close"] = sensex_r["Close"]
        df["sensex_prev_close"] = sensex_r["Close"].shift(1)
    else:
        df["sensex_open"] = 0.0
        df["sensex_close"] = 0.0
        df["sensex_prev_close"] = 0.0
        log.warning("Sensex data unavailable; Thursday trades will be skipped")

    # ── India VIX ─────────────────────────────────────────────────────────────
    if not vix.empty:
        df["vix_close"] = vix["Close"].reindex(df.index, method="ffill")
    else:
        df["vix_close"] = 15.0
        log.warning("India VIX data unavailable; using default 15.0")

    # ── Dow Jones (pre-market directional signal) ─────────────────────────────
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
