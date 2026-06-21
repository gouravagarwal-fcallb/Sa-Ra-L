"""
Backtesting Engine v2
─────────────────────
Replays the strategy on historical data.

Trading schedule:
  Monday    -> Nifty 50 (T1, T3)
  Tuesday   -> Nifty 50 EXPIRY (T1, T2, T3, T4)
  Wednesday -> SKIP
  Thursday  -> Sensex EXPIRY (T1, T2, T3, T4)
  Friday    -> Nifty 50 (T1, T3)

Position sizing:
  Not a fixed quantity. Each trade budget = Rs. 7L-15L based on signal score.
  Quantity = floor(budget / option_premium / lot_size) * lot_size

Stop loss:
  Daily cumulative limit = Rs. 10 Lakhs (real trades stop; paper trades continue).
  Per-trade max loss (Rs.) = daily_limit / base_trades_per_day (~Rs. 1L / trade).
  Per-trade stop % = per_trade_max_loss / (entry_price * quantity)  [varies by size]

Limitations:
  - VIX used as ATM IV proxy (actual IV differs by strike/time)
  - OI-based exit signals cannot be replicated in backtest (no OI history)
  - Gift Nifty approximated from Dow x 0.6 correlation
  - Intraday paths: ~60 days via yfinance; older dates use daily OHLC
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
from src.utils.market_calendar import (
    get_nifty_weekly_expiry,
    get_sensex_weekly_expiry,
    is_trading_day,
)
from src.utils.helpers import round_to_strike, format_inr
from src.utils.logger import setup_logger

log = setup_logger("backtest")


@dataclass
class BacktestTrade:
    date: date
    window_id: str
    instrument: str          # "NIFTY" or "SENSEX"
    direction: str
    option_type: str
    strike: int
    entry_price: float
    exit_price: float
    entry_time: str          # "HH:MM:SS" — window open time
    exit_time: str           # "HH:MM:SS" — derived from entry + holding_minutes
    pnl_pct: float
    gross_pnl: float         # (exit_price - entry_price) × quantity, before costs
    transaction_cost: float  # STT + exchange charges + stamp + SEBI + GST + brokerage
    pnl_rupees: float        # net = gross_pnl - transaction_cost
    quantity: int
    lot_size: int
    trade_budget: float      # Rs. budget used for this trade
    exit_reason: str
    holding_minutes: int
    is_expiry: bool
    is_paper: bool           # True when real trading stopped for the day


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)          # Real trades only
    daily_pnl_paper: dict = field(default_factory=dict)    # All trades (real + paper)
    total_pnl: float = 0.0
    total_pnl_paper: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    initial_capital: float = 50_000_000


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
        self.initial_capital = bt_cfg.get("initial_capital", 50_000_000)
        self.slippage_pct = bt_cfg.get("slippage_pct", 0.1) / 100
        self.brokerage_per_lot = bt_cfg.get("brokerage_per_lot", 20)

        # Instrument configs
        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.nifty_lot_size = inst_n.get("lot_size", 75)
        self.nifty_strike_step = inst_n.get("strike_step", 50)

        inst_s = strategy_config.get("instruments", {}).get("sensex", {})
        self.sensex_lot_size = inst_s.get("lot_size", 20)
        self.sensex_strike_step = inst_s.get("strike_step", 100)

        # Position sizing
        ps_cfg = strategy_config.get("position_sizing", {})
        self.budget_min = ps_cfg.get("trade_budget_min", 700_000)
        self.budget_max = ps_cfg.get("trade_budget_max", 1_500_000)
        self.min_score_for_max = ps_cfg.get("min_score_for_max_budget", 5)

        # Risk
        risk = strategy_config.get("risk", {})
        self.daily_loss_limit = risk.get("daily_loss_limit", 1_000_000)
        self.base_trades = risk.get("base_trades_per_day", 10)
        self.per_trade_max_loss = self.daily_loss_limit / self.base_trades

        # Exit
        self.exit_target_pct = strategy_config.get("exit", {}).get("profit_target_pct", 27.5) / 100

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _calculate_transaction_cost(
        self, entry_price: float, exit_price: float, quantity: int, exchange: str = "NSE"
    ) -> float:
        """
        Indian market transaction costs for option buying (round trip).
        Based on Zerodha rate card for F&O.

        Components:
          Brokerage     : Rs. 20 flat per order × 2 = Rs. 40 (regardless of qty)
          STT           : 0.1% on sell-side premium value only
          Exchange levy : 0.0495% (NSE) / 0.035% (BSE) on total premium turnover
          Stamp duty    : 0.003% on buy-side value only
          SEBI charges  : Rs. 10 per crore = 0.0001% of turnover
          GST           : 18% on (brokerage + exchange levy)
        """
        buy_value = entry_price * quantity
        sell_value = exit_price * quantity
        turnover = buy_value + sell_value

        brokerage = 40.0
        stt = 0.001 * sell_value
        exch_rate = 0.000495 if exchange == "NSE" else 0.00035
        exchange_charges = exch_rate * turnover
        stamp_duty = 0.00003 * buy_value
        sebi = 0.000001 * turnover
        gst = 0.18 * (brokerage + exchange_charges)

        return round(brokerage + stt + exchange_charges + stamp_duty + sebi + gst, 2)

    def _gift_nifty_proxy(self, dow_change_pct: float, spot_prev: float) -> float:
        return spot_prev * dow_change_pct * 0.6 / 100

    def _signal_to_budget(self, score: int) -> float:
        """Map direction score magnitude to trade budget (Rs. 7L to Rs. 15L)."""
        abs_score = min(abs(score), self.min_score_for_max)
        t = (abs_score - 3) / max(self.min_score_for_max - 3, 1)
        return self.budget_min + t * (self.budget_max - self.budget_min)

    def _calculate_quantity(self, budget: float, option_price: float, lot_size: int) -> int:
        """Dynamic qty: lots = floor(budget / (price * lot_size)), minimum 1 lot."""
        if option_price < 1.0:
            return 0
        lots = int(budget / (option_price * lot_size))
        return max(1, lots) * lot_size

    def _per_trade_stop_pct(self, entry_price: float, quantity: int) -> float:
        """Stop % derived from per-trade Rs. limit vs position size."""
        position_value = entry_price * quantity
        if position_value <= 0:
            return 0.15
        pct = self.per_trade_max_loss / position_value
        return min(max(pct, 0.05), 0.30)  # clamp 5%–30%

    def _get_spot_at_time(
        self, intraday: pd.DataFrame, hour: int, minute: int, fallback: float
    ) -> float:
        if intraday.empty:
            return fallback
        target = f"{hour:02d}:{minute:02d}"
        try:
            times = intraday.index.strftime("%H:%M")
            idx = times <= target
            if idx.any():
                return float(intraday.loc[idx, "Close"].iloc[-1])
        except Exception:
            pass
        return fallback

    def _get_spot_path(
        self, intraday: pd.DataFrame, start_hour: int, start_min: int
    ) -> list:
        if intraday.empty:
            return []
        start_str = f"{start_hour:02d}:{start_min:02d}"
        try:
            times = intraday.index.strftime("%H:%M")
            mask = (times >= start_str) & (times <= "15:20")
            return intraday.loc[mask, "Close"].tolist()
        except Exception:
            return []

    def _T_to_expiry(
        self, trade_date: date, expiry_date: date, w_hour: int, w_min: int
    ) -> float:
        entry_dt = datetime(trade_date.year, trade_date.month, trade_date.day, w_hour, w_min)
        expiry_dt = datetime(expiry_date.year, expiry_date.month, expiry_date.day, 15, 30)
        return max((expiry_dt - entry_dt).total_seconds() / 3600, 0.05)

    def _T_eod_to_expiry(self, trade_date: date, expiry_date: date) -> float:
        eod_dt = datetime(trade_date.year, trade_date.month, trade_date.day, 15, 20)
        expiry_dt = datetime(expiry_date.year, expiry_date.month, expiry_date.day, 15, 30)
        return max((expiry_dt - eod_dt).total_seconds() / 3600, 0.05)

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self) -> BacktestResult:
        log.info(f"Starting backtest v2: {self.start_date} to {self.end_date}")
        dataset = build_backtest_dataset(self.start_date, self.end_date)

        if dataset.empty:
            log.error("No data loaded — cannot run backtest")
            return BacktestResult()

        result = BacktestResult(initial_capital=self.initial_capital)

        for idx, row in dataset.iterrows():
            trade_date = idx.date()
            weekday = trade_date.weekday()  # 0=Mon,1=Tue,2=Wed,3=Thu,4=Fri

            # Skip Wednesday, weekends, holidays
            if weekday == 2:
                continue
            if not is_trading_day(trade_date):
                continue

            # ── Determine instrument & expiry ─────────────────────────────────
            if weekday == 3:   # Thursday -> Sensex expiry
                instrument = "SENSEX"
                expiry_date = get_sensex_weekly_expiry(trade_date)
                is_expiry = True   # We trade Sensex only on its expiry day
                lot_size = self.sensex_lot_size
                strike_step = self.sensex_strike_step
                spot_prev = float(row.get("sensex_prev_close", 0))
                spot_open = float(row.get("sensex_open", 0))
                spot_close = float(row.get("sensex_close", spot_open))
                intraday_key = "sensex"
            else:              # Mon / Tue / Fri -> Nifty
                instrument = "NIFTY"
                expiry_date = get_nifty_weekly_expiry(trade_date)
                is_expiry = (trade_date == expiry_date)  # True only on Tuesday
                lot_size = self.nifty_lot_size
                strike_step = self.nifty_strike_step
                spot_prev = float(row.get("nifty_prev_close", 0))
                spot_open = float(row.get("nifty_open", 0))
                spot_close = float(row.get("nifty_close", spot_open))
                intraday_key = "nifty"

            if spot_prev == 0 or spot_open == 0:
                continue

            # ── Pre-market direction scoring ──────────────────────────────────
            vix = float(row.get("vix_close", 15.0))
            dow_chg = float(row.get("dow_change_pct", 0.0))
            gift_premium = self._gift_nifty_proxy(dow_chg, spot_prev)

            dir_inputs = DirectionInputs(
                dow_change_pct=dow_chg,
                gift_nifty_premium=gift_premium,
                india_vix=vix,
                sensex_change_pct=dow_chg * 0.55,
                nifty_prev_close=spot_prev,
            )
            dir_result = self.direction_engine.evaluate(dir_inputs)

            if dir_result.direction == Direction.NEUTRAL:
                continue

            # ── Trade budget from signal strength ─────────────────────────────
            budget = self._signal_to_budget(dir_result.score)

            # ── Intraday data (5-min candles) ─────────────────────────────────
            intraday = load_intraday(intraday_key, trade_date, interval="5m")

            # ── Day P&L tracking ──────────────────────────────────────────────
            daily_pnl_real = 0.0
            daily_pnl_paper = 0.0
            real_stopped = False
            day_trades: list[BacktestTrade] = []

            for window_id, (w_hour, w_min) in self.WINDOW_TIMES.items():
                if window_id in self.WINDOW_EXPIRY_ONLY and not is_expiry:
                    continue

                # Real trading halts at cumulative day loss limit
                if daily_pnl_real <= -self.daily_loss_limit:
                    real_stopped = True

                spot = self._get_spot_at_time(intraday, w_hour, w_min, spot_open)
                if spot == 0:
                    continue

                atm = round_to_strike(spot, strike_step)
                opt_type = "CE" if dir_result.direction == Direction.BULLISH else "PE"
                T_entry = self._T_to_expiry(trade_date, expiry_date, w_hour, w_min)

                # Pre-price to determine dynamic quantity
                entry_opt_pre = self.pricer.price(spot, atm, vix, T_entry, opt_type)
                if entry_opt_pre.price < 0.5:
                    continue

                quantity = self._calculate_quantity(budget, entry_opt_pre.price, lot_size)
                if quantity == 0:
                    continue
                lots = quantity // lot_size

                # Per-trade stop % (derived from Rs. per-trade loss limit)
                stop_pct = self._per_trade_stop_pct(entry_opt_pre.price, quantity)

                spot_path = self._get_spot_path(intraday, w_hour, w_min)

                if spot_path:
                    sim = self.pricer.simulate_trade(
                        spot_at_entry=spot,
                        spot_path=spot_path,
                        vix=vix,
                        option_type=opt_type,
                        entry_hour=float(w_hour),
                        entry_minute=float(w_min),
                        target_pct=self.exit_target_pct,
                        stop_loss_pct=stop_pct,
                    )
                else:
                    # No intraday: estimate from open -> close
                    T_eod = self._T_eod_to_expiry(trade_date, expiry_date)
                    T_intraday = self.pricer.hours_to_expiry(float(w_hour), float(w_min))

                    entry_opt = self.pricer.price(spot, atm, vix, T_entry, opt_type)
                    exit_opt = self.pricer.price(spot_close, atm, vix, T_eod, opt_type)
                    entry_p_raw = entry_opt.price
                    exit_p_raw = exit_opt.price

                    if entry_p_raw > 0:
                        raw_pnl_pct = (exit_p_raw - entry_p_raw) / entry_p_raw * 100
                        if raw_pnl_pct >= self.exit_target_pct * 100:
                            exit_p_raw = entry_p_raw * (1 + self.exit_target_pct)
                            pnl_pct = self.exit_target_pct * 100
                            exit_reason = "TARGET_HIT"
                            holding_minutes = int(T_intraday * 60 * 0.4)
                        elif raw_pnl_pct <= -stop_pct * 100:
                            exit_p_raw = entry_p_raw * (1 - stop_pct)
                            pnl_pct = -stop_pct * 100
                            exit_reason = "STOP_LOSS"
                            holding_minutes = int(T_intraday * 60 * 0.3)
                        else:
                            pnl_pct = raw_pnl_pct
                            exit_reason = "EOD_APPROX"
                            holding_minutes = 360
                    else:
                        pnl_pct = 0
                        exit_reason = "EOD_APPROX"
                        holding_minutes = 360

                    sim = {
                        "valid": entry_p_raw > 0.5,
                        "entry_price": entry_p_raw,
                        "exit_price": exit_p_raw,
                        "pnl_pct": pnl_pct,
                        "exit_reason": exit_reason,
                        "holding_minutes": holding_minutes,
                        "strike": atm,
                    }

                if not sim.get("valid", False):
                    continue

                # Apply slippage
                entry_p = sim["entry_price"] * (1 + self.slippage_pct)
                exit_p = sim["exit_price"] * (1 - self.slippage_pct)
                gross_pnl = (exit_p - entry_p) * quantity

                # Proper Indian market transaction costs (STT, exchange, stamp, SEBI, GST)
                exchange_name = "BSE" if instrument == "SENSEX" else "NSE"
                txn_cost = self._calculate_transaction_cost(entry_p, exit_p, quantity, exchange_name)
                net_pnl = gross_pnl - txn_cost

                # Entry / exit timestamps from window time + holding duration
                holding_min = sim.get("holding_minutes", 0)
                entry_dt = datetime(2000, 1, 1, w_hour, w_min, 0)
                exit_dt = entry_dt + timedelta(minutes=holding_min)
                if exit_dt.time() > datetime(2000, 1, 1, 15, 30).time():
                    exit_dt = datetime(2000, 1, 1, 15, 30, 0)
                entry_time_str = entry_dt.strftime("%H:%M:%S")
                exit_time_str = exit_dt.strftime("%H:%M:%S")

                if not real_stopped:
                    daily_pnl_real += net_pnl
                daily_pnl_paper += net_pnl

                trade = BacktestTrade(
                    date=trade_date,
                    window_id=window_id,
                    instrument=instrument,
                    direction=dir_result.direction.value,
                    option_type=opt_type,
                    strike=sim.get("strike", atm),
                    entry_price=round(entry_p, 2),
                    exit_price=round(exit_p, 2),
                    entry_time=entry_time_str,
                    exit_time=exit_time_str,
                    pnl_pct=round(sim["pnl_pct"], 2),
                    gross_pnl=round(gross_pnl, 2),
                    transaction_cost=txn_cost,
                    pnl_rupees=round(net_pnl, 2),
                    quantity=quantity,
                    lot_size=lot_size,
                    trade_budget=round(budget),
                    exit_reason=sim["exit_reason"],
                    holding_minutes=holding_min,
                    is_expiry=is_expiry,
                    is_paper=real_stopped,
                )
                day_trades.append(trade)
                result.trades.append(trade)

            if day_trades:
                result.daily_pnl[trade_date] = daily_pnl_real
                result.daily_pnl_paper[trade_date] = daily_pnl_paper
                paper_note = " [DAY STOP - PAPER ONLY]" if real_stopped else ""
                log.info(
                    f"{trade_date} {'[EXPIRY]' if is_expiry else '        '} "
                    f"{instrument:6s} | {dir_result.direction.value:8s} | "
                    f"{len(day_trades)} trades | Real: {format_inr(daily_pnl_real)} "
                    f"Paper: {format_inr(daily_pnl_paper)}{paper_note}"
                )

        result.total_pnl = sum(result.daily_pnl.values())
        result.total_pnl_paper = sum(result.daily_pnl_paper.values())
        result = self._compute_metrics(result)
        log.info(
            f"Backtest complete | Real P&L: {format_inr(result.total_pnl)} | "
            f"Paper P&L: {format_inr(result.total_pnl_paper)}"
        )
        return result

    def _compute_metrics(self, result: BacktestResult) -> BacktestResult:
        if not result.trades:
            return result

        real_trades = [t for t in result.trades if not t.is_paper]
        if real_trades:
            wins = sum(1 for t in real_trades if t.pnl_rupees > 0)
            result.win_rate = wins / len(real_trades) * 100

        pnl_series = pd.Series(result.daily_pnl).sort_index()
        if not pnl_series.empty:
            cumulative = pnl_series.cumsum()
            peak = cumulative.cummax()
            result.max_drawdown = float((cumulative - peak).min())

            if len(pnl_series) > 1 and pnl_series.std() > 0:
                result.sharpe = float(
                    (pnl_series.mean() / pnl_series.std()) * np.sqrt(252)
                )

        return result
