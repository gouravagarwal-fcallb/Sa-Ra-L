"""
Backfill — Historical OHLCV cache and indicator warmup
───────────────────────────────────────────────────────
Downloads and caches 1-min / 5-min historical bars so live engines
can warm up their indicators (EMA, VWAP, BB, ORB) when starting
mid-session rather than operating blind for the first N bars.

Usage (CLI via main.py):
  python main.py --mode backfill --strategy ATM_PULSE_BURST_v1
  python main.py --mode backfill --strategy BB_EXPIRY_SCALPER_v1

Usage (from code):
  from src.data.backfill import BackfillManager
  bf = BackfillManager()
  bars = bf.get_today_bars("NIFTY", interval="1m")   # list of OHLCV dicts
  warmup = bf.compute_warmup_state("NIFTY", strategy_config)
"""

from __future__ import annotations

import os
import json
import math
import datetime as _dt
from datetime import date, timedelta, timezone
from typing import Optional
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

CACHE_DIR = Path("data/backfill_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── Bar fetch (yfinance) ─────────────────────────────────────────────────────

def _fetch_yfinance(symbol_yf: str, interval: str, period: str) -> list[dict]:
    """Download bars from yfinance. Returns list of {t, o, h, l, c, v} dicts."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [backfill] yfinance not installed. Run: pip install yfinance")
        return []

    ticker = yf.Ticker(symbol_yf)
    df = ticker.history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        return []

    bars = []
    for ts, row in df.iterrows():
        # yfinance returns tz-aware timestamps
        try:
            ts_ist = ts.tz_convert("Asia/Kolkata")
        except Exception:
            ts_ist = ts
        bars.append({
            "t": ts_ist.strftime("%Y-%m-%d %H:%M"),
            "o": round(float(row["Open"]),  2),
            "h": round(float(row["High"]),  2),
            "l": round(float(row["Low"]),   2),
            "c": round(float(row["Close"]), 2),
            "v": int(row.get("Volume", 0)),
        })
    return bars


def _yf_symbol(instrument: str) -> str:
    mapping = {
        "NIFTY":    "^NSEI",
        "BANKNIFTY":"^NSEBANK",
        "SENSEX":   "^BSESN",
        # USD/INR is a currency pair on Yahoo — 'USDINR=X' (the '.NS' default is a
        # delisted equity symbol and returns nothing). Cover both name spellings.
        "USDINR":   "USDINR=X",
        "INRUSD":   "USDINR=X",
    }
    return mapping.get(instrument.upper(), f"{instrument}.NS")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(instrument: str, interval: str, day: date) -> Path:
    return CACHE_DIR / f"{instrument}_{interval}_{day.isoformat()}.json"


def _save_cache(instrument: str, interval: str, day: date, bars: list[dict]) -> None:
    path = _cache_path(instrument, interval, day)
    with open(path, "w") as f:
        json.dump(bars, f)


def _load_cache(instrument: str, interval: str, day: date) -> Optional[list[dict]]:
    path = _cache_path(instrument, interval, day)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ── BackfillManager ───────────────────────────────────────────────────────────

class BackfillManager:
    """
    Downloads and caches intraday OHLCV bars for indicator warmup.

    Methods:
      get_today_bars(instrument, interval)  → list of bars up to now
      get_bars(instrument, interval, day)   → all bars for a specific day
      compute_warmup_state(instrument, cfg) → pre-computed indicator state dict
      clear_cache(days_older_than)          → remove stale cache files
    """

    def get_today_bars(self, instrument: str, interval: str = "1m",
                       force_refresh: bool = False) -> list[dict]:
        """
        Return all bars from 9:15 AM until now for today.
        Uses cache if available and today's session is still ongoing.
        Force-refreshes cache if force_refresh=True.
        """
        today = date.today()
        now   = _dt.datetime.now(IST)
        market_open  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

        # Use cache if it exists and market is still open (partial day — refresh every call)
        # Use cache if market is closed for today (full day — stable)
        market_closed = now > market_close

        if not force_refresh and market_closed:
            cached = _load_cache(instrument, interval, today)
            if cached:
                return cached

        yf_sym  = _yf_symbol(instrument)
        yf_period = "1d"
        bars = _fetch_yfinance(yf_sym, interval, yf_period)

        # Filter to today only and 9:15 onwards
        today_str = today.isoformat()
        bars = [b for b in bars if b["t"].startswith(today_str)
                and b["t"] >= f"{today_str} 09:15"]

        if market_closed and bars:
            _save_cache(instrument, interval, today, bars)

        return bars

    def get_bars(self, instrument: str, interval: str = "1m",
                 day: Optional[date] = None,
                 days_back: int = 1) -> list[dict]:
        """
        Return all bars for a specific day (or days_back days of history).
        Uses cache where possible.
        """
        if day is None:
            day = date.today()

        cached = _load_cache(instrument, interval, day)
        if cached:
            return cached

        yf_sym = _yf_symbol(instrument)
        period = f"{max(2, days_back + 1)}d"
        bars   = _fetch_yfinance(yf_sym, interval, period)

        day_str = day.isoformat()
        day_bars = [b for b in bars if b["t"].startswith(day_str)]

        if day_bars:
            _save_cache(instrument, interval, day, day_bars)

        return day_bars

    def compute_warmup_state(self, instrument: str,
                             strategy_config: dict) -> dict:
        """
        Computes indicator warmup from today's bars so far.
        Returns a dict consumed by live engines on startup.

        Computed fields depend on strategy_type:
          atm_pulse_burst  → ema9, ema21, vwap, orb_high, orb_low, bar_count
          bb_expiry_scalper → bb_spots (last bb_period values), bb_upper/middle/lower
          expiry_scalper   → spot_history (last 30 values)
          range_scalper    → same as expiry_scalper
          default          → bar_count, last_close
        """
        stype = strategy_config.get("strategy_type", "")
        bars  = self.get_today_bars(instrument, interval="1m")

        if not bars:
            print(f"  [backfill] No bars available for {instrument} today.")
            return {"bar_count": 0, "bars": []}

        result: dict = {
            "bar_count": len(bars),
            "bars":      bars,
            "instrument": instrument,
        }

        closes = [b["c"] for b in bars]
        highs  = [b["h"] for b in bars]
        lows   = [b["l"] for b in bars]

        if stype == "atm_pulse_burst":
            cfg  = strategy_config.get("atm_pulse_burst", {})
            ema9_period  = int(cfg.get("ema_short",  9))
            ema21_period = int(cfg.get("ema_long",  21))
            orb_minutes  = int(cfg.get("orb_minutes", 15))

            ema9  = _calc_ema(closes, ema9_period)
            ema21 = _calc_ema(closes, ema21_period)
            vwap  = sum(closes) / len(closes) if closes else 0.0

            orb_bars = [b for b in bars
                        if "09:15" <= b["t"][-5:] < f"09:{15+orb_minutes:02d}"]
            orb_high = max(b["h"] for b in orb_bars) if orb_bars else 0.0
            orb_low  = min(b["l"] for b in orb_bars) if orb_bars else 0.0
            orb_locked = len(bars) >= orb_minutes

            result.update({
                "ema9":      round(ema9,  2),
                "ema21":     round(ema21, 2),
                "vwap":      round(vwap,  2),
                "orb_high":  round(orb_high, 2),
                "orb_low":   round(orb_low,  2),
                "orb_locked": orb_locked,
            })

        elif stype == "bb_expiry_scalper":
            cfg      = strategy_config.get("bb_expiry_scalper", {})
            bb_period = int(cfg.get("bb_period",  20))
            bb_std    = float(cfg.get("bb_std_dev", 2.0))

            spots = closes[-bb_period:] if len(closes) >= bb_period else closes
            if len(spots) >= bb_period:
                bb_mid   = sum(spots) / bb_period
                variance = sum((v - bb_mid) ** 2 for v in spots) / bb_period
                bb_std_val = math.sqrt(variance)
                result.update({
                    "bb_upper":  round(bb_mid + bb_std * bb_std_val, 2),
                    "bb_middle": round(bb_mid, 2),
                    "bb_lower":  round(bb_mid - bb_std * bb_std_val, 2),
                    "bb_spots":  spots,
                    "bb_bandwidth_pct": round(
                        (bb_std * 2 * bb_std_val) / bb_mid * 100, 4
                    ) if bb_mid else 0.0,
                })
            else:
                result["bb_spots"] = spots
                result["bb_warmup_bars_needed"] = bb_period - len(spots)

        else:
            result["last_close"] = closes[-1] if closes else 0.0

        bar_count = len(bars)
        last_bar  = bars[-1] if bars else {}
        print(
            f"  [backfill] {instrument}  {stype or 'default'}"
            f"  bars={bar_count}  last={last_bar.get('t','?')}  "
            f"close={last_bar.get('c', 0):.1f}"
        )
        return result

    def clear_cache(self, days_older_than: int = 7) -> int:
        """Remove cache files older than N days. Returns count deleted."""
        cutoff = date.today() - timedelta(days=days_older_than)
        deleted = 0
        for f in CACHE_DIR.glob("*.json"):
            try:
                # filename: INSTRUMENT_1m_YYYY-MM-DD.json
                parts = f.stem.split("_")
                day_str = parts[-1]
                file_day = date.fromisoformat(day_str)
                if file_day < cutoff:
                    f.unlink()
                    deleted += 1
            except Exception:
                pass
        return deleted

    def prefetch(self, instruments: list[str],
                 intervals: list[str] = None,
                 days_back: int = 5) -> dict:
        """
        Pre-download and cache historical bars for a list of instruments.
        Returns dict {instrument: {interval: bar_count}}.
        """
        if intervals is None:
            intervals = ["1m", "5m"]
        summary: dict = {}
        today = date.today()
        for inst in instruments:
            summary[inst] = {}
            yf_sym = _yf_symbol(inst)
            for iv in intervals:
                period = f"{max(2, days_back + 1)}d"
                all_bars = _fetch_yfinance(yf_sym, iv, period)
                count = 0
                for delta in range(days_back):
                    check_day = today - timedelta(days=delta)
                    day_str   = check_day.isoformat()
                    day_bars  = [b for b in all_bars if b["t"].startswith(day_str)]
                    if day_bars:
                        _save_cache(inst, iv, check_day, day_bars)
                        count += len(day_bars)
                summary[inst][iv] = count
                print(f"  [backfill] {inst} {iv}  {count} bars cached ({days_back} days)")
        return summary


# ── EMA helper ────────────────────────────────────────────────────────────────

def _calc_ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (period + 1)
    ema   = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def run_backfill(strategy_config: dict = None,
                 strategy_name:   str  = None,
                 instruments:     list[str] = None,
                 days_back:       int  = 5) -> None:
    """
    CLI mode: download and cache historical data.
    Called from main.py via --mode backfill.
    """
    bf = BackfillManager()

    # Determine instruments from strategy config
    if instruments is None:
        if strategy_config:
            inst_cfg = strategy_config.get("instruments", {})
            instruments = [k.upper() for k in inst_cfg.keys()]
        else:
            instruments = ["NIFTY", "SENSEX"]

    label = f"  Strategy: {strategy_name}" if strategy_name else "  (all instruments)"
    print(
        f"\n  ══ Backfill Mode ══════════════════════════════════════\n"
        f"{label}\n"
        f"  Instruments: {', '.join(instruments)}\n"
        f"  Fetching last {days_back} trading days of 1-min + 5-min bars...\n"
        f"  Cache directory: {CACHE_DIR.resolve()}\n"
        f"  ═══════════════════════════════════════════════════════"
    )

    summary = bf.prefetch(instruments, intervals=["1m", "5m"], days_back=days_back)

    # If strategy config given, compute warmup for today
    if strategy_config:
        print(f"\n  Computing today's warmup indicators...")
        for inst in instruments:
            state = bf.compute_warmup_state(inst, strategy_config)
            bar_count = state.get("bar_count", 0)
            print(f"  {inst}: {bar_count} bars processed")
            if "ema9" in state:
                print(
                    f"    EMA9={state['ema9']:.1f}  EMA21={state.get('ema21',0):.1f}"
                    f"  VWAP={state.get('vwap',0):.1f}"
                )
            if "bb_middle" in state:
                print(
                    f"    BB=[{state.get('bb_lower',0):.1f}|"
                    f"{state['bb_middle']:.1f}|{state.get('bb_upper',0):.1f}]"
                    f"  BW={state.get('bb_bandwidth_pct',0):.3f}%"
                )
            if "orb_high" in state:
                print(
                    f"    ORB=[{state.get('orb_low',0):.1f}–{state['orb_high']:.1f}]"
                    f"  locked={state.get('orb_locked',False)}"
                )

    # Clean up old cache files
    deleted = bf.clear_cache(days_older_than=7)
    if deleted:
        print(f"\n  Cleaned up {deleted} cache files older than 7 days.")

    print(f"\n  Backfill complete. Cache ready for live warmup.\n")
