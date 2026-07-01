"""
kite_data.py
============
Chunked historical-data fetcher for Zerodha Kite Connect, built to feed
gti_backtest.py. Handles the three things that make raw historical_data() calls
painful for multi-month pulls:

    1. PER-REQUEST DAY CAPS  — Kite limits how many days you can pull per call
       (minute=60d, 3/5/10-min~100d, 15/30-min~200d, hourly~400d, day~2000d).
       This module auto-chunks the [from, to] range into legal windows.

    2. RATE LIMITS — the historical endpoint allows ~3 req/sec. We throttle with
       a min-interval clock + jitter, and back off exponentially on 429 /
       "Too many requests" errors.

    3. RE-FETCHING — results are cached to CSV per (token, interval). Re-runs only
       fetch the missing date gaps (incremental), so pulling "last 6 months" the
       second time is nearly free.

Output is a clean OHLCV DataFrame (DatetimeIndex, IST) ready for detect_zones()
and run_backtest().

Usage
-----
    from kiteconnect import KiteConnect
    from kite_data import fetch_history
    from gti_backtest import run_backtest, print_report

    kite = KiteConnect(api_key="...")
    kite.set_access_token("...")

    # NIFTY 50 spot token = 256265 ; use the FUT token if you want real volume
    df = fetch_history(kite, 256265, "2026-01-01", "2026-06-30",
                       interval="5minute", cache_dir="./cache")
    print_report(run_backtest(df))

Note: the historical API is a paid Kite add-on and is intended for backtesting.
      Nothing here places orders or touches your funds.
"""

from __future__ import annotations

import os
import time as _time
import random
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Union

import pandas as pd

try:
    # reuse the clean converter from the zones module if it's importable
    from src.research.gti.gti_zones import candles_to_df
except Exception:  # pragma: no cover - fallback if run standalone
    def candles_to_df(records: list) -> pd.DataFrame:
        df = pd.DataFrame(records)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[keep].astype(float)


# Legal per-request span (days) by interval. Kept a touch below the hard cap
# as a safety margin against boundary/holiday edge cases.
INTERVAL_MAX_DAYS = {
    "minute": 55,
    "3minute": 90,
    "5minute": 90,
    "10minute": 90,
    "15minute": 180,
    "30minute": 180,
    "60minute": 360,
    "day": 1800,
}

# how many OHLCV rows a full session yields (rough, for progress messages only)
_ROWS_PER_DAY = {"minute": 375, "3minute": 125, "5minute": 75, "10minute": 38,
                 "15minute": 25, "30minute": 13, "60minute": 7, "day": 1}


def _to_dt(x: Union[str, datetime]) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x))


def _date_chunks(from_dt: datetime, to_dt: datetime, span_days: int
                 ) -> List[Tuple[datetime, datetime]]:
    """Split [from, to] into consecutive windows no longer than span_days."""
    chunks = []
    start = from_dt
    step = timedelta(days=span_days)
    while start <= to_dt:
        end = min(start + step - timedelta(days=1), to_dt)
        chunks.append((start, end))
        start = end + timedelta(days=1)
    return chunks


def _is_rate_limit_error(err: Exception) -> bool:
    msg = str(err).lower()
    return ("too many requests" in msg or "429" in msg
            or err.__class__.__name__ in ("TooManyRequests", "NetworkException"))


class _Throttle:
    """Enforce a minimum interval between calls (with jitter)."""
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self):
        now = _time.monotonic()
        gap = now - self._last
        need = self.min_interval - gap
        if need > 0:
            _time.sleep(need + random.uniform(0, 0.05))
        self._last = _time.monotonic()


def _cache_path(cache_dir: str, token: int, interval: str) -> str:
    return os.path.join(cache_dir, f"{token}_{interval}.csv")


def _load_cache(path: str) -> Optional[pd.DataFrame]:
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
        return df.sort_index()
    except Exception:
        return None


def _missing_gaps(cached: Optional[pd.DataFrame],
                  from_dt: datetime, to_dt: datetime
                  ) -> List[Tuple[datetime, datetime]]:
    """Ranges within [from, to] not already covered by the cache."""
    if cached is None or cached.empty:
        return [(from_dt, to_dt)]
    cmin = cached.index.min().to_pydatetime()
    cmax = cached.index.max().to_pydatetime()
    gaps = []
    if from_dt < cmin:
        gaps.append((from_dt, min(cmin - timedelta(days=1), to_dt)))
    if to_dt > cmax:
        gaps.append((max(cmax + timedelta(days=1), from_dt), to_dt))
    return [(a, b) for a, b in gaps if a <= b]


def fetch_history(
    kite,
    instrument_token: int,
    from_date: Union[str, datetime],
    to_date: Union[str, datetime],
    interval: str = "5minute",
    continuous: bool = False,
    oi: bool = False,
    cache_dir: Optional[str] = "./cache",
    throttle_sec: float = 0.34,          # ~2.9 req/s, under the 3/s cap
    max_retries: int = 5,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles for one instrument across an arbitrary date range,
    auto-chunked and rate-limited, with incremental CSV caching.

    Parameters
    ----------
    kite        : authenticated KiteConnect instance
    instrument_token : e.g. 256265 for NIFTY 50 spot (use FUT token for volume)
    interval    : 'minute','3minute','5minute','10minute','15minute',
                  '30minute','60minute','day'
    continuous  : True to stitch expired FUT/MCX contracts (needs a live token)
    cache_dir   : folder for per-instrument CSV cache; None disables caching
    throttle_sec: min seconds between API calls
    """
    if interval not in INTERVAL_MAX_DAYS:
        raise ValueError(f"Unsupported interval '{interval}'. "
                         f"Use one of {list(INTERVAL_MAX_DAYS)}")

    from_dt, to_dt = _to_dt(from_date), _to_dt(to_date)
    if from_dt > to_dt:
        raise ValueError("from_date must be <= to_date")

    span = INTERVAL_MAX_DAYS[interval]
    throttle = _Throttle(throttle_sec)

    cache_path = None
    cached = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = _cache_path(cache_dir, instrument_token, interval)
        cached = _load_cache(cache_path)

    gaps = _missing_gaps(cached, from_dt, to_dt)
    if verbose:
        have = 0 if cached is None else len(cached)
        print(f"[kite_data] token={instrument_token} {interval} | "
              f"cache rows={have} | fetching {len(gaps)} gap(s)")

    new_frames: List[pd.DataFrame] = []
    for gap_from, gap_to in gaps:
        for (c_from, c_to) in _date_chunks(gap_from, gap_to, span):
            df_chunk = _fetch_chunk(kite, instrument_token, c_from, c_to, interval,
                                    continuous, oi, throttle, max_retries, verbose)
            if df_chunk is not None and not df_chunk.empty:
                new_frames.append(df_chunk)

    # ---- merge cache + new, dedupe, sort, clip to requested range ----
    frames = [f for f in ([cached] + new_frames) if f is not None and not f.empty]
    if not frames:
        if verbose:
            print("[kite_data] no data returned")
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    full = pd.concat(frames)
    full = full[~full.index.duplicated(keep="last")].sort_index()

    if cache_path:
        full.to_csv(cache_path, index_label="date")
        if verbose:
            print(f"[kite_data] cache updated -> {cache_path} ({len(full)} rows)")

    mask = (full.index >= pd.Timestamp(from_dt)) & \
           (full.index <= pd.Timestamp(to_dt) + pd.Timedelta(days=1))
    return full.loc[mask]


def _fetch_chunk(kite, token, c_from, c_to, interval, continuous, oi,
                 throttle: _Throttle, max_retries: int, verbose: bool
                 ) -> Optional[pd.DataFrame]:
    """One rate-limited, retried historical_data() call for a single window."""
    attempt = 0
    while True:
        throttle.wait()
        try:
            records = kite.historical_data(
                token, c_from, c_to, interval,
                continuous=continuous, oi=oi,
            )
            if verbose:
                rows = len(records) if records else 0
                print(f"    {c_from.date()} -> {c_to.date()}: {rows} rows")
            return candles_to_df(records) if records else None
        except Exception as err:  # noqa: BLE001 - kiteconnect exc types vary
            attempt += 1
            if attempt > max_retries:
                print(f"    ! giving up {c_from.date()}->{c_to.date()}: {err}")
                return None
            backoff = (2 ** attempt) * 0.5 + random.uniform(0, 0.4)
            if _is_rate_limit_error(err):
                print(f"    ~ rate-limited, backoff {backoff:.1f}s "
                      f"(attempt {attempt}/{max_retries})")
            else:
                print(f"    ~ error '{err}', retry in {backoff:.1f}s "
                      f"(attempt {attempt}/{max_retries})")
            _time.sleep(backoff)


# convenience wrapper matching gti_zones.fetch_kite_candles signature
def load_for_backtest(kite, instrument_token, from_date, to_date,
                      interval="5minute", **kw) -> pd.DataFrame:
    return fetch_history(kite, instrument_token, from_date, to_date,
                         interval=interval, **kw)


# --------------------------------------------------------------------------- #
# Self-test with a MOCK kite (no network) — validates chunking/cache/gap logic
# --------------------------------------------------------------------------- #
class _MockKite:
    """Fake kite that returns synthetic candles; counts calls & records windows."""
    def __init__(self):
        self.calls: List[Tuple[datetime, datetime]] = []

    def historical_data(self, token, from_dt, to_dt, interval,
                        continuous=False, oi=False):
        self.calls.append((from_dt, to_dt))
        out, t, price = [], pd.Timestamp(from_dt).replace(hour=9, minute=15), 22000.0
        end = pd.Timestamp(to_dt)
        while t <= end:
            if t.weekday() < 5 and 9 <= t.hour < 16:
                out.append({"date": t.to_pydatetime(), "open": price,
                            "high": price + 5, "low": price - 5,
                            "close": price + 1, "volume": 1000})
                price += 1
            t += pd.Timedelta(minutes=5)
            if t.hour >= 16:
                t = (t + pd.Timedelta(days=1)).replace(hour=9, minute=15)
        return out


def _self_test():
    print("=== chunking ===")
    ch = _date_chunks(datetime(2026, 1, 1), datetime(2026, 6, 30), 90)
    for a, b in ch:
        print(f"  {a.date()} -> {b.date()}  ({(b-a).days+1}d)")
    assert all((b - a).days + 1 <= 90 for a, b in ch)

    print("\n=== first fetch (cold cache) ===")
    mk = _MockKite()
    tmp = "/tmp/kite_cache_test"
    if os.path.exists(tmp):
        import shutil; shutil.rmtree(tmp)
    df1 = fetch_history(mk, 256265, "2026-05-01", "2026-06-30", "5minute",
                        cache_dir=tmp, throttle_sec=0.0)
    print(f"  rows={len(df1)}  api_calls={len(mk.calls)}")

    print("\n=== second fetch, SAME range (should hit cache, 0 new calls) ===")
    mk2 = _MockKite()
    df2 = fetch_history(mk2, 256265, "2026-05-01", "2026-06-30", "5minute",
                        cache_dir=tmp, throttle_sec=0.0)
    print(f"  rows={len(df2)}  api_calls={len(mk2.calls)} (expect 0)")
    assert len(mk2.calls) == 0

    print("\n=== third fetch, EXTENDED range (only the new gap is fetched) ===")
    mk3 = _MockKite()
    df3 = fetch_history(mk3, 256265, "2026-05-01", "2026-07-15", "5minute",
                        cache_dir=tmp, throttle_sec=0.0)
    print(f"  rows={len(df3)}  api_calls={len(mk3.calls)} "
          f"(only ~Jul gap, not the whole range)")
    for a, b in mk3.calls:
        print(f"    fetched {a.date()} -> {b.date()}")

    print("\n=== integration: feed into detector + backtest ===")
    try:
        from src.research.gti.gti_zones import detect_zones
        from src.research.gti.gti_backtest import run_backtest, print_report
        z = detect_zones(df3)
        print(f"  zones detected on mock data: {len(z)}")
        print_report(run_backtest(df3))
    except Exception as e:
        print(f"  (skipped integration: {e})")

    print("\nAll self-tests passed.")


if __name__ == "__main__":
    _self_test()
