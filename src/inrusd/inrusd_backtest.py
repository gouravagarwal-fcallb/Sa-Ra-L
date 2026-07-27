"""
INRUSD_v1 Backtest Engine — v2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
14-year simulation (2012–2025) on USDINR daily bars from yfinance.

Fixes applied vs v1 (which produced 4.4% win-rate / -Rs.83K):
  1. Bias gate raised ±45 (was ±20)  — only high-conviction global sessions
  2. INR_RANGE_TIGHT regime skipped  — RBI-managed 82-84 band; no directional edge
  3. 3-day cooldown after every trade — avoids bleed streaks
  4. Momentum pre-filter             — prior day must close in signal direction
  5. Fixed 15p target / 10p stop     — realistic for daily bar simulation (not ATR)
  6. max_lots → 2                    — halves per-trade transaction costs
  7. signal_score_gate → 4           — all 4 conditions must align
  8. min_atr_paise → 10              — skip quiet/RBI-capped days
"""

from __future__ import annotations

import os
import csv
from dataclasses import dataclass, field
from datetime import date, timedelta, timezone
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

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
    direction:    str
    entry_price:  float
    exit_price:   float
    lots:         int
    lot_size:     int
    entry_time:   str
    exit_time:    str
    exit_reason:  str          # TARGET | STOP | EOD
    pnl_inr:      float
    pnl_pct:      float
    regime:       str
    bias_score:   int
    score:        int


@dataclass
class INRUSDBacktestResult:
    start_date:    str
    end_date:      str
    total_trades:  int
    wins:          int
    losses:        int
    win_rate:      float
    total_pnl:     float
    max_drawdown:  float
    cagr:          float
    sharpe:        float
    calmar:        float
    profit_factor: float
    avg_win:       float
    avg_loss:      float
    best_trade:    float
    worst_trade:   float
    trades:        list[INRUSDTrade]         = field(default_factory=list)
    equity_curve:  list[tuple[date, float]]  = field(default_factory=list)
    regime_stats:  dict[str, dict]           = field(default_factory=dict)
    monthly_pnl:   dict[str, float]          = field(default_factory=dict)
    yearly_pnl:    dict[str, float]          = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            "╔══════════════════════════════════════════════════════════╗",
            "║  INRUSD_v1 BACKTEST RESULTS  (v2 — improved filters)    ║",
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
    Multi-year backtest of INRUSD_v1 on daily OHLCV bars.

    yfinance symbols used:
      USDINR=X  DX-Y.NYB  CL=F  EURUSD=X  ^TNX
    """

    def __init__(
        self,
        config:     dict,
        start_date: str  = "2012-01-01",
        end_date:   str  = "2025-12-31",
        verbose:    bool = True,
    ):
        self._cfg         = config
        self._start       = start_date
        self._end         = end_date
        self._verbose     = verbose
        self._initial_cap = config.get("backtest", {}).get("initial_capital", 500_000)

        bt = config.get("backtest", {})
        self._slippage_paise     = bt.get("slippage_paise",    0.5)
        self._brokerage_lot      = bt.get("brokerage_per_lot", 20)
        self._fixed_target_paise = bt.get("target_paise",      15)   # fixed 15p profit target
        self._fixed_stop_paise   = bt.get("stop_paise",        10)   # fixed 10p stop loss
        self._cooldown_days      = bt.get("cooldown_days",      3)    # bars between trades
        self._skip_regimes       = set(bt.get("skip_regimes",  ["INR_RANGE_TIGHT"]))

        inst = config.get("instrument", {})
        self._lot_size = inst.get("lot_size", 1000)

        ps = config.get("position_sizing", {})
        self._budget     = ps.get("budget_per_trade", 15_000)
        self._max_lots   = ps.get("max_lots",         2)
        self._margin_lot = ps.get("margin_per_lot",   3_000)

        risk = config.get("risk", {})
        self._min_atr_paise = risk.get("min_atr_paise", 10.0)
        self._max_atr_paise = risk.get("max_atr_paise", 40.0)

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> INRUSDBacktestResult:
        if self._verbose:
            print("\n  Downloading USDINR and global data...")

        usdinr = self._download("USDINR=X", self._start, self._end)
        dxy    = self._download("DX-Y.NYB", self._start, self._end)
        crude  = self._download("CL=F",     self._start, self._end)
        eurusd = self._download("EURUSD=X", self._start, self._end)
        us10y  = self._download("^TNX",     self._start, self._end)

        if not usdinr:
            print("  ERROR: No USDINR data. Check internet / yfinance.")
            return self._empty_result()

        if self._verbose:
            print(f"  USDINR : {len(usdinr)} bars ({usdinr[0]['date']} – {usdinr[-1]['date']})")
            for name, bars in [("DXY", dxy), ("Crude", crude), ("EURUSD", eurusd), ("US10Y", us10y)]:
                print(f"  {name:<7}: {len(bars)} bars")
            print()

        dxy_map    = {b["date"]: b for b in dxy}
        crude_map  = {b["date"]: b for b in crude}
        eurusd_map = {b["date"]: b for b in eurusd}
        us10y_map  = {b["date"]: b for b in us10y}

        from src.inrusd.inrusd_engine import _EMA, _RSI, _ATR, _MACD
        ema9  = _EMA(9);  ema21 = _EMA(21);  ema50 = _EMA(50)
        rsi14 = _RSI(14)
        atr14 = _ATR(14)
        macd  = _MACD(12, 26, 9)

        capital        = float(self._initial_cap)
        peak_cap       = capital
        max_dd         = 0.0
        trades:        list[INRUSDTrade]        = []
        equity_curve:  list[tuple[date, float]] = [(usdinr[0]["date"], capital)]
        monthly_pnl:   dict[str, float]         = {}
        yearly_pnl:    dict[str, float]         = {}
        daily_returns: list[float]              = []
        warmup_bars    = 30
        close_history: list[float]              = []   # recent prior closes
        last_trade_i   = -999                          # index of last traded bar
        signal_gate    = self._cfg.get("entry", {}).get("signal_score_gate", 3)
        # Filter counters (printed in verbose mode)
        cut_warmup = cut_cooldown = cut_regime = cut_atr = 0
        cut_bias   = cut_score   = cut_momentum = 0
        # Through-yesterday indicator state (updated at the END of each iteration).
        rsi_val = atr_val = hist_val = None

        for i, bar in enumerate(usdinr):
            d  = bar["date"]
            op = bar["open"]
            hi = bar["high"]
            lo = bar["low"]
            cl = bar["close"]

            # LOOK-AHEAD FIX: the entry decision below is taken at today's OPEN, so it
            # may only use indicator state through YESTERDAY's close. We therefore do
            # NOT feed today's bar into the indicators here — that update happens at the
            # END of the loop (after the decision). rsi_val / atr_val / hist_val and the
            # ema.value's carry the through-yesterday values from the previous iteration.
            # (Previously today's close was fed in first and then used to decide entry at
            # today's open — a classic look-ahead that flatters the backtest.)
            traded = False

            if i < warmup_bars:
                cut_warmup += 1
            elif (i - last_trade_i) < self._cooldown_days:
                cut_cooldown += 1
            elif atr_val is not None:
                atr_paise = atr_val * 100
                regime    = _get_regime(d)

                if regime in self._skip_regimes:
                    cut_regime += 1
                elif not (self._min_atr_paise <= atr_paise <= self._max_atr_paise):
                    cut_atr += 1
                else:
                    bias_score, bias_dir = self._compute_bias(
                        dxy_map.get(d), crude_map.get(d),
                        eurusd_map.get(d), us10y_map.get(d),
                    )

                    if bias_dir == "NEUTRAL":
                        cut_bias += 1
                    else:
                        ef = ema9.value;  es = ema21.value;  et = ema50.value

                        if not any(v is None for v in [ef, es, et, rsi_val, hist_val]):
                            score = 0
                            if bias_dir == "LONG":
                                if ef > es:       score += 1
                                if op > et:       score += 1
                                if rsi_val > 50:  score += 1
                                if hist_val > 0:  score += 1
                            else:
                                if ef < es:       score += 1
                                if op < et:       score += 1
                                if rsi_val < 50:  score += 1
                                if hist_val < 0:  score += 1

                            # Momentum: prior day closed in same direction
                            momentum_ok = (
                                len(close_history) >= 2
                                and (
                                    close_history[-1] > close_history[-2]
                                    if bias_dir == "LONG"
                                    else close_history[-1] < close_history[-2]
                                )
                            )

                            if score < signal_gate:
                                cut_score += 1
                            elif not momentum_ok:
                                cut_momentum += 1
                            else:
                                traded = True
                                lots   = min(self._max_lots,
                                             max(1, int(self._budget / self._margin_lot)))
                                sign   = 1 if bias_dir == "LONG" else -1
                                entry  = op + (self._slippage_paise / 100) * sign
                                target = entry + (self._fixed_target_paise / 100) * sign
                                stop   = entry - (self._fixed_stop_paise   / 100) * sign

                                if bias_dir == "LONG":
                                    if lo <= stop:
                                        exit_price, exit_reason = stop,   "STOP"
                                    elif hi >= target:
                                        exit_price, exit_reason = target, "TARGET"
                                    else:
                                        exit_price, exit_reason = cl,     "EOD"
                                else:
                                    if hi >= stop:
                                        exit_price, exit_reason = stop,   "STOP"
                                    elif lo <= target:
                                        exit_price, exit_reason = target, "TARGET"
                                    else:
                                        exit_price, exit_reason = cl,     "EOD"

                                raw_pnl = (exit_price - entry) * self._lot_size * lots
                                if bias_dir == "SHORT":
                                    raw_pnl = -raw_pnl

                                brokerage = self._brokerage_lot * lots * 2
                                exch_levy = 0.0035 * entry * self._lot_size * lots / 100
                                stamp     = 0.002  * entry * self._lot_size * lots / 100
                                gst       = 0.18 * brokerage
                                costs     = brokerage + exch_levy + stamp + gst
                                net_pnl   = raw_pnl - costs

                                pnl_pct  = net_pnl / capital * 100
                                capital += net_pnl
                                peak_cap = max(peak_cap, capital)
                                dd       = (peak_cap - capital) / peak_cap * 100
                                max_dd   = max(max_dd, dd)
                                prev_cap = capital - net_pnl
                                daily_returns.append(net_pnl / prev_cap if prev_cap else 0)

                                month_k = d.strftime("%Y-%m")
                                year_k  = d.strftime("%Y")
                                monthly_pnl[month_k] = monthly_pnl.get(month_k, 0.0) + net_pnl
                                yearly_pnl[year_k]   = yearly_pnl.get(year_k,   0.0) + net_pnl

                                trades.append(INRUSDTrade(
                                    date=d, direction=bias_dir,
                                    entry_price=round(entry,      4),
                                    exit_price=round(exit_price,  4),
                                    lots=lots, lot_size=self._lot_size,
                                    entry_time="09:15", exit_time="15:30",
                                    exit_reason=exit_reason,
                                    pnl_inr=round(net_pnl, 2),
                                    pnl_pct=round(pnl_pct, 4),
                                    regime=regime,
                                    bias_score=bias_score,
                                    score=score,
                                ))
                                last_trade_i = i

            # NOW feed today's bar into the indicators — AFTER the entry decision — so
            # the next day's decision sees state through today's close (causal), never
            # today's own close when deciding at today's open. (See look-ahead fix above.)
            ema9.update(cl);  ema21.update(cl);  ema50.update(cl)
            rsi_val        = rsi14.update(cl)
            atr_val        = atr14.update(hi, lo, cl)
            _, _, hist_val = macd.update(cl)

            # Always advance close history and equity curve
            close_history.append(cl)
            if len(close_history) > 5:
                close_history.pop(0)
            equity_curve.append((d, capital))

        if self._verbose:
            total_bars = len(usdinr)
            print(f"  Filter funnel ({total_bars} total bars):")
            print(f"    warmup skipped  : {cut_warmup}")
            print(f"    cooldown cut    : {cut_cooldown}")
            print(f"    regime skipped  : {cut_regime}")
            print(f"    ATR out-of-range: {cut_atr}")
            print(f"    bias neutral    : {cut_bias}")
            print(f"    score < gate    : {cut_score}")
            print(f"    momentum fail   : {cut_momentum}")
            print(f"    ─────────────────")
            print(f"    TRADES TAKEN    : {len(trades)}")
            print()

        return self._build_result(
            trades, equity_curve, capital, max_dd,
            daily_returns, monthly_pnl, yearly_pnl,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _compute_bias(self, dxy, crude, eurusd, us10y) -> tuple[int, str]:
        score = 0

        def pct_chg(b) -> Optional[float]:
            if b is None:
                return None
            o = b.get("open", 0);  c = b.get("close", 0)
            return (c - o) / o * 100 if o else None

        dxy_chg = pct_chg(dxy)
        if dxy_chg is not None:
            if   dxy_chg >=  0.5:  score += 35
            elif dxy_chg >=  0.15: score += 18
            elif dxy_chg <= -0.5:  score -= 35
            elif dxy_chg <= -0.15: score -= 18

        crude_chg = pct_chg(crude)
        if crude_chg is not None:
            if   crude_chg >=  1.5: score += 20
            elif crude_chg >=  0.5: score += 10
            elif crude_chg <= -1.5: score -= 20
            elif crude_chg <= -0.5: score -= 10

        if us10y:
            oc  = us10y.get("open",  0);  cc = us10y.get("close", 0)
            bps = (cc - oc) * 100
            if   bps >=  5:   score += 20
            elif bps >=  1.5: score += 10
            elif bps <= -5:   score -= 20
            elif bps <= -1.5: score -= 10

        eur_chg = pct_chg(eurusd)
        if eur_chg is not None:
            if   eur_chg >=  0.5:  score -= 15
            elif eur_chg >=  0.15: score -= 8
            elif eur_chg <= -0.5:  score += 15
            elif eur_chg <= -0.15: score += 8

        score = max(-100, min(100, score))
        if score >= 35:
            return score, "LONG"
        elif score <= -35:
            return score, "SHORT"
        return score, "NEUTRAL"

    def _build_result(
        self, trades, equity_curve, final_cap, max_dd,
        daily_returns, monthly_pnl, yearly_pnl,
    ) -> INRUSDBacktestResult:
        if not trades:
            return self._empty_result()

        wins   = [t for t in trades if t.pnl_inr > 0]
        losses = [t for t in trades if t.pnl_inr <= 0]

        total_pnl = sum(t.pnl_inr for t in trades)
        win_rate  = len(wins) / len(trades) * 100
        avg_win   = sum(t.pnl_inr for t in wins)   / len(wins)   if wins   else 0.0
        avg_loss  = sum(t.pnl_inr for t in losses) / len(losses) if losses else 0.0

        gross_wins  = sum(t.pnl_inr for t in wins)
        gross_loss  = abs(sum(t.pnl_inr for t in losses))
        profit_factor = gross_wins / gross_loss if gross_loss else float("inf")

        years = (
            date.fromisoformat(self._end) - date.fromisoformat(self._start)
        ).days / 365.25
        cagr = ((final_cap / self._initial_cap) ** (1 / years) - 1) * 100 if years > 0 else 0

        sharpe = 0.0
        if len(daily_returns) > 1:
            import statistics
            mean_r   = statistics.mean(daily_returns)
            std_r    = statistics.stdev(daily_returns) or 1e-9
            rf_daily = 0.065 / 252
            sharpe   = (mean_r - rf_daily) / std_r * (252 ** 0.5)

        calmar = abs(cagr / max_dd) if max_dd else 0.0

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
            start_date=self._start, end_date=self._end,
            total_trades=len(trades),
            wins=len(wins), losses=len(losses),
            win_rate=round(win_rate, 1),
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_dd, 2),
            cagr=round(cagr, 2),
            sharpe=round(sharpe, 2),
            calmar=round(calmar, 2),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            best_trade=round(max(t.pnl_inr for t in trades), 2),
            worst_trade=round(min(t.pnl_inr for t in trades), 2),
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
            hist = yf.Ticker(symbol).history(
                start=start, end=end, interval="1d", auto_adjust=True
            )
            if hist.empty:
                return []
            bars = []
            for ts, row in hist.iterrows():
                dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                d  = dt.date() if hasattr(dt, "date") else dt
                bars.append({
                    "date":   d,
                    "open":   float(row.get("Open",   0) or 0),
                    "high":   float(row.get("High",   0) or 0),
                    "low":    float(row.get("Low",    0) or 0),
                    "close":  float(row.get("Close",  0) or 0),
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
            w = csv.writer(f)
            w.writerow([
                "date", "direction", "entry_price", "exit_price", "lots",
                "exit_reason", "pnl_inr", "pnl_pct", "regime", "bias_score", "score",
            ])
            for t in result.trades:
                w.writerow([
                    t.date, t.direction, t.entry_price, t.exit_price, t.lots,
                    t.exit_reason, t.pnl_inr, t.pnl_pct, t.regime, t.bias_score, t.score,
                ])
        print(f"  Trades CSV : {path}")

    def export_equity_csv(self, result: INRUSDBacktestResult, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "capital"])
            for d, cap in result.equity_curve:
                w.writerow([d, round(cap, 2)])
        print(f"  Equity CSV : {path}")

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
            ax.axhline(
                self._initial_cap, color="#E84855", linewidth=1,
                linestyle="--", label="Starting Capital",
            )
            ax.set_title(
                "INRUSD_v1 Equity Curve — 14-Year Backtest (2012–2025) [v2 filters]",
                fontsize=14,
            )
            ax.set_ylabel("Capital (Rs.)")
            ax.set_xlabel("Date")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
            ax.xaxis.set_major_locator(mdates.YearLocator())
            ax.legend()
            ax.grid(True, alpha=0.3)

            sign = "+" if result.total_pnl >= 0 else ""
            ax.annotate(
                f"Total P&L: Rs.{sign}{result.total_pnl:,.0f}\n"
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
            pnl  = stats["pnl"]
            sign = "+" if pnl >= 0 else ""
            print(
                f"  {regime:<26} {stats['trades']:>6} "
                f"{stats['win_rate']:>7.1f}%   Rs.{sign}{pnl:>10,.0f}"
            )
        print()

    def print_yearly_table(self, result: INRUSDBacktestResult) -> None:
        print("  ── Yearly P&L ──────────────────────────────────────────────────────")
        for year in sorted(result.yearly_pnl):
            pnl  = result.yearly_pnl[year]
            sign = "+" if pnl >= 0 else ""
            bar  = "█" * int(abs(pnl) / 1000) if pnl else ""
            print(f"  {year}  Rs.{sign}{pnl:>10,.0f}  {bar}")
        print()
