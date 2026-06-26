"""
BRAHMASTRA_v1 — Backtest Runner
================================
Run this on your Windows machine from the project root:

    python run_backtest.py

What it does:
  1. Downloads real NIFTY daily data from Yahoo Finance (2008–2024)
  2. Runs Phase 5 structural backtest (daily bars, all 9 market regimes)
  3. Runs Phase 6b indicator-driven backtest (5m synthetic bars, 2018–2024)
  4. Saves a text report to reports/backtest_<date>.txt
  5. Saves a trade-level CSV to reports/backtest_trades_<date>.csv
  6. Saves an equity curve PNG to reports/equity_curve_<date>.png

NOTE: Phase 6b uses synthetic 5-minute bars because Yahoo Finance does not
provide historical intraday data beyond 60 days for Indian indices. The
daily structural test (Phase 5) uses REAL downloaded data.
"""
import os
import sys
from datetime import datetime

# Make sure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.brahmastra.backtest.backtest_engine import (
    BrahmastraBacktest,
    IndicatorDrivenBacktest,
)

os.makedirs("reports", exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M")
report_path   = f"reports/backtest_{stamp}.txt"
csv_path      = f"reports/backtest_trades_{stamp}.csv"
png_path      = f"reports/equity_curve_{stamp}.png"

LOG = []

def log(line=""):
    print(line)
    LOG.append(line)


log("=" * 65)
log("  BRAHMASTRA_v1  FULL BACKTEST SUITE")
log(f"  Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log("=" * 65)

# ── PHASE 5: Daily structural backtest (real yfinance data) ───────────────────
log()
log("  PHASE 5 — Daily Structural Backtest (REAL DATA via yfinance)")
log("  Attempting to download NIFTY daily bars from Yahoo Finance...")
log("  (Fallback: calibrated synthetic data if download fails)")
log()

bt = BrahmastraBacktest(
    instrument       = "NIFTY",
    start_year       = 2008,
    end_year         = 2024,
    starting_capital = 10_000,
    lot_size         = 75,
)
u_result, f_result = bt.compare(verbose=True)

LOG.append("")
LOG.append("  [NOTE: Lines above show whether REAL or SYNTHETIC data was used]")

# Save CSV of filtered-run trades
bt.export_csv(f_result, csv_path)
log(f"  Trade CSV saved: {csv_path}")

# Save equity curve PNG
bt.plot_equity_curve(f_result, png_path)

# ── PHASE 6b: Indicator-driven 5m backtest ────────────────────────────────────
log()
log("=" * 65)
log("  PHASE 6b — Indicator-Driven 5m Backtest (SYNTHETIC 5m bars)")
log("  NOTE: Real 5m history unavailable via free API beyond 60 days.")
log("        This test validates the confluence logic on plausible 5m data.")
log("=" * 65)

ibt = IndicatorDrivenBacktest(
    instrument            = "NIFTY",
    start_year            = 2018,
    end_year              = 2024,
    starting_capital      = 10_000,
    lot_size              = 75,
    confluence_threshold  = 70.0,
    atr_sl_mult           = 2.0,
    rr_target             = 2.0,
)
i_result = ibt.run(verbose=True)

# Rising-score filtered variant
log()
log("  PHASE 6b-FILTERED — Rising confluence score gate enabled")
ibt_f = IndicatorDrivenBacktest(
    instrument            = "NIFTY",
    start_year            = 2018,
    end_year              = 2024,
    starting_capital      = 10_000,
    lot_size              = 75,
    confluence_threshold  = 70.0,
    atr_sl_mult           = 2.0,
    rr_target             = 2.0,
    require_rising_score  = True,
    score_lookback        = 3,
)
i_result_f = ibt_f.run(verbose=True)

# ── Summary ───────────────────────────────────────────────────────────────────
log()
log("=" * 65)
log("  FINAL SUMMARY")
log("=" * 65)
log()
log("  Phase 5 — Daily Structural Test (UNFILTERED):")
log(f"    Trades: {u_result.total_trades}  |  Win Rate: {u_result.win_rate:.1f}%  |  "
    f"CAGR: {u_result.cagr:.1f}%  |  Sharpe: {u_result.sharpe:.2f}  |  "
    f"Max DD: {u_result.max_drawdown:.1f}%")
log()
log("  Phase 5 — Daily Structural Test (FILTERED — 85% gate + VIX + trend):")
log(f"    Trades: {f_result.total_trades}  |  Win Rate: {f_result.win_rate:.1f}%  |  "
    f"CAGR: {f_result.cagr:.1f}%  |  Sharpe: {f_result.sharpe:.2f}  |  "
    f"Max DD: {f_result.max_drawdown:.1f}%")
log()
log("  Phase 6b — Indicator-Driven 5m Test (no rising-score gate):")
log(f"    Trades: {i_result.total_trades}  |  Win Rate: {i_result.win_rate:.1f}%  |  "
    f"CAGR: {i_result.cagr:.1f}%  |  Sharpe: {i_result.sharpe:.2f}  |  "
    f"Max DD: {i_result.max_drawdown:.1f}%")
log()
log("  Phase 6b — Indicator-Driven 5m Test (rising-score gate ON):")
log(f"    Trades: {i_result_f.total_trades}  |  Win Rate: {i_result_f.win_rate:.1f}%  |  "
    f"CAGR: {i_result_f.cagr:.1f}%  |  Sharpe: {i_result_f.sharpe:.2f}  |  "
    f"Max DD: {i_result_f.max_drawdown:.1f}%")
log()

VERDICT = []
if f_result.win_rate >= 55 and f_result.profit_factor >= 1.5:
    VERDICT.append("  Phase 5 PASS: Win rate ≥55% and Profit Factor ≥1.5")
else:
    VERDICT.append("  Phase 5 FAIL: Strategy needs tuning before going live")

if i_result.win_rate >= 55 and i_result.sharpe >= 1.0:
    VERDICT.append("  Phase 6b PASS: Win rate ≥55% and Sharpe ≥1.0")
else:
    VERDICT.append("  Phase 6b FAIL: Confluence threshold or SL mult needs adjustment")

log("  VERDICT:")
for v in VERDICT:
    log(v)

log()
log(f"  Full report saved: {report_path}")
log(f"  Trade CSV saved:   {csv_path}")
log(f"  Equity curve:      {png_path}")
log()

# Write text report
with open(report_path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(LOG))

print(f"\n  Done. Open {report_path} to review the full results.")
