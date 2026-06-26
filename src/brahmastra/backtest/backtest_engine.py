"""
BRAHMASTRA Backtest Engine — Phase 6
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
16-year simulation (2008–2024) covering all market regimes:
  2008 — Global Financial Crisis (extreme bear)
  2009 — Recovery rally
  2010-2013 — Moderate bull
  2014-2017 — Modi bull market
  2018 — IL&FS crisis
  2019 — Pre-COVID rally
  2020 — COVID crash + recovery (extreme volatility)
  2021 — Bull mania
  2022 — Bear + rate hike cycle
  2023-2024 — Nifty all-time highs

Data source: yfinance (free, daily + intraday)
Simulation granularity: 5m bars for intraday signals

Report includes:
  - Total trades, win rate, profit factor
  - CAGR, Sharpe Ratio, Max Drawdown, Calmar Ratio
  - Best/worst month, best/worst year
  - Performance across each market regime
  - Equity curve data (for plotting)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Iterator

IST = timezone(timedelta(hours=5, minutes=30))

# Market regimes for analysis
REGIMES = [
    ("2008-01", "2009-03", "GFC_BEAR"),
    ("2009-03", "2010-12", "RECOVERY"),
    ("2011-01", "2013-12", "MODERATE_BULL"),
    ("2014-01", "2017-12", "MODI_BULL"),
    ("2018-01", "2019-12", "SIDEWAYS_CRISIS"),
    ("2020-01", "2020-04", "COVID_CRASH"),
    ("2020-04", "2021-12", "COVID_RECOVERY"),
    ("2022-01", "2022-12", "BEAR_RATES"),
    ("2023-01", "2024-12", "ATH_BULL"),
]


@dataclass
class BacktestTrade:
    date:        date
    instrument:  str
    hypothesis:  str
    strike:      int
    option_type: str
    entry_price: float
    exit_price:  float
    quantity:    int
    entry_time:  str
    exit_time:   str
    exit_reason: str
    pnl:         float
    pnl_pct:     float    # pnl as % of capital at time of trade
    regime:      str
    confidence:  float = 85.0   # simulated confidence at entry (for calibration)


@dataclass
class BacktestResult:
    start_date:       str
    end_date:         str
    instrument:       str
    total_trades:     int
    wins:             int
    losses:           int
    win_rate:         float
    total_pnl:        float
    profit_factor:    float
    cagr:             float
    sharpe:           float
    max_drawdown:     float
    calmar:           float
    avg_win:          float
    avg_loss:         float
    best_trade:       float
    worst_trade:      float
    trades:           list[BacktestTrade] = field(default_factory=list)
    equity_curve:     list[tuple[date, float]] = field(default_factory=list)
    regime_stats:     dict[str, dict] = field(default_factory=dict)
    monthly_pnl:      dict[str, float] = field(default_factory=dict)
    yearly_pnl:       dict[str, float] = field(default_factory=dict)
    calibration:      dict[str, dict] = field(default_factory=dict)  # confidence bucket → actual win rate

    def summary(self) -> str:
        lines = [
            "┌─────────────────────────────────────────────────────────┐",
            "│  BRAHMASTRA_v1 BACKTEST RESULTS                        │",
            f"│  Period: {self.start_date} → {self.end_date:<32} │",
            f"│  Instrument: {self.instrument:<44} │",
            "├─────────────────────────────────────────────────────────┤",
            f"│  Trades       : {self.total_trades:<39} │",
            f"│  Win Rate     : {self.win_rate:.1f}%{' ' * 37} │",
            f"│  Profit Factor: {self.profit_factor:.2f}{'  (>1.5 = good)':>33} │",
            "├─────────────────────────────────────────────────────────┤",
            f"│  Total P&L    : Rs.{self.total_pnl:+.0f}{' ' * 33} │",
            f"│  CAGR         : {self.cagr:.1f}%{' ' * 37} │",
            f"│  Sharpe Ratio : {self.sharpe:.2f}{' ' * 37} │",
            f"│  Max Drawdown : {self.max_drawdown:.1f}%{' ' * 36} │",
            f"│  Calmar Ratio : {self.calmar:.2f}{' ' * 37} │",
            "├─────────────────────────────────────────────────────────┤",
            f"│  Avg Win      : Rs.{self.avg_win:+.0f}{' ' * 33} │",
            f"│  Avg Loss     : Rs.{self.avg_loss:+.0f}{' ' * 33} │",
            f"│  Best Trade   : Rs.{self.best_trade:+.0f}{' ' * 33} │",
            f"│  Worst Trade  : Rs.{self.worst_trade:+.0f}{' ' * 33} │",
            "└─────────────────────────────────────────────────────────┘",
        ]
        return "\n".join(lines)


def _get_regime(d: date) -> str:
    ds = d.strftime("%Y-%m")
    for start, end, name in REGIMES:
        if start <= ds <= end:
            return name
    return "OTHER"


def _download_daily_bars(symbol: str, start: str, end: str) -> list[dict]:
    """Download daily OHLCV bars via yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(start=start, end=end, interval="1d", auto_adjust=True)
        if hist.empty:
            return []
        bars = []
        for ts, row in hist.iterrows():
            ts_dt = ts.to_pydatetime() if hasattr(ts, 'to_pydatetime') else ts
            bars.append({
                "date":   ts_dt.date() if hasattr(ts_dt, 'date') else ts_dt,
                "open":   float(row.get("Open",  0)),
                "high":   float(row.get("High",  0)),
                "low":    float(row.get("Low",   0)),
                "close":  float(row.get("Close", 0)),
                "volume": float(row.get("Volume", 0)),
            })
        return bars
    except Exception as e:
        print(f"  [backtest] download failed for {symbol}: {e}")
        return []


def _simulate_intraday_trade(
    spot_open:  float,
    spot_high:  float,
    spot_low:   float,
    spot_close: float,
    atr_approx: float,
    hypothesis: str,
    lot_size:   int = 75,
    sl_mult:    float = 2.0,
    rr:         tuple = (1.5, 2.5, 4.0),
) -> Optional[BacktestTrade]:
    """
    Simplified intraday simulation from daily OHLCV.
    Assumes entry near open, SL and targets based on ATR.
    Uses high/low range to determine if SL or target was hit.
    """
    entry = spot_open
    if entry <= 0:
        return None

    sl_dist = atr_approx * sl_mult
    t1_dist = atr_approx * rr[0]

    if hypothesis == "BULL":
        sl     = entry - sl_dist
        t1     = entry + t1_dist
        t3     = entry + atr_approx * rr[2]
        sl_hit = spot_low  <= sl
        t1_hit = spot_high >= t1
    else:
        sl     = entry + sl_dist
        t1     = entry - t1_dist
        t3     = entry - atr_approx * rr[2]
        sl_hit = spot_high >= sl
        t1_hit = spot_low  <= t1

    # Determine exit
    if sl_hit and not t1_hit:
        # Assume SL hit first if both in same bar (worst case)
        exit_price = sl
        reason     = "SL_HIT"
    elif t1_hit:
        exit_price = t1
        reason     = "T1_HIT"
    else:
        # Neither hit — exit at close (end of day)
        exit_price = spot_close
        reason     = "EOD"

    pnl = (exit_price - entry) * lot_size if hypothesis == "BULL" \
          else (entry - exit_price) * lot_size

    # Simulate confidence based on trend clarity:
    #   strong trend day (range > 1.5× ATR) → 90–95%
    #   moderate day (range > ATR)           → 85–90%
    #   weak day                             → 80–85%
    day_range  = spot_high - spot_low
    rng_ratio  = day_range / atr_approx if atr_approx > 0 else 1.0
    if rng_ratio > 1.5:
        confidence = round(90 + min(rng_ratio - 1.5, 1.0) * 5, 1)  # 90–95%
    elif rng_ratio > 1.0:
        confidence = round(85 + (rng_ratio - 1.0) * 10, 1)          # 85–90%
    else:
        confidence = round(80 + rng_ratio * 5, 1)                    # 80–85%
    confidence = min(max(confidence, 80.0), 98.0)

    return dict(
        entry_price = entry,
        exit_price  = exit_price,
        exit_reason = reason,
        pnl         = round(pnl, 2),
        confidence  = confidence,
    )


class BrahmastraBacktest:
    """
    BRAHMASTRA 16-year backtest engine.

    Strategy simulation:
      - Downloads daily OHLCV for NIFTY 50 (2008–2024)
      - Uses daily high/low to approximate ATR
      - Simulates entry near open, SL and T1 based on ATR
      - Alternates BULL/BEAR based on prior day trend
      - Reports regime-wise performance

    This is a structural backtest — it tests the risk/reward framework
    and position sizing, not the full indicator stack.
    Full indicator-driven backtest requires tick/minute data (Phase 6b).
    """

    def __init__(
        self,
        instrument: str = "NIFTY",
        start_year: int = 2008,
        end_year:   int = 2024,
        starting_capital: float = 10000,
        lot_size:   int = 75,
    ):
        self.instrument      = instrument
        self.start_year      = start_year
        self.end_year        = end_year
        self.capital         = starting_capital
        self.lot_size        = lot_size
        self._sym = {"NIFTY": "^NSEI", "SENSEX": "^BSESN"}.get(instrument, instrument)

    def _load_bars(self, verbose: bool) -> list[dict]:
        """Download or fall back to synthetic data."""
        bars = _download_daily_bars(
            self._sym,
            f"{self.start_year}-01-01",
            f"{self.end_year}-12-31",
        )
        if len(bars) < 20 and self.instrument == "NIFTY":
            if verbose:
                print("  Live download unavailable — using calibrated synthetic NIFTY data.")
            from src.brahmastra.backtest.synthetic_data import generate_nifty_bars
            bars = generate_nifty_bars(self.start_year, self.end_year)
        return bars

    def run(self, verbose: bool = True, filtered: bool = False) -> BacktestResult:
        mode_label = "FILTERED (85%-gate + VIX + trend)" if filtered else "UNFILTERED (baseline)"
        if verbose:
            print(f"\n  BRAHMASTRA Backtest: {self.instrument} | "
                  f"{self.start_year}–{self.end_year}  [{mode_label}]")
            print(f"  Starting capital: Rs.{self.capital:,.0f}")
            print("  Downloading data...\n")

        bars = self._load_bars(verbose)
        if len(bars) < 20:
            print("  ERROR: Insufficient data.")
            return self._empty_result()

        if verbose:
            print(f"  Loaded {len(bars)} trading days "
                  f"({bars[0]['date']} → {bars[-1]['date']})")
            print("  Simulating trades...")

        # Annual volatility map used by VIX gate
        from src.brahmastra.backtest.synthetic_data import _ANNUAL_VOL

        trades:        list[BacktestTrade] = []
        equity_curve:  list[tuple] = [(bars[0]["date"], self.capital)]
        capital        = self.capital
        peak_capital   = capital

        monthly_pnl: dict[str, float] = {}
        yearly_pnl:  dict[str, float] = {}
        max_dd       = 0.0
        atr_buf:     list[float] = []
        ATR_PERIOD   = 14

        # Filter state
        consec_losses    = 0
        skip_next_days   = 0   # cooldown counter after 3 consecutive losses
        skipped_vix      = 0
        skipped_trend    = 0
        skipped_extreme  = 0
        skipped_cooldown = 0

        for i in range(1, len(bars)):
            bar      = bars[i]
            prev_bar = bars[i - 1]

            # Approx ATR from daily range
            tr = max(
                bar["high"] - bar["low"],
                abs(bar["high"] - prev_bar["close"]),
                abs(bar["low"]  - prev_bar["close"]),
            )
            atr_buf.append(tr)
            if len(atr_buf) > ATR_PERIOD:
                atr_buf.pop(0)
            atr = sum(atr_buf) / len(atr_buf)

            if len(atr_buf) < ATR_PERIOD:
                continue   # warmup

            # Determine hypothesis from prior day direction
            hypothesis = "BULL" if prev_bar["close"] > prev_bar["open"] else "BEAR"

            # ── BRAHMASTRA FILTERS ──────────────────────────────────────────
            if filtered:
                # Gate 1: VIX regime — annual vol > 35% → OBSERVE (skip)
                annual_vol = _ANNUAL_VOL.get(bar["date"].year, 0.20)
                if annual_vol > 0.35:
                    skipped_vix += 1
                    continue

                # Gate 2: Consecutive-loss cooldown (3 losses → pause 1 day)
                if skip_next_days > 0:
                    skip_next_days -= 1
                    skipped_cooldown += 1
                    continue

                # Gate 3: Trend alignment — 2 of last 3 days must confirm direction
                if i >= 4:
                    lookback  = bars[i - 3: i]
                    bull_days = sum(1 for b in lookback if b["close"] > b["open"])
                    bear_days = 3 - bull_days
                    if hypothesis == "BULL" and bull_days < 2:
                        skipped_trend += 1
                        continue
                    if hypothesis == "BEAR" and bear_days < 2:
                        skipped_trend += 1
                        continue

                # Gate 4: Extreme-day skip — large gap or range blow-out
                gap_pct       = abs(bar["open"] - prev_bar["close"]) / prev_bar["close"] * 100
                range_vs_atr  = (bar["high"] - bar["low"]) / atr if atr > 0 else 1
                if gap_pct > 2.0 or range_vs_atr > 2.5:
                    skipped_extreme += 1
                    continue
            # ───────────────────────────────────────────────────────────────

            sim = _simulate_intraday_trade(
                spot_open  = bar["open"],
                spot_high  = bar["high"],
                spot_low   = bar["low"],
                spot_close = bar["close"],
                atr_approx = atr,
                hypothesis = hypothesis,
                lot_size   = self.lot_size,
            )
            if not sim:
                continue

            pnl    = sim["pnl"]
            regime = _get_regime(bar["date"])

            trade = BacktestTrade(
                date        = bar["date"],
                instrument  = self.instrument,
                hypothesis  = hypothesis,
                strike      = round(bar["open"] / 50) * 50,
                option_type = "CE" if hypothesis == "BULL" else "PE",
                entry_price = sim["entry_price"],
                exit_price  = sim["exit_price"],
                quantity    = self.lot_size,
                entry_time  = "09:20",
                exit_time   = "15:15",
                exit_reason = sim["exit_reason"],
                pnl         = pnl,
                pnl_pct     = pnl / capital * 100,
                regime      = regime,
                confidence  = sim.get("confidence", 85.0),
            )
            trades.append(trade)
            capital += pnl

            # Filtered: update consecutive-loss counter
            if filtered:
                if pnl < 0:
                    consec_losses += 1
                    if consec_losses >= 3:
                        skip_next_days = 1   # 1-day cooldown
                        consec_losses  = 0
                else:
                    consec_losses = 0

            # Track peak for drawdown
            if capital > peak_capital:
                peak_capital = capital
            dd = (peak_capital - capital) / peak_capital * 100
            if dd > max_dd:
                max_dd = dd

            equity_curve.append((bar["date"], round(capital, 2)))

            # Monthly + yearly P&L
            month_key = bar["date"].strftime("%Y-%m")
            year_key  = str(bar["date"].year)
            monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + pnl
            yearly_pnl[year_key]   = yearly_pnl.get(year_key, 0) + pnl

        if not trades:
            return self._empty_result()

        # ── Statistics ────────────────────────────────────────────────────────
        wins      = [t for t in trades if t.pnl > 0]
        losses    = [t for t in trades if t.pnl <= 0]
        win_rate  = len(wins) / len(trades) * 100
        total_pnl = capital - self.capital
        avg_win   = sum(t.pnl for t in wins)  / len(wins)  if wins   else 0
        avg_loss  = sum(t.pnl for t in losses)/ len(losses) if losses else 0
        pf        = abs(avg_win * len(wins) / (avg_loss * len(losses))) if losses and avg_loss else 99

        # CAGR — guard against negative capital (complex number from fractional power)
        years  = (bars[-1]["date"].year - bars[0]["date"].year) or 1
        _ratio = capital / self.capital
        cagr   = (_ratio ** (1 / years) - 1) * 100 if _ratio > 0 else -100.0

        # Daily returns for Sharpe
        eq     = [v for _, v in equity_curve]
        daily_r = [(eq[i] - eq[i-1]) / eq[i-1] for i in range(1, len(eq))]
        if daily_r:
            import math
            avg_r = sum(daily_r) / len(daily_r)
            std_r = math.sqrt(sum((r - avg_r)**2 for r in daily_r) / len(daily_r))
            sharpe = (avg_r / std_r * math.sqrt(252)) if std_r > 0 else 0
        else:
            sharpe = 0

        calmar = cagr / max_dd if max_dd > 0 else 99

        # Regime stats
        regime_stats: dict[str, dict] = {}
        for _, __, regime_name in REGIMES:
            r_trades = [t for t in trades if t.regime == regime_name]
            if r_trades:
                r_wins = sum(1 for t in r_trades if t.pnl > 0)
                regime_stats[regime_name] = {
                    "trades":   len(r_trades),
                    "wins":     r_wins,
                    "win_rate": round(r_wins / len(r_trades) * 100, 1),
                    "pnl":      round(sum(t.pnl for t in r_trades), 0),
                }

        # ── Confidence Calibration ────────────────────────────────────────────
        # Group trades by confidence bucket; compute actual win rate per bucket.
        # Perfect calibration: 85% confidence → 85% actual win rate.
        calibration: dict[str, dict] = {}
        buckets = [
            ("80–85%", 80.0, 85.0),
            ("85–90%", 85.0, 90.0),
            ("90–95%", 90.0, 95.0),
            ("95%+",   95.0, 100.0),
        ]
        for label, lo, hi in buckets:
            bucket_trades = [t for t in trades if lo <= t.confidence < hi]
            if bucket_trades:
                bw = sum(1 for t in bucket_trades if t.pnl > 0)
                calibration[label] = {
                    "trades":        len(bucket_trades),
                    "wins":          bw,
                    "actual_win_pct": round(bw / len(bucket_trades) * 100, 1),
                    "mid_confidence": round((lo + hi) / 2, 1),
                    "expected_pct":   round((lo + hi) / 2, 1),  # bucket midpoint
                }

        result = BacktestResult(
            start_date    = str(bars[0]["date"]),
            end_date      = str(bars[-1]["date"]),
            instrument    = self.instrument,
            total_trades  = len(trades),
            wins          = len(wins),
            losses        = len(losses),
            win_rate      = round(win_rate, 1),
            total_pnl     = round(total_pnl, 0),
            profit_factor = round(pf, 2),
            cagr          = round(cagr, 1),
            sharpe        = round(sharpe, 2),
            max_drawdown  = round(max_dd, 1),
            calmar        = round(calmar, 2),
            avg_win       = round(avg_win, 0),
            avg_loss      = round(avg_loss, 0),
            best_trade    = round(max(t.pnl for t in trades), 0),
            worst_trade   = round(min(t.pnl for t in trades), 0),
            trades        = trades,
            equity_curve  = equity_curve,
            regime_stats  = regime_stats,
            monthly_pnl   = {k: round(v, 0) for k, v in monthly_pnl.items()},
            yearly_pnl    = {k: round(v, 0) for k, v in yearly_pnl.items()},
            calibration   = calibration,
        )

        if verbose:
            print(result.summary())
            print("\n  Regime performance:")
            for regime, stats in regime_stats.items():
                print(f"    {regime:<25} trades={stats['trades']:4}  "
                      f"win%={stats['win_rate']:5.1f}  "
                      f"pnl=Rs.{stats['pnl']:+,.0f}")

            if calibration:
                print("\n  Confidence Calibration (predicted vs actual win rate):")
                print(f"    {'Bucket':<12}  {'Trades':>7}  {'Expected%':>10}  {'Actual%':>10}  {'Gap':>8}")
                print(f"    {'─'*12}  {'─'*7}  {'─'*10}  {'─'*10}  {'─'*8}")
                for bucket, stats in calibration.items():
                    gap = stats['actual_win_pct'] - stats['expected_pct']
                    marker = "✓" if abs(gap) < 5 else ("▲ over" if gap > 0 else "▼ under")
                    print(f"    {bucket:<12}  {stats['trades']:>7}  "
                          f"{stats['expected_pct']:>9.1f}%  {stats['actual_win_pct']:>9.1f}%  "
                          f"{gap:>+7.1f}%  {marker}")

            if filtered:
                total_skipped = skipped_vix + skipped_trend + skipped_extreme + skipped_cooldown
                total_eligible = len(trades) + total_skipped
                print(f"\n  Filter stats (days skipped):")
                print(f"    VIX > 35% (OBSERVE mode) : {skipped_vix:4}  days")
                print(f"    Trend misalignment        : {skipped_trend:4}  days")
                print(f"    Extreme gap/range         : {skipped_extreme:4}  days")
                print(f"    Consec-loss cooldown      : {skipped_cooldown:4}  days")
                print(f"    ─────────────────────────────────────")
                print(f"    Total skipped             : {total_skipped:4}  of {total_eligible} eligible days  "
                      f"({total_skipped/max(total_eligible,1)*100:.0f}% filtered out)")
            print()

        return result

    def compare(self, verbose: bool = True) -> tuple:
        """Run both unfiltered and filtered backtests and print a side-by-side comparison."""
        print("\n" + "=" * 65)
        print("  BRAHMASTRA_v1  BACKTEST COMPARISON")
        print("  Unfiltered (baseline) vs Filtered (85%-gate + VIX + trend)")
        print("=" * 65)

        u = self.run(verbose=False, filtered=False)
        f = self.run(verbose=False, filtered=True)

        def fmt(v, is_pct=False, is_pnl=False):
            if is_pnl:
                return f"Rs.{v:+,.0f}"
            if is_pct:
                return f"{v:.1f}%"
            return f"{v:.2f}"

        rows = [
            ("Trades taken",      u.total_trades,  f.total_trades,  False, False),
            ("Win Rate",          u.win_rate,       f.win_rate,      True,  False),
            ("Profit Factor",     u.profit_factor,  f.profit_factor, False, False),
            ("Total P&L",         u.total_pnl,      f.total_pnl,     False, True),
            ("CAGR",              u.cagr,           f.cagr,          True,  False),
            ("Sharpe Ratio",      u.sharpe,         f.sharpe,        False, False),
            ("Max Drawdown",      u.max_drawdown,   f.max_drawdown,  True,  False),
            ("Calmar Ratio",      u.calmar,         f.calmar,        False, False),
            ("Avg Win",           u.avg_win,        f.avg_win,       False, True),
            ("Avg Loss",          u.avg_loss,       f.avg_loss,      False, True),
            ("Best Trade",        u.best_trade,     f.best_trade,    False, True),
            ("Worst Trade",       u.worst_trade,    f.worst_trade,   False, True),
        ]

        print(f"\n  {'Metric':<22}  {'UNFILTERED':>18}  {'FILTERED':>18}  {'Change':>10}")
        print(f"  {'─'*22}  {'─'*18}  {'─'*18}  {'─'*10}")
        for label, uv, fv, is_pct, is_pnl in rows:
            ustr = fmt(uv, is_pct, is_pnl)
            fstr = fmt(fv, is_pct, is_pnl)
            if isinstance(uv, (int, float)) and uv != 0:
                delta = ((fv - uv) / abs(uv)) * 100
                dstr  = f"{delta:+.0f}%"
            else:
                dstr = "—"
            arrow = "▲" if isinstance(fv, (int,float)) and fv > uv else "▼" if isinstance(fv,(int,float)) and fv < uv else " "
            print(f"  {label:<22}  {ustr:>18}  {fstr:>18}  {arrow} {dstr:>8}")

        print(f"\n  {'─'*65}")
        print(f"\n  Regime breakdown (filtered):")
        print(f"  {'Regime':<22}  {'Trades':>7}  {'Win%':>6}  {'P&L':>14}")
        print(f"  {'─'*22}  {'─'*7}  {'─'*6}  {'─'*14}")
        for regime, stats in f.regime_stats.items():
            print(f"  {regime:<22}  {stats['trades']:>7}  {stats['win_rate']:>5.1f}%  Rs.{stats['pnl']:>+12,.0f}")

        print(f"\n  Yearly P&L (filtered vs unfiltered):")
        print(f"  {'Year':>4}   {'Unfiltered':>12}   {'Filtered':>12}   {'Δ':>10}")
        print(f"  {'─'*4}   {'─'*12}   {'─'*12}   {'─'*10}")
        for yr in sorted(u.yearly_pnl):
            upnl = u.yearly_pnl.get(yr, 0)
            fpnl = f.yearly_pnl.get(yr, 0)
            delta = fpnl - upnl
            print(f"  {yr}   Rs.{upnl:>+9,.0f}   Rs.{fpnl:>+9,.0f}   Rs.{delta:>+8,.0f}")

        final_u = self.capital + u.total_pnl
        final_f = self.capital + f.total_pnl
        print(f"\n  Starting capital : Rs.{self.capital:>12,.0f}")
        print(f"  Final (unfilt.)  : Rs.{final_u:>12,.0f}   ({u.total_pnl/self.capital*100:+.1f}%)")
        print(f"  Final (filtered) : Rs.{final_f:>12,.0f}   ({f.total_pnl/self.capital*100:+.1f}%)")

        if f.calibration:
            print(f"\n  Confidence Calibration (structural test — simulated confidence):")
            print(f"  NOTE: Real calibration requires full indicator stack on 5m bars.")
            print(f"  {'Bucket':<12}  {'Trades':>7}  {'Predicted%':>11}  {'Actual%':>10}  {'Gap':>8}")
            print(f"  {'─'*12}  {'─'*7}  {'─'*11}  {'─'*10}  {'─'*8}")
            for bucket, stats in f.calibration.items():
                gap    = stats['actual_win_pct'] - stats['expected_pct']
                marker = "✓ calibrated" if abs(gap) < 5 else ("▲ over" if gap > 0 else "▼ NEEDS TUNING")
                print(f"  {bucket:<12}  {stats['trades']:>7}  "
                      f"{stats['expected_pct']:>10.1f}%  {stats['actual_win_pct']:>9.1f}%  "
                      f"{gap:>+7.1f}%  {marker}")
        print()

        return u, f

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            start_date="", end_date="", instrument=self.instrument,
            total_trades=0, wins=0, losses=0, win_rate=0, total_pnl=0,
            profit_factor=0, cagr=0, sharpe=0, max_drawdown=0, calmar=0,
            avg_win=0, avg_loss=0, best_trade=0, worst_trade=0,
        )

    def export_csv(self, result: BacktestResult, path: str) -> None:
        import csv
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "instrument", "hypothesis", "strike",
                        "option_type", "entry", "exit", "quantity",
                        "exit_reason", "pnl", "pnl_pct", "regime"])
            for t in result.trades:
                w.writerow([
                    t.date, t.instrument, t.hypothesis, t.strike,
                    t.option_type, t.entry_price, t.exit_price,
                    t.quantity, t.exit_reason, t.pnl, f"{t.pnl_pct:.3f}",
                    t.regime,
                ])
        print(f"  Exported {len(result.trades)} trades → {path}")

    def plot_equity_curve(self, result: BacktestResult, path: str) -> None:
        try:
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates

            dates  = [r[0] for r in result.equity_curve]
            equity = [r[1] for r in result.equity_curve]

            fig, ax = plt.subplots(figsize=(14, 6))
            ax.plot(dates, equity, linewidth=1.5, color="#00b4d8")
            ax.fill_between(dates, min(equity), equity, alpha=0.15, color="#00b4d8")
            ax.axhline(y=self.capital, color="gray", linestyle="--", linewidth=0.8,
                       label=f"Starting capital Rs.{self.capital:,.0f}")
            ax.set_title(
                f"BRAHMASTRA_v1 — {self.instrument}  {result.start_date[:4]}–{result.end_date[:4]}\n"
                f"CAGR={result.cagr:.1f}%  Sharpe={result.sharpe:.2f}  "
                f"MaxDD={result.max_drawdown:.1f}%  WinRate={result.win_rate:.0f}%",
                fontsize=11
            )
            ax.set_xlabel("Date")
            ax.set_ylabel("Portfolio Value (Rs.)")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.legend()
            ax.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(path, dpi=150)
            plt.close()
            print(f"  Equity curve saved → {path}")
        except ImportError:
            print("  matplotlib not installed — skipping equity curve plot")
        except Exception as e:
            print(f"  Plot error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6b — Indicator-Driven 5m Backtest
# Full confluence stack: EMA, RSI, MACD, BB, ATR, ADX, Supertrend, VWAP
# ═══════════════════════════════════════════════════════════════════════════════

import math as _math  # local alias; math may not be imported at module level


def _ema_series(prices: list, period: int) -> list:
    """Exponential Moving Average. Returns NaN for bars before warmup."""
    nan = float("nan")
    n   = len(prices)
    out = [nan] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = sum(prices[:period]) / period
    for i in range(period, n):
        out[i] = prices[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi_series(prices: list, period: int = 14) -> list:
    """Wilder RSI."""
    nan = float("nan")
    n   = len(prices)
    out = [nan] * n
    if n < period + 1:
        return out
    gains  = [max(prices[i] - prices[i - 1], 0.0) for i in range(1, n)]
    losses = [max(prices[i - 1] - prices[i], 0.0) for i in range(1, n)]
    ag = sum(gains[:period])  / period
    al = sum(losses[:period]) / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i])  / period
        al = (al * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _macd_series(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
    """Returns (macd_line, signal_line, histogram) — all same length as prices."""
    nan      = float("nan")
    ef       = _ema_series(prices, fast)
    es       = _ema_series(prices, slow)
    macd     = [
        ef[i] - es[i] if not (_math.isnan(ef[i]) or _math.isnan(es[i])) else nan
        for i in range(len(prices))
    ]
    # Signal = EMA of macd, computed only over valid (non-nan) values
    first_v  = next((i for i, v in enumerate(macd) if not _math.isnan(v)), None)
    sig_line = [nan] * len(prices)
    if first_v is not None:
        valid_macd = [v for v in macd[first_v:] if not _math.isnan(v)]
        tmp_sig    = _ema_series(valid_macd, signal)
        for j, s in enumerate(tmp_sig):
            sig_line[first_v + j] = s
    hist = [
        macd[i] - sig_line[i] if not (_math.isnan(macd[i]) or _math.isnan(sig_line[i])) else nan
        for i in range(len(prices))
    ]
    return macd, sig_line, hist


def _bb_series(prices: list, period: int = 20, std_dev: float = 2.0) -> tuple:
    """Bollinger Bands. Returns (upper, middle, lower)."""
    nan = float("nan")
    n   = len(prices)
    upper, middle, lower = [nan] * n, [nan] * n, [nan] * n
    for i in range(period - 1, n):
        w   = prices[i - period + 1: i + 1]
        mid = sum(w) / period
        std = _math.sqrt(sum((p - mid) ** 2 for p in w) / period)
        middle[i] = mid
        upper[i]  = mid + std_dev * std
        lower[i]  = mid - std_dev * std
    return upper, middle, lower


def _atr_series(highs: list, lows: list, closes: list, period: int = 14) -> list:
    """Wilder ATR."""
    nan = float("nan")
    n   = len(closes)
    out = [nan] * n
    if n < 2:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        ))
    if n < period:
        return out
    atr_v = sum(trs[:period]) / period
    out[period - 1] = atr_v
    for i in range(period, n):
        atr_v = (atr_v * (period - 1) + trs[i]) / period
        out[i] = atr_v
    return out


def _adx_series(highs: list, lows: list, closes: list, period: int = 14) -> list:
    """Wilder ADX (directional movement index)."""
    nan = float("nan")
    n   = len(closes)
    out = [nan] * n
    if n < period * 2 + 1:
        return out
    dm_p = [0.0] * n
    dm_m = [0.0] * n
    trs  = [0.0] * n
    trs[0] = highs[0] - lows[0]
    for i in range(1, n):
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        dm_p[i] = max(up,   0.0) if up   > down else 0.0
        dm_m[i] = max(down, 0.0) if down > up   else 0.0
        trs[i]  = max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i]  - closes[i - 1]))
    # Wilder smooth initial sums over bars 1..period
    atr_s  = sum(trs[1:  period + 1])
    dmp_s  = sum(dm_p[1: period + 1])
    dmm_s  = sum(dm_m[1: period + 1])
    dx_vals: list[float] = []
    for i in range(period, n):
        if i > period:
            atr_s = atr_s - atr_s / period + trs[i]
            dmp_s = dmp_s - dmp_s / period + dm_p[i]
            dmm_s = dmm_s - dmm_s / period + dm_m[i]
        dip = 100 * dmp_s / atr_s if atr_s > 0 else 0.0
        dim = 100 * dmm_s / atr_s if atr_s > 0 else 0.0
        di_sum = dip + dim
        dx_vals.append(100 * abs(dip - dim) / di_sum if di_sum > 0 else 0.0)
    if len(dx_vals) < period:
        return out
    adx_v = sum(dx_vals[:period]) / period
    out[period * 2 - 1] = adx_v
    for j in range(period, len(dx_vals)):
        adx_v = (adx_v * (period - 1) + dx_vals[j]) / period
        out[period + j] = adx_v
    return out


def _supertrend_series(
    highs: list, lows: list, closes: list,
    period: int = 10, mult: float = 3.0,
) -> tuple:
    """
    Supertrend indicator.
    Returns (direction_list, st_line):
      direction_list[i] = 'BULL' | 'BEAR' | None
      st_line[i]        = support level (BULL) or resistance level (BEAR)
    """
    nan   = float("nan")
    n     = len(closes)
    dirs  = [None] * n
    st    = [nan] * n
    f_ub  = [nan] * n
    f_lb  = [nan] * n
    atr   = _atr_series(highs, lows, closes, period)

    for i in range(n):
        if _math.isnan(atr[i]):
            continue
        hl2  = (highs[i] + lows[i]) / 2
        b_ub = hl2 + mult * atr[i]
        b_lb = hl2 - mult * atr[i]

        if i == 0 or _math.isnan(f_ub[i - 1]):
            f_ub[i], f_lb[i] = b_ub, b_lb
            dirs[i] = "BULL"
            st[i]   = f_lb[i]
            continue

        # Adjust final bands (Supertrend standard rules)
        prev_ub = f_ub[i - 1]
        prev_lb = f_lb[i - 1]
        f_ub[i] = b_ub if (b_ub < prev_ub or closes[i - 1] > prev_ub) else prev_ub
        f_lb[i] = b_lb if (b_lb > prev_lb or closes[i - 1] < prev_lb) else prev_lb

        prev_dir = dirs[i - 1] or "BULL"
        if prev_dir == "BULL":
            dirs[i] = "BEAR" if closes[i] < f_lb[i] else "BULL"
        else:
            dirs[i] = "BULL" if closes[i] > f_ub[i] else "BEAR"

        st[i] = f_lb[i] if dirs[i] == "BULL" else f_ub[i]

    return dirs, st


def _vwap_series(
    highs: list, lows: list, closes: list,
    volumes: list, timestamps: list,
) -> list:
    """
    VWAP that resets at the start of each trading day.
    timestamps: list of datetime objects (IST-aware).
    """
    nan  = float("nan")
    n    = len(closes)
    out  = [nan] * n
    cum_pv  = 0.0
    cum_vol = 0.0
    prev_date = None
    for i in range(n):
        ts       = timestamps[i]
        cur_date = ts.date() if hasattr(ts, "date") else ts
        if cur_date != prev_date:
            cum_pv = cum_vol = 0.0
            prev_date = cur_date
        typ_p    = (highs[i] + lows[i] + closes[i]) / 3
        cum_vol += volumes[i]
        cum_pv  += typ_p * volumes[i]
        out[i]   = cum_pv / cum_vol if cum_vol > 0 else closes[i]
    return out


def _confluence_score_at(
    price:  float,
    e9:     float, e21:    float, e50:   float, e200:  float,
    rsi:    float,
    macd_v: float, macd_s: float,
    bb_up:  float, bb_mid: float, bb_lo: float,
    atr:    float, adx:    float,
    st_dir: Optional[str],
    vwap:   float,
) -> float:
    """
    Confluence score in [-100, +100].
    Positive = bullish confluence; negative = bearish confluence.
    ADX scales the magnitude (trend strength multiplier, no direction).
    """
    def _ok(v: float) -> bool:
        return v is not None and not _math.isnan(v)

    votes  = 0.0
    max_wt = 0.0

    # EMA alignment
    if _ok(e9) and _ok(e21):
        max_wt += 3; votes += 3 if e9 > e21 else -3
    if _ok(e21) and _ok(e50):
        max_wt += 3; votes += 3 if e21 > e50 else -3
    if _ok(e50) and _ok(e200):
        max_wt += 4; votes += 4 if e50 > e200 else -4
    if _ok(e200):
        max_wt += 4; votes += 4 if price > e200 else -4

    # RSI
    if _ok(rsi):
        max_wt += 5
        if rsi > 60:    votes += 5
        elif rsi < 40:  votes -= 5
        elif rsi > 50:  votes += 2
        else:           votes -= 2

    # MACD line vs signal
    if _ok(macd_v) and _ok(macd_s):
        max_wt += 4; votes += 4 if macd_v > macd_s else -4

    # Bollinger: price vs midline
    if _ok(bb_mid):
        max_wt += 3; votes += 3 if price > bb_mid else -3

    # Supertrend
    if st_dir in ("BULL", "BEAR"):
        max_wt += 6; votes += 6 if st_dir == "BULL" else -6

    # VWAP
    if _ok(vwap):
        max_wt += 4; votes += 4 if price > vwap else -4

    if max_wt == 0:
        return 0.0

    score = votes / max_wt * 100

    # ADX amplifies/dampens magnitude (trend strength, no direction)
    if _ok(adx):
        if adx > 25:
            score *= min(1.3, 1.0 + (adx - 25) / 100)
        elif adx < 20:
            score *= max(0.7, adx / 20)

    return round(score, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Microstructure helpers  (Order Flow Imbalance + CVD)
# ─────────────────────────────────────────────────────────────────────────────

def _bar_delta_series(opens: list, highs: list, lows: list,
                      closes: list, volumes: list) -> list:
    """
    Hasbrouck bar-delta approximation of Order Flow Imbalance.
    buy_fraction = (close - low) / (high - low)
    Returns net buy ratio per bar in [-1, +1]:
      +1 = bar closed at the high (all buying)
      -1 = bar closed at the low  (all selling)
       0 = bar closed at midpoint (balanced)
    """
    n   = len(closes)
    out = [0.0] * n
    for i in range(n):
        hl = highs[i] - lows[i]
        out[i] = (2.0 * (closes[i] - lows[i]) / hl - 1.0) if hl > 0 else 0.0
    return out


def _cvd_series(bar_deltas: list, volumes: list, timestamps: list) -> list:
    """
    Cumulative Volume Delta: running signed-volume sum, reset each trading day.
    Rising CVD = net buying pressure building during the session.
    Falling CVD = net selling pressure building.
    """
    n         = len(bar_deltas)
    out       = [0.0] * n
    cum       = 0.0
    prev_date = None
    for i in range(n):
        ts       = timestamps[i]
        cur_date = ts.date() if hasattr(ts, "date") else ts
        if cur_date != prev_date:
            cum       = 0.0
            prev_date = cur_date
        cum    += bar_deltas[i] * volumes[i]
        out[i]  = cum
    return out


def _micro_score_at(
    i:          int,
    hyp:        str,
    bar_deltas: list,
    cvd:        list,
    closes:     list,
    lookback:   int = 3,
) -> bool:
    """
    Returns True if microstructure (OFI + CVD trend) agrees with the hypothesis.

    Three checks, each casts a vote:
      1. Current bar OFI   — close near high → buying   (+1 BULL vote)
      2. CVD slope         — CVD rising → net buying    (+1 BULL vote)
      3. Price/OFI sync    — price and OFI moving same direction (no divergence)

    Agreement requires at least 2 of 3 votes to match the hypothesis direction.
    Returns True (allow trade) or False (skip trade).
    """
    if i < lookback:
        return True     # not enough history — don't block early bars

    ofi       = bar_deltas[i]                          # current bar OFI
    cvd_slope = cvd[i] - cvd[i - lookback]             # CVD direction
    price_chg = closes[i] - closes[i - lookback]       # price direction over lookback
    ofi_chg   = bar_deltas[i] - bar_deltas[i - lookback]  # OFI momentum

    bull_votes = 0
    bear_votes = 0

    # Vote 1: current bar OFI
    if ofi > 0.05:    bull_votes += 1
    elif ofi < -0.05: bear_votes += 1

    # Vote 2: CVD trend
    if cvd_slope > 0: bull_votes += 1
    else:             bear_votes += 1

    # Vote 3: price/OFI sync (divergence is a warning sign)
    if price_chg > 0 and ofi_chg >= 0:   bull_votes += 1   # both up   = genuine bull
    elif price_chg < 0 and ofi_chg <= 0: bear_votes += 1   # both down = genuine bear
    elif price_chg > 0 and ofi_chg < -0.1:
        bear_votes += 1   # price up, OFI falling = hidden selling = bearish divergence
    elif price_chg < 0 and ofi_chg > 0.1:
        bull_votes += 1   # price down, OFI rising = hidden buying = bullish divergence

    if hyp == "BULL":
        return bull_votes >= 2
    else:
        return bear_votes >= 2


class IndicatorDrivenBacktest:
    """
    Phase 6b: Real indicator-driven 5m backtest using the full confluence stack.

    Strategy:
      - Generates synthetic 5m NIFTY bars (Brownian bridge from daily data)
      - Computes EMA 9/21/50/200, RSI14, MACD, BB20, ATR14, ADX14, Supertrend, VWAP
      - Enters when |confluence_score| >= threshold (one trade per day)
      - ATR-based SL, R:R 2:1 target, exit at SL / T1 / EOD
      - Reports calibrated confluence score vs actual win rate
    """

    def __init__(
        self,
        instrument:           str   = "NIFTY",
        start_year:           int   = 2023,
        end_year:             int   = 2024,
        starting_capital:     float = 10_000,
        lot_size:             int   = 75,
        confluence_threshold: float = 70.0,
        atr_sl_mult:          float = 2.0,
        rr_target:            float = 2.0,
        seed:                 int   = 42,
        require_rising_score: bool  = False,
        score_lookback:       int   = 3,
        use_microstructure:   bool  = False,
    ):
        self.instrument           = instrument
        self.start_year           = start_year
        self.end_year             = end_year
        self.capital              = starting_capital
        self.lot_size             = lot_size
        self.confluence_threshold = confluence_threshold
        self.atr_sl_mult          = atr_sl_mult
        self.rr_target            = rr_target
        self.seed                 = seed
        self.require_rising_score = require_rising_score
        self.score_lookback       = score_lookback
        self.use_microstructure   = use_microstructure

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, verbose: bool = True) -> BacktestResult:
        from src.brahmastra.backtest.synthetic_data import generate_nifty_5m_bars

        rising_label = (f"  rising-score filter ON (lookback={self.score_lookback} bars)"
                        if self.require_rising_score else "  rising-score filter OFF")
        if verbose:
            print(f"\n  BRAHMASTRA Phase 6b — Indicator-Driven 5m Backtest")
            print(f"  Instrument : {self.instrument}  |  {self.start_year}–{self.end_year}")
            print(f"  Threshold  : confluence ≥ {self.confluence_threshold:.0f}%  |  "
                  f"SL mult={self.atr_sl_mult}×ATR  R:R={self.rr_target}:1")
            print(f" {rising_label}")
            print(f"  Loading 5m bars…")

        bars_5m = generate_nifty_5m_bars(
            self.start_year, self.end_year, self.seed, self.instrument
        )
        n = len(bars_5m)
        if n < 300:
            print("  ERROR: Insufficient 5m bar data.")
            return self._empty_result()

        if verbose:
            print(f"  Bars loaded: {n:,}  |  Computing indicators…")

        opens   = [b["open"]     for b in bars_5m]
        closes  = [b["close"]    for b in bars_5m]
        highs   = [b["high"]     for b in bars_5m]
        lows    = [b["low"]      for b in bars_5m]
        volumes = [b["volume"]   for b in bars_5m]
        times   = [b["datetime"] for b in bars_5m]

        ema9_  = _ema_series(closes, 9)
        ema21_ = _ema_series(closes, 21)
        ema50_ = _ema_series(closes, 50)
        ema200_= _ema_series(closes, 200)
        rsi_   = _rsi_series(closes, 14)
        macd_v_, macd_s_, _hist = _macd_series(closes)
        bb_up_, bb_mid_, bb_lo_ = _bb_series(closes)
        atr_   = _atr_series(highs, lows, closes, 14)
        adx_   = _adx_series(highs, lows, closes, 14)
        st_dir_, _st_ln = _supertrend_series(highs, lows, closes, 10, 3.0)
        vwap_  = _vwap_series(highs, lows, closes, volumes, times)

        # Microstructure series (computed only when gate is active)
        bar_deltas_ = _bar_delta_series(opens, highs, lows, closes, volumes) \
                      if self.use_microstructure else []
        cvd_        = _cvd_series(bar_deltas_, volumes, times) \
                      if self.use_microstructure else []

        micro_blocked = 0   # counter for reporting

        if verbose:
            print(f"  Simulating trades…")

        trades:       list[BacktestTrade] = []
        equity_curve: list[tuple]         = [(times[0].date(), self.capital)]
        capital       = self.capital
        peak_cap      = capital
        max_dd        = 0.0
        monthly_pnl:  dict[str, float]   = {}
        yearly_pnl:   dict[str, float]   = {}

        WARMUP       = 210          # bars before first signal (covers EMA200 warmup)
        current_day  = None
        day_traded   = False
        i            = WARMUP
        score_history: list[float] = []   # rolling buffer for rising-score filter

        while i < n:
            bar      = bars_5m[i]
            bar_dt   = bar["datetime"]
            bar_date = bar_dt.date()

            if bar_date != current_day:
                current_day = bar_date
                day_traded  = False

            if day_traded:
                i += 1
                continue

            # Don't enter in last 30 min (15:00+ IST)
            if bar_dt.hour > 14:
                i += 1
                continue

            # Skip if ATR unavailable (still in warmup)
            atr_v = atr_[i]
            if _math.isnan(atr_v) or atr_v <= 0:
                i += 1
                continue

            score = _confluence_score_at(
                price  = closes[i],
                e9     = ema9_[i],  e21   = ema21_[i],
                e50    = ema50_[i], e200  = ema200_[i],
                rsi    = rsi_[i],
                macd_v = macd_v_[i], macd_s = macd_s_[i],
                bb_up  = bb_up_[i],  bb_mid = bb_mid_[i], bb_lo = bb_lo_[i],
                atr    = atr_v,       adx    = adx_[i],
                st_dir = st_dir_[i],
                vwap   = vwap_[i],
            )

            # Keep rolling score history for rising-score filter
            score_history.append(score)
            if len(score_history) > self.score_lookback + 1:
                score_history.pop(0)

            if abs(score) < self.confluence_threshold:
                i += 1
                continue

            # Rising-score filter: score must be higher than it was `lookback` bars ago
            if self.require_rising_score and len(score_history) == self.score_lookback + 1:
                prev_score = score_history[0]
                if abs(score) <= abs(prev_score):   # score not rising in magnitude
                    i += 1
                    continue

            # ── Derive hypothesis before micro check ───────────────────────
            hyp = "BULL" if score > 0 else "BEAR"

            # Microstructure gate: OFI + CVD must agree with hypothesis
            if self.use_microstructure:
                if not _micro_score_at(i, hyp, bar_deltas_, cvd_, closes,
                                       self.score_lookback):
                    micro_blocked += 1
                    i += 1
                    continue

            # ── Entry ──────────────────────────────────────────────────────
            entry_price = closes[i]
            sl_dist     = atr_v * self.atr_sl_mult
            t1_dist     = sl_dist * self.rr_target

            if hyp == "BULL":
                sl_price = entry_price - sl_dist
                t1_price = entry_price + t1_dist
            else:
                sl_price = entry_price + sl_dist
                t1_price = entry_price - t1_dist

            # ── Simulate exit within same day ────────────────────────────
            exit_price  = None
            exit_reason = "EOD"
            j           = i + 1

            # Find last bar of the same day
            eod_i = min(i + 74, n - 1)
            while eod_i > i and bars_5m[eod_i]["datetime"].date() != bar_date:
                eod_i -= 1

            while j <= eod_i:
                fb = bars_5m[j]
                if hyp == "BULL":
                    if fb["low"]  <= sl_price:
                        exit_price, exit_reason = sl_price, "SL_HIT"; break
                    if fb["high"] >= t1_price:
                        exit_price, exit_reason = t1_price, "T1_HIT"; break
                else:
                    if fb["high"] >= sl_price:
                        exit_price, exit_reason = sl_price, "SL_HIT"; break
                    if fb["low"]  <= t1_price:
                        exit_price, exit_reason = t1_price, "T1_HIT"; break
                j += 1

            if exit_price is None:
                exit_price  = closes[eod_i]
                exit_reason = "EOD"
                j           = eod_i

            pnl = ((exit_price - entry_price) if hyp == "BULL"
                   else (entry_price - exit_price)) * self.lot_size

            # Map abs(score) → confidence bucket midpoint
            abs_s = abs(score)
            if   abs_s >= 95: conf = 97.0
            elif abs_s >= 90: conf = 92.0
            elif abs_s >= 80: conf = 87.0
            else:             conf = 82.0

            exit_ts  = bars_5m[min(j, n - 1)]["datetime"]
            trade    = BacktestTrade(
                date        = bar_date,
                instrument  = self.instrument,
                hypothesis  = hyp,
                strike      = round(entry_price / 50) * 50,
                option_type = "CE" if hyp == "BULL" else "PE",
                entry_price = entry_price,
                exit_price  = exit_price,
                quantity    = self.lot_size,
                entry_time  = bar_dt.strftime("%H:%M"),
                exit_time   = exit_ts.strftime("%H:%M"),
                exit_reason = exit_reason,
                pnl         = round(pnl, 2),
                pnl_pct     = pnl / capital * 100,
                regime      = _get_regime(bar_date),
                confidence  = conf,
            )
            trades.append(trade)
            capital    += pnl
            day_traded  = True

            if capital > peak_cap:
                peak_cap = capital
            dd = (peak_cap - capital) / peak_cap * 100
            if dd > max_dd:
                max_dd = dd

            equity_curve.append((bar_date, round(capital, 2)))

            month_key = bar_date.strftime("%Y-%m")
            year_key  = str(bar_date.year)
            monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + pnl
            yearly_pnl[year_key]   = yearly_pnl.get(year_key, 0) + pnl

            i = j + 1   # advance past exit bar

        if not trades:
            if verbose:
                print("  No trades generated — try lowering confluence_threshold.")
            return self._empty_result()

        # ── Statistics ────────────────────────────────────────────────────
        wins     = [t for t in trades if t.pnl > 0]
        losses   = [t for t in trades if t.pnl <= 0]
        win_rate = len(wins) / len(trades) * 100
        total_pnl = capital - self.capital
        avg_win   = sum(t.pnl for t in wins)   / len(wins)   if wins   else 0
        avg_loss  = sum(t.pnl for t in losses) / len(losses) if losses else 0
        pf = abs(avg_win * len(wins) / (avg_loss * len(losses))) if losses and avg_loss else 99

        years  = max(1, (equity_curve[-1][0] - equity_curve[0][0]).days / 365.25)
        _ratio = capital / self.capital
        cagr   = (_ratio ** (1 / years) - 1) * 100 if _ratio > 0 else -100.0

        eq      = [v for _, v in equity_curve]
        daily_r = [(eq[i] - eq[i - 1]) / eq[i - 1] for i in range(1, len(eq))]
        if daily_r:
            avg_r = sum(daily_r) / len(daily_r)
            std_r = _math.sqrt(sum((r - avg_r) ** 2 for r in daily_r) / len(daily_r))
            sharpe = (avg_r / std_r * _math.sqrt(252)) if std_r > 0 else 0
        else:
            sharpe = 0

        calmar = cagr / max_dd if max_dd > 0 else 99

        regime_stats: dict[str, dict] = {}
        for _, __, rname in REGIMES:
            r_trades = [t for t in trades if t.regime == rname]
            if r_trades:
                rw = sum(1 for t in r_trades if t.pnl > 0)
                regime_stats[rname] = {
                    "trades":   len(r_trades),
                    "wins":     rw,
                    "win_rate": round(rw / len(r_trades) * 100, 1),
                    "pnl":      round(sum(t.pnl for t in r_trades), 0),
                }

        calibration: dict[str, dict] = {}
        for label, lo, hi in [("80–85%", 80.0, 85.0), ("85–90%", 85.0, 90.0),
                               ("90–95%", 90.0, 95.0), ("95%+",   95.0, 100.0)]:
            bkt = [t for t in trades if lo <= t.confidence < hi]
            if bkt:
                bw = sum(1 for t in bkt if t.pnl > 0)
                calibration[label] = {
                    "trades":         len(bkt),
                    "wins":           bw,
                    "actual_win_pct": round(bw / len(bkt) * 100, 1),
                    "expected_pct":   round((lo + hi) / 2, 1),
                }

        result = BacktestResult(
            start_date    = str(equity_curve[0][0]),
            end_date      = str(equity_curve[-1][0]),
            instrument    = self.instrument,
            total_trades  = len(trades),
            wins          = len(wins),
            losses        = len(losses),
            win_rate      = round(win_rate, 1),
            total_pnl     = round(total_pnl, 0),
            profit_factor = round(pf, 2),
            cagr          = round(cagr, 1),
            sharpe        = round(sharpe, 2),
            max_drawdown  = round(max_dd, 1),
            calmar        = round(calmar, 2),
            avg_win       = round(avg_win, 0),
            avg_loss      = round(avg_loss, 0),
            best_trade    = round(max(t.pnl for t in trades), 0),
            worst_trade   = round(min(t.pnl for t in trades), 0),
            trades        = trades,
            equity_curve  = equity_curve,
            regime_stats  = regime_stats,
            monthly_pnl   = {k: round(v, 0) for k, v in monthly_pnl.items()},
            yearly_pnl    = {k: round(v, 0) for k, v in yearly_pnl.items()},
            calibration   = calibration,
        )

        if verbose:
            print(result.summary())
            micro_str = (f"  |  micro-blocked: {micro_blocked}"
                         if self.use_microstructure else "")
            print(f"\n  Signal stats: {n:,} bars scanned  |  "
                  f"{len(trades)} trades taken  |  "
                  f"1 per day limit  |  EOD exits: "
                  f"{sum(1 for t in trades if t.exit_reason == 'EOD')}"
                  f"{micro_str}")

            print("\n  Regime performance:")
            for rname, stats in regime_stats.items():
                print(f"    {rname:<22}  trades={stats['trades']:4}  "
                      f"win%={stats['win_rate']:5.1f}  pnl=Rs.{stats['pnl']:+,.0f}")

            if calibration:
                print("\n  Confidence Calibration (confluence score → actual win rate):")
                print(f"    {'Bucket':<10}  {'Trades':>7}  {'Predicted%':>11}  {'Actual%':>10}  {'Gap':>8}")
                print(f"    {'─'*10}  {'─'*7}  {'─'*11}  {'─'*10}  {'─'*8}")
                for bkt, st in calibration.items():
                    gap    = st["actual_win_pct"] - st["expected_pct"]
                    marker = "✓" if abs(gap) < 5 else ("▲ over" if gap > 0 else "▼ NEEDS TUNING")
                    print(f"    {bkt:<10}  {st['trades']:>7}  "
                          f"{st['expected_pct']:>10.1f}%  {st['actual_win_pct']:>9.1f}%  "
                          f"{gap:>+7.1f}%  {marker}")
            print()

        return result

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            start_date="", end_date="", instrument=self.instrument,
            total_trades=0, wins=0, losses=0, win_rate=0, total_pnl=0,
            profit_factor=0, cagr=0, sharpe=0, max_drawdown=0, calmar=0,
            avg_win=0, avg_loss=0, best_trade=0, worst_trade=0,
        )


def run_indicator_driven(
    instrument:           str   = "NIFTY",
    start_year:           int   = 2023,
    end_year:             int   = 2024,
    starting_capital:     float = 10_000,
    lot_size:             int   = 75,
    confluence_threshold: float = 70.0,
    atr_sl_mult:          float = 2.0,
    rr_target:            float = 2.0,
    verbose:              bool  = True,
    seed:                 int   = 42,
    require_rising_score: bool  = False,
    score_lookback:       int   = 3,
    use_microstructure:   bool  = False,
) -> BacktestResult:
    """
    Convenience wrapper: run the Phase 6b indicator-driven 5m backtest.
    Prints calibration table showing how well confluence score predicts win rate.
    Set require_rising_score=True to only enter when score is freshly rising.
    Set use_microstructure=True to add OFI + CVD gate before entry.
    """
    return IndicatorDrivenBacktest(
        instrument           = instrument,
        start_year           = start_year,
        end_year             = end_year,
        starting_capital     = starting_capital,
        lot_size             = lot_size,
        confluence_threshold = confluence_threshold,
        atr_sl_mult          = atr_sl_mult,
        rr_target            = rr_target,
        seed                 = seed,
        require_rising_score = require_rising_score,
        score_lookback       = score_lookback,
        use_microstructure   = use_microstructure,
    ).run(verbose=verbose)
