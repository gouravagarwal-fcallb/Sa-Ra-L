#!/usr/bin/env python3
"""
Sa-Ra-L  |  Nifty & Sensex Weekly Options Strategy
════════════════════════════════════════════════════

Usage:
  python main.py --mode backtest          # Run historical backtest (5-min, 3 years)
  python main.py --mode backtest1m        # 1-min strategy backtest (last 7 days)
  python main.py --mode paper             # Live paper trading (no real orders)
  python main.py --mode live              # Live trading via Kite Connect
  python main.py --mode premarket         # Pre-market direction report only
  python main.py --mode login             # Generate daily Kite access token
  python main.py --mode test              # Run unit tests

Modes:
  backtest   — Replay 5-min intraday strategy on historical data (2023 → present).
  backtest1m — Replay 1-min three-layer confluence strategy. Uses yfinance 1-min
               data which covers only the last ~7 calendar days (~5 trading days).
  wfv        — Walk-Forward Validation: splits the full history into 8 equal
               folds and tests the strategy independently on each. Reports
               consistency across market regimes (fold win rate, Sharpe, drawdown).
  paper      — 1-min tick loop with Black-Scholes pricing. No Kite credentials needed.
  live       — Same loop but places real orders via Kite Connect.
               Requires KITE_API_KEY and KITE_ACCESS_TOKEN env vars.
  login      — Guides you through Kite OAuth to generate today's access token.
  premarket  — Quick morning briefing: direction score + today's bias.
"""

import argparse
import sys
import yaml
import os

CONFIG_PATH   = "config/settings.yaml"
STRATEGY_PATH = "config/strategy_config.yaml"


def load_configs(strategy_name: str = None):
    with open(CONFIG_PATH) as f:
        settings = yaml.safe_load(f)

    if strategy_name:
        strat_path = f"strategies/{strategy_name}/config.yaml"
        if not os.path.exists(strat_path):
            print(f"ERROR: Strategy '{strategy_name}' not found at {strat_path}")
            print("Available strategies:")
            for d in sorted(os.listdir("strategies")):
                if os.path.isdir(f"strategies/{d}"):
                    print(f"  --strategy {d}")
            sys.exit(1)
    else:
        strat_path = STRATEGY_PATH

    with open(strat_path) as f:
        strategy = yaml.safe_load(f)

    return settings, strategy


def get_results_dir(strategy_name: str = None) -> str:
    """Return the output directory for backtest results."""
    if strategy_name:
        path = f"strategies/{strategy_name}/results"
        os.makedirs(path, exist_ok=True)
        return path
    os.makedirs("data/historical", exist_ok=True)
    return "data/historical"


# ─────────────────────────────────────────────────────
#  Mode: login  (generate daily Kite access token)
# ─────────────────────────────────────────────────────

def run_login(settings: dict) -> None:
    from src.broker.kite_broker import kite_login_flow
    kite_login_flow(settings)


# ─────────────────────────────────────────────────────
#  Mode: pre-market
# ─────────────────────────────────────────────────────

def run_premarket(strategy_config: dict) -> None:
    from src.strategy.direction_engine import DirectionEngine
    from src.data.market_data import get_india_vix
    from src.dashboard.signal_display import print_premarket_report

    engine = DirectionEngine(strategy_config)
    vix = get_india_vix()

    print("\nFetching pre-market data...")
    try:
        result = engine.evaluate_from_live_data()
    except Exception as e:
        print(f"Live data fetch failed ({e}). Running with sample data.")
        from src.strategy.direction_engine import DirectionInputs
        result = engine.evaluate(DirectionInputs(india_vix=vix or 15.0))

    print_premarket_report(result, vix or 15.0)


# ─────────────────────────────────────────────────────
#  Mode: backtest
# ─────────────────────────────────────────────────────

def run_backtest(strategy_config: dict, strategy_name: str = None) -> None:
    from src.backtest.engine import BacktestEngine
    from src.backtest.report import print_summary, export_csv, plot_equity_curve

    engine = BacktestEngine({}, strategy_config)
    out    = get_results_dir(strategy_name)
    print(f"\nRunning backtest: {engine.start_date} → {engine.end_date}")
    print(f"Strategy: {strategy_name or 'default'}  |  Output: {out}/")
    print("Downloading historical data (first run may take 1–2 minutes)...\n")

    result = engine.run()

    print_summary(result)
    export_csv(result, path=f"{out}/backtest_trades.csv")
    plot_equity_curve(result, path=f"{out}/equity_curve.png")

    print(f"\nBacktest complete. Results saved to {out}/")


# ─────────────────────────────────────────────────────
#  Mode: wfv  (Walk-Forward Validation)
# ─────────────────────────────────────────────────────

def run_wfv(strategy_config: dict, strategy_name: str = None) -> None:
    from src.backtest.engine import BacktestEngine
    from src.backtest.report import print_walk_forward_report, export_walk_forward_csv

    engine = BacktestEngine({}, strategy_config)
    out    = get_results_dir(strategy_name)
    print(f"\nWalk-Forward Validation: {engine.start_date} → {engine.end_date}")
    print(f"Strategy: {strategy_name or 'default'}  |  Output: {out}/")
    print("Splitting into 8 equal folds (~5 months each)...")
    print("Downloading historical data (first run may take 1–2 minutes)...\n")

    folds = engine.run_walk_forward(n_folds=8)
    print_walk_forward_report(folds)
    export_walk_forward_csv(folds, output_dir=out)
    print(f"\nWFV complete. Results saved to {out}/")


# ─────────────────────────────────────────────────────
#  Mode: backtest1m  (1-min strategy, last ~7 days)
# ─────────────────────────────────────────────────────

def run_backtest_1min(strategy_config: dict, strategy_name: str = None) -> None:
    from src.backtest.engine import BacktestEngine
    from src.backtest.report import print_summary, export_csv, plot_equity_curve

    engine = BacktestEngine({}, strategy_config)
    out    = get_results_dir(strategy_name)
    print("\nRunning 1-min backtest — last 7 calendar days")
    print(f"Strategy: {strategy_name or 'default'}  |  Output: {out}/")
    print("(yfinance 1-min data limit: ~5 trading days)\n")

    result = engine.run_1min(days_back=7)

    print_summary(result)
    export_csv(result, path=f"{out}/backtest_1min_trades.csv")
    plot_equity_curve(result, path=f"{out}/equity_curve_1min.png")

    print(f"\n1-min backtest complete. Results saved to {out}/")


# ─────────────────────────────────────────────────────
#  Mode: paper trading (no real orders)
# ─────────────────────────────────────────────────────

def run_paper(settings: dict, strategy_config: dict) -> None:
    from src.broker.paper_broker import PaperBroker
    from src.live.live_engine import LiveEngine

    broker = PaperBroker(slippage_pct=strategy_config.get("backtest", {}).get("slippage_pct", 0.1))
    engine = LiveEngine(strategy_config, broker, mode="paper")
    engine.run()


# ─────────────────────────────────────────────────────
#  Mode: live trading (real Kite Connect orders)
# ─────────────────────────────────────────────────────

def run_live(settings: dict, strategy_config: dict) -> None:
    from src.broker.kite_broker import create_kite_broker
    from src.live.live_engine import LiveEngine

    print("\n⚠  LIVE MODE — Real orders will be placed on Kite Connect.")
    confirm = input("Type 'YES' or 'y' to confirm: ").strip().upper()
    if confirm not in ("YES", "Y"):
        print("Aborted.")
        return

    broker = create_kite_broker(settings)
    engine = LiveEngine(strategy_config, broker, mode="live")
    engine.run()


# ─────────────────────────────────────────────────────
#  Mode: test
# ─────────────────────────────────────────────────────

def run_tests() -> None:
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        check=False,
    )
    sys.exit(result.returncode)


# ─────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sa-Ra-L — Nifty & Sensex Weekly Options Strategy"
    )
    parser.add_argument(
        "--mode",
        choices=["backtest", "backtest1m", "wfv", "paper", "live", "premarket", "login", "test"],
        default="premarket",
        help="Execution mode (default: premarket)",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        metavar="NAME",
        help=(
            "Strategy name from strategies/ registry "
            "(e.g. --strategy RAMS_v1). "
            "Omit to use config/strategy_config.yaml with output to data/historical/."
        ),
    )
    args = parser.parse_args()

    if args.mode == "test":
        run_tests()
        return

    settings, strategy_config = load_configs(args.strategy)

    if args.mode == "login":
        run_login(settings)
    elif args.mode == "premarket":
        run_premarket(strategy_config)
    elif args.mode == "backtest":
        run_backtest(strategy_config, args.strategy)
    elif args.mode == "backtest1m":
        run_backtest_1min(strategy_config, args.strategy)
    elif args.mode == "wfv":
        run_wfv(strategy_config, args.strategy)
    elif args.mode == "paper":
        run_paper(settings, strategy_config)
    elif args.mode == "live":
        run_live(settings, strategy_config)


if __name__ == "__main__":
    main()

