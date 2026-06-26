#!/usr/bin/env python3
"""
INRUSD_v1 Backtest Runner
━━━━━━━━━━━━━━━━━━━━━━━━━
Runs a 14-year backtest (2012–2025) of the INRUSD currency futures strategy.

Usage:
  python run_inrusd_backtest.py
  python run_inrusd_backtest.py --start 2018-01-01 --end 2025-12-31
  python run_inrusd_backtest.py --start 2020-01-01 --verbose

Results are saved to:  strategies/INRUSD_v1/results/
"""

import argparse
import os
import sys
import yaml


def main():
    parser = argparse.ArgumentParser(
        description="INRUSD_v1 — Currency Futures Trend Scalper Backtest"
    )
    parser.add_argument("--start",   default="2012-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end",     default="2025-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--verbose", action="store_true",  help="Verbose output")
    args = parser.parse_args()

    config_path = "strategies/INRUSD_v1/config.yaml"
    if not os.path.exists(config_path):
        print(f"ERROR: config not found at {config_path}")
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    out_dir = "strategies/INRUSD_v1/results"
    os.makedirs(out_dir, exist_ok=True)

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  INRUSD_v1 — Currency Futures Trend Scalper                 ║")
    print(f"║  Period: {args.start} → {args.end}                    ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("  Strategy:   EMA9/21 crossover + RSI + MACD + pre-session bias")
    print("  Instrument: USDINR monthly futures (NSE Currency Derivatives)")
    print("  Lot size:   1,000 USD per lot")
    print("  Capital:    Rs.5,00,000")
    print()

    from src.inrusd.inrusd_backtest import INRUSDBacktest
    bt = INRUSDBacktest(
        config=config,
        start_date=args.start,
        end_date=args.end,
        verbose=args.verbose or True,
    )
    result = bt.run()

    if result.total_trades == 0:
        print("\n  No trades generated. Check data availability and signal conditions.")
        sys.exit(0)

    print(result.summary())
    bt.print_regime_table(result)
    bt.print_yearly_table(result)

    bt.export_csv(result, f"{out_dir}/backtest_trades.csv")
    bt.export_equity_csv(result, f"{out_dir}/equity_curve.csv")
    bt.plot_equity_curve(result, f"{out_dir}/equity_curve.png")

    print(f"\n  All results saved to {out_dir}/")
    print()


if __name__ == "__main__":
    main()
