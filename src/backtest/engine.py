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

    def run(self, start_date: date = None, end_date: date = None) -> BacktestResult:
        start = start_date or self.start_date
        end   = end_date   or self.end_date
        log.info(f"Starting backtest v3: {start} to {end}")
        dataset = build_backtest_dataset(start, end)

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

    def run_walk_forward(self, n_folds: int = 8) -> list:
        """
        Walk-Forward Validation: split the full backtest period into n_folds
        equal windows, run the strategy independently on each, and return a
        list of (fold_meta, BacktestResult) for consistency analysis.

        Since the strategy has no free parameters to optimise, each fold is a
        pure out-of-sample test that checks whether the edge is consistent
        across different market regimes (bull, bear, range-bound, high-VIX, etc.)

        fold_meta dict keys: fold_num, start, end, trading_days, label
        """
        total_days = (self.end_date - self.start_date).days
        fold_days  = total_days // n_folds

        log.info(
            f"Walk-Forward Validation: {n_folds} folds × "
            f"~{fold_days} calendar days each "
            f"({self.start_date} → {self.end_date})"
        )

        folds = []
        for i in range(n_folds):
            fold_start = self.start_date + timedelta(days=i * fold_days)
            fold_end   = (fold_start + timedelta(days=fold_days - 1)
                          if i < n_folds - 1 else self.end_date)

            log.info(f"  Fold {i+1}/{n_folds}: {fold_start} → {fold_end}")
            result = self.run(start_date=fold_start, end_date=fold_end)

            real_trades = [t for t in result.trades if not t.is_paper]
            folds.append({
                "meta": {
                    "fold_num":     i + 1,
                    "start":        fold_start,
                    "end":          fold_end,
                    "trading_days": len(result.daily_pnl),
                    "label":        fold_start.strftime("%b'%y"),
                },
                "result": result,
            })

        log.info("Walk-Forward Validation complete")
        return folds

    def run_expiry_scalper(self) -> BacktestResult:
        """
        Expiry Scalper backtest — multi-window version.
        Fires ONLY on expiry days. Up to 3 windows per day (W1/W2/W3), each
        with its own momentum threshold, premium range, target, and stop.
        Fixed Rs.10K budget per trade (binary bet). Max 1 trade per window.
        """
        import datetime as _dt
        from src.utils.market_calendar import get_day_instrument

        sc_es    = self.sc.get("expiry_scalper", {})
        budget   = sc_es.get("trade_budget_rs", 10000)
        otm_n    = sc_es.get("otm_strikes", 2)
        vol_mult = sc_es.get("volume_surge_multiplier", 1.3)
        close_time_str = sc_es.get("hard_close_time", "15:29")
        close_h, close_m = int(close_time_str[:2]), int(close_time_str[3:])
        day_stop_limit = self.sc.get("risk", {}).get("daily_loss_limit", 30000)

        # Parse trading windows from config (fall back to legacy single-window)
        raw_windows = sc_es.get("windows")
        if not raw_windows:
            raw_windows = [{
                "id": "W3", "name": "End-of-Day Gamma",
                "start": sc_es.get("entry_after_time", "14:45"),
                "end":   "15:10",
                "momentum_threshold_pct": sc_es.get("momentum_threshold_pct", 0.25),
                "max_premium_rs": sc_es.get("max_premium_rs", 25.0),
                "min_premium_rs": sc_es.get("min_premium_rs", 0.5),
                "target_multiplier": sc_es.get("target_multiplier", 5.0),
                "stop_loss_pct": sc_es.get("stop_loss_pct", 50),
                "require_score_direction": False,
            }]

        def _parse_hm(s: str):
            return int(s[:2]), int(s[3:])

        windows = []
        for w in raw_windows:
            sh, sm = _parse_hm(w["start"])
            eh, em = _parse_hm(w["end"])
            windows.append({
                "id":       w["id"],
                "name":     w.get("name", w["id"]),
                "start":    _dt.time(sh, sm),
                "end":      _dt.time(eh, em),
                "mom_thr":  w.get("momentum_threshold_pct", 0.25) / 100,
                "max_prem": w.get("max_premium_rs", 25.0),
                "min_prem": w.get("min_premium_rs", 0.5),
                "tgt_mult": w.get("target_multiplier", 5.0),
                "stop_pct": w.get("stop_loss_pct", 50) / 100,
                "req_dir":  w.get("require_score_direction", False),
                "otm_n":    w.get("otm_strikes", otm_n),   # per-window override of global
            })

        trades:          list[BacktestTrade] = []
        daily_pnl:       dict = {}
        daily_pnl_paper: dict = {}
        total_pnl = 0.0
        equity    = float(self.initial_capital)

        current = self.start_date
        while current <= self.end_date:
            instrument = get_day_instrument(current)
            if not instrument:
                current += timedelta(days=1)
                continue

            lot_size    = self.nifty_lot_size    if instrument == "NIFTY" else self.sensex_lot_size
            strike_step = self.nifty_strike_step if instrument == "NIFTY" else self.sensex_strike_step
            expiry      = (get_nifty_weekly_expiry(current)
                           if instrument == "NIFTY" else get_sensex_weekly_expiry(current))
            is_expiry   = (current == expiry)

            if not is_expiry:
                current += timedelta(days=1)
                continue

            intraday_key = "nifty" if instrument == "NIFTY" else "sensex"
            bars = load_intraday(intraday_key, current, interval="5m")
            if bars is None or bars.empty:
                log.debug(f"Expiry scalper: no intraday data for {current}")
                current += timedelta(days=1)
                continue

            bars.index = pd.to_datetime(bars.index)
            vix = 15.0  # Historical VIX unavailable; use neutral default

            # Pre-market score for W1 direction filter (use 0 = neutral if unavailable)
            pre_score = 0

            day_pnl       = 0.0
            day_trade_cnt = 0

            for win in windows:
                if day_pnl <= -day_stop_limit:
                    break  # Hit daily stop — no more windows today

                win_bars = bars[
                    (bars.index.time >= win["start"]) &
                    (bars.index.time <= win["end"])
                ]
                if win_bars.empty:
                    continue

                # Reference spot = first bar of this window
                ref_spot = float(win_bars["Close"].iloc[0])

                # Average volume of the 5 bars immediately before this window
                before_bars = bars[bars.index.time < win["start"]]
                vol_series  = before_bars["Volume"].iloc[-5:] if not before_bars.empty else win_bars["Volume"].iloc[:1]
                avg_vol     = float(vol_series.mean()) if len(vol_series) > 0 else 0

                window_entered = False
                for ts, row in win_bars.iterrows():
                    if window_entered:
                        break

                    spot   = float(row["Close"])
                    volume = float(row.get("Volume", 0))
                    move   = (spot - ref_spot) / ref_spot
                    vol_ok = (avg_vol == 0) or (volume >= avg_vol * vol_mult)

                    if move >= win["mom_thr"] and vol_ok:
                        direction = "BULLISH"
                        opt_type  = "CE"
                        atm       = round_to_strike(spot, strike_step)
                        strike    = atm + win["otm_n"] * strike_step
                    elif move <= -win["mom_thr"] and vol_ok:
                        direction = "BEARISH"
                        opt_type  = "PE"
                        atm       = round_to_strike(spot, strike_step)
                        strike    = atm - win["otm_n"] * strike_step
                    else:
                        continue

                    # W1 direction filter: skip if breakout opposes pre-market bias
                    if win["req_dir"] and abs(pre_score) >= 3:
                        score_dir = "BULLISH" if pre_score > 0 else "BEARISH"
                        if direction != score_dir:
                            continue

                    entry_hr  = ts.hour
                    entry_min = ts.minute
                    T_minutes = (close_h * 60 + close_m) - (entry_hr * 60 + entry_min)
                    T_hours   = max(T_minutes / 60, 0.05)
                    T_years   = T_hours / (6.25 * 252)

                    opt_data = self.pricer.price_option(
                        spot=spot, strike=strike, opt_type=opt_type,
                        T_years=T_years, vix=vix,
                    )
                    ltp = (opt_data.get("ltp", 0.0) if isinstance(opt_data, dict)
                           else getattr(opt_data, "ltp", 0.0))

                    if ltp < win["min_prem"] or ltp > win["max_prem"]:
                        log.debug(
                            f"  {win['id']} {current}: LTP Rs.{ltp:.2f} "
                            f"outside [{win['min_prem']}, {win['max_prem']}] — skip"
                        )
                        window_entered = True  # Don't keep scanning same window
                        break

                    entry_price  = ltp * (1 + self.slippage_pct)
                    qty = self._calculate_quantity(budget, entry_price, lot_size)
                    if qty == 0:
                        window_entered = True
                        break

                    target_price = entry_price * win["tgt_mult"]
                    stop_price   = entry_price * (1 - win["stop_pct"])

                    # Simulate outcome on all remaining bars of the day
                    remaining = bars[bars.index.time >= ts.time()]
                    exit_price  = None
                    exit_reason = "FORCE_CLOSE"
                    exit_ts     = f"{close_h:02d}:{close_m:02d}:00"

                    for fts, frow in remaining.iterrows():
                        if fts == ts:
                            continue
                        fspot  = float(frow["Close"])
                        fT_min = (close_h * 60 + close_m) - (fts.hour * 60 + fts.minute)
                        fT_yrs = max(fT_min / 60, 0.02) / (6.25 * 252)
                        fopt   = self.pricer.price_option(
                            spot=fspot, strike=strike, opt_type=opt_type,
                            T_years=fT_yrs, vix=vix,
                        )
                        flt = (fopt.get("ltp", 0.0) if isinstance(fopt, dict)
                               else getattr(fopt, "ltp", 0.0))

                        if flt >= target_price:
                            exit_price  = flt * (1 - self.slippage_pct)
                            exit_reason = "TARGET_HIT"
                            exit_ts     = f"{fts.hour:02d}:{fts.minute:02d}:00"
                            break
                        if flt <= stop_price:
                            exit_price  = flt * (1 - self.slippage_pct)
                            exit_reason = "STOP_LOSS"
                            exit_ts     = f"{fts.hour:02d}:{fts.minute:02d}:00"
                            break

                    if exit_price is None:
                        fT_yrs = 0.001 / (6.25 * 252)
                        fopt   = self.pricer.price_option(
                            spot=float(bars["Close"].iloc[-1]),
                            strike=strike, opt_type=opt_type,
                            T_years=fT_yrs, vix=vix,
                        )
                        close_ltp  = (fopt.get("ltp", 0.0) if isinstance(fopt, dict)
                                      else getattr(fopt, "ltp", 0.0))
                        exit_price = close_ltp * (1 - self.slippage_pct)

                    gross_pnl = (exit_price - entry_price) * qty
                    pnl_pct   = (exit_price - entry_price) / entry_price * 100
                    txn_cost  = self._calculate_transaction_cost(
                        entry_price, exit_price, qty, lot_size, spot, is_expiry=True
                    )
                    net_pnl = gross_pnl - txn_cost

                    trade = BacktestTrade(
                        date=current,
                        window_id=win["id"],
                        instrument=instrument,
                        direction=direction,
                        option_type=opt_type,
                        strike=strike,
                        entry_price=round(entry_price, 2),
                        exit_price=round(exit_price, 2),
                        entry_time=f"{entry_hr:02d}:{entry_min:02d}:00",
                        exit_time=exit_ts,
                        pnl_pct=round(pnl_pct, 2),
                        gross_pnl=round(gross_pnl, 2),
                        transaction_cost=round(txn_cost, 2),
                        pnl_rupees=round(net_pnl, 2),
                        quantity=qty,
                        lot_size=lot_size,
                        trade_budget=budget,
                        exit_reason=exit_reason,
                        holding_minutes=int(T_minutes),
                        is_expiry=True,
                        is_paper=False,
                    )
                    trades.append(trade)
                    day_pnl       += net_pnl
                    total_pnl     += net_pnl
                    equity        += net_pnl
                    day_trade_cnt += 1
                    window_entered = True
                    log.debug(
                        f"  {win['id']} {current} {direction} {opt_type}{strike} "
                        f"entry=Rs.{entry_price:.1f} exit=Rs.{exit_price:.1f} "
                        f"P&L=Rs.{net_pnl:.0f} ({exit_reason})"
                    )

            if day_pnl != 0:
                daily_pnl[str(current)] = day_pnl

            current += timedelta(days=1)

        result = BacktestResult(
            trades=trades,
            daily_pnl=daily_pnl,
            daily_pnl_paper=daily_pnl_paper,
            total_pnl=round(total_pnl, 2),
            total_pnl_paper=0.0,
            initial_capital=self.initial_capital,
            final_equity=round(self.initial_capital + total_pnl, 2),
        )
        return self._compute_metrics(result)

    def run_range_scalper(self) -> BacktestResult:
        """
        Range Scalper backtest — replays the three-phase state machine on
        non-expiry days using 1-min intraday data from yfinance.

        Phase gate (all times in HH:MM IST):
          FORMING    09:15–09:45  — collect bars; range = (max-min)/open ≤ max_range_pct
          VALIDATING 09:45–10:00  — no bar outside breakout buffer
          TRADING    10:00–13:00  — mean-reversion entries at R_high/R_low touches
          INVALIDATED             — 2 consecutive bars outside buffer → close all

        Option pricing: Black-Scholes via OptionPricer, same slippage/costs as other methods.
        Budget: fixed Rs.10K per trade. ATM options only (OTM=0).
        Max trades per day: from config. Hard close: 13:00.
        """
        import datetime as _dt
        from src.utils.market_calendar import get_day_instrument

        rs           = self.sc.get("range_scalper", {})
        budget       = rs.get("trade_budget_rs", 10000)
        otm_n        = rs.get("otm_strikes", 0)
        target_pct   = rs.get("target_pct", 40) / 100
        stop_pct     = rs.get("stop_loss_pct", 25) / 100
        max_trades   = rs.get("max_trades_per_day", 6)
        form_mins    = rs.get("formation_minutes", 30)
        valid_mins   = rs.get("confirmation_minutes", 15)
        max_rng_pct  = rs.get("max_range_pct", 0.20) / 100
        touch_pct    = rs.get("boundary_touch_pct", 0.05) / 100
        brk_pct      = rs.get("breakout_buffer_pct", 0.07) / 100
        brk_n        = rs.get("breakout_confirm_bars", 2)
        min_prem     = rs.get("min_premium_rs", 15.0)
        max_prem     = rs.get("max_premium_rs", 120.0)
        close_str    = rs.get("hard_close_time", "13:00")
        close_h, close_m = int(close_str[:2]), int(close_str[3:])
        day_stop     = self.sc.get("risk", {}).get("daily_loss_limit", 20000)

        rf           = self.sc.get("range_day_filter", {})
        max_vix      = rf.get("max_vix", 16.0)
        skip_expiry  = rf.get("skip_expiry_days", True)

        open_min     = 9 * 60 + 15
        form_end_min = open_min + form_mins          # 9:45 by default
        valid_end_min = form_end_min + valid_mins    # 10:00 by default

        trades:          list[BacktestTrade] = []
        daily_pnl:       dict = {}
        total_pnl        = 0.0

        current = self.start_date
        while current <= self.end_date:
            instrument = get_day_instrument(current)
            if not instrument:
                current += timedelta(days=1)
                continue

            lot_size    = self.nifty_lot_size    if instrument == "NIFTY" else self.sensex_lot_size
            strike_step = self.nifty_strike_step if instrument == "NIFTY" else self.sensex_strike_step
            exchange    = "NSE" if instrument == "NIFTY" else "BSE"
            expiry      = (get_nifty_weekly_expiry(current)
                           if instrument == "NIFTY" else get_sensex_weekly_expiry(current))
            is_expiry   = (current == expiry)

            if skip_expiry and is_expiry:
                current += timedelta(days=1)
                continue

            intraday_key = "nifty" if instrument == "NIFTY" else "sensex"
            candles = self._load_candles_1m(intraday_key, current)
            if not candles:
                current += timedelta(days=1)
                continue

            vix = 15.0  # neutral default (historical VIX unavailable in 1-min data)

            # ── Build fast lookup: abs_minute → [candle index, ...] ───────────
            min_map: dict[int, int] = {}
            for ci, c in enumerate(candles):
                ab = c.timestamp.hour * 60 + c.timestamp.minute
                if ab not in min_map:
                    min_map[ab] = ci  # first bar of that minute

            spot_open = candles[0].close if candles else 0.0
            if spot_open == 0:
                current += timedelta(days=1)
                continue

            # ─── Phase 1: FORMING ─────────────────────────────────────────────
            form_bars = [c for c in candles
                         if open_min <= c.timestamp.hour * 60 + c.timestamp.minute < form_end_min]
            if not form_bars:
                current += timedelta(days=1)
                continue

            r_hi = max(c.high for c in form_bars)
            r_lo = min(c.low  for c in form_bars)
            r_pct = (r_hi - r_lo) / spot_open

            if r_pct > max_rng_pct:
                log.debug(f"Range scalper {current}: range {r_pct*100:.3f}% > {max_rng_pct*100:.2f}% — skip")
                current += timedelta(days=1)
                continue

            # ─── Phase 2: VALIDATING ──────────────────────────────────────────
            buf_h = r_hi * (1 + brk_pct)
            buf_l = r_lo * (1 - brk_pct)

            valid_bars = [c for c in candles
                          if form_end_min <= c.timestamp.hour * 60 + c.timestamp.minute < valid_end_min]
            validated = True
            for c in valid_bars:
                if c.close > buf_h or c.close < buf_l:
                    validated = False
                    break

            if not validated:
                log.debug(f"Range scalper {current}: validation failed — range broke during confirm")
                current += timedelta(days=1)
                continue

            # ─── Phase 3: TRADING ─────────────────────────────────────────────
            trade_bars = [c for c in candles
                          if valid_end_min <= c.timestamp.hour * 60 + c.timestamp.minute
                          < close_h * 60 + close_m]

            day_pnl       = 0.0
            trade_count   = 0
            breakout_ct   = 0
            open_entry    = None  # dict with entry_price, target, stop, strike, opt_type, qty, e_h, e_m
            day_trades: list[BacktestTrade] = []

            for c in trade_bars:
                spot = c.close
                c_abs = c.timestamp.hour * 60 + c.timestamp.minute

                # Daily stop
                if day_pnl <= -day_stop:
                    break

                # Breakout check
                if spot > buf_h or spot < buf_l:
                    breakout_ct += 1
                    if breakout_ct >= brk_n:
                        # Close open trade at range-break price
                        if open_entry:
                            exit_p = open_entry["entry_price"]  # no LTP available; use entry as flat proxy
                            # More realistic: reprice at this spot
                            T_e = self._T_to_expiry(current, expiry, c.timestamp.hour, c.timestamp.minute)
                            ep  = self.pricer.price(spot, open_entry["strike"], vix, T_e, open_entry["opt_type"])
                            exit_p = ep.price * (1 - self.slippage_pct)
                            gross  = (exit_p - open_entry["entry_price"]) * open_entry["qty"]
                            txn    = self._calculate_transaction_cost(
                                open_entry["entry_price"], exit_p, open_entry["qty"], exchange
                            )
                            net = gross - txn
                            day_pnl += net
                            day_trades.append(BacktestTrade(
                                date=current, window_id="RANGE",
                                instrument=instrument,
                                direction=open_entry["direction"],
                                option_type=open_entry["opt_type"],
                                strike=open_entry["strike"],
                                entry_price=round(open_entry["entry_price"], 2),
                                exit_price=round(exit_p, 2),
                                entry_time=self._fmt_time(open_entry["e_h"], open_entry["e_m"]),
                                exit_time=self._fmt_time(c.timestamp.hour, c.timestamp.minute),
                                pnl_pct=round((exit_p - open_entry["entry_price"]) / open_entry["entry_price"] * 100, 2),
                                gross_pnl=round(gross, 2),
                                transaction_cost=round(txn, 2),
                                pnl_rupees=round(net, 2),
                                quantity=open_entry["qty"],
                                lot_size=lot_size,
                                trade_budget=budget,
                                exit_reason="RANGE_BROKEN",
                                holding_minutes=c_abs - open_entry["e_h"] * 60 - open_entry["e_m"],
                                is_expiry=False,
                                is_paper=False,
                            ))
                            open_entry = None
                        break  # INVALIDATED — stop for the day
                else:
                    breakout_ct = 0  # reset if back inside

                # Monitor open position
                if open_entry:
                    T_e  = self._T_to_expiry(current, expiry, c.timestamp.hour, c.timestamp.minute)
                    ep   = self.pricer.price(spot, open_entry["strike"], vix, T_e, open_entry["opt_type"])
                    ltp  = ep.price

                    exit_reason = None
                    if ltp >= open_entry["target"]:
                        exit_reason = "TARGET_HIT"
                        exit_p = ltp * (1 - self.slippage_pct)
                    elif ltp <= open_entry["stop"]:
                        exit_reason = "STOP_LOSS"
                        exit_p = ltp * (1 - self.slippage_pct)

                    if exit_reason:
                        gross = (exit_p - open_entry["entry_price"]) * open_entry["qty"]
                        txn   = self._calculate_transaction_cost(
                            open_entry["entry_price"], exit_p, open_entry["qty"], exchange
                        )
                        net = gross - txn
                        day_pnl += net
                        day_trades.append(BacktestTrade(
                            date=current, window_id="RANGE",
                            instrument=instrument,
                            direction=open_entry["direction"],
                            option_type=open_entry["opt_type"],
                            strike=open_entry["strike"],
                            entry_price=round(open_entry["entry_price"], 2),
                            exit_price=round(exit_p, 2),
                            entry_time=self._fmt_time(open_entry["e_h"], open_entry["e_m"]),
                            exit_time=self._fmt_time(c.timestamp.hour, c.timestamp.minute),
                            pnl_pct=round((exit_p - open_entry["entry_price"]) / open_entry["entry_price"] * 100, 2),
                            gross_pnl=round(gross, 2),
                            transaction_cost=round(txn, 2),
                            pnl_rupees=round(net, 2),
                            quantity=open_entry["qty"],
                            lot_size=lot_size,
                            trade_budget=budget,
                            exit_reason=exit_reason,
                            holding_minutes=c_abs - open_entry["e_h"] * 60 - open_entry["e_m"],
                            is_expiry=False,
                            is_paper=False,
                        ))
                        open_entry = None
                    continue  # don't scan for new entry while position is open

                # Scan for new entry
                if trade_count >= max_trades:
                    continue

                near_high = spot >= r_hi * (1 - touch_pct)
                near_low  = spot <= r_lo * (1 + touch_pct)

                if not (near_high or near_low):
                    continue

                boundary  = "R_HIGH" if near_high else "R_LOW"
                direction = "BEARISH" if near_high else "BULLISH"
                opt_type  = "PE"      if near_high else "CE"
                atm       = round_to_strike(spot, strike_step)
                strike    = (atm - otm_n * strike_step if opt_type == "PE"
                             else atm + otm_n * strike_step)

                T_e   = self._T_to_expiry(current, expiry, c.timestamp.hour, c.timestamp.minute)
                ep    = self.pricer.price(spot, strike, vix, T_e, opt_type)
                ltp   = ep.price

                if not (min_prem <= ltp <= max_prem):
                    continue

                entry_px   = ltp * (1 + self.slippage_pct)
                qty        = self._calculate_quantity(budget, entry_px, lot_size)
                if qty == 0:
                    continue

                target_px  = entry_px * (1 + target_pct)
                stop_px    = entry_px * (1 - stop_pct)
                trade_count += 1

                open_entry = {
                    "e_h": c.timestamp.hour, "e_m": c.timestamp.minute,
                    "strike": strike, "opt_type": opt_type, "direction": direction,
                    "entry_price": entry_px, "target": target_px, "stop": stop_px,
                    "qty": qty, "boundary": boundary,
                }
                log.debug(
                    f"  {current} {boundary} {direction} {opt_type}{strike}"
                    f"  entry=Rs.{entry_px:.1f}  tgt=Rs.{target_px:.1f}  qty={qty}"
                )

            # Hard close — flush any remaining open position
            if open_entry:
                last_c = trade_bars[-1] if trade_bars else None
                if last_c:
                    T_e   = self._T_to_expiry(current, expiry, close_h, close_m)
                    ep    = self.pricer.price(last_c.close, open_entry["strike"],
                                              vix, T_e, open_entry["opt_type"])
                    exit_p = ep.price * (1 - self.slippage_pct)
                    gross  = (exit_p - open_entry["entry_price"]) * open_entry["qty"]
                    txn    = self._calculate_transaction_cost(
                        open_entry["entry_price"], exit_p, open_entry["qty"], exchange
                    )
                    net = gross - txn
                    day_pnl += net
                    day_trades.append(BacktestTrade(
                        date=current, window_id="RANGE",
                        instrument=instrument,
                        direction=open_entry["direction"],
                        option_type=open_entry["opt_type"],
                        strike=open_entry["strike"],
                        entry_price=round(open_entry["entry_price"], 2),
                        exit_price=round(exit_p, 2),
                        entry_time=self._fmt_time(open_entry["e_h"], open_entry["e_m"]),
                        exit_time=f"{close_h:02d}:{close_m:02d}:00",
                        pnl_pct=round((exit_p - open_entry["entry_price"]) / open_entry["entry_price"] * 100, 2),
                        gross_pnl=round(gross, 2),
                        transaction_cost=round(txn, 2),
                        pnl_rupees=round(net, 2),
                        quantity=open_entry["qty"],
                        lot_size=lot_size,
                        trade_budget=budget,
                        exit_reason="FORCE_CLOSE",
                        holding_minutes=(close_h * 60 + close_m) - open_entry["e_h"] * 60 - open_entry["e_m"],
                        is_expiry=False,
                        is_paper=False,
                    ))

            if day_trades:
                trades.extend(day_trades)
                daily_pnl[str(current)] = day_pnl
                total_pnl += day_pnl
                wins = sum(1 for t in day_trades if t.pnl_rupees > 0)
                log.info(
                    f"{current}  RANGE  {instrument:6s}  "
                    f"range={r_lo:.0f}–{r_hi:.0f} ({r_pct*100:.3f}%)  "
                    f"trades={len(day_trades)}  wins={wins}  "
                    f"P&L={format_inr(day_pnl)}"
                )

            current += timedelta(days=1)

        result = BacktestResult(
            trades=trades,
            daily_pnl=daily_pnl,
            daily_pnl_paper={},
            total_pnl=round(total_pnl, 2),
            total_pnl_paper=0.0,
            initial_capital=self.initial_capital,
        )
        log.info(
            f"Range scalper backtest complete | "
            f"{len([d for d in daily_pnl])} range days | "
            f"Total: {format_inr(total_pnl)}"
        )
        return self._compute_metrics(result)

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
