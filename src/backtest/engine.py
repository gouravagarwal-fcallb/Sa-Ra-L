"""
Backtesting Engine v3
─────────────────────
Replays the strategy on historical data.

Trading schedule:
  Monday    -> Nifty 50  (T1, T3)
  Tuesday   -> Nifty 50 EXPIRY (T1, T2, T3, T4)
  Wednesday -> SKIP
  Thursday  -> Sensex EXPIRY (T1, T2, T3, T4)
  Friday    -> Nifty 50  (T1, T3)

Slot structure per day:
  Real windows  : T1 (09:22-10:10), T2* (11:30-11:45),
                  T3 (12:45-13:20), T4* (14:20-14:44)  *expiry days only
  Off-windows   : OW0..OWn — between/before/after real windows
                  Always paper trades. Only simulated when intraday data available.

Continuous re-entry:
  Within each real window, after a trade exits we immediately scan for the
  next entry (same direction, same budget). Re-entry allowed as long as
  entry time is still inside the window.  Trade itself can run past window end.
  Off-windows follow the same scan logic but all trades are paper.

Stop logic:
  Per-trade max loss (Rs.) = daily_limit / base_trades  (~Rs. 1L per trade)
  Stop % = per_trade_max_loss / position_value  (6.7%–14.3% depending on budget)
  Once cumulative real P&L ≤ −daily_loss_limit, all further trades become paper.

Data availability:
  Intraday 5-min : yfinance ~last 60 trading days → continuous re-entry
  Older dates    : daily OHLC → single entry per real window (OHLC approximation)
  Off-windows    : only simulated when 5-min intraday data available

Date range:
  2023-01-01 to 2026-06-19 (config driven).  Intraday available for ~last 60 days.
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
    window_id: str           # T1/T2/T3/T4 or OW0/OW1/... for off-window
    instrument: str          # "NIFTY" or "SENSEX"
    direction: str
    option_type: str
    strike: int
    entry_price: float
    exit_price: float
    entry_time: str          # "HH:MM:SS"
    exit_time: str           # "HH:MM:SS"
    pnl_pct: float
    gross_pnl: float
    transaction_cost: float
    pnl_rupees: float        # net = gross_pnl − transaction_cost
    quantity: int
    lot_size: int
    trade_budget: float
    exit_reason: str
    holding_minutes: int
    is_expiry: bool
    is_paper: bool           # True for off-window slots OR after day stop hit


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)
    daily_pnl_paper: dict = field(default_factory=dict)
    total_pnl: float = 0.0
    total_pnl_paper: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    initial_capital: float = 10_000_000


# ── Day slot definitions ──────────────────────────────────────────────────────
# Each tuple: (slot_id, start_hhmm, end_hhmm, is_real_window)
# Real windows: T1/T2/T3/T4  |  Off-windows: OW0..OW4 (always paper)

_SLOTS_EXPIRY = [
    ("OW0", (9, 15), (9, 22),  False),
    ("T1",  (9, 22), (10, 10), True),
    ("OW1", (10, 10),(11, 30), False),
    ("T2",  (11, 30),(11, 45), True),
    ("OW2", (11, 45),(12, 45), False),
    ("T3",  (12, 45),(13, 20), True),
    ("OW3", (13, 20),(14, 20), False),
    ("T4",  (14, 20),(14, 44), True),
    ("OW4", (14, 44),(15, 20), False),
]

_SLOTS_NORMAL = [
    ("OW0", (9, 15), (9, 22),  False),
    ("T1",  (9, 22), (10, 10), True),
    ("OW1", (10, 10),(12, 45), False),
    ("T3",  (12, 45),(13, 20), True),
    ("OW2", (13, 20),(15, 20), False),
]


class BacktestEngine:

    def __init__(self, config: dict, strategy_config: dict):
        self.config = config
        self.sc = strategy_config
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065)
        )
        self.direction_engine = DirectionEngine(strategy_config)
        self.entry_logic = EntryLogic(strategy_config)

        bt = strategy_config.get("backtest", {})
        self.start_date = date.fromisoformat(bt.get("start_date", "2023-01-01"))
        self.end_date   = date.fromisoformat(bt.get("end_date",   "2026-06-19"))
        self.initial_capital = bt.get("initial_capital", 10_000_000)
        self.slippage_pct    = bt.get("slippage_pct", 0.1) / 100

        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.nifty_lot_size    = inst_n.get("lot_size", 75)
        self.nifty_strike_step = inst_n.get("strike_step", 50)

        inst_s = strategy_config.get("instruments", {}).get("sensex", {})
        self.sensex_lot_size    = inst_s.get("lot_size", 20)
        self.sensex_strike_step = inst_s.get("strike_step", 100)

        ps = strategy_config.get("position_sizing", {})
        self.budget_min         = ps.get("trade_budget_min", 700_000)
        self.budget_max         = ps.get("trade_budget_max", 1_500_000)
        self.min_score_for_max  = ps.get("min_score_for_max_budget", 5)

        risk = strategy_config.get("risk", {})
        self.daily_loss_limit   = risk.get("daily_loss_limit", 1_000_000)
        self.base_trades        = risk.get("base_trades_per_day", 10)
        self.per_trade_max_loss = self.daily_loss_limit / self.base_trades

        self.exit_target_pct = strategy_config.get("exit", {}).get("profit_target_pct", 27.5) / 100

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _gift_nifty_proxy(self, dow_change_pct: float, spot_prev: float) -> float:
        return spot_prev * dow_change_pct * 0.6 / 100

    def _signal_to_budget(self, score: int) -> float:
        t = max(0.0, (min(abs(score), self.min_score_for_max) - 3) / max(self.min_score_for_max - 3, 1))
        return self.budget_min + t * (self.budget_max - self.budget_min)

    def _calculate_quantity(self, budget: float, option_price: float, lot_size: int) -> int:
        if option_price < 1.0:
            return 0
        return max(1, int(budget / (option_price * lot_size))) * lot_size

    def _per_trade_stop_pct(self, entry_price: float, quantity: int) -> float:
        pv = entry_price * quantity
        return min(max(self.per_trade_max_loss / pv if pv > 0 else 0.15, 0.05), 0.30)

    def _calculate_transaction_cost(
        self, entry_price: float, exit_price: float, quantity: int, exchange: str = "NSE"
    ) -> float:
        """
        Indian market round-trip cost: brokerage + STT + exchange levy + stamp + SEBI + GST.
        Brokerage: Rs.20 flat per order × 2 (Zerodha F&O, NOT per lot).
        """
        buy_val  = entry_price * quantity
        sell_val = exit_price  * quantity
        turnover = buy_val + sell_val
        brokerage = 40.0
        stt       = 0.001 * sell_val
        exch      = (0.000495 if exchange == "NSE" else 0.00035) * turnover
        stamp     = 0.00003 * buy_val
        sebi      = 0.000001 * turnover
        gst       = 0.18 * (brokerage + exch)
        return round(brokerage + stt + exch + stamp + sebi + gst, 2)

    def _T_to_expiry(self, trade_date: date, expiry_date: date, h: int, m: int) -> float:
        entry_dt  = datetime(trade_date.year, trade_date.month, trade_date.day, h, m)
        expiry_dt = datetime(expiry_date.year, expiry_date.month, expiry_date.day, 15, 30)
        return max((expiry_dt - entry_dt).total_seconds() / 3600, 0.05)

    def _T_eod(self, trade_date: date, expiry_date: date) -> float:
        eod = datetime(trade_date.year, trade_date.month, trade_date.day, 15, 20)
        exp = datetime(expiry_date.year, expiry_date.month, expiry_date.day, 15, 30)
        return max((exp - eod).total_seconds() / 3600, 0.05)

    def _get_window_candles(
        self, intraday: pd.DataFrame, sh: int, sm: int, eh: int, em: int
    ) -> list[tuple]:
        """
        Return [(timestamp, close_price), ...] for candles whose time falls in [start, end).
        Entry time must be strictly BEFORE end so the last entry is within the window.
        """
        if intraday.empty:
            return []
        s_str = f"{sh:02d}:{sm:02d}"
        e_str = f"{eh:02d}:{em:02d}"
        try:
            times = intraday.index.strftime("%H:%M")
            mask  = (times >= s_str) & (times < e_str)
            sub   = intraday.loc[mask]
            return [(ts, float(sub.at[ts, "Close"])) for ts in sub.index]
        except Exception:
            return []

    def _get_spot_path_from(
        self, intraday: pd.DataFrame, h: int, m: int
    ) -> list[float]:
        """Spot path from a given time until 15:20 (used as trade simulation path)."""
        if intraday.empty:
            return []
        s_str = f"{h:02d}:{m:02d}"
        try:
            times = intraday.index.strftime("%H:%M")
            mask  = (times > s_str) & (times <= "15:20")
            return intraday.loc[mask, "Close"].tolist()
        except Exception:
            return []

    def _fmt_time(self, h: int, m: int, extra_minutes: int = 0) -> str:
        dt = datetime(2000, 1, 1, h, m) + timedelta(minutes=extra_minutes)
        if dt.time() > datetime(2000, 1, 1, 15, 20).time():
            dt = datetime(2000, 1, 1, 15, 20)
        return dt.strftime("%H:%M:%S")

    # ── Slot simulation ───────────────────────────────────────────────────────

    def _simulate_slot_continuous(
        self,
        intraday: pd.DataFrame,
        slot_id: str,
        sh: int, sm: int, eh: int, em: int,
        spot_open: float, spot_close: float, spot_prev: float,
        vix: float,
        trade_date: date, expiry_date: date,
        lot_size: int, strike_step: int,
        budget: float,
        exchange: str,
    ) -> list[dict]:
        """
        Continuously scan and trade within one time slot.
        Direction is re-evaluated live at EVERY 5-min candle using intraday signals
        (spot % chg from prev close + intraday momentum vs open + VIX).
        On OHLC-only dates, open price is used as the live condition proxy.
        Returns list of raw sim dicts; each carries _opt_type and _direction.
        """
        candles = self._get_window_candles(intraday, sh, sm, eh, em)

        if not candles:
            if slot_id.startswith("OW"):
                return []
            return self._ohlc_fallback(
                spot_open, spot_close, spot_prev, vix,
                trade_date, expiry_date,
                sh, sm, eh, em, lot_size, strike_step, budget, exchange,
            )

        results = []
        window_end_abs = eh * 60 + em
        candle_idx = 0

        while candle_idx < len(candles):
            ts, spot = candles[candle_idx]
            e_h, e_m = ts.hour, ts.minute

            # ── Live direction re-evaluation ──────────────────────────────────
            intra_dir = self.direction_engine.evaluate_intraday(
                spot, spot_prev, spot_open, vix
            )
            if intra_dir.direction == Direction.NEUTRAL:
                candle_idx += 1
                continue

            # CE vs PE decided by live intraday conditions at this candle's time —
            # independent of pre-market direction.
            opt_type = "CE" if intra_dir.direction == Direction.BULLISH else "PE"

            atm      = round_to_strike(spot, strike_step)
            T_entry  = self._T_to_expiry(trade_date, expiry_date, e_h, e_m)
            pre_opt  = self.pricer.price(spot, atm, vix, T_entry, opt_type)

            if pre_opt.price < 0.5:
                candle_idx += 1
                continue

            qty = self._calculate_quantity(budget, pre_opt.price, lot_size)
            if qty == 0:
                candle_idx += 1
                continue

            stop_pct  = self._per_trade_stop_pct(pre_opt.price, qty)
            spot_path = self._get_spot_path_from(intraday, e_h, e_m)

            sim = self.pricer.simulate_trade(
                spot_at_entry=spot,
                spot_path=spot_path,
                vix=vix,
                option_type=opt_type,
                entry_hour=float(e_h),
                entry_minute=float(e_m),
                target_pct=self.exit_target_pct,
                stop_loss_pct=stop_pct,
                force_exit_hour=eh + em / 60.0,
                strike_step=strike_step,
            )

            if not sim.get("valid", False):
                candle_idx += 1
                continue

            sim.update({
                "_e_h": e_h, "_e_m": e_m,
                "_atm": atm, "_qty": qty,
                "_opt_type": opt_type,
                "_direction": intra_dir.direction.value,
            })
            results.append(sim)

            trade_exit_abs = e_h * 60 + e_m + sim.get("holding_minutes", 5)
            if trade_exit_abs >= window_end_abs:
                break

            next_idx = candle_idx + 1
            while next_idx < len(candles):
                nts, _ = candles[next_idx]
                if nts.hour * 60 + nts.minute >= trade_exit_abs:
                    break
                next_idx += 1
            candle_idx = next_idx

        return results

    def _ohlc_fallback(
        self,
        spot_open: float, spot_close: float, spot_prev: float,
        vix: float,
        trade_date: date, expiry_date: date,
        wh: int, wm: int, eh: int, em: int,
        lot_size: int, strike_step: int,
        budget: float, exchange: str,
    ) -> list[dict]:
        """
        Single-trade estimate using daily OHLC when intraday data unavailable.
        Direction for ALL windows is derived from live intraday signals at open
        (open vs prev_close + open momentum). Pre-market never overrides this.
        Exit is always capped at window end (eh:em), not EOD.
        """
        spot = spot_open

        # All windows: direction from live open-price intraday evaluation
        intra_dir = self.direction_engine.evaluate_intraday(
            spot_open, spot_prev, spot_open, vix
        )
        if intra_dir.direction == Direction.NEUTRAL:
            return []
        direction = intra_dir.direction

        opt_type = "CE" if direction == Direction.BULLISH else "PE"

        atm  = round_to_strike(spot, strike_step)
        T_entry = self._T_to_expiry(trade_date, expiry_date, wh, wm)
        T_eod   = self._T_eod(trade_date, expiry_date)

        # Window duration in hours (e.g. T3 = 35 min = 0.583 h)
        T_intra = max(((eh * 60 + em) - (wh * 60 + wm)) / 60.0, 0.05)
        window_minutes = int(T_intra * 60)  # e.g. 35 min for T3

        entry_opt = self.pricer.price(spot, atm, vix, T_entry, opt_type)
        exit_opt  = self.pricer.price(spot_close, atm, vix, T_eod, opt_type)

        if entry_opt.price < 0.5:
            return []

        qty      = self._calculate_quantity(budget, entry_opt.price, lot_size)
        if qty == 0:
            return []

        stop_pct = self._per_trade_stop_pct(entry_opt.price, qty)
        entry_p  = entry_opt.price
        exit_p   = exit_opt.price
        raw_pct  = (exit_p - entry_p) / entry_p * 100 if entry_p > 0 else 0

        if raw_pct >= self.exit_target_pct * 100:
            exit_p   = entry_p * (1 + self.exit_target_pct)
            reason   = "TARGET_HIT"
            hold_min = max(10, min(30, int(T_intra * 60 * 0.25)))
        elif raw_pct <= -stop_pct * 100:
            exit_p   = entry_p * (1 - stop_pct)
            reason   = "STOP_LOSS"
            hold_min = max(8,  min(20, int(T_intra * 60 * 0.15)))
        else:
            reason   = "FORCE_CLOSE"
            hold_min = window_minutes  # exit at window end (e.g. 13:20 for T3)

        return [{
            "valid":           True,
            "entry_price":     entry_p,
            "exit_price":      exit_p,
            "pnl_pct":         (exit_p - entry_p) / entry_p * 100 if entry_p > 0 else 0,
            "exit_reason":     reason,
            "holding_minutes": hold_min,
            "strike":          atm,
            "_e_h": wh, "_e_m": wm,
            "_atm": atm, "_qty": qty,
            "_opt_type":  opt_type,
            "_direction": direction.value,
        }]

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self) -> BacktestResult:
        log.info(f"Starting backtest v3: {self.start_date} to {self.end_date}")
        dataset = build_backtest_dataset(self.start_date, self.end_date)

        if dataset.empty:
            log.error("No data loaded")
            return BacktestResult()

        result = BacktestResult(initial_capital=self.initial_capital)

        for idx, row in dataset.iterrows():
            trade_date = idx.date()
            weekday    = trade_date.weekday()

            if weekday == 2 or not is_trading_day(trade_date):
                continue

            # ── Instrument / expiry / spot ────────────────────────────────────
            if weekday == 3:
                instrument   = "SENSEX"
                expiry_date  = get_sensex_weekly_expiry(trade_date)
                is_expiry    = True
                lot_size     = self.sensex_lot_size
                strike_step  = self.sensex_strike_step
                exchange     = "BSE"
                spot_prev    = float(row.get("sensex_prev_close", 0))
                spot_open    = float(row.get("sensex_open", 0))
                spot_close   = float(row.get("sensex_close", spot_open))
                intraday_key = "sensex"
            else:
                instrument   = "NIFTY"
                expiry_date  = get_nifty_weekly_expiry(trade_date)
                is_expiry    = (trade_date == expiry_date)
                lot_size     = self.nifty_lot_size
                strike_step  = self.nifty_strike_step
                exchange     = "NSE"
                spot_prev    = float(row.get("nifty_prev_close", 0))
                spot_open    = float(row.get("nifty_open", 0))
                spot_close   = float(row.get("nifty_close", spot_open))
                intraday_key = "nifty"

            if spot_prev == 0 or spot_open == 0:
                continue

            # ── Pre-market signal ─────────────────────────────────────────────
            # Never skips the day — only sets the trade budget.
            # Dow + Gift Nifty + VIX scored to get conviction level.
            # Strong signal → higher budget. Weak/neutral → min budget.
            # CE vs PE at every window is decided by live intraday conditions.
            vix       = float(row.get("vix_close", 15.0))
            dow_chg   = float(row.get("dow_change_pct", 0.0))
            gift_prem = self._gift_nifty_proxy(dow_chg, spot_prev)

            dir_inputs = DirectionInputs(
                dow_change_pct=dow_chg,
                gift_nifty_premium=gift_prem,
                india_vix=vix,
                sensex_change_pct=dow_chg * 0.55,
                nifty_prev_close=spot_prev,
            )
            dir_result = self.direction_engine.evaluate(dir_inputs)
            budget     = self._signal_to_budget(dir_result.score)  # min_budget if neutral

            intraday = load_intraday(intraday_key, trade_date, interval="5m")

            # ── Day tracking ──────────────────────────────────────────────────
            daily_pnl_real  = 0.0
            daily_pnl_paper = 0.0
            real_stopped    = False
            day_trades: list[BacktestTrade] = []

            slots = _SLOTS_EXPIRY if is_expiry else _SLOTS_NORMAL

            for slot_id, (sh, sm), (eh, em), is_real_slot in slots:
                # Check day limit before each slot
                if daily_pnl_real <= -self.daily_loss_limit:
                    real_stopped = True

                # Off-window always paper; real windows paper if day stopped
                slot_is_paper = (not is_real_slot) or real_stopped

                sims = self._simulate_slot_continuous(
                    intraday, slot_id, sh, sm, eh, em,
                    spot_open, spot_close, spot_prev, vix,
                    trade_date, expiry_date,
                    lot_size, strike_step, budget, exchange,
                )

                for sim in sims:
                    e_h  = sim["_e_h"]
                    e_m  = sim["_e_m"]
                    qty  = sim["_qty"]
                    atm  = sim["_atm"]
                    hold = sim.get("holding_minutes", 0)

                    entry_p = sim["entry_price"] * (1 + self.slippage_pct)
                    exit_p  = sim["exit_price"]  * (1 - self.slippage_pct)

                    gross_pnl = (exit_p - entry_p) * qty
                    txn_cost  = self._calculate_transaction_cost(entry_p, exit_p, qty, exchange)
                    net_pnl   = gross_pnl - txn_cost

                    # Re-check day limit mid-slot (can flip mid-way through a slot's trades)
                    if daily_pnl_real <= -self.daily_loss_limit:
                        real_stopped  = True
                        slot_is_paper = True

                    if not slot_is_paper:
                        daily_pnl_real += net_pnl
                    daily_pnl_paper += net_pnl

                    trade = BacktestTrade(
                        date=trade_date,
                        window_id=slot_id,
                        instrument=instrument,
                        direction=sim.get("_direction", "NEUTRAL"),
                        option_type=sim.get("_opt_type", "CE"),
                        strike=sim.get("strike", atm),
                        entry_price=round(entry_p, 2),
                        exit_price=round(exit_p, 2),
                        entry_time=self._fmt_time(e_h, e_m),
                        exit_time=self._fmt_time(e_h, e_m, hold),
                        pnl_pct=round(sim["pnl_pct"], 2),
                        gross_pnl=round(gross_pnl, 2),
                        transaction_cost=txn_cost,
                        pnl_rupees=round(net_pnl, 2),
                        quantity=qty,
                        lot_size=lot_size,
                        trade_budget=round(budget),
                        exit_reason=sim["exit_reason"],
                        holding_minutes=hold,
                        is_expiry=is_expiry,
                        is_paper=slot_is_paper,
                    )
                    day_trades.append(trade)
                    result.trades.append(trade)

            if day_trades:
                result.daily_pnl[trade_date]       = daily_pnl_real
                result.daily_pnl_paper[trade_date] = daily_pnl_paper
                real_cnt  = sum(1 for t in day_trades if not t.is_paper)
                paper_cnt = len(day_trades) - real_cnt
                suffix    = " [DAY STOP]" if real_stopped else ""
                log.info(
                    f"{trade_date} {'[EXP]' if is_expiry else '     '} "
                    f"{instrument:6s} | {dir_result.direction.value:8s} | "
                    f"real={real_cnt} paper={paper_cnt} | "
                    f"Real: {format_inr(daily_pnl_real)} "
                    f"Paper: {format_inr(daily_pnl_paper)}{suffix}"
                )

        result.total_pnl       = sum(result.daily_pnl.values())
        result.total_pnl_paper = sum(result.daily_pnl_paper.values())
        result = self._compute_metrics(result)
        log.info(
            f"Backtest complete | Real: {format_inr(result.total_pnl)} | "
            f"Paper: {format_inr(result.total_pnl_paper)}"
        )
        return result

    # ── 1-min strategy backtest ───────────────────────────────────────────────

    def _load_candles_1m(self, symbol_key: str, trade_date: date) -> list:
        """
        Load 1-min OHLCV candles for a trading date via yfinance.
        yfinance supports only ~7 calendar days of 1-min history.
        Returns [] if data is outside that window.
        """
        from src.data.candle_builder import Candle
        df = load_intraday(symbol_key, trade_date, interval="1m")
        if df.empty:
            return []
        candles = []
        for ts, row in df.iterrows():
            try:
                t = ts.to_pydatetime()
                t_abs = t.hour * 60 + t.minute
                if not (9 * 60 + 15 <= t_abs <= 15 * 60 + 30):
                    continue
                candles.append(Candle(
                    timestamp=t,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=max(int(row.get("Volume", 0) or 0), 1),
                ))
            except Exception:
                continue
        return candles

    def _simulate_slot_1min(
        self,
        all_candles_1m: list,
        sh: int, sm: int, eh: int, em: int,
        spot_prev: float, day_open: float,
        vix: float,
        trade_date: date, expiry_date: date,
        lot_size: int, strike_step: int,
        budget: float, exchange: str,
    ) -> list[dict]:
        """
        Walk every 1-min candle inside [sh:sm, eh:em).
        At each bar, evaluate_1min() receives ALL bars from day open for
        proper EMA/RSI warmup and VWAP context.
        Entries re-trigger after each trade exits, same as the 5-min engine.
        """
        from src.data.candle_builder import compute_vwap, build_5min_from_1min

        window_start = sh * 60 + sm
        window_end   = eh * 60 + em

        w_indices = [
            i for i, c in enumerate(all_candles_1m)
            if window_start <= c.timestamp.hour * 60 + c.timestamp.minute < window_end
        ]
        if not w_indices:
            return []

        results = []
        idx = 0

        while idx < len(w_indices):
            c_global = w_indices[idx]
            c        = all_candles_1m[c_global]
            c_abs    = c.timestamp.hour * 60 + c.timestamp.minute

            bars_1m = all_candles_1m[:c_global + 1]
            bars_5m = build_5min_from_1min(bars_1m)
            vwap    = compute_vwap(bars_1m)

            intra = self.direction_engine.evaluate_1min(
                bars_1m, bars_5m, vix, day_open, spot_prev, vwap
            )

            if intra.direction == Direction.NEUTRAL:
                idx += 1
                continue

            opt_type  = "CE" if intra.direction == Direction.BULLISH else "PE"
            spot      = c.close
            atm       = round_to_strike(spot, strike_step)
            T_entry   = self._T_to_expiry(trade_date, expiry_date, c.timestamp.hour, c.timestamp.minute)
            entry_opt = self.pricer.price(spot, atm, vix, T_entry, opt_type)

            if entry_opt.price < 0.5:
                idx += 1
                continue

            qty = self._calculate_quantity(budget, entry_opt.price, lot_size)
            if qty == 0:
                idx += 1
                continue

            stop_pct  = self._per_trade_stop_pct(entry_opt.price, qty)
            spot_path = [
                all_candles_1m[j].close
                for j in range(c_global + 1, len(all_candles_1m))
                if all_candles_1m[j].timestamp.hour * 60 + all_candles_1m[j].timestamp.minute < window_end
            ]

            sim = self.pricer.simulate_trade(
                spot_at_entry=spot,
                spot_path=spot_path,
                vix=vix,
                option_type=opt_type,
                entry_hour=float(c.timestamp.hour),
                entry_minute=float(c.timestamp.minute),
                target_pct=self.exit_target_pct,
                stop_loss_pct=stop_pct,
                force_exit_hour=eh + em / 60.0,
                strike_step=strike_step,
            )

            if not sim.get("valid", False):
                idx += 1
                continue

            sim.update({
                "_e_h": c.timestamp.hour, "_e_m": c.timestamp.minute,
                "_atm": atm, "_qty": qty,
                "_opt_type": opt_type,
                "_direction": intra.direction.value,
            })
            results.append(sim)

            trade_exit_abs = c_abs + sim.get("holding_minutes", 5)
            if trade_exit_abs >= window_end:
                break

            next_idx = idx + 1
            while next_idx < len(w_indices):
                nc = all_candles_1m[w_indices[next_idx]]
                if nc.timestamp.hour * 60 + nc.timestamp.minute >= trade_exit_abs:
                    break
                next_idx += 1
            idx = next_idx

        return results

    def run_1min(self, days_back: int = 7) -> BacktestResult:
        """
        Backtest the 1-min multi-TF confluence strategy over the last
        `days_back` calendar days (yfinance 1-min data limit ~7 days).

        Uses evaluate_1min() with real 1-min OHLCV bars.
        Trade simulation, option pricing, stops, targets and costs
        are identical to the 5-min engine.
        """
        from datetime import date as _date
        end_date   = _date.today()
        start_date = end_date - timedelta(days=days_back + 7)  # buffer for weekends/holidays

        log.info(f"Starting 1-min backtest — last {days_back} calendar days")
        dataset = build_backtest_dataset(start_date, end_date)
        if dataset.empty:
            log.error("No data loaded")
            return BacktestResult()

        result = BacktestResult(initial_capital=self.initial_capital)
        traded_days = 0

        for idx, row in dataset.iterrows():
            trade_date = idx.date()
            if (end_date - trade_date).days > days_back:
                continue

            weekday = trade_date.weekday()
            if weekday == 2 or not is_trading_day(trade_date):
                continue

            # ── Instrument / expiry ───────────────────────────────────────────
            if weekday == 3:
                instrument  = "SENSEX"
                expiry_date = get_sensex_weekly_expiry(trade_date)
                is_expiry   = True
                lot_size    = self.sensex_lot_size
                strike_step = self.sensex_strike_step
                exchange    = "BSE"
                spot_prev   = float(row.get("sensex_prev_close", 0))
                spot_open   = float(row.get("sensex_open", 0))
                intraday_key = "sensex"
            else:
                instrument  = "NIFTY"
                expiry_date = get_nifty_weekly_expiry(trade_date)
                is_expiry   = (trade_date == expiry_date)
                lot_size    = self.nifty_lot_size
                strike_step = self.nifty_strike_step
                exchange    = "NSE"
                spot_prev   = float(row.get("nifty_prev_close", 0))
                spot_open   = float(row.get("nifty_open", 0))
                intraday_key = "nifty"

            if spot_prev == 0 or spot_open == 0:
                continue

            # ── Pre-market signal (sets budget only) ──────────────────────────
            vix      = float(row.get("vix_close", 15.0))
            dow_chg  = float(row.get("dow_change_pct", 0.0))
            gift_prem = self._gift_nifty_proxy(dow_chg, spot_prev)

            dir_result = self.direction_engine.evaluate(DirectionInputs(
                dow_change_pct=dow_chg,
                gift_nifty_premium=gift_prem,
                india_vix=vix,
                sensex_change_pct=dow_chg * 0.55,
                nifty_prev_close=spot_prev,
            ))
            budget = self._signal_to_budget(dir_result.score)

            # ── Load 1-min candles ────────────────────────────────────────────
            candles_1m = self._load_candles_1m(intraday_key, trade_date)
            if not candles_1m:
                log.warning(f"{trade_date}: no 1-min data — skipping (outside 7-day yfinance window?)")
                continue

            traded_days  += 1
            daily_pnl_real  = 0.0
            daily_pnl_paper = 0.0
            real_stopped    = False
            day_trades: list[BacktestTrade] = []

            slots = _SLOTS_EXPIRY if is_expiry else _SLOTS_NORMAL

            for slot_id, (sh, sm), (eh, em), is_real_slot in slots:
                if daily_pnl_real <= -self.daily_loss_limit:
                    real_stopped = True
                slot_is_paper = (not is_real_slot) or real_stopped

                sims = self._simulate_slot_1min(
                    candles_1m, sh, sm, eh, em,
                    spot_prev, spot_open, vix,
                    trade_date, expiry_date,
                    lot_size, strike_step, budget, exchange,
                )

                for sim in sims:
                    e_h  = sim["_e_h"];  e_m  = sim["_e_m"]
                    qty  = sim["_qty"];  atm  = sim["_atm"]
                    hold = sim.get("holding_minutes", 0)

                    entry_p   = sim["entry_price"] * (1 + self.slippage_pct)
                    exit_p    = sim["exit_price"]  * (1 - self.slippage_pct)
                    gross_pnl = (exit_p - entry_p) * qty
                    txn_cost  = self._calculate_transaction_cost(entry_p, exit_p, qty, exchange)
                    net_pnl   = gross_pnl - txn_cost

                    if daily_pnl_real <= -self.daily_loss_limit:
                        real_stopped  = True
                        slot_is_paper = True

                    if not slot_is_paper:
                        daily_pnl_real += net_pnl
                    daily_pnl_paper += net_pnl

                    day_trades.append(BacktestTrade(
                        date=trade_date,
                        window_id=slot_id,
                        instrument=instrument,
                        direction=sim.get("_direction", "NEUTRAL"),
                        option_type=sim.get("_opt_type", "CE"),
                        strike=sim.get("strike", atm),
                        entry_price=round(entry_p, 2),
                        exit_price=round(exit_p, 2),
                        entry_time=self._fmt_time(e_h, e_m),
                        exit_time=self._fmt_time(e_h, e_m, hold),
                        pnl_pct=round(sim["pnl_pct"], 2),
                        gross_pnl=round(gross_pnl, 2),
                        transaction_cost=txn_cost,
                        pnl_rupees=round(net_pnl, 2),
                        quantity=qty,
                        lot_size=lot_size,
                        trade_budget=round(budget),
                        exit_reason=sim["exit_reason"],
                        holding_minutes=hold,
                        is_expiry=is_expiry,
                        is_paper=slot_is_paper,
                    ))
                    result.trades.append(day_trades[-1])

            if day_trades:
                result.daily_pnl[trade_date]       = daily_pnl_real
                result.daily_pnl_paper[trade_date] = daily_pnl_paper
                real_cnt  = sum(1 for t in day_trades if not t.is_paper)
                paper_cnt = len(day_trades) - real_cnt
                log.info(
                    f"{trade_date} {'[EXP]' if is_expiry else '     '} "
                    f"{instrument:6s} | {dir_result.direction.value:8s} | "
                    f"real={real_cnt} paper={paper_cnt} | "
                    f"Real: {format_inr(daily_pnl_real)} "
                    f"Paper: {format_inr(daily_pnl_paper)}"
                    + (" [DAY STOP]" if real_stopped else "")
                )
            else:
                log.info(f"{trade_date}: no 1-min signals fired")

        result.total_pnl       = sum(result.daily_pnl.values())
        result.total_pnl_paper = sum(result.daily_pnl_paper.values())
        result = self._compute_metrics(result)
        log.info(
            f"1-min backtest complete | {traded_days} days | "
            f"Real: {format_inr(result.total_pnl)} | "
            f"Paper: {format_inr(result.total_pnl_paper)}"
        )
        return result

    def _compute_metrics(self, result: BacktestResult) -> BacktestResult:
        if not result.trades:
            return result

        real_trades = [t for t in result.trades if not t.is_paper]
        if real_trades:
            result.win_rate = sum(1 for t in real_trades if t.pnl_rupees > 0) / len(real_trades) * 100

        pnl_series = pd.Series(result.daily_pnl).sort_index()
        if not pnl_series.empty:
            cumulative = pnl_series.cumsum()
            result.max_drawdown = float((cumulative - cumulative.cummax()).min())
            if len(pnl_series) > 1 and pnl_series.std() > 0:
                result.sharpe = float((pnl_series.mean() / pnl_series.std()) * np.sqrt(252))

        return result
