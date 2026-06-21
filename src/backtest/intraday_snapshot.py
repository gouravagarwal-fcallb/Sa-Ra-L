"""
Intraday Indicators Snapshot
─────────────────────────────
Exports a comprehensive time-series of ALL strategy indicators at 5-min
(or finest available) granularity for every trading day in a date range.

Output CSV columns (one row per candle):
  timestamp          YYYY-MM-DD HH:MM:SS.000000000  (nanosecond-formatted)
  date               YYYY-MM-DD
  instrument         NIFTY / SENSEX
  period_id          T1 / OW1 / T2 / ... (which slot this candle belongs to)
  is_real_window     True if this is a real trading window
  spot               Underlying spot price
  vix                India VIX
  atm_strike         ATM strike rounded to step (50 for Nifty, 100 for Sensex)
  T_hours_to_expiry  Hours remaining until expiry at 15:30
  ce_price           BS-priced ATM call premium
  ce_delta           Call delta
  ce_gamma           Call gamma
  ce_theta           Call theta (per calendar day)
  ce_vega            Call vega (per 1% IV)
  pe_price           BS-priced ATM put premium
  pe_delta           Put delta
  pe_gamma           Put gamma
  pe_theta           Put theta
  pe_vega            Put vega
  direction_score    Pre-market direction score (fixed for the day)
  direction          BULLISH / BEARISH / NEUTRAL
  dow_change_pct     Dow Jones % change (pre-market signal)
  gift_nifty_prem    Gift Nifty premium proxy
  is_trading_possible  False if VIX > max_vix or direction is NEUTRAL

Notes:
  - Timestamps are formatted as HH:MM:SS.000000000 to indicate nanosecond
    precision format even though yfinance delivers 5-min bars (not tick data).
    True nanosecond / tick data requires an exchange feed subscription.
  - For dates beyond the ~60-day intraday window, a single synthetic row is
    emitted using daily OHLC (open/close) with time 09:22:00.000000000.
"""

from __future__ import annotations
import os
import pandas as pd
from datetime import date, datetime, timedelta
from typing import Optional

from src.data.historical_loader import build_backtest_dataset, load_intraday
from src.strategy.direction_engine import DirectionEngine, DirectionInputs, Direction
from src.backtest.option_pricer import OptionPricer
from src.utils.market_calendar import (
    get_nifty_weekly_expiry,
    get_sensex_weekly_expiry,
    is_trading_day,
)
from src.utils.helpers import round_to_strike
from src.utils.logger import setup_logger

log = setup_logger("intraday_snapshot")

# Slot definitions (id, start_hhmm, end_hhmm, is_real)
_SLOTS_EXPIRY = [
    ("OW0", (9, 15), (9, 22),  False),
    ("T1",  (9, 22), (10, 10), True),
    ("OW1", (10, 10),(11, 30), False),
    ("T2",  (11, 30),(11, 45), True),
    ("OW2", (11, 45),(12, 45), False),
    ("T3",  (12, 45),(13, 20), True),
    ("OW3", (13, 20),(14, 20), False),
    ("T4",  (14, 20),(14, 44), True),
    ("OW4", (14, 44),(15, 20), False),
]
_SLOTS_NORMAL = [
    ("OW0", (9, 15), (9, 22),  False),
    ("T1",  (9, 22), (10, 10), True),
    ("OW1", (10, 10),(12, 45), False),
    ("T3",  (12, 45),(13, 20), True),
    ("OW2", (13, 20),(15, 20), False),
]


def _slot_for_time(hh: int, mm: int, slots: list) -> tuple[str, bool]:
    """Return (period_id, is_real) for a given time."""
    abs_min = hh * 60 + mm
    for sid, (sh, sm), (eh, em), is_real in slots:
        if sh * 60 + sm <= abs_min < eh * 60 + em:
            return sid, is_real
    return "OFF", False


def _gift_proxy(dow_chg: float, spot_prev: float) -> float:
    return spot_prev * dow_chg * 0.6 / 100


def export_intraday_snapshot(
    strategy_config: dict,
    start: Optional[date] = None,
    end: Optional[date] = None,
    out_path: str = "data/historical/intraday_indicators.csv",
) -> None:
    """
    Build and export the comprehensive intraday indicators CSV.

    For days with 5-min intraday data (last ~60 trading days):
      One row per 5-min candle from 09:15 to 15:20.
    For older days (OHLC only):
      One synthetic row at 09:22 using open price.
    """
    bt = strategy_config.get("backtest", {})
    start = start or date.fromisoformat(bt.get("start_date", "2023-01-01"))
    end   = end   or date.fromisoformat(bt.get("end_date",   "2026-06-19"))

    pricer  = OptionPricer(risk_free_rate=bt.get("risk_free_rate", 0.065))
    de      = DirectionEngine(strategy_config)
    max_vix = strategy_config.get("option_chain", {}).get("max_vix_for_entry", 22.0)

    inst_n  = strategy_config.get("instruments", {}).get("nifty", {})
    inst_s  = strategy_config.get("instruments", {}).get("sensex", {})
    n_step  = inst_n.get("strike_step", 50)
    s_step  = inst_s.get("strike_step", 100)

    dataset = build_backtest_dataset(start, end)
    if dataset.empty:
        log.error("No dataset available for snapshot")
        return

    rows = []

    for idx, row in dataset.iterrows():
        trade_date = idx.date()
        weekday    = trade_date.weekday()
        if weekday == 2 or not is_trading_day(trade_date):
            continue

        if weekday == 3:
            instrument   = "SENSEX"
            expiry_date  = get_sensex_weekly_expiry(trade_date)
            strike_step  = s_step
            intra_key    = "sensex"
            spot_prev    = float(row.get("sensex_prev_close", 0) or 0)
            spot_open    = float(row.get("sensex_open", 0) or 0)
        else:
            instrument   = "NIFTY"
            expiry_date  = get_nifty_weekly_expiry(trade_date)
            strike_step  = n_step
            intra_key    = "nifty"
            spot_prev    = float(row.get("nifty_prev_close", 0) or 0)
            spot_open    = float(row.get("nifty_open", 0) or 0)

        if spot_prev == 0 or spot_open == 0:
            continue

        is_expiry = (weekday == 3) or (trade_date == get_nifty_weekly_expiry(trade_date))
        slots     = _SLOTS_EXPIRY if is_expiry else _SLOTS_NORMAL

        vix       = float(row.get("vix_close", 15.0))
        dow_chg   = float(row.get("dow_change_pct", 0.0))
        gift_prem = _gift_proxy(dow_chg, spot_prev)

        dir_in  = DirectionInputs(
            dow_change_pct=dow_chg,
            gift_nifty_premium=gift_prem,
            india_vix=vix,
            sensex_change_pct=dow_chg * 0.55,
            nifty_prev_close=spot_prev,
        )
        dir_res = de.evaluate(dir_in)
        d_score = dir_res.score
        d_label = dir_res.direction.value
        tradeable = dir_res.direction != Direction.NEUTRAL and vix <= max_vix

        # ── Intraday candles ──────────────────────────────────────────────────
        intraday = load_intraday(intra_key, trade_date, interval="5m")

        if not intraday.empty:
            candle_times = intraday.index.strftime("%H:%M")
            mask = (candle_times >= "09:15") & (candle_times <= "15:20")
            intra_sub = intraday[mask]

            for ts, candle_row in intra_sub.iterrows():
                spot = float(candle_row["Close"])
                h, m = ts.hour, ts.minute

                atm     = round_to_strike(spot, strike_step)
                expiry_dt = datetime(expiry_date.year, expiry_date.month, expiry_date.day, 15, 30)
                entry_dt  = datetime(trade_date.year, trade_date.month, trade_date.day, h, m)
                T_hrs   = max((expiry_dt - entry_dt).total_seconds() / 3600, 0.05)

                ce = pricer.price(spot, atm, vix, T_hrs, "CE")
                pe = pricer.price(spot, atm, vix, T_hrs, "PE")

                period_id, is_real = _slot_for_time(h, m, slots)
                intra_dir = de.evaluate_intraday(spot, spot_prev, spot_open, vix)

                # Nanosecond-formatted timestamp
                ts_nano = f"{trade_date} {h:02d}:{m:02d}:00.000000000"

                rows.append({
                    "timestamp":                ts_nano,
                    "date":                     str(trade_date),
                    "instrument":               instrument,
                    "period_id":                period_id,
                    "is_real_window":           is_real,
                    "spot":                     round(spot, 2),
                    "vix":                      round(vix, 2),
                    "atm_strike":               atm,
                    "T_hours_to_expiry":        round(T_hrs, 4),
                    "ce_price":                 ce.price,
                    "ce_delta":                 ce.delta,
                    "ce_gamma":                 ce.gamma,
                    "ce_theta":                 ce.theta,
                    "ce_vega":                  ce.vega,
                    "pe_price":                 pe.price,
                    "pe_delta":                 pe.delta,
                    "pe_gamma":                 pe.gamma,
                    "pe_theta":                 pe.theta,
                    "pe_vega":                  pe.vega,
                    "pm_direction_score":       d_score,
                    "pm_direction":             d_label,
                    "intraday_direction_score": intra_dir.score,
                    "intraday_direction":       intra_dir.direction.value,
                    "dow_change_pct":           round(dow_chg, 4),
                    "gift_nifty_prem":          round(gift_prem, 1),
                    "is_trading_possible":      tradeable,
                })

        else:
            # OHLC fallback: single synthetic row at 09:22
            spot  = spot_open
            h, m  = 9, 22
            atm   = round_to_strike(spot, strike_step)
            expiry_dt = datetime(expiry_date.year, expiry_date.month, expiry_date.day, 15, 30)
            entry_dt  = datetime(trade_date.year, trade_date.month, trade_date.day, h, m)
            T_hrs = max((expiry_dt - entry_dt).total_seconds() / 3600, 0.05)
            ce    = pricer.price(spot, atm, vix, T_hrs, "CE")
            pe    = pricer.price(spot, atm, vix, T_hrs, "PE")

            ts_nano = f"{trade_date} {h:02d}:{m:02d}:00.000000000"
            period_id, is_real = _slot_for_time(h, m, slots)
            intra_dir = de.evaluate_intraday(spot, spot_prev, spot_open, vix)

            rows.append({
                "timestamp":                ts_nano,
                "date":                     str(trade_date),
                "instrument":               instrument,
                "period_id":                period_id,
                "is_real_window":           is_real,
                "spot":                     round(spot, 2),
                "vix":                      round(vix, 2),
                "atm_strike":               atm,
                "T_hours_to_expiry":        round(T_hrs, 4),
                "ce_price":                 ce.price,
                "ce_delta":                 ce.delta,
                "ce_gamma":                 ce.gamma,
                "ce_theta":                 ce.theta,
                "ce_vega":                  ce.vega,
                "pe_price":                 pe.price,
                "pe_delta":                 pe.delta,
                "pe_gamma":                 pe.gamma,
                "pe_theta":                 pe.theta,
                "pe_vega":                  pe.vega,
                "pm_direction_score":       d_score,
                "pm_direction":             d_label,
                "intraday_direction_score": intra_dir.score,
                "intraday_direction":       intra_dir.direction.value,
                "dow_change_pct":           round(dow_chg, 4),
                "gift_nifty_prem":          round(gift_prem, 1),
                "is_trading_possible":      tradeable,
            })

    if not rows:
        log.warning("No rows generated for intraday snapshot")
        return

    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)
    log.info(f"Intraday snapshot exported: {out_path}  ({len(df):,} rows)")
    print(f"Intraday indicators snapshot: {out_path}  ({len(df):,} rows, {df['date'].nunique()} days)")
