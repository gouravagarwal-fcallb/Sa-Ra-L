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

# ── Futures-volume overlay ────────────────────────────────────────────────────
# Index spot candles (NIFTY 50 / SENSEX) carry volume = 0, so volume-surge filters
# (RAMS, TREND_RIDER, ATM_PULSE_BURST) never trigger — they fired 0 trades in the
# net backtest. When this is on, we fetch the near-month FUTURES series (real
# volume) and overlay ITS volume onto the spot bars, keeping spot OHLC so strikes
# and gap maths stay correct. Opt-in (off by default) — set SARAL_FUTURES_VOLUME=1
# or call set_futures_volume(True). Each fetch is one extra throttled call/day and
# degrades silently to spot (volume=0) if the futures contract can't be resolved.
import os as _os
_futures_volume = {"on": _os.environ.get("SARAL_FUTURES_VOLUME", "") not in ("", "0", "false", "False")}
_fut_token_cache: dict[str, int] = {}


def set_futures_volume(on: bool) -> None:
    _futures_volume["on"] = bool(on)


def futures_volume_on() -> bool:
    return bool(_futures_volume["on"])


def _resolve_futures_token(symbol_key: str) -> int | None:
    """Near-month NIFTY/SENSEX futures instrument_token (used with continuous=True
    to stitch deep history). Cached; returns None if the instrument list is
    unavailable so the caller falls back to spot."""
    sym = symbol_key.upper()
    if sym not in ("NIFTY", "SENSEX"):
        return None
    if sym in _fut_token_cache:
        return _fut_token_cache[sym]
    broker = _state.get("broker")
    if broker is None:
        return None
    try:
        broker._refresh_instruments()
        pool = broker._instruments_bfo if sym == "SENSEX" else broker._instruments_nfo
        futs = [i for i in (pool or [])
                if str(i.get("name", "")).upper() == sym
                and str(i.get("instrument_type", "")).upper() == "FUT"]
        if not futs:
            return None
        futs.sort(key=lambda i: i.get("expiry") or date.max)
        token = int(futs[0]["instrument_token"])
        _fut_token_cache[sym] = token
        return token
    except Exception as e:
        log.warning(f"Futures token resolve failed for {sym}: {type(e).__name__} {str(e)[:60]}")
        return None


def enable(settings: dict) -> bool:
    """Create a Kite broker and verify the Historical Data API actually works
    (it's a separate paid add-on). Probes one small fetch up front so a missing
    subscription fails loudly instead of silently degrading to yfinance on every
    one of thousands of per-day calls."""
    try:
        from src.broker.kite_broker import create_kite_broker
        broker = create_kite_broker(settings)
    except Exception as e:
        print(f"\n  [!] Kite login failed: {str(e)[:140]}\n"
              f"      Run: python main.py --mode login\n")
        with _lock:
            _state.update(enabled=False, broker=None)
        return False

    # ── Probe: a small daily historical fetch to confirm data access works ────
    try:
        from datetime import datetime, timedelta
        to  = datetime.now(IST)
        frm = to - timedelta(days=7)
        raw = broker._kite.historical_data(256265, frm, to, "day", continuous=False)
        if not raw:
            raise RuntimeError("historical_data returned empty")
    except Exception as e:
        print("\n  ╔══════════════════════════════════════════════════════════════╗")
        print("  ║  Kite historical data probe FAILED.                           ║")
        print("  ╚══════════════════════════════════════════════════════════════╝")
        print(f"      Exact error: {type(e).__name__}: {str(e)[:160]}")
        print("      Historical data is INCLUDED with a PAID Kite Connect plan")
        print("      (since Feb 2025 — no separate add-on). Likely causes:")
        print("        • You're on the FREE/personal Connect plan (no live/historical")
        print("          data) — upgrade to paid Kite Connect at kite.trade, OR")
        print("        • a token/permission issue (re-run: python main.py --mode login), OR")
        print("        • an API-format issue (paste the exact error above to me).")
        print("      Meanwhile, deep history without intraday: 20-year STRUCTURAL —")
        print("        python main.py --mode brahmastra_bt --strategy BRAHMASTRA_v1\n")
        with _lock:
            _state.update(enabled=False, broker=None)
        return False

    with _lock:
        _state.update(enabled=True, broker=broker)
    log.info("Kite historical data source ENABLED (probe OK).")
    print("  ✓ Kite historical data verified — deep intraday backtests available.")
    return True


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
        # Only NIFTY/SENSEX have index tokens here. For anything else (e.g. USDINR)
        # do NOT silently fall back to the NIFTY token — that renders NIFTY candles
        # under the wrong label. Return empty so the caller degrades to yfinance.
        if symbol_key.upper() not in ("NIFTY", "SENSEX"):
            return pd.DataFrame()
        token = broker.get_index_token(symbol_key.upper())
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
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        # Overlay real futures volume onto spot bars (spot index volume is 0).
        if futures_volume_on() and symbol_key.upper() in ("NIFTY", "SENSEX"):
            df = _overlay_futures_volume(df, symbol_key, trade_date, kite_interval)
        return df
    except Exception as e:
        log.warning(f"Kite historical fetch failed for {symbol_key} {trade_date} "
                    f"{interval}: {type(e).__name__} {str(e)[:80]} — falling back to yfinance.")
        return pd.DataFrame()


def _resample_minute_bars(bars: list, n: int) -> list:
    """Aggregate 1-minute OHLCV dicts into n-minute candles (bucket label = bucket
    start time). Tolerant of common timestamp formats; assumes chronological input.
    Used to synthesise 3-minute candles, which Kite does not serve natively."""
    from datetime import datetime as _dtcls

    def _parse(t):
        if isinstance(t, (int, float)):
            return _dtcls.fromtimestamp(t)
        s = str(t)
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return _dtcls.strptime(s, fmt)
            except Exception:
                pass
        try:
            return _dtcls.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    buckets, order = {}, []
    for b in bars or []:
        dt = _parse(b.get("t"))
        if dt is None:
            continue
        fm = (dt.minute // n) * n
        key = dt.replace(minute=fm, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        h, l, c, v = b.get("h"), b.get("l"), b.get("c"), b.get("v", 0) or 0
        if key not in buckets:
            buckets[key] = {"t": key, "o": b.get("o"), "h": h, "l": l, "c": c, "v": v}
            order.append(key)
        else:
            agg = buckets[key]
            if h is not None:
                agg["h"] = h if agg["h"] is None else max(agg["h"], h)
            if l is not None:
                agg["l"] = l if agg["l"] is None else min(agg["l"], l)
            agg["c"] = c
            agg["v"] = (agg["v"] or 0) + (v or 0)
    return [buckets[k] for k in order]


def fetch_range(symbol_key: str, frm: datetime, to: datetime,
                interval: str = "5m") -> list:
    """Fetch OHLCV dicts for an arbitrary datetime range + interval (for the live
    dashboard charts). Returns [{t,o,h,l,c,v}] like the yfinance backfill, or [] on
    any failure so the caller degrades to yfinance. Reuses the same broker/token/
    throttle path as load_intraday_kite (which is proven on this account)."""
    if not is_enabled():
        return []
    broker = _state["broker"]
    # 3-minute candles aren't a native Kite interval — fetch 1-minute and resample.
    if interval == "3m":
        return _resample_minute_bars(fetch_range(symbol_key, frm, to, "1m"), 3)
    kite_interval = _INTERVAL.get(interval)
    if kite_interval is None:
        return []
    try:
        sym = symbol_key.upper()
        # No Kite index token for non-index instruments (e.g. USDINR) — never fall
        # back to the NIFTY token (that mislabels NIFTY as USDINR). Degrade to yfinance.
        if sym not in ("NIFTY", "SENSEX"):
            return []
        token = broker.get_index_token(sym)
        _throttle()
        raw = broker._kite.historical_data(token, frm, to, kite_interval, continuous=False)
        if not raw:
            return []
        out = []
        for r in raw:
            ts = r["date"]
            tstr = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            out.append({"t": tstr,
                        "o": round(float(r["open"]), 2), "h": round(float(r["high"]), 2),
                        "l": round(float(r["low"]), 2), "c": round(float(r["close"]), 2),
                        "v": int(r.get("volume", 0) or 0)})
        return out
    except Exception as e:
        log.warning(f"Kite chart fetch failed {symbol_key} {interval}: "
                    f"{type(e).__name__} {str(e)[:80]}")
        return []


def fetch_futures_range(symbol_key: str, frm: datetime, to: datetime,
                        interval: str = "5m") -> list:
    """Near-month FUTURES OHLCV dicts [{t,o,h,l,c,v}] for an index — the REAL traded
    volume that the spot series lacks (index spot volume is always 0). Mirrors
    fetch_range but resolves the near-month FUT token instead of the spot token.

    Deliberately kept SEPARATE from the spot chart path so the always-on chart feed
    is never slowed or altered by this. Returns [] on any failure (Kite off, no
    subscription, token unresolvable) so the caller degrades honestly to
    'no volume'. Used only by the observe-only flow-metrics panel — never an order
    path."""
    if not is_enabled():
        return []
    # 3-minute candles aren't a native Kite interval — fetch 1-minute and resample.
    if interval == "3m":
        return _resample_minute_bars(fetch_futures_range(symbol_key, frm, to, "1m"), 3)
    kite_interval = _INTERVAL.get(interval)
    if kite_interval is None:
        return []
    token = _resolve_futures_token(symbol_key)
    if not token:
        return []
    try:
        broker = _state["broker"]
        _throttle()
        raw = broker._kite.historical_data(token, frm, to, kite_interval, continuous=False)
        if not raw:
            return []
        out = []
        for r in raw:
            ts = r["date"]
            tstr = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            out.append({"t": tstr,
                        "o": round(float(r["open"]), 2), "h": round(float(r["high"]), 2),
                        "l": round(float(r["low"]), 2), "c": round(float(r["close"]), 2),
                        "v": int(r.get("volume", 0) or 0)})
        return out
    except Exception as e:
        log.warning(f"Kite futures fetch failed {symbol_key} {interval}: "
                    f"{type(e).__name__} {str(e)[:80]}")
        return []


def get_vix() -> float | None:
    """Current India VIX via Kite LTP, or None if unavailable."""
    if not is_enabled():
        return None
    try:
        broker = _state["broker"]
        data = broker._kite.ltp(["NSE:INDIA VIX"])
        return float(list(data.values())[0]["last_price"])
    except Exception:
        return None


def get_option_chain_metrics(instrument: str = "NIFTY") -> tuple:
    """Compute (PCR, Max-Pain strike) from Kite option-chain Open Interest for the
    nearest weekly expiry — used as a fallback for the pre-market page when NSE
    scraping is blocked. Returns (None, None) if Kite is unavailable."""
    if not is_enabled():
        return (None, None)
    try:
        from datetime import date as _date
        broker = _state["broker"]
        broker._refresh_instruments()
        name = instrument.upper()
        exch = "BFO" if name == "SENSEX" else "NFO"
        pool = broker._instruments_bfo if name == "SENSEX" else broker._instruments_nfo
        opts = [i for i in (pool or [])
                if str(i.get("name", "")).upper() == name
                and str(i.get("instrument_type", "")).upper() in ("CE", "PE")
                and i.get("expiry")]
        if not opts:
            return (None, None)
        future = sorted({i["expiry"] for i in opts if i["expiry"] >= _date.today()})
        if not future:
            return (None, None)
        chain = [i for i in opts if i["expiry"] == future[0]]
        keys = [f"{exch}:{i['tradingsymbol']}" for i in chain]
        oi_by = {}
        for k in range(0, len(keys), 400):
            _throttle()
            oi_by.update(broker._kite.quote(keys[k:k + 400]))
        ce_oi = pe_oi = 0.0
        strike_oi: dict = {}
        for i in chain:
            qq = oi_by.get(f"{exch}:{i['tradingsymbol']}") or {}
            oi = float(qq.get("oi") or 0)
            strike = i.get("strike")
            d = strike_oi.setdefault(strike, {"ce": 0.0, "pe": 0.0})
            if str(i.get("instrument_type")).upper() == "CE":
                ce_oi += oi; d["ce"] += oi
            else:
                pe_oi += oi; d["pe"] += oi
        pcr = round(pe_oi / ce_oi, 2) if ce_oi > 0 else None
        # Max pain = expiry strike that minimises total payoff to option holders.
        best, best_pain = None, None
        for s in sorted(strike_oi):
            pain = sum((s - k) * d["ce"] for k, d in strike_oi.items() if k < s) \
                 + sum((k - s) * d["pe"] for k, d in strike_oi.items() if k > s)
            if best_pain is None or pain < best_pain:
                best_pain, best = pain, s
        return (pcr, int(best) if best is not None else None)
    except Exception as e:
        log.warning(f"Kite option-chain metrics failed: {type(e).__name__} {str(e)[:80]}")
        return (None, None)


_QUOTE_SYM = {"NIFTY": "NSE:NIFTY 50", "SENSEX": "BSE:SENSEX", "VIX": "NSE:INDIA VIX"}


def get_quotes() -> dict:
    """Live NIFTY/SENSEX/VIX quotes via Kite in one call: each ->
    {ltp, prev_close, change, change_pct}. Returns {} if Kite is unavailable."""
    if not is_enabled():
        return {}
    try:
        broker = _state["broker"]
        raw = broker._kite.quote(list(_QUOTE_SYM.values()))
        out = {}
        for key, sym in _QUOTE_SYM.items():
            q = raw.get(sym)
            if not q:
                continue
            ltp = float(q.get("last_price") or 0)
            prev = float((q.get("ohlc") or {}).get("close") or 0)
            chg = (ltp - prev) if prev else 0.0
            out[key] = {"ltp": round(ltp, 2), "prev_close": round(prev, 2),
                        "change": round(chg, 2),
                        "change_pct": round(chg / prev * 100, 2) if prev else 0.0}
        return out
    except Exception as e:
        log.warning(f"Kite quotes failed: {type(e).__name__} {str(e)[:80]}")
        return {}


def _overlay_futures_volume(spot_df: pd.DataFrame, symbol_key: str,
                            trade_date: date, kite_interval: str) -> pd.DataFrame:
    """Replace spot's (zero) Volume with the near-month futures Volume, aligned by
    timestamp. Price columns are left untouched. On any failure the spot df is
    returned unchanged so volume-sensitive strategies simply see no surge rather
    than crashing the backtest."""
    token = _resolve_futures_token(symbol_key)
    if token is None:
        return spot_df
    try:
        broker = _state["broker"]
        frm = datetime(trade_date.year, trade_date.month, trade_date.day, 9, 15, tzinfo=IST)
        to  = datetime(trade_date.year, trade_date.month, trade_date.day, 15, 30, tzinfo=IST)
        _throttle()
        raw = broker._kite.historical_data(token, frm, to, kite_interval, continuous=True)
        if not raw:
            return spot_df
        fdf = pd.DataFrame(raw)
        fdf["date"] = pd.to_datetime(fdf["date"])
        fdf = fdf.set_index("date").sort_index()
        # Align each spot bar to the futures bar nearest in time so every spot bar
        # gets a real, non-zero volume (price columns stay spot).
        vol = fdf["volume"].reindex(spot_df.index, method="nearest")
        out = spot_df.copy()
        if vol.notna().any():
            out["Volume"] = vol.fillna(0).astype("int64")
        return out
    except Exception as e:
        log.warning(f"Futures-volume overlay failed for {symbol_key} {trade_date}: "
                    f"{type(e).__name__} {str(e)[:60]} — using spot volume.")
        return spot_df
