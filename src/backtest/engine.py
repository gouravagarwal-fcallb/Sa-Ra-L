"""
Backtesting Engine
───────────────────
Replays the strategy on historical data.

Method:
  1. For each trading day in the date range:
     a. Compute pre-market direction score using DOW + Gift Nifty proxy + VIX
     b. For each trade window (T1–T4, filtered by expiry_only rule):
        - Get Nifty spot at window start time (5m intraday data or open approximation)
        - Use Black-Scholes to price ATM option
        - Simulate hold until 25-30% target or force close at 15:20
     c. Aggregate daily P&L

Limitations (documented):
  - Uses VIX as IV proxy; actual IV may differ by strike/time
  - No live OI data; OI filter is bypassed in backtest
  - Gift Nifty is approximated from Dow Jones + SGX correlation
  - Intraday paths available only for last ~60 days via yfinance; older dates use daily OHLC
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

from src.data.historical_loader import build_backtest_dataset, load_intraday
from src.strategy.direction_engine import DirectionEngine, DirectionInputs, Direction
from src.strategy.entry_logic import EntryLogic
from src.backtest.option_pricer import OptionPricer
from src.utils.market_calendar import is_expiry_day, get_all_expiry_dates
from src.utils.helpers import round_to_strike, format_inr
from src.utils.logger import setup_logger

log = setup_logger("backtest")


@dataclass
class BacktestTrade:
    date: date
    window_id: str
    direction: str
    option_type: str
    strike: int
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_rupees: float
    quantity: int
    lot_size: int
    exit_reason: str
    holding_minutes: int
    is_expiry: bool


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)
    total_pnl: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    initial_capital: float = 10_000_000


class BacktestEngine:
    WINDOW_TIMES = {
        "T1": (9, 22),
        "T2": (11, 30),
        "T3": (12, 45),
        "T4": (14, 20),
    }
    WINDOW_EXPIRY_ONLY = {"T2", "T4"}

    def __init__(self, config: dict, strategy_config: dict):
        self.config = config
        self.sc = strategy_config
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065)
        )
        self.direction_engine = DirectionEngine(strategy_config)
        self.entry_logic = EntryLogic(strategy_config)

        bt_cfg = strategy_config.get("backtest", {})
        self.start_date = date.fromisoformat(bt_cfg.get("start_date", "2023-01-01"))
        self.end_date = date.fromisoformat(bt_cfg.get("end_date", "2024-12-31"))
        self.initial_capital = bt_cfg.get("initial_capital", 10_000_000)
        self.slippage_pct = bt_cfg.get("slippage_pct", 0.1) / 100
        self.brokerage_per_lot = bt_cfg.get("brokerage_per_lot", 20)

        inst = strategy_config.get("instruments", {}).get("nifty", {})
        self.quantity = inst.get("quantity", 26000)
        self.lot_size = inst.get("lot_size", 75)
        self.strike_step = inst.get("strike_step", 50)

        risk = strategy_config.get("risk", {})
        self.daily_loss_limit = risk.get("daily_loss_limit", 1_000_000)
        self.exit_target_pct = strategy_config.get("exit", {}).get("profit_target_pct", 27.5) / 100

    def _gift_nifty_proxy(self, dow_change_pct: float, nifty_prev: float) -> float:
        """Approximate Gift Nifty premium using Dow Jones correlation (~0.6 corr)."""
        estimated_nifty_change_pct = dow_change_pct * 0.6
        return nifty_prev * estimated_nifty_change_pct / 100

    def _get_spot_at_time(
        self, intraday: pd.DataFrame, hour: int, minute: int, fallback_open: float
    ) -> float:
        """Return Nifty spot price closest to given time from intraday data."""
        if intraday.empty:
            return fallback_open
        target = f"{hour:02d}:{minute:02d}"
        try:
            times = intraday.index.strftime("%H:%M")
            idx = (times <= target)
            if idx.any():
                return float(intraday.loc[idx, "Close"].iloc[-1])
        except Exception:
            pass
        return fallback_open

    def _get_spot_path(
        self, intraday: pd.DataFrame, start_hour: int, start_min: int
    ) -> list[float]:
        """Extract spot price path from window start to 15:20 at 5-min intervals."""
        if intraday.empty:
            return []
        start_str = f"{start_hour:02d}:{start_min:02d}"
        end_str = "15:20"
        try:
            times = intraday.index.strftime("%H:%M")
            mask = (times >= start_str) & (times <= end_str)
            return intraday.loc[mask, "Close"].tolist()
        except Exception:
            return []

    def run(self) -> BacktestResult:
        log.info(f"Starting backtest: {self.start_date} → {self.end_date}")
        dataset = build_backtest_dataset(self.start_date, self.end_date)

        if dataset.empty:
            log.error("No data loaded — cannot run backtest")
            return BacktestResult()

        result = BacktestResult(initial_capital=self.initial_capital)
        lots = self.quantity // self.lot_size

        for idx, row in dataset.iterrows():
            trade_date = idx.date()
            expiry = is_expiry_day(trade_date)

            nifty_prev = float(row.get("nifty_prev_close", 0))
            nifty_open = float(row.get("nifty_open", 0))
            vix = float(row.get("vix_close", 15.0))
            dow_chg = float(row.get("dow_change_pct", 0.0))

            if nifty_prev == 0 or nifty_open == 0:
                continue

            gift_premium = self._gift_nifty_proxy(dow_chg, nifty_prev)
            dir_inputs = DirectionInputs(
                dow_change_pct=dow_chg,
                gift_nifty_premium=gift_premium,
                india_vix=vix,
                sensex_change_pct=dow_chg * 0.55,
                nifty_prev_close=nifty_prev,
            )
            dir_result = self.direction_engine.evaluate(dir_inputs)

            if dir_result.direction == Direction.NEUTRAL:
                continue

            # Load intraday data (5-min candles)
            intraday = load_intraday("nifty", trade_date, interval="5m")

            daily_pnl = 0.0
            day_trades = []

            for window_id, (w_hour, w_min) in self.WINDOW_TIMES.items():
                if window_id in self.WINDOW_EXPIRY_ONLY and not expiry:
                    continue
                if daily_pnl <= -self.daily_loss_limit:
                    break

                spot = self._get_spot_at_time(intraday, w_hour, w_min, nifty_open)
                if spot == 0:
                    continue

                atm = round_to_strike(spot, self.strike_step)
                opt_type = "CE" if dir_result.direction == Direction.BULLISH else "PE"

                spot_path = self._get_spot_path(intraday, w_hour, w_min)

                if spot_path:
                    # Simulate with actual intraday path
                    sim = self.pricer.simulate_trade(
                        spot_at_entry=spot,
                        spot_path=spot_path,
                        vix=vix,
                        option_type=opt_type,
                        entry_hour=float(w_hour),
                        entry_minute=float(w_min),
                        target_pct=self.exit_target_pct,
                    )
                else:
                    # No intraday data: estimate using open vs close move
                    T_hours = self.pricer.hours_to_expiry(float(w_hour), float(w_min))
                    T_eod = max(T_hours - 6.0, 0.05)
                    entry_opt = self.pricer.price(spot, atm, vix, T_hours, opt_type)
                    nifty_close = float(row.get("nifty_close", spot))
                    exit_opt = self.pricer.price(nifty_close, atm, vix, T_eod, opt_type)
                    entry_p = entry_opt.price
                    exit_p = exit_opt.price
                    pnl_pct = (exit_p - entry_p) / entry_p * 100 if entry_p > 0 else 0
                    sim = {
                        "valid": entry_p > 0.5,
                        "entry_price": entry_p,
                        "exit_price": exit_p,
                        "pnl_pct": pnl_pct,
                        "exit_reason": "EOD_APPROX",
                        "holding_minutes": 360,
                        "strike": atm,
                    }

                if not sim.get("valid", False):
                    continue

                # Apply slippage and brokerage
                entry_p = sim["entry_price"] * (1 + self.slippage_pct)
                exit_p = sim["exit_price"] * (1 - self.slippage_pct)
                gross_pnl = (exit_p - entry_p) * self.quantity
                brokerage = self.brokerage_per_lot * lots * 2  # entry + exit
                net_pnl = gross_pnl - brokerage

                daily_pnl += net_pnl

                trade = BacktestTrade(
                    date=trade_date,
                    window_id=window_id,
                    direction=dir_result.direction.value,
                    option_type=opt_type,
                    strike=sim.get("strike", atm),
                    entry_price=round(entry_p, 2),
                    exit_price=round(exit_p, 2),
                    pnl_pct=round(sim["pnl_pct"], 2),
                    pnl_rupees=round(net_pnl, 2),
                    quantity=self.quantity,
                    lot_size=self.lot_size,
                    exit_reason=sim["exit_reason"],
                    holding_minutes=sim.get("holding_minutes", 0),
                    is_expiry=expiry,
                )
                day_trades.append(trade)
                result.trades.append(trade)

            if day_trades:
                result.daily_pnl[trade_date] = daily_pnl
                log.info(
                    f"{trade_date} {'[EXPIRY]' if expiry else '        '} | "
                    f"{dir_result.direction.value:8s} | "
                    f"{len(day_trades)} trades | Day P&L: {format_inr(daily_pnl)}"
                )

        result.total_pnl = sum(result.daily_pnl.values())
        result = self._compute_metrics(result)
        log.info(f"Backtest complete — Total P&L: {format_inr(result.total_pnl)}")
        return result

    def _compute_metrics(self, result: BacktestResult) -> BacktestResult:
        if not result.trades:
            return result

        wins = sum(1 for t in result.trades if t.pnl_rupees > 0)
        result.win_rate = wins / len(result.trades) * 100

        pnl_series = pd.Series(result.daily_pnl).sort_index()
        cumulative = pnl_series.cumsum()
        peak = cumulative.cummax()
        drawdown = (cumulative - peak)
        result.max_drawdown = float(drawdown.min())

        if len(pnl_series) > 1 and pnl_series.std() > 0:
            trading_days_per_year = 252
            result.sharpe = float(
                (pnl_series.mean() / pnl_series.std()) * np.sqrt(trading_days_per_year)
            )

        return result
