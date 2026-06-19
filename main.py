#!/usr/bin/env python3
"""
Sa-Ra-L  |  Nifty 50 Expiry-Day Options Strategy
═══════════════════════════════════════════════════

Usage:
  python main.py --mode backtest          # Run historical backtest
  python main.py --mode paper             # Live signal generation (paper trading)
  python main.py --mode premarket         # Pre-market direction report only
  python main.py --mode test              # Run unit tests

Modes:
  backtest  — Replay strategy on 2023-2024 historical data, show P&L report
  paper     — Run live during market hours; print trade signals for manual execution
  premarket — Quick morning briefing: direction score + today's bias
  test      — Run pytest unit tests
"""

import argparse
import sys
import yaml
import os

CONFIG_PATH = "config/settings.yaml"
STRATEGY_PATH = "config/strategy_config.yaml"


def load_configs():
    with open(CONFIG_PATH) as f:
        settings = yaml.safe_load(f)
    with open(STRATEGY_PATH) as f:
        strategy = yaml.safe_load(f)
    return settings, strategy


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
#  Mode: paper trading
# ─────────────────────────────────────────────────────

def run_paper(settings: dict, strategy_config: dict) -> None:
    import time
    import schedule
    from datetime import datetime

    from src.strategy.direction_engine import DirectionEngine
    from src.strategy.entry_logic import EntryLogic
    from src.strategy.exit_logic import ExitLogic
    from src.strategy.position_manager import PositionManager
    from src.broker.paper_broker import PaperBroker
    from src.data.market_data import get_spot_price, get_india_vix
    from src.data.option_chain import fetch_nse_option_chain, parse_option_chain, synthetic_option_data
    from src.utils.market_calendar import is_expiry_day
    from src.utils.helpers import round_to_strike, time_in_window, current_time_ist
    from src.utils.logger import get_trade_logger
    from src.dashboard.signal_display import print_premarket_report, print_trade_alert, print_pnl_dashboard

    log = get_trade_logger()
    today_is_expiry = is_expiry_day()

    direction_engine = DirectionEngine(strategy_config)
    entry_logic = EntryLogic(strategy_config)
    exit_logic = ExitLogic(strategy_config)
    position_mgr = PositionManager(strategy_config)
    broker = PaperBroker(slippage_pct=0.1)

    risk_cfg = strategy_config.get("risk", {})
    inst_cfg = strategy_config.get("instruments", {}).get("nifty", {})
    quantity = inst_cfg.get("quantity", 26000)
    lot_size = inst_cfg.get("lot_size", 75)
    strike_step = inst_cfg.get("strike_step", 50)
    daily_loss_limit = risk_cfg.get("daily_loss_limit", 1_000_000)

    print(f"\nSa-Ra-L Paper Trading — {datetime.now().strftime('%A, %d %b %Y')}")
    print(f"Expiry Day: {'YES' if today_is_expiry else 'No'}\n")

    # ── Pre-market direction ──────────────────────────
    print("Computing pre-market direction...")
    try:
        direction_result = direction_engine.evaluate_from_live_data()
    except Exception as e:
        log.warning(f"Live direction fetch failed: {e}")
        from src.strategy.direction_engine import DirectionInputs
        direction_result = direction_engine.evaluate(DirectionInputs())

    vix = direction_result.inputs.india_vix or get_india_vix()
    print_premarket_report(direction_result, vix)

    windows = strategy_config.get("trade_windows", [])

    def process_window(window_id: str, window_start: str, window_end: str) -> None:
        if not time_in_window(window_start, window_end):
            return
        if not position_mgr.can_trade():
            return

        spot = get_spot_price("NIFTY")
        current_vix = get_india_vix()
        atm = round_to_strike(spot, strike_step)

        # Build option chain snapshot
        oc_cfg = strategy_config.get("option_chain", {})
        from datetime import datetime as dt
        is_afternoon = dt.now().hour >= 12
        oi_range = oc_cfg.get("oi_range_afternoon_pts", 50) if is_afternoon \
            else oc_cfg.get("oi_range_morning_pts", 100)

        raw_chain = fetch_nse_option_chain("NIFTY")
        if raw_chain:
            chain = parse_option_chain(raw_chain, spot, oi_range, strike_step)
        else:
            # Fallback to synthetic chain for paper testing
            from src.utils.market_calendar import days_to_expiry
            from src.data.option_chain import OptionChainSnapshot, synthetic_option_data
            T_hours = (15.5 - (dt.now().hour + dt.now().minute / 60))
            chain = OptionChainSnapshot(spot=spot, atm_strike=atm)
            chain.atm_call = synthetic_option_data(spot, atm, "CE", T_hours, current_vix)
            chain.atm_put = synthetic_option_data(spot, atm, "PE", T_hours, current_vix)

        signal = entry_logic.check_entry(
            direction_result=direction_result,
            chain=chain,
            daily_pnl=position_mgr.realised_pnl,
            daily_loss_limit=daily_loss_limit,
            window_id=window_id,
        )

        if signal.should_enter:
            target = exit_logic.target_price(signal.expected_entry_price)
            print_trade_alert(
                action="ENTRY",
                window_id=window_id,
                symbol="NIFTY",
                strike=signal.strike,
                opt_type=signal.option_type,
                price=signal.expected_entry_price,
                quantity=quantity,
                target_price=target,
                reason=signal.reason,
            )

            trade = position_mgr.open_trade(
                window_id=window_id,
                symbol="NIFTY",
                option_type=signal.option_type,
                strike=signal.strike,
                quantity=quantity,
                lot_size=lot_size,
                entry_price=signal.expected_entry_price,
            )
            log.info(f"Paper trade opened: {trade.trade_id}")
        else:
            log.info(f"[{window_id}] No entry: {signal.reason}")

    def monitor_open_positions() -> None:
        from datetime import datetime as dt
        if not position_mgr.open_trades:
            return

        spot = get_spot_price("NIFTY")
        current_vix = get_india_vix()
        is_force_time = current_time_ist().hour >= 15 and current_time_ist().minute >= 20

        for trade in position_mgr.open_trades[:]:
            from src.utils.market_calendar import days_to_expiry
            T_hours = max(0.1, 15.5 - (dt.now().hour + dt.now().minute / 60))
            current_opt = synthetic_option_data(spot, trade.strike, trade.option_type, T_hours, current_vix)
            current_price = current_opt.price

            exit_signal = exit_logic.check_exit(
                entry_price=trade.entry_price,
                current_price=current_price,
                quantity=quantity,
                lot_size=lot_size,
                cumulative_day_pnl=position_mgr.realised_pnl,
                peak_trade_pnl=max(0.0, (current_price - trade.entry_price) * quantity),
                force_close=is_force_time,
            )

            if exit_signal.should_exit:
                position_mgr.close_trade(trade, current_price, exit_signal.reason.value)
                print_trade_alert(
                    action="EXIT",
                    window_id=trade.window_id,
                    symbol="NIFTY",
                    strike=trade.strike,
                    opt_type=trade.option_type,
                    price=current_price,
                    quantity=quantity,
                    target_price=current_price,
                    reason=exit_signal.description,
                )

    # ── Schedule trade windows ────────────────────────
    for w in windows:
        wid = w["id"]
        if w.get("expiry_only") and not today_is_expiry:
            continue
        schedule.every(30).seconds.do(
            process_window, window_id=wid, window_start=w["start"], window_end=w["end"]
        )

    schedule.every(60).seconds.do(monitor_open_positions)
    schedule.every(5).minutes.do(print_pnl_dashboard, pm=position_mgr)

    print("\nPaper trading active. Monitoring market... (Ctrl+C to stop)\n")
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n\nStopping paper trading.")
        print_pnl_dashboard(position_mgr)


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
        description="Sa-Ra-L — Nifty 50 Expiry-Day Options Strategy"
    )
    parser.add_argument(
        "--mode",
        choices=["backtest", "paper", "premarket", "test"],
        default="premarket",
        help="Execution mode (default: premarket)",
    )
    args = parser.parse_args()

    if args.mode == "test":
        run_tests()
        return

    settings, strategy_config = load_configs()

    if args.mode == "premarket":
        run_premarket(strategy_config)
    elif args.mode == "backtest":
        run_backtest(strategy_config)
    elif args.mode == "paper":
        run_paper(settings, strategy_config)


if __name__ == "__main__":
    main()
