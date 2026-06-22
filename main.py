#!/usr/bin/env python3
"""
Sa-Ra-L  |  Nifty & Sensex Weekly Options Strategy
════════════════════════════════════════════════════

Usage:
  python main.py --mode backtest          # Run historical backtest
  python main.py --mode paper             # Live paper trading (no real orders)
  python main.py --mode live              # Live trading via Kite Connect
  python main.py --mode premarket         # Pre-market direction report only
  python main.py --mode login             # Generate daily Kite access token
  python main.py --mode test              # Run unit tests

Modes:
  backtest  — Replay strategy on historical data (2023 → 2026-06-19).
  paper     — 5-min tick loop with Black-Scholes pricing. Prints trade signals
              to terminal for manual execution. No Kite credentials needed.
  live      — Same loop but places real orders via Kite Connect.
              Requires KITE_API_KEY and KITE_ACCESS_TOKEN env vars.
  login     — Guides you through Kite OAuth to generate today's access token.
  premarket — Quick morning briefing: direction score + today's bias.
"""

import argparse
import sys
import yaml
import os

CONFIG_PATH   = "config/settings.yaml"
STRATEGY_PATH = "config/strategy_config.yaml"


def load_configs():
    with open(CONFIG_PATH) as f:
        settings = yaml.safe_load(f)
    with open(STRATEGY_PATH) as f:
        strategy = yaml.safe_load(f)
    return settings, strategy


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

def run_backtest(strategy_config: dict) -> None:
    from src.backtest.engine import BacktestEngine
    from src.backtest.report import print_summary, export_csv, plot_equity_curve

    engine = BacktestEngine({}, strategy_config)
    print(f"\nRunning backtest: {engine.start_date} → {engine.end_date}")
    print("Downloading historical data (first run may take 1–2 minutes)...\n")

    result = engine.run()

    print_summary(result)
    export_csv(result)
    plot_equity_curve(result)

    print(f"\nBacktest complete. Check data/historical/ for CSV and chart.")


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
        choices=["backtest", "paper", "live", "premarket", "login", "test"],
        default="premarket",
        help="Execution mode (default: premarket)",
    )
    args = parser.parse_args()

    if args.mode == "test":
        run_tests()
        return

    settings, strategy_config = load_configs()

    if args.mode == "login":
        run_login(settings)
    elif args.mode == "premarket":
        run_premarket(strategy_config)
    elif args.mode == "backtest":
        run_backtest(strategy_config)
    elif args.mode == "paper":
        run_paper(settings, strategy_config)
    elif args.mode == "live":
        run_live(settings, strategy_config)


if __name__ == "__main__":
    main()

