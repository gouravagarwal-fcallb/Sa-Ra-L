"""
Elliott Wave Backtest — 20-year simulation (2004-2024).

Feeds daily OHLCV bars through ElliottWaveAnalyzer bar-by-bar, records
every wave call and simulates hypothetical options P&L using Black-Scholes
from the existing option_pricer module.

Trade simulation rules:
  - Enter on W3 or WC detection (highest conviction waves)
  - Option premium estimated via Black-Scholes (IV from ATR proxy)
  - Position size: 30% of capital (seller-perspective 30% rule)
  - SL: 40% of premium paid
  - Target: 150% of premium paid (W3/WC), 80% (W5), 100% (others)
  - Max 1 trade at a time per instrument
  - Auto-exit at end of expiry week

Usage:
    python -m src.brahmastra.backtest.elliott_wave_backtest
    python -m src.brahmastra.backtest.elliott_wave_backtest --instrument NIFTY --years 10
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────

INSTRUMENT_META = {
    "SENSEX": {"yf_symbol": "^BSESN", "lot_size": 20, "strike_step": 100},
    "NIFTY":  {"yf_symbol": "^NSEI",  "lot_size": 75, "strike_step": 50},
}

DEFAULT_CAPITAL   = 100_000   # ₹1 lakh
POSITION_PCT      = 0.30      # 30% capital rule
SL_PCT            = 0.40      # stop loss: 40% of premium
TARGET_PCT_W3     = 1.50      # W3/WC target: 150% gain on premium
TARGET_PCT_W5     = 0.80      # W5 target: 80% gain
TARGET_PCT_OTHER  = 1.00      # other waves: 100%
MIN_CONFIDENCE    = 50.0      # minimum wave confidence to trade
SLIPPAGE_PCT      = 0.002     # 0.2% slippage per side
BROKERAGE         = 40.0      # ₹40 flat (Zerodha F&O)
IV_BASE           = 0.18      # base implied volatility (18% annualised)

TRADE_WAVES = {"3", "C"}      # only trade W3 and WC (highest conviction)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    date:           date
    instrument:     str
    wave:           str
    wave_type:      str
    confidence:     float
    direction:      str        # 'CE' | 'PE'
    spot_entry:     float
    strike:         int
    option_type:    str
    entry_premium:  float
    exit_premium:   float
    exit_reason:    str        # 'TARGET' | 'SL' | 'EXPIRY' | 'WAVE_CHANGE'
    holding_days:   int
    pnl_pct:        float
    pnl_rupees:     float
    lots:           int
    capital_used:   float
    transaction_cost: float


@dataclass
class BacktestResult:
    instrument:     str
    start_date:     date
    end_date:       date
    total_days:     int
    total_trades:   int
    winning_trades: int
    losing_trades:  int
    win_rate:       float
    total_pnl:      float
    max_drawdown:   float
    sharpe_ratio:   float
    avg_pnl_per_trade: float
    avg_holding_days: float
    best_trade:     float
    worst_trade:    float
    wave_stats:     dict       # { wave_label: {trades, wins, pnl} }
    monthly_pnl:    dict       # { 'YYYY-MM': pnl }
    trades:         list[BacktestTrade] = field(default_factory=list)

    def print_report(self):
        sep = "=" * 72
        print(f"\n{sep}")
        print(f"  ELLIOTT WAVE BACKTEST — {self.instrument}")
        print(f"  Period: {self.start_date} → {self.end_date}  ({self.total_days} days)")
        print(sep)
        print(f"  Total trades    : {self.total_trades}")
        print(f"  Win rate        : {self.win_rate:.1%}")
        print(f"  Total P&L       : ₹{self.total_pnl:+,.0f}")
        print(f"  Avg P&L/trade   : ₹{self.avg_pnl_per_trade:+,.0f}")
        print(f"  Avg holding     : {self.avg_holding_days:.1f} days")
        print(f"  Best trade      : ₹{self.best_trade:+,.0f}")
        print(f"  Worst trade     : ₹{self.worst_trade:+,.0f}")
        print(f"  Max drawdown    : ₹{self.max_drawdown:,.0f}")
        print(f"  Sharpe ratio    : {self.sharpe_ratio:.2f}")
        print(f"\n  Wave breakdown:")
        for wave, stats in sorted(self.wave_stats.items()):
            wr = stats['wins'] / stats['trades'] if stats['trades'] else 0
            print(f"    W{wave}: {stats['trades']:3d} trades | "
                  f"win={wr:.0%} | P&L=₹{stats['pnl']:+,.0f}")
        print(f"\n  Exit reason breakdown:")
        exit_counts: dict[str, int] = {}
        for t in self.trades:
            exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1
        for reason, count in sorted(exit_counts.items()):
            print(f"    {reason:15s}: {count}")
        print(sep)


# ── Black-Scholes option pricer ───────────────────────────────────────────────

def _bs_premium(spot: float, strike: float, days_to_expiry: int,
                iv: float, option_type: str) -> float:
    """Simplified Black-Scholes for ATM/OTM premium estimate."""
    import math
    T = max(days_to_expiry, 1) / 365.0
    r = 0.065   # risk-free rate (approx RBI repo)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)

    def norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))

    if option_type == "CE":
        return max(0.5, spot * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2))
    else:
        return max(0.5, strike * math.exp(-r * T) * norm_cdf(-d2) - spot * norm_cdf(-d1))


def _iv_from_atr(atr: float, spot: float, days: int = 30) -> float:
    """Rough IV estimate from ATR (annualise the daily range %)."""
    daily_vol = (atr / spot)
    ann_vol   = daily_vol * math.sqrt(252)
    return max(IV_BASE, min(0.60, ann_vol))


def _atr(highs: list[float], lows: list[float], closes: list[float],
         period: int = 14) -> float:
    if len(closes) < 2:
        return closes[-1] * 0.01 if closes else 100.0
    trs = []
    for i in range(1, min(period + 1, len(closes))):
        h, l, pc = highs[-i], lows[-i], closes[-i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else closes[-1] * 0.01


# ── Data download ─────────────────────────────────────────────────────────────

def _download_bars(yf_symbol: str, start: date, end: date) -> list[dict]:
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance required: pip install yfinance")
        sys.exit(1)

    print(f"  Downloading {yf_symbol} {start} → {end} …", end=" ", flush=True)
    df = yf.download(yf_symbol, start=str(start), end=str(end),
                     interval="1d", progress=False, auto_adjust=True)
    if df is None or df.empty:
        print("NO DATA")
        return []

    bars = []
    for ts, row in df.iterrows():
        try:
            d = ts.date() if hasattr(ts, "date") else ts
        except Exception:
            d = ts
        bars.append({
            "date":   d,
            "open":   float(row["Open"]),
            "high":   float(row["High"]),
            "low":    float(row["Low"]),
            "close":  float(row["Close"]),
            "volume": int(row.get("Volume", 0)),
        })
    print(f"{len(bars)} bars")
    return bars


# ── Main backtest engine ──────────────────────────────────────────────────────

def run_backtest(
    instrument:    str  = "SENSEX",
    years:         int  = 20,
    capital:       float = DEFAULT_CAPITAL,
    min_confidence: float = MIN_CONFIDENCE,
    min_swing_pct: float = 2.0,
) -> BacktestResult:

    meta       = INSTRUMENT_META[instrument]
    lot_size   = meta["lot_size"]
    strike_step = meta["strike_step"]
    end_dt     = date.today()
    start_dt   = date(end_dt.year - years, end_dt.month, end_dt.day)

    print(f"\nElliott Wave Backtest — {instrument} ({years} years)")
    print(f"Capital: ₹{capital:,.0f}  |  Min confidence: {min_confidence:.0f}%  |  Min swing: {min_swing_pct:.1f}%")

    bars = _download_bars(meta["yf_symbol"], start_dt, end_dt)
    if not bars:
        print("No data. Exiting.")
        sys.exit(1)

    from src.brahmastra.indicators.elliott_wave import (
        ElliottWaveAnalyzer, WaveLabel, WaveType, OptionsAction
    )
    from src.brahmastra.data.bar_builder import Bar
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))

    analyzer = ElliottWaveAnalyzer(
        timeframe="1D", instrument=instrument,
        min_swing_pct=min_swing_pct, lookback=500, strike_step=strike_step,
    )

    trades: list[BacktestTrade] = []
    equity_curve: list[float]   = []
    running_capital = capital
    open_trade: Optional[dict]  = None

    highs, lows, closes = [], [], []

    for i, bar_data in enumerate(bars):
        d      = bar_data["date"]
        close  = bar_data["close"]
        high   = bar_data["high"]
        low    = bar_data["low"]

        highs.append(high)
        lows.append(low)
        closes.append(close)

        # Build a fake Bar for the analyzer
        ts = __import__("datetime").datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
        fake_bar = Bar(
            instrument=instrument, timeframe="1D",
            ts_open=ts, ts_close=ts,
            open=bar_data["open"], high=high, low=low, close=close,
            volume=bar_data["volume"], vwap=close,
            tick_count=1, complete=True,
        )
        analyzer.update(fake_bar)

        # Check open trade exit conditions
        if open_trade:
            entry_p = open_trade["entry_premium"]
            sl_p    = entry_p * (1 - SL_PCT)
            tgt_p   = entry_p * (1 + open_trade["target_pct"])
            atv     = _atr(highs, lows, closes)
            iv      = _iv_from_atr(atv, close)
            days_left = max(1, (open_trade["expiry_date"] - d).days)

            # Estimate current premium via Black-Scholes
            cur_premium = _bs_premium(
                close, open_trade["strike"], days_left,
                iv, open_trade["option_type"]
            )

            exit_reason = None
            exit_premium = cur_premium

            if cur_premium <= sl_p:
                exit_reason  = "SL"
                exit_premium = sl_p
            elif cur_premium >= tgt_p:
                exit_reason  = "TARGET"
                exit_premium = tgt_p
            elif d >= open_trade["expiry_date"]:
                exit_reason  = "EXPIRY"
                exit_premium = max(0.5, cur_premium)

            if exit_reason:
                lots   = open_trade["lots"]
                ep     = open_trade["entry_premium"] * (1 + SLIPPAGE_PCT)
                xp     = exit_premium * (1 - SLIPPAGE_PCT)
                gross  = (xp - ep) * lots * lot_size
                txcost = BROKERAGE * 2 + abs(gross) * 0.001
                net    = gross - txcost
                pct    = (xp - ep) / ep * 100

                running_capital += net
                equity_curve.append(running_capital)

                t = BacktestTrade(
                    date=d, instrument=instrument,
                    wave=open_trade["wave"], wave_type=open_trade["wave_type"],
                    confidence=open_trade["confidence"],
                    direction=open_trade["option_type"],
                    spot_entry=open_trade["spot_entry"],
                    strike=open_trade["strike"],
                    option_type=open_trade["option_type"],
                    entry_premium=round(ep, 2),
                    exit_premium=round(xp, 2),
                    exit_reason=exit_reason,
                    holding_days=(d - open_trade["entry_date"]).days,
                    pnl_pct=round(pct, 2),
                    pnl_rupees=round(net, 2),
                    lots=lots,
                    capital_used=open_trade["capital_used"],
                    transaction_cost=round(txcost, 2),
                )
                trades.append(t)
                open_trade = None

        # Analyze wave (skip first 50 bars for warmup)
        if i < 50 or open_trade:
            continue

        result = analyzer.analyze()
        if result.confidence < min_confidence:
            continue
        if result.current_wave.value not in TRADE_WAVES:
            continue
        if result.action == OptionsAction.WAIT:
            continue

        # Determine option type from action
        is_call = "CE" in result.action.value
        opt_type = "CE" if is_call else "PE"
        atm = round(close / strike_step) * strike_step
        strike = (atm + strike_step) if is_call else (atm - strike_step)

        # Days to next expiry (approx: nearest Thursday for SENSEX, Tuesday for NIFTY)
        exp_day = 3 if instrument == "SENSEX" else 1   # Thu=3, Tue=1
        days_ahead = (exp_day - d.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        expiry_date = d + timedelta(days=days_ahead)
        days_to_exp = days_ahead

        # Estimate entry premium
        atv = _atr(highs, lows, closes)
        iv  = _iv_from_atr(atv, close)
        entry_premium = _bs_premium(close, strike, days_to_exp, iv, opt_type)

        # Position sizing (30% rule)
        capital_to_use = running_capital * POSITION_PCT
        cost_per_lot   = entry_premium * lot_size
        lots = max(1, int(capital_to_use / cost_per_lot)) if cost_per_lot > 0 else 1
        actual_capital = lots * cost_per_lot

        wave_label = result.current_wave.value
        target_pct = (TARGET_PCT_W3 if wave_label in ("3", "C")
                      else TARGET_PCT_W5 if wave_label == "5"
                      else TARGET_PCT_OTHER)

        open_trade = {
            "entry_date":    d,
            "expiry_date":   expiry_date,
            "wave":          wave_label,
            "wave_type":     result.wave_type.value,
            "confidence":    result.confidence,
            "option_type":   opt_type,
            "strike":        strike,
            "spot_entry":    close,
            "entry_premium": entry_premium,
            "target_pct":    target_pct,
            "lots":          lots,
            "capital_used":  actual_capital,
        }

    # Force-close any open trade at end
    if open_trade and closes:
        last_d     = bars[-1]["date"]
        last_close = closes[-1]
        atv        = _atr(highs, lows, closes)
        iv         = _iv_from_atr(atv, last_close)
        ep         = open_trade["entry_premium"] * (1 + SLIPPAGE_PCT)
        xp         = max(0.5, _bs_premium(last_close, open_trade["strike"], 1, iv, open_trade["option_type"])) * (1 - SLIPPAGE_PCT)
        gross      = (xp - ep) * open_trade["lots"] * lot_size
        txcost     = BROKERAGE * 2 + abs(gross) * 0.001
        net        = gross - txcost
        pct        = (xp - ep) / ep * 100
        running_capital += net
        trades.append(BacktestTrade(
            date=last_d, instrument=instrument,
            wave=open_trade["wave"], wave_type=open_trade["wave_type"],
            confidence=open_trade["confidence"],
            direction=open_trade["option_type"],
            spot_entry=open_trade["spot_entry"],
            strike=open_trade["strike"], option_type=open_trade["option_type"],
            entry_premium=round(ep, 2), exit_premium=round(xp, 2),
            exit_reason="END_OF_DATA",
            holding_days=(last_d - open_trade["entry_date"]).days,
            pnl_pct=round(pct, 2), pnl_rupees=round(net, 2),
            lots=open_trade["lots"], capital_used=open_trade["capital_used"],
            transaction_cost=round(txcost, 2),
        ))

    # ── Metrics ───────────────────────────────────────────────────────────────

    if not trades:
        print("No trades generated.")
        return BacktestResult(
            instrument=instrument, start_date=start_dt, end_date=end_dt,
            total_days=len(bars), total_trades=0, winning_trades=0,
            losing_trades=0, win_rate=0.0, total_pnl=0.0,
            max_drawdown=0.0, sharpe_ratio=0.0,
            avg_pnl_per_trade=0.0, avg_holding_days=0.0,
            best_trade=0.0, worst_trade=0.0, wave_stats={}, monthly_pnl={},
        )

    pnls       = [t.pnl_rupees for t in trades]
    wins       = [p for p in pnls if p > 0]
    losses     = [p for p in pnls if p <= 0]
    total_pnl  = sum(pnls)
    win_rate   = len(wins) / len(pnls)

    # Max drawdown
    peak = capital
    max_dd = 0.0
    running = capital
    for p in pnls:
        running += p
        peak     = max(peak, running)
        dd       = peak - running
        max_dd   = max(max_dd, dd)

    # Sharpe (annualised, rf=6%)
    daily_rets = [p / capital for p in pnls]
    if len(daily_rets) > 1:
        import statistics
        mean_r = statistics.mean(daily_rets)
        std_r  = statistics.stdev(daily_rets)
        rf_daily = 0.065 / 252
        sharpe = ((mean_r - rf_daily) / std_r * math.sqrt(252)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Wave stats
    wave_stats: dict = {}
    for t in trades:
        w = t.wave
        if w not in wave_stats:
            wave_stats[w] = {"trades": 0, "wins": 0, "pnl": 0.0}
        wave_stats[w]["trades"] += 1
        if t.pnl_rupees > 0:
            wave_stats[w]["wins"] += 1
        wave_stats[w]["pnl"] += t.pnl_rupees

    # Monthly P&L
    monthly: dict = {}
    for t in trades:
        key = t.date.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0.0) + t.pnl_rupees

    return BacktestResult(
        instrument=instrument,
        start_date=start_dt, end_date=end_dt,
        total_days=len(bars),
        total_trades=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=win_rate,
        total_pnl=round(total_pnl, 2),
        max_drawdown=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 2),
        avg_pnl_per_trade=round(total_pnl / len(trades), 2),
        avg_holding_days=round(sum(t.holding_days for t in trades) / len(trades), 1),
        best_trade=max(pnls),
        worst_trade=min(pnls),
        wave_stats=wave_stats,
        monthly_pnl=monthly,
        trades=trades,
    )


def export_csv(result: BacktestResult, out_dir: str = "results") -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    fname = f"{out_dir}/elliott_wave_{result.instrument}_{result.start_date}_{result.end_date}.csv"
    with open(fname, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "date", "instrument", "wave", "wave_type", "confidence",
            "direction", "spot_entry", "strike", "entry_premium",
            "exit_premium", "exit_reason", "holding_days",
            "pnl_pct", "pnl_rupees", "lots", "transaction_cost",
        ])
        w.writeheader()
        for t in result.trades:
            w.writerow({
                "date": t.date, "instrument": t.instrument,
                "wave": t.wave, "wave_type": t.wave_type,
                "confidence": t.confidence, "direction": t.direction,
                "spot_entry": t.spot_entry, "strike": t.strike,
                "entry_premium": t.entry_premium, "exit_premium": t.exit_premium,
                "exit_reason": t.exit_reason, "holding_days": t.holding_days,
                "pnl_pct": t.pnl_pct, "pnl_rupees": t.pnl_rupees,
                "lots": t.lots, "transaction_cost": t.transaction_cost,
            })
    return fname


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Elliott Wave Backtest")
    parser.add_argument("--instrument", default="SENSEX", choices=["SENSEX", "NIFTY"])
    parser.add_argument("--years",      default=20, type=int)
    parser.add_argument("--capital",    default=100_000, type=float)
    parser.add_argument("--min-conf",   default=50.0, type=float)
    parser.add_argument("--swing",      default=2.0, type=float, help="ZigZag min swing %%")
    parser.add_argument("--export",     action="store_true", help="Export trades to CSV")
    args = parser.parse_args()

    result = run_backtest(
        instrument=args.instrument,
        years=args.years,
        capital=args.capital,
        min_confidence=args.min_conf,
        min_swing_pct=args.swing,
    )
    result.print_report()

    if args.export:
        path = export_csv(result)
        print(f"\n  Trades exported to: {path}")

    # Print last 5 trades
    if result.trades:
        print("\n  Last 5 trades:")
        for t in result.trades[-5:]:
            sign = "✓" if t.pnl_rupees > 0 else "✗"
            print(f"    {sign} {t.date} W{t.wave} {t.direction} "
                  f"strike={t.strike} entry={t.entry_premium:.1f} "
                  f"exit={t.exit_premium:.1f} [{t.exit_reason}] "
                  f"P&L=₹{t.pnl_rupees:+,.0f}")
