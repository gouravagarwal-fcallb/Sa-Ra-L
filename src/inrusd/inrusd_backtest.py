"""
INRUSD_v1 Backtest Engine
━━━━━━━━━━━━━━━━━━━━━━━━━
14-year simulation (2012–2025) on USDINR daily bars from yfinance.

Simulation model:
  - Uses daily OHLCV bars to simulate intraday signals (same approach as BRAHMASTRA).
  - Pre-session bias derived from DXY, crude oil, EUR/USD, US 10Y yield daily changes.
  - Signal evaluated on open+10min proxy (open + 0.3 × daily range) to avoid look-ahead.
  - Entry: open-based price (market open fill + small slippage).
  - Exit: either hits target/stop within the day (simulated from High/Low) OR closes at EOD.

Currency regimes covered:
  2012–2013 : INR_DEPRECIATION_1   (55→68 vs USD — rupee crisis)
  2014–2016 : INR_STABILISATION    (NDA era; oil crash helped INR)
  2017–2018 : INR_DEPRECIATION_2   (64→74; EM selloff, oil spike)
  2019      : INR_RANGE_BOUND      (68–72 consolidation)
  2020      : COVID_CRASH          (INR hit 76+; RBI defended)
  2021      : COVID_RECOVERY       (FII inflows; INR back to 72)
  2022      : INR_DEPRECIATION_3   (Fed hikes; 74→83)
  2023–2024 : INR_RANGE_TIGHT      (82–84; RBI actively managing)
  2025      : CURRENT
"""

from __future__ import annotations

import os
import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

# Currency regimes for categorised analysis
CURRENCY_REGIMES = [
    ("2012-01", "2013-08", "INR_DEPRECIATION_1"),
    ("2013-09", "2014-05", "INR_STABILISATION"),
    ("2014-06", "2016-12", "MODERATE_INR"),
    ("2017-01", "2018-12", "INR_DEPRECIATION_2"),
    ("2019-01", "2019-12", "INR_RANGE_BOUND"),
    ("2020-01", "2020-04", "COVID_CRASH"),
    ("2020-05", "2021-12", "COVID_RECOVERY"),
    ("2022-01", "2022-12", "INR_DEPRECIATION_3"),
    ("2023-01", "2024-12", "INR_RANGE_TIGHT"),
    ("2025-01", "2025-12", "CURRENT"),
]


def _get_regime(d: date) -> str:
    ds = d.strftime("%Y-%m")
    for start, end, name in CURRENCY_REGIMES:
        if start <= ds <= end:
            return name
    return "OTHER"


@dataclass
class INRUSDTrade:
    date:         date
    direction:    str          # LONG | SHORT
    entry_price:  float
    exit_price:   float
    lots:         int
    lot_size:     int          # 1000 USD
    entry_time:   str
    exit_time:    str
    exit_reason:  str          # TARGET | STOP | EOD
    pnl_inr:     float         # net P&L in INR after costs
    pnl_pct:     float         # as % of initial capital at time of trade
    regime:       str
    bias_score:   int
    score:        int          # number of technical conditions met


@dataclass
class INRUSDBacktestResult:
    start_date:      str
    end_date:        str
    total_trades:    int
    wins:            int
    losses:          int
    win_rate:        float
    total_pnl:       float
    max_drawdown:    float
    cagr:            float
    sharpe:          float
    calmar:          float
    profit_factor:   float
    avg_win:         float
    avg_loss:        float
    best_trade:      float
    worst_trade:     float
    trades:          list[INRUSDTrade] = field(default_factory=list)
    equity_curve:    list[tuple[date, float]] = field(default_factory=list)
    regime_stats:    dict[str, dict] = field(default_factory=dict)
    monthly_pnl:     dict[str, float] = field(default_factory=dict)
    yearly_pnl:      dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            "╔══════════════════════════════════════════════════════════╗",
            "║  INRUSD_v1 BACKTEST RESULTS                             ║",
            f"║  Period : {self.start_date} → {self.end_date:<30} ║",
            f"║  Instrument: USDINR Currency Futures{' ' * 20} ║",
            "╠══════════════════════════════════════════════════════════╣",
            f"║  Trades       : {self.total_trades:<38} ║",
            f"║  Win Rate     : {self.win_rate:.1f}%{' ' * 36} ║",
            f"║  Profit Factor: {self.profit_factor:.2f}{'  (>1.5 = good)':>32} ║",
            "╠══════════════════════════════════════════════════════════╣",
            f"║  Total P&L    : Rs.{self.total_pnl:+,.0f}{' ' * 30} ║",
            f"║  CAGR         : {self.cagr:.1f}%{' ' * 36} ║",
            f"║  Sharpe Ratio : {self.sharpe:.2f}{' ' * 36} ║",
            f"║  Max Drawdown : {self.max_drawdown:.1f}%{' ' * 35} ║",
            f"║  Calmar Ratio : {self.calmar:.2f}{' ' * 36} ║",
            "╠══════════════════════════════════════════════════════════╣",
            f"║  Avg Win      : Rs.{self.avg_win:+,.0f}{' ' * 30} ║",
            f"║  Avg Loss     : Rs.{self.avg_loss:+,.0f}{' ' * 30} ║",
            f"║  Best Trade   : Rs.{self.best_trade:+,.0f}{' ' * 30} ║",
            f"║  Worst Trade  : Rs.{self.worst_trade:+,.0f}{' ' * 30} ║",
            "╚══════════════════════════════════════════════════════════╝",
        ]
        return "\n".join(lines)


class INRUSDBacktest:
    """
    Run a multi-year backtest of INRUSD_v1 using daily OHLCV bars.

    Data downloaded from yfinance:
      USDINR=X    — spot USD/INR (proxy for front-month futures)
      DX-Y.NYB    — US Dollar Index (DXY)
      CL=F        — WTI Crude Oil futures
      EURUSD=X    — EUR/USD spot
      ^TNX        — US 10-year Treasury yield
    """

    def __init__(
        self,
        config: dict,
        start_date: str = "2012-01-01",
        end_date:   str = "2025-12-31",
        verbose:    bool = True,
    ):
        self._cfg        = config
        self._start      = start_date
        self._end        = end_date
        self._verbose    = verbose
        self._initial_cap = config.get("backtest", {}).get("initial_capital", 500000)

        bt_cfg = config.get("backtest", {})
        self._slippage_paise = bt_cfg.get("slippage_paise", 0.5)   # per side
        self._brokerage_lot  = bt_cfg.get("brokerage_per_lot", 20)  # per lot

        inst = config.get("instrument", {})
        self._lot_size  = inst.get("lot_size", 1000)   # 1000 USD

        ps = config.get("position_sizing", {})
        self._budget     = ps.get("budget_per_trade", 15000)
        self._max_lots   = ps.get("max_lots", 5)
        self._margin_lot = ps.get("margin_per_lot", 3000)

        exit_cfg = config.get("exit", {})
        self._tgt_mult = exit_cfg.get("target_atr_mult", 1.5)
        self._sl_mult  = exit_cfg.get("stop_atr_mult",   1.0)

        risk = config.get("risk", {})
        self._daily_stop    = risk.get("daily_loss_limit_rs", 5000)
        self._max_trades    = risk.get("max_trades_per_day", 3)
        self._min_atr_paise = risk.get("min_atr_paise", 3.0)
        self._max_atr_paise = risk.get("max_atr_paise", 40.0)

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> INRUSDBacktestResult:
        if self._verbose:
            print("\n  Downloading USDINR and global data...")

        usdinr  = self._download("USDINR=X",  self._start, self._end)
        dxy     = self._download("DX-Y.NYB",  self._start, self._end)
        crude   = self._download("CL=F",      self._start, self._end)
        eurusd  = self._download("EURUSD=X",  self._start, self._end)
        us10y   = self._download("^TNX",      self._start, self._end)

        if not usdinr:
            print("  ERROR: No USDINR data available.  Check internet / yfinance install.")
            return self._empty_result()

        if self._verbose:
            print(f"  USDINR : {len(usdinr)} bars ({usdinr[0]['date']} – {usdinr[-1]['date']})")
            for name, bars in [("DXY", dxy), ("Crude", crude), ("EURUSD", eurusd), ("US10Y", us10y)]:
                print(f"  {name:<7}: {len(bars)} bars")
            print()

        # Index aux bars by date for O(1) lookup
        dxy_map    = {b["date"]: b for b in dxy}
        crude_map  = {b["date"]: b for b in crude}
        eurusd_map = {b["date"]: b for b in eurusd}
        us10y_map  = {b["date"]: b for b in us10y}

        # ── EMA/RSI/MACD state carried across days ───────────────────────────
        from src.inrusd.inrusd_engine import _EMA, _RSI, _ATR, _MACD
        ema9  = _EMA(9);   ema21 = _EMA(21);  ema50 = _EMA(50)
        rsi14 = _RSI(14)
        atr14 = _ATR(14)
        macd  = _MACD(12, 26, 9)

        # ── Simulation ───────────────────────────────────────────────────────
        capital     = float(self._initial_cap)
        peak_cap    = capital
        max_dd      = 0.0
        trades:     list[INRUSDTrade] = []
        equity_curve: list[tuple[date, float]] = [(usdinr[0]["date"], capital)]
        monthly_pnl: dict[str, float] = {}
        yearly_pnl:  dict[str, float] = {}
        daily_returns: list[float] = []
        warmup_bars = 30

        for i, bar in enumerate(usdinr):
            d   = bar["date"]
            op  = bar["open"]
            hi  = bar["high"]
            lo  = bar["low"]
            cl  = bar["close"]

            # Feed indicators
            ema9.update(cl);  ema21.update(cl);  ema50.update(cl)
            rsi_val  = rsi14.update(cl)
            atr_val  = atr14.update(hi, lo, cl)
            _, _, hist_val = macd.update(cl)

            if i < warmup_bars:
                equity_curve.append((d, capital))
                continue

            # ── Pre-session bias from yesterday's global data ─────────────────
            prev_dxy    = dxy_map.get(d)
            prev_crude  = crude_map.get(d)
            prev_eurusd = eurusd_map.get(d)
            prev_us10y  = us10y_map.get(d)

            bias_score, bias_dir = self._compute_bias(
                prev_dxy, prev_crude, prev_eurusd, prev_us10y
            )

            # Skip neutral days
            if bias_dir == "NEUTRAL":
                equity_curve.append((d, capital))
                continue

            # Skip if ATR is outside acceptable range
            if atr_val is None:
                equity_curve.append((d, capital))
                continue
            atr_paise = atr_val * 100
            if atr_paise < self._min_atr_paise or atr_paise > self._max_atr_paise:
                equity_curve.append((d, capital))
                continue

            # ── Signal scoring ────────────────────────────────────────────────
            ef = ema9.value;  es = ema21.value;  et = ema50.value
            if any(v is None for v in [ef, es, et, rsi_val, hist_val]):
                equity_curve.append((d, capital))
                continue

            score = 0
            if bias_dir == "LONG":
                if ef > es:        score += 1
                if op > et:        score += 1
                if rsi_val > 50:   score += 1
                if hist_val > 0:   score += 1
            else:
                if ef < es:        score += 1
                if op < et:        score += 1
                if rsi_val < 50:   score += 1
                if hist_val < 0:   score += 1

            signal_gate = self._cfg.get("entry", {}).get("signal_score_gate", 3)
            if score < signal_gate:
                equity_curve.append((d, capital))
                continue

            # ── Trade simulation ──────────────────────────────────────────────
            lots    = min(self._max_lots, max(1, int(self._budget / self._margin_lot)))
            entry   = op + (self._slippage_paise / 100) * (1 if bias_dir == "LONG" else -1)
            target  = entry + self._tgt_mult * atr_val * (1 if bias_dir == "LONG" else -1)
            stop    = entry - self._sl_mult  * atr_val * (1 if bias_dir == "LONG" else -1)

            # Simulate fill using High/Low
            if bias_dir == "LONG":
                if lo <= stop:
                    exit_price  = stop
                    exit_reason = "STOP"
                elif hi >= target:
                    exit_price  = target
                    exit_reason = "TARGET"
                else:
                    exit_price  = cl
                    exit_reason = "EOD"
            else:
                if hi >= stop:
                    exit_price  = stop
                    exit_reason = "STOP"
                elif lo <= target:
                    exit_price  = target
                    exit_reason = "TARGET"
                else:
                    exit_price  = cl
                    exit_reason = "EOD"

            # P&L = (exit - entry) * lot_size * lots — direction adjusted — minus costs
            raw_pnl = (exit_price - entry) * self._lot_size * lots
            if bias_dir == "SHORT":
                raw_pnl = -raw_pnl

            # Transaction costs: brokerage (flat per lot) + exchange levy + STT (nil on CDS)
            brokerage  = self._brokerage_lot * lots * 2        # buy + sell
            exch_levy  = 0.0035 * entry * self._lot_size * lots / 100  # 0.00035%
            stamp      = 0.002  * entry * self._lot_size * lots / 100  # 0.002%
            gst        = 0.18 * brokerage
            costs      = brokerage + exch_levy + stamp + gst
            net_pnl    = raw_pnl - costs

            pnl_pct    = net_pnl / capital * 100
            capital   += net_pnl
            peak_cap   = max(peak_cap, capital)
            dd         = (peak_cap - capital) / peak_cap * 100
            max_dd     = max(max_dd, dd)
            daily_returns.append(net_pnl / (capital - net_pnl) if (capital - net_pnl) else 0)

            regime  = _get_regime(d)
            month_k = d.strftime("%Y-%m")
            year_k  = d.strftime("%Y")
            monthly_pnl[month_k] = monthly_pnl.get(month_k, 0.0) + net_pnl
            yearly_pnl[year_k]   = yearly_pnl.get(year_k,  0.0) + net_pnl

            trades.append(INRUSDTrade(
                date=d, direction=bias_dir,
                entry_price=round(entry, 4), exit_price=round(exit_price, 4),
                lots=lots, lot_size=self._lot_size,
                entry_time="09:15", exit_time="15:30",
                exit_reason=exit_reason,
                pnl_inr=round(net_pnl, 2), pnl_pct=round(pnl_pct, 4),
                regime=regime, bias_score=bias_score, score=score,
            ))
            equity_curve.append((d, capital))

        return self._build_result(
            trades, equity_curve, capital, max_dd, daily_returns,
            monthly_pnl, yearly_pnl,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _compute_bias(self, dxy, crude, eurusd, us10y) -> tuple[int, str]:
        """Reproduce CurrencyBiasEngine logic without needing a live snapshot."""
        score = 0

        def pct_change(b) -> Optional[float]:
            if b is None:
                return None
            o = b.get("open", 0); c = b.get("close", 0)
            return (c - o) / o * 100 if o else None

        # DXY (35)
        dxy_chg = pct_change(dxy)
        if dxy_chg is not None:
            if dxy_chg >= 0.5:  score += 35
            elif dxy_chg >= 0.15: score += 18
            elif dxy_chg <= -0.5: score -= 35
            elif dxy_chg <= -0.15: score -= 18

        # Crude (20) — same direction
        crude_chg = pct_change(crude)
        if crude_chg is not None:
            if crude_chg >= 1.5:  score += 20
            elif crude_chg >= 0.5: score += 10
            elif crude_chg <= -1.5: score -= 20
            elif crude_chg <= -0.5: score -= 10

        # US 10Y (20) — using daily close change in bps
        if us10y:
            oc = us10y.get("open", 0); cc = us10y.get("close", 0)
            bps = (cc - oc) * 100  # yield in % → bps
            if bps >= 5:   score += 20
            elif bps >= 1.5: score += 10
            elif bps <= -5: score -= 20
            elif bps <= -1.5: score -= 10

        # EUR/USD (15) — inverse
        eur_chg = pct_change(eurusd)
        if eur_chg is not None:
            if eur_chg >= 0.5:   score -= 15
            elif eur_chg >= 0.15: score -= 8
            elif eur_chg <= -0.5: score += 15
            elif eur_chg <= -0.15: score += 8

        score = max(-100, min(100, score))
        if score >= 20:
            return score, "LONG"
        elif score <= -20:
            return score, "SHORT"
        return score, "NEUTRAL"

    def _build_result(
        self, trades, equity_curve, final_cap, max_dd, daily_returns,
        monthly_pnl, yearly_pnl,
    ) -> INRUSDBacktestResult:
        if not trades:
            return self._empty_result()

        wins   = [t for t in trades if t.pnl_inr > 0]
        losses = [t for t in trades if t.pnl_inr <= 0]
        total_pnl  = sum(t.pnl_inr for t in trades)
        win_rate   = len(wins) / len(trades) * 100 if trades else 0
        avg_win    = sum(t.pnl_inr for t in wins)   / len(wins)   if wins   else 0
        avg_loss   = sum(t.pnl_inr for t in losses) / len(losses) if losses else 0

        gross_wins  = sum(t.pnl_inr for t in wins)
        gross_loss  = abs(sum(t.pnl_inr for t in losses))
        profit_factor = gross_wins / gross_loss if gross_loss else float("inf")

        years = (
            date.fromisoformat(self._end) - date.fromisoformat(self._start)
        ).days / 365.25
        cagr  = ((final_cap / self._initial_cap) ** (1 / years) - 1) * 100 if years > 0 else 0

        sharpe = 0.0
        if daily_returns:
            import statistics
            mean_r = statistics.mean(daily_returns)
            std_r  = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 1e-9
            rf_daily = 0.065 / 252
            sharpe   = (mean_r - rf_daily) / std_r * (252 ** 0.5) if std_r else 0.0

        calmar = abs(cagr / max_dd) if max_dd else 0.0

        # Regime stats
        regime_stats: dict[str, dict] = {}
        for t in trades:
            r = t.regime
            if r not in regime_stats:
                regime_stats[r] = {"trades": 0, "wins": 0, "pnl": 0.0}
            regime_stats[r]["trades"] += 1
            regime_stats[r]["pnl"]    += t.pnl_inr
            if t.pnl_inr > 0:
                regime_stats[r]["wins"] += 1
        for r, s in regime_stats.items():
            s["win_rate"] = s["wins"] / s["trades"] * 100 if s["trades"] else 0

        return INRUSDBacktestResult(
            start_date=self._start,
            end_date=self._end,
            total_trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            win_rate=round(win_rate, 1),
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_dd, 2),
            cagr=round(cagr, 2),
            sharpe=round(sharpe, 2),
            calmar=round(calmar, 2),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            best_trade=round(max((t.pnl_inr for t in trades), default=0), 2),
            worst_trade=round(min((t.pnl_inr for t in trades), default=0), 2),
            trades=trades,
            equity_curve=equity_curve,
            regime_stats=regime_stats,
            monthly_pnl=monthly_pnl,
            yearly_pnl=yearly_pnl,
        )

    def _empty_result(self) -> INRUSDBacktestResult:
        return INRUSDBacktestResult(
            start_date=self._start, end_date=self._end,
            total_trades=0, wins=0, losses=0, win_rate=0, total_pnl=0,
            max_drawdown=0, cagr=0, sharpe=0, calmar=0, profit_factor=0,
            avg_win=0, avg_loss=0, best_trade=0, worst_trade=0,
        )

    def _download(self, symbol: str, start: str, end: str) -> list[dict]:
        try:
            import yfinance as yf
            t    = yf.Ticker(symbol)
            hist = t.history(start=start, end=end, interval="1d", auto_adjust=True)
            if hist.empty:
                return []
            bars = []
            for ts, row in hist.iterrows():
                dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                d  = dt.date() if hasattr(dt, "date") else dt
                bars.append({
                    "date":   d,
                    "open":   float(row.get("Open",  0) or 0),
                    "high":   float(row.get("High",  0) or 0),
                    "low":    float(row.get("Low",   0) or 0),
                    "close":  float(row.get("Close", 0) or 0),
                    "volume": float(row.get("Volume", 0) or 0),
                })
            return bars
        except Exception as e:
            if self._verbose:
                print(f"  [download] {symbol}: {e}")
            return []

    # ── Export ────────────────────────────────────────────────────────────────

    def export_csv(self, result: INRUSDBacktestResult, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "date", "direction", "entry_price", "exit_price", "lots",
                "exit_reason", "pnl_inr", "pnl_pct", "regime", "bias_score", "score",
            ])
            for t in result.trades:
                writer.writerow([
                    t.date, t.direction, t.entry_price, t.exit_price, t.lots,
                    t.exit_reason, t.pnl_inr, t.pnl_pct, t.regime, t.bias_score, t.score,
                ])
        print(f"  Trades CSV: {path}")

    def export_equity_csv(self, result: INRUSDBacktestResult, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "capital"])
            for d, cap in result.equity_curve:
                writer.writerow([d, round(cap, 2)])
        print(f"  Equity CSV: {path}")

    def plot_equity_curve(self, result: INRUSDBacktestResult, path: str) -> None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates

            dates = [e[0] for e in result.equity_curve]
            caps  = [e[1] for e in result.equity_curve]

            fig, ax = plt.subplots(figsize=(14, 6))
            ax.plot(dates, caps, color="#2E86AB", linewidth=1.5)
            ax.fill_between(dates, caps, alpha=0.12, color="#2E86AB")
            ax.axhline(self._initial_cap, color="#E84855", linewidth=1, linestyle="--", label="Starting Capital")
            ax.set_title("INRUSD_v1 Equity Curve — 14-Year Backtest (2012–2025)", fontsize=14)
            ax.set_ylabel("Capital (Rs.)")
            ax.set_xlabel("Date")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.legend()
            ax.grid(True, alpha=0.3)

            total_rs = result.total_pnl
            sign = "+" if total_rs >= 0 else ""
            ax.annotate(
                f"Total P&L: Rs.{sign}{total_rs:,.0f}\n"
                f"CAGR: {result.cagr:.1f}%   Sharpe: {result.sharpe:.2f}\n"
                f"Max DD: {result.max_drawdown:.1f}%   Win Rate: {result.win_rate:.1f}%",
                xy=(0.02, 0.96), xycoords="axes fraction",
                fontsize=9, va="top",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="lightyellow", alpha=0.8),
            )
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"  Equity plot: {path}")
        except Exception as e:
            print(f"  (plot skipped: {e})")

    def print_regime_table(self, result: INRUSDBacktestResult) -> None:
        print("\n  ── Regime Performance ──────────────────────────────────────────────")
        print(f"  {'Regime':<26} {'Trades':>6} {'WinRate':>8} {'P&L':>12}")
        print(f"  {'──────':<26} {'──────':>6} {'───────':>8} {'───':>12}")
        for regime, stats in sorted(result.regime_stats.items()):
            pnl = stats["pnl"]
            sign = "+" if pnl >= 0 else ""
            print(
                f"  {regime:<26} {stats['trades']:>6} "
                f"{stats['win_rate']:>7.1f}% "
                f"  Rs.{sign}{pnl:>10,.0f}"
            )
        print()

    def print_yearly_table(self, result: INRUSDBacktestResult) -> None:
        print("  ── Yearly P&L ──────────────────────────────────────────────────────")
        for year in sorted(result.yearly_pnl):
            pnl  = result.yearly_pnl[year]
            sign = "+" if pnl >= 0 else ""
            bar  = "█" * int(abs(pnl) / 2000) if pnl else ""
            col  = "" if pnl >= 0 else ""
            print(f"  {year}  Rs.{sign}{pnl:>10,.0f}  {bar}")
        print()
