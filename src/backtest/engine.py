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
    get_day_instrument,
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

        # Clamp the intraday window to what the data source can actually serve.
        # yfinance only has ~60 days of 5-min bars, so a configured range that
        # ends before that window would invert (start > end) and silently yield
        # 0 trades. With Kite enabled there is deep history, so no clamp.
        try:
            from src.data import kite_historical
            _kite_deep = kite_historical.is_enabled()
        except Exception:
            _kite_deep = False
        if not _kite_deep:
            _today = date.today()
            _earliest = _today - timedelta(days=58)
            _start = max(self.start_date, _earliest)
            _end   = min(self.end_date, _today)
            if _start > _end:
                _start, _end = _earliest, _today   # configured range predates the window
            if (_start, _end) != (self.start_date, self.end_date):
                log.info(f"Intraday data source covers ~last 60 days — backtesting "
                         f"{_start} → {_end} (configured {self.start_date} → {self.end_date}). "
                         f"Use --source kite for deep history.")
            self.start_date, self.end_date = _start, _end

        self.initial_capital = bt.get("initial_capital", 10_000_000)
        self.slippage_pct    = bt.get("slippage_pct", 0.1) / 100

        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.nifty_lot_size    = inst_n.get("lot_size", 65)
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
            if not spot_path:                      # no forward bars → nothing to simulate
                candle_idx += 1
                continue

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
        # Clamp the default range to the yfinance 5-min window (~60 days) unless an
        # explicit range was passed (e.g. walk-forward folds).
        start = start_date if start_date is not None else self._intraday_start()
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

                try:
                    sims = self._simulate_slot_continuous(
                        intraday, slot_id, sh, sm, eh, em,
                        spot_open, spot_close, spot_prev, vix,
                        trade_date, expiry_date,
                        lot_size, strike_step, budget, exchange,
                    )
                except Exception as _e:
                    log.warning(f"{trade_date} {instrument} slot {slot_id}: "
                                f"{type(_e).__name__}: {str(_e)[:90]} — skipping slot")
                    sims = []

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
        # yfinance only serves 1-min data for ~the last 30 days — skip older dates
        # immediately rather than firing a network call that always fails. With
        # the Kite source enabled there is deep history, so don't skip.
        try:
            from src.data import kite_historical
            _kite_on = kite_historical.is_enabled()
        except Exception:
            _kite_on = False
        if not _kite_on and (date.today() - trade_date).days > 28:
            return []
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

    def run_1min(self, days_back: int = 7, start=None, end=None) -> BacktestResult:
        """
        Backtest the 1-min multi-TF confluence strategy.

        Default: the last `days_back` calendar days (yfinance 1-min limit ~7 days).
        DEEP mode: when Kite historical is enabled AND a `start` is given, the full
        configured range is used instead (Kite serves 1-min history back to ~2015),
        so RAMS gets a real multi-year backtest rather than a 7-day sample.

        Uses evaluate_1min() with real 1-min OHLCV bars. Trade simulation, option
        pricing, stops, targets and costs are identical to the 5-min engine.
        """
        from datetime import date as _date
        deep = False
        try:
            from src.data import kite_historical
            deep = bool(kite_historical.is_enabled() and start is not None)
        except Exception:
            deep = False

        if deep:
            start_date = start
            end_date   = end or _date.today()
            log.info(f"Starting 1-min backtest (DEEP via Kite) {start_date} → {end_date}")
        else:
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
            if not deep and (end_date - trade_date).days > days_back:
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

                try:
                    sims = self._simulate_slot_1min(
                        candles_1m, sh, sm, eh, em,
                        spot_prev, spot_open, vix,
                        trade_date, expiry_date,
                        lot_size, strike_step, budget, exchange,
                    )
                except Exception as _e:
                    log.warning(f"{trade_date} slot {slot_id}: "
                                f"{type(_e).__name__}: {str(_e)[:90]} — skipping slot")
                    sims = []

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
        # Explicit diagnosis so a 0-trade result tells you WHY (data vs signal).
        n_trades = len(result.trades)
        log.info(
            f"1-min backtest complete | {len(dataset)} days scanned, "
            f"{traded_days} had 1-min data, {n_trades} trades fired | "
            f"Real: {format_inr(result.total_pnl)} | Paper: {format_inr(result.total_pnl_paper)}"
        )
        if traded_days == 0:
            log.warning("RAMS/1-min: ZERO days had 1-min data — the Kite 1-min fetch "
                        "returned empty for the whole range (data problem, not signal). "
                        "Check Kite historical access / instrument tokens.")
        elif n_trades == 0:
            log.warning(f"RAMS/1-min: {traded_days} days HAD data but ZERO trades fired — "
                        "the 3-layer 1-min confluence never triggered (signal gating, not "
                        "a data problem). The gates are very strict on this data.")
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

        # ── Polish flags (mirror the live engine; ALL default OFF → this backtest
        #    reduces EXACTLY to the original single-shot target/stop simulation) ──
        confirm_breakout   = bool(sc_es.get("confirm_breakout", False))
        confirmation_ticks = max(1, int(sc_es.get("confirmation_ticks", 2)))
        partial_book       = bool(sc_es.get("partial_book", False))
        partial_trig       = float(sc_es.get("partial_trigger_mult", 1.6))
        partial_frac       = min(max(float(sc_es.get("partial_fraction", 0.5)), 0.1), 0.9)
        stop_to_be         = bool(sc_es.get("move_stop_to_breakeven", True))
        trail_stop         = bool(sc_es.get("trail_stop", False))
        trail_pct          = min(max(float(sc_es.get("trail_pct", 0.30)), 0.05), 0.90)

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
                # Per-window polish overrides — fall back to the strategy-level flag
                # when the window doesn't set its own (so W2 can keep the fixed stop
                # while W1/W3 trail). All ultimately default OFF.
                "confirm":  bool(w.get("confirm_breakout", confirm_breakout)),
                "conf_n":   max(1, int(w.get("confirmation_ticks", confirmation_ticks))),
                "pbook":    bool(w.get("partial_book", partial_book)),
                "ptrig":    float(w.get("partial_trigger_mult", partial_trig)),
                "pfrac":    min(max(float(w.get("partial_fraction", partial_frac)), 0.1), 0.9),
                "be":       bool(w.get("move_stop_to_breakeven", stop_to_be)),
                "trail":    bool(w.get("trail_stop", trail_stop)),
                "tpct":     min(max(float(w.get("trail_pct", trail_pct)), 0.05), 0.90),
            })

        trades:          list[BacktestTrade] = []
        daily_pnl:       dict = {}
        daily_pnl_paper: dict = {}
        total_pnl = 0.0
        equity    = float(self.initial_capital)

        current = self._intraday_start()
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
                confirm_dir = None      # breakout-persistence state (#1)
                confirm_cnt = 0
                for ts, row in win_bars.iterrows():
                    if window_entered:
                        break

                    spot   = float(row["Close"])
                    volume = float(row.get("Volume", 0))
                    move   = (spot - ref_spot) / ref_spot
                    vol_ok = (avg_vol == 0) or (volume >= avg_vol * vol_mult)

                    if move >= win["mom_thr"] and vol_ok:
                        bar_dir = "BULLISH"
                    elif move <= -win["mom_thr"] and vol_ok:
                        bar_dir = "BEARISH"
                    else:
                        confirm_dir, confirm_cnt = None, 0   # breakout faded → reset
                        continue

                    # #1 Confirmation: require the same-direction breakout to persist
                    # for N consecutive bars before entering (no-op when flag is off).
                    if win["confirm"]:
                        if bar_dir == confirm_dir:
                            confirm_cnt += 1
                        else:
                            confirm_dir, confirm_cnt = bar_dir, 1
                        if confirm_cnt < win["conf_n"]:
                            continue

                    direction = bar_dir
                    atm       = round_to_strike(spot, strike_step)
                    if direction == "BULLISH":
                        opt_type = "CE"
                        strike   = atm + win["otm_n"] * strike_step
                    else:
                        opt_type = "PE"
                        strike   = atm - win["otm_n"] * strike_step

                    # W1 direction filter: skip if breakout opposes pre-market bias
                    if win["req_dir"] and abs(pre_score) >= 3:
                        score_dir = "BULLISH" if pre_score > 0 else "BEARISH"
                        if direction != score_dir:
                            continue

                    entry_hr  = ts.hour
                    entry_min = ts.minute
                    T_minutes = (close_h * 60 + close_m) - (entry_hr * 60 + entry_min)
                    T_hours   = max(T_minutes / 60, 0.05)

                    ltp = self.pricer.price(spot, strike, vix, T_hours, opt_type).price

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

                    # Simulate outcome on all remaining bars of the day.
                    # Partial-book (#2): book a slice when the option pops, move the
                    # remainder's stop to breakeven, and optionally trail it under the
                    # peak. cur_stop/rem_qty/partial_net start at the single-shot values
                    # so with the flags OFF this loop is byte-identical to the original.
                    remaining = bars[bars.index.time >= ts.time()]
                    exit_price  = None
                    exit_reason = "FORCE_CLOSE"
                    exit_ts     = f"{close_h:02d}:{close_m:02d}:00"
                    cur_stop     = stop_price
                    rem_qty      = qty
                    peak         = entry_price
                    partial_done = False
                    partial_net  = 0.0
                    partial_gross = 0.0
                    partial_cost = 0.0

                    for fts, frow in remaining.iterrows():
                        if fts == ts:
                            continue
                        fspot  = float(frow["Close"])
                        fT_min = (close_h * 60 + close_m) - (fts.hour * 60 + fts.minute)
                        fT_hrs = max(fT_min / 60, 0.02)
                        flt    = self.pricer.price(fspot, strike, vix, fT_hrs, opt_type).price
                        peak   = max(peak, flt)

                        if flt >= target_price:
                            exit_price  = flt * (1 - self.slippage_pct)
                            exit_reason = "TARGET_HIT"
                            exit_ts     = f"{fts.hour:02d}:{fts.minute:02d}:00"
                            break

                        # Book a partial once, when the runner has popped enough.
                        if (win["pbook"] and not partial_done
                                and flt >= entry_price * win["ptrig"]):
                            book_qty = int((rem_qty * win["pfrac"]) / lot_size) * lot_size
                            if lot_size <= book_qty < rem_qty:
                                p_exit  = flt * (1 - self.slippage_pct)
                                p_gross = (p_exit - entry_price) * book_qty
                                p_cost  = self._calculate_transaction_cost(
                                    entry_price, p_exit, book_qty, exchange)
                                partial_net   += p_gross - p_cost
                                partial_gross += p_gross
                                partial_cost  += p_cost
                                rem_qty       -= book_qty
                                partial_done   = True
                                if win["be"] and cur_stop < entry_price:
                                    cur_stop = entry_price

                        # Trail the remainder's stop up under the peak.
                        if win["trail"] and (partial_done or not win["pbook"]):
                            tr = peak * (1 - win["tpct"])
                            if tr > cur_stop:
                                cur_stop = tr

                        if flt <= cur_stop:
                            exit_price  = flt * (1 - self.slippage_pct)
                            exit_reason = "TRAIL_STOP" if cur_stop >= entry_price else "STOP_LOSS"
                            exit_ts     = f"{fts.hour:02d}:{fts.minute:02d}:00"
                            break

                    if exit_price is None:
                        close_ltp  = self.pricer.price(
                            float(bars["Close"].iloc[-1]), strike, vix, 0.001, opt_type
                        ).price
                        exit_price = close_ltp * (1 - self.slippage_pct)

                    # Remainder leg + any booked partial → the trade's full round-trip.
                    rem_gross = (exit_price - entry_price) * rem_qty
                    rem_cost  = self._calculate_transaction_cost(
                        entry_price, exit_price, rem_qty, exchange)
                    gross_pnl = partial_gross + rem_gross
                    txn_cost  = partial_cost + rem_cost
                    net_pnl   = partial_net + (rem_gross - rem_cost)
                    pnl_pct   = net_pnl / (entry_price * qty) * 100 if qty else 0.0

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

        current = self._intraday_start()
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

    def run_nifty_intraday(self) -> BacktestResult:
        """
        Nifty Intraday v1 backtest.

        Runs on every Nifty trading day in the configured date range.
        Requires 5-min intraday data (yfinance supports ~60 calendar days);
        days outside that window are skipped with a debug log.

        Strategy:
          - Regime classification (TREND vs RANGE) from ORB range, VWAP slope,
            price vs ORB position.
          - TREND: ORB breakout + VWAP + 1.3× volume surge → buy CE/PE.
          - RANGE: S/R level + RSI <35/>65 → buy CE/PE.
          - Position sizing guarantees ₹1,200 stop: qty=floor(10K/(LTP×75))×75.
          - Breakeven trail after 50% of target (be_price).
          - Daily gates: -₹4,800 LOCKED_LOSS, +₹8,250 → half-size.
          - Hard close at 15:10 IST.
        """
        from src.utils.market_calendar import get_day_instrument

        ni          = self.sc.get("nifty_intraday", {})
        lot_size    = self.sc.get("instruments", {}).get("nifty", {}).get("lot_size", 65)
        strike_step = self.sc.get("instruments", {}).get("nifty", {}).get("strike_step", 50)

        max_trade_rs    = ni.get("max_trade_rs",         10000)
        hard_stop_rs    = ni.get("hard_stop_rs",          1200)
        profit_tgt_rs   = ni.get("profit_target_rs",      2750)
        daily_loss_lock = ni.get("daily_loss_lock_rs",    4800)
        daily_pft_lock  = ni.get("daily_profit_lock_rs",  8250)
        cooldown_loss   = ni.get("cooldown_loss_min",       15)
        cooldown_profit = ni.get("cooldown_profit_min",      5)
        orb_mins        = ni.get("opening_range_minutes",   15)
        orb_buffer_pct  = ni.get("orb_buffer_pct",        0.05) / 100
        vol_ratio_min   = ni.get("min_volume_ratio",        1.3)
        vwap_slope_thr  = ni.get("vwap_slope_threshold",  0.05) / 100
        sr_lookback     = ni.get("sr_lookback_bars",         30)
        sr_touch_pct    = ni.get("sr_touch_pct",           0.15) / 100
        rsi_oversold    = ni.get("rsi_oversold",             35)
        rsi_overbought  = ni.get("rsi_overbought",           65)
        itm_offset      = ni.get("prefer_itm_strikes",       0)
        be_pct          = ni.get("breakeven_at_pct_target", 0.50)
        avoid_first     = ni.get("avoid_first_minutes",      10)
        close_str       = ni.get("hard_close_time",      "15:10")
        close_h, close_m = int(close_str[:2]), int(close_str[3:])
        close_abs       = close_h * 60 + close_m
        slippage        = self.sc.get("backtest", {}).get("slippage_pct", 0.3) / 100
        exchange        = "NSE"

        # ── Inline indicator helpers ──────────────────────────────────────────

        def _atr14(closes, highs, lows):
            n = len(closes)
            if n < 2:
                return 0.0
            trs = [max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1]))
                   for i in range(1, n)]
            return sum(trs[-14:]) / min(len(trs), 14)

        def _rsi14(closes):
            n = len(closes)
            if n < 15:
                return 50.0
            gains, losses = [], []
            for i in range(1, n):
                d = closes[i] - closes[i - 1]
                gains.append(max(d, 0.0))
                losses.append(max(-d, 0.0))
            ag = sum(gains[-14:]) / 14
            al = sum(losses[-14:]) / 14
            if al == 0:
                return 100.0
            return 100 - 100 / (1 + ag / al)

        def _vwap_val(closes, volumes):
            tv = sum(volumes)
            if tv == 0:
                return closes[-1] if closes else 0.0
            return sum(c * v for c, v in zip(closes, volumes)) / tv

        def _compute_qty(entry_prem, half_size=False):
            budget = max_trade_rs / 2 if half_size else max_trade_rs
            max_lots = int(budget / (entry_prem * lot_size))
            qty = max_lots * lot_size
            if qty == 0:
                return 0, 0.0, 0.0, 0.0
            stop_p   = entry_prem - hard_stop_rs   / qty
            target_p = entry_prem + profit_tgt_rs  / qty
            be_p     = entry_prem + (profit_tgt_rs * be_pct) / qty
            return qty, stop_p, target_p, be_p

        # ── Main loop ─────────────────────────────────────────────────────────

        trades:    list[BacktestTrade] = []
        daily_pnl: dict = {}
        total_pnl  = 0.0

        current = self._intraday_start()
        while current <= self.end_date:
            instrument = get_day_instrument(current)
            if not instrument or instrument != "NIFTY":
                current += timedelta(days=1)
                continue

            expiry = get_nifty_weekly_expiry(current)
            bars   = load_intraday("nifty", current, interval="5m")
            if bars is None or bars.empty:
                log.debug(f"Nifty intraday BT: no 5-min data for {current} — skip")
                current += timedelta(days=1)
                continue

            bars.index   = pd.to_datetime(bars.index)
            timestamps   = list(bars.index)
            closes_all   = [float(bars.at[ts, "Close"]) for ts in timestamps]
            highs_all    = [float(bars.at[ts, "High"])  for ts in timestamps]
            lows_all     = [float(bars.at[ts, "Low"])   for ts in timestamps]
            volumes_all  = [max(float(bars.at[ts, "Volume"] or 1), 1) for ts in timestamps]

            def abs_min(ts):
                return ts.hour * 60 + ts.minute

            # ── ORB: first orb_mins minutes ───────────────────────────────────
            orb_start = 9 * 60 + 15
            orb_end   = orb_start + orb_mins
            orb_idx   = [i for i, ts in enumerate(timestamps)
                         if orb_start <= abs_min(ts) < orb_end]
            if not orb_idx:
                current += timedelta(days=1)
                continue

            or_high  = max(highs_all[i]  for i in orb_idx)
            or_low   = min(lows_all[i]   for i in orb_idx)
            day_open = closes_all[0]

            # ── Regime classification ─────────────────────────────────────────
            day_atr   = _atr14(closes_all, highs_all, lows_all)
            orb_rng_pct = (or_high - or_low) / day_open if day_open > 0 else 0
            final_close = closes_all[-1]
            vwap_full   = _vwap_val(closes_all, volumes_all)

            if len(closes_all) >= 6:
                vwap_slope = abs((closes_all[-1] - closes_all[0]) / (closes_all[0] or 1))
            else:
                vwap_slope = 0.0

            trend_atr  = orb_rng_pct > 0.005
            trend_vwap = vwap_slope > vwap_slope_thr
            trend_orb  = (final_close > or_high * (1 + orb_buffer_pct) or
                          final_close < or_low  * (1 - orb_buffer_pct))
            regime     = "TREND" if (int(trend_atr) + int(trend_vwap) + int(trend_orb)) >= 2 else "RANGE"

            # ── Day simulation ────────────────────────────────────────────────
            day_pnl        = 0.0
            state          = "IDLE"
            locked_loss    = False
            locked_pft     = False
            cooldown_until = -1
            open_trade     = None
            day_trades:    list[BacktestTrade] = []
            session_start  = 9 * 60 + 15 + avoid_first

            for i, ts in enumerate(timestamps):
                t_abs = abs_min(ts)
                if t_abs < session_start or t_abs >= close_abs:
                    continue
                spot = closes_all[i]

                # ── Monitor open trade ────────────────────────────────────────
                if state == "IN_TRADE" and open_trade:
                    ot    = open_trade
                    T_hrs = max((close_abs - t_abs) / 60, 0.05)
                    ltp   = self.pricer.price(spot, ot["strike"], 15.0, T_hrs, ot["opt_type"]).price

                    if not ot["be_triggered"] and ltp >= ot["be_price"]:
                        ot["stop_price"]  = ot["entry_price"]
                        ot["be_triggered"] = True

                    exit_reason = None
                    if ltp >= ot["target_price"]:
                        exit_reason = "TARGET_HIT"
                    elif ltp <= ot["stop_price"]:
                        exit_reason = "BE_STOP" if ot["be_triggered"] else "STOP_LOSS"

                    if exit_reason:
                        exit_p = ltp * (1 - slippage)
                        gross  = (exit_p - ot["entry_price"]) * ot["qty"]
                        txn    = self._calculate_transaction_cost(
                            ot["entry_price"], exit_p, ot["qty"], exchange
                        )
                        net    = gross - txn
                        day_pnl += net
                        hold    = t_abs - ot["entry_abs"]

                        day_trades.append(BacktestTrade(
                            date=current,
                            window_id=f"{regime}_{ot['direction'][:1]}",
                            instrument="NIFTY",
                            direction=ot["direction"],
                            option_type=ot["opt_type"],
                            strike=ot["strike"],
                            entry_price=round(ot["entry_price"], 2),
                            exit_price=round(exit_p, 2),
                            entry_time=self._fmt_time(ot["e_h"], ot["e_m"]),
                            exit_time=self._fmt_time(ts.hour, ts.minute),
                            pnl_pct=round((exit_p - ot["entry_price"]) / ot["entry_price"] * 100, 2),
                            gross_pnl=round(gross, 2),
                            transaction_cost=round(txn, 2),
                            pnl_rupees=round(net, 2),
                            quantity=ot["qty"],
                            lot_size=lot_size,
                            trade_budget=round(ot["entry_price"] * ot["qty"], 2),
                            exit_reason=exit_reason,
                            holding_minutes=hold,
                            is_expiry=(current == expiry),
                            is_paper=False,
                        ))
                        open_trade = None

                        if exit_reason == "STOP_LOSS":
                            cooldown_until = t_abs + cooldown_loss
                        else:
                            cooldown_until = t_abs + cooldown_profit

                        if day_pnl <= -daily_loss_lock:
                            locked_loss = True
                        if day_pnl >= daily_pft_lock:
                            locked_pft = True

                        state = "COOLDOWN"
                    continue

                # ── Cooldown ──────────────────────────────────────────────────
                if state == "COOLDOWN":
                    if t_abs >= cooldown_until:
                        state = "IDLE"
                    else:
                        continue

                if locked_loss:
                    break

                if state != "IDLE":
                    continue

                # ── Scan for entry ────────────────────────────────────────────
                half_size = locked_pft
                direction = None
                opt_type  = None

                if regime == "TREND":
                    buf_high = or_high * (1 + orb_buffer_pct)
                    buf_low  = or_low  * (1 - orb_buffer_pct)
                    chase    = 0.005

                    if buf_high < spot <= buf_high * (1 + chase):
                        avg_vol = (sum(volumes_all[max(0, i - 10):i]) / min(10, i)) if i > 0 else 1
                        vwap_n  = _vwap_val(closes_all[:i + 1], volumes_all[:i + 1])
                        if volumes_all[i] >= avg_vol * vol_ratio_min and spot > vwap_n:
                            direction = "BULLISH"; opt_type = "CE"

                    elif buf_low * (1 - chase) <= spot < buf_low:
                        avg_vol = (sum(volumes_all[max(0, i - 10):i]) / min(10, i)) if i > 0 else 1
                        vwap_n  = _vwap_val(closes_all[:i + 1], volumes_all[:i + 1])
                        if volumes_all[i] >= avg_vol * vol_ratio_min and spot < vwap_n:
                            direction = "BEARISH"; opt_type = "PE"

                else:  # RANGE
                    s = max(0, i - sr_lookback)
                    resistance = max(highs_all[s:i]) if i > s else or_high
                    support    = min(lows_all[s:i])  if i > s else or_low
                    rsi_val    = _rsi14(closes_all[:i + 1])

                    if spot >= resistance * (1 - sr_touch_pct) and rsi_val > rsi_overbought:
                        direction = "BEARISH"; opt_type = "PE"
                    elif spot <= support * (1 + sr_touch_pct) and rsi_val < rsi_oversold:
                        direction = "BULLISH"; opt_type = "CE"

                if direction is None:
                    continue

                atm    = round_to_strike(spot, strike_step)
                strike = atm + itm_offset * (-1 if direction == "BULLISH" else 1) * strike_step
                strike = max(strike, strike_step)

                T_hrs = max((close_abs - t_abs) / 60, 0.05)
                ltp   = self.pricer.price(spot, strike, 15.0, T_hrs, opt_type).price

                if ltp < 10:
                    continue

                qty, stop_p, target_p, be_p = _compute_qty(ltp, half_size=half_size)
                if qty == 0 or stop_p <= 0:
                    continue

                entry_price = ltp * (1 + slippage)
                open_trade  = {
                    "direction":  direction, "opt_type": opt_type, "strike": strike,
                    "e_h": ts.hour, "e_m": ts.minute, "entry_abs": t_abs,
                    "entry_price": entry_price, "stop_price": stop_p,
                    "target_price": target_p, "be_price": be_p, "be_triggered": False,
                    "qty": qty,
                }
                state = "IN_TRADE"

            # ── Hard close at 15:10 ───────────────────────────────────────────
            if open_trade and state == "IN_TRADE":
                last_spot = closes_all[-1]
                ltp    = self.pricer.price(last_spot, open_trade["strike"], 15.0, 0.001, open_trade["opt_type"]).price
                exit_p = ltp * (1 - slippage)
                gross  = (exit_p - open_trade["entry_price"]) * open_trade["qty"]
                txn    = self._calculate_transaction_cost(
                    open_trade["entry_price"], exit_p, open_trade["qty"], exchange
                )
                net    = gross - txn
                day_pnl += net
                hold    = close_abs - open_trade["entry_abs"]

                day_trades.append(BacktestTrade(
                    date=current,
                    window_id=f"{regime}_CLOSE",
                    instrument="NIFTY",
                    direction=open_trade["direction"],
                    option_type=open_trade["opt_type"],
                    strike=open_trade["strike"],
                    entry_price=round(open_trade["entry_price"], 2),
                    exit_price=round(exit_p, 2),
                    entry_time=self._fmt_time(open_trade["e_h"], open_trade["e_m"]),
                    exit_time=f"{close_h:02d}:{close_m:02d}:00",
                    pnl_pct=round((exit_p - open_trade["entry_price"]) / open_trade["entry_price"] * 100, 2),
                    gross_pnl=round(gross, 2),
                    transaction_cost=round(txn, 2),
                    pnl_rupees=round(net, 2),
                    quantity=open_trade["qty"],
                    lot_size=lot_size,
                    trade_budget=round(open_trade["entry_price"] * open_trade["qty"], 2),
                    exit_reason="TIME_EXIT",
                    holding_minutes=hold,
                    is_expiry=(current == expiry),
                    is_paper=False,
                ))

            if day_trades:
                trades.extend(day_trades)
                daily_pnl[str(current)] = day_pnl
                total_pnl += day_pnl
                wins = sum(1 for t in day_trades if t.pnl_rupees > 0)
                log.info(
                    f"{current}  {regime:5s}  NIFTY  "
                    f"ORB=[{or_low:.0f}–{or_high:.0f}]  "
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
            initial_capital=50_000,
        )
        result = self._compute_metrics(result)
        log.info(
            f"Nifty Intraday backtest complete | "
            f"{len(daily_pnl)} trading days | "
            f"Total P&L: {format_inr(result.total_pnl)} | "
            f"Win rate: {result.win_rate:.1f}% | "
            f"Sharpe: {result.sharpe:.2f} | "
            f"Max drawdown: {format_inr(result.max_drawdown)}"
        )
        return result

    # ── Shared option-trade builder for the simpler intraday backtests ────────

    def _simulate_option_trade(self, current, expiry, entry_ts, spot_path, vix,
                               opt_type, *, budget, target_pct, stop_pct,
                               force_exit_hour, window_id, strike_step=50,
                               exchange="NSE"):
        """Price one option round-trip via OptionPricer.simulate_trade and wrap
        it in a BacktestTrade. Returns (trade | None, net_pnl)."""
        if len(spot_path) < 2:
            return None, 0.0
        eh, em = entry_ts.hour, entry_ts.minute
        sim = self.pricer.simulate_trade(
            spot_at_entry=spot_path[0], spot_path=spot_path[1:], vix=vix,
            option_type=opt_type, entry_hour=eh, entry_minute=em,
            target_pct=target_pct, stop_loss_pct=stop_pct,
            force_exit_hour=force_exit_hour, strike_step=strike_step,
        )
        if not sim.get("valid"):
            return None, 0.0
        entry_p, exit_p = sim["entry_price"], sim["exit_price"]
        lot  = self.sc.get("instruments", {}).get("nifty", {}).get("lot_size", 65)
        lots = int(budget / (entry_p * lot)) if entry_p > 0 else 0
        qty  = lots * lot
        if qty == 0:
            return None, 0.0
        gross = (exit_p - entry_p) * qty
        txn   = self._calculate_transaction_cost(entry_p, exit_p, qty, exchange)
        net   = gross - txn
        hold  = sim["holding_minutes"]
        exit_min = (eh * 60 + em + hold)
        trade = BacktestTrade(
            date=current, window_id=window_id, instrument="NIFTY",
            direction=("BULLISH" if opt_type == "CE" else "BEARISH"),
            option_type=opt_type, strike=sim["strike"],
            entry_price=round(entry_p, 2), exit_price=round(exit_p, 2),
            entry_time=f"{eh:02d}:{em:02d}:00",
            exit_time=f"{exit_min // 60:02d}:{exit_min % 60:02d}:00",
            pnl_pct=round(sim["pnl_pct"], 2), gross_pnl=round(gross, 2),
            transaction_cost=round(txn, 2), pnl_rupees=round(net, 2),
            quantity=qty, lot_size=lot, trade_budget=round(entry_p * qty, 2),
            exit_reason=sim["exit_reason"], holding_minutes=hold,
            is_expiry=(current == expiry), is_paper=False,
        )
        return trade, net

    @staticmethod
    def _ema(values, period):
        if not values:
            return 0.0
        k = 2 / (period + 1)
        e = values[0]
        for v in values[1:]:
            e = v * k + e * (1 - k)
        return e

    def _intraday_start(self) -> date:
        """yfinance only serves 5-min intraday data for ~the last 60 days, so a
        multi-year configured start just floods Yahoo with un-fulfillable requests.
        Clamp the loop start to the valid window (logged once). With the Kite
        source enabled there is deep history, so no clamp."""
        try:
            from src.data import kite_historical
            if kite_historical.is_enabled():
                return self.start_date
        except Exception:
            pass
        earliest = date.today() - timedelta(days=58)
        if self.start_date < earliest:
            log.info(f"Intraday (5-min) data is limited to ~last 60 days on yfinance "
                     f"— backtesting {earliest} → {self.end_date} "
                     f"(configured start {self.start_date} clamped). "
                     f"For multi-year history use a Kite historical data source.")
            return earliest
        return self.start_date

    def _intraday_arrays(self, current):
        """Load 5-min NIFTY bars and return (timestamps, o, h, l, c, v) or None."""
        bars = load_intraday("nifty", current, interval="5m")
        if bars is None or bars.empty:
            return None
        bars.index = pd.to_datetime(bars.index)
        ts = list(bars.index)
        o = [float(bars.at[t, "Open"])  for t in ts]
        h = [float(bars.at[t, "High"])  for t in ts]
        l = [float(bars.at[t, "Low"])   for t in ts]
        c = [float(bars.at[t, "Close"]) for t in ts]
        v = [max(float(bars.at[t, "Volume"] or 1), 1) for t in ts]
        return ts, o, h, l, c, v

    # ── ATM Pulse Burst backtest (NIFTY CE momentum scalper) ──────────────────

    def run_atm_pulse_burst(self) -> BacktestResult:
        cfg = self.sc.get("atm_pulse_burst", {})
        budget      = cfg.get("budget_rs", 10000)
        orb_mins    = cfg.get("opening_range_minutes", 15)
        buffer_pct  = cfg.get("breakout_buffer_pct", 0.05) / 100
        vol_ratio   = cfg.get("min_volume_ratio", 1.3)
        target_pct  = cfg.get("target_pct", 0.125)
        stop_pct    = cfg.get("stop_pct", 0.067)
        max_trades  = cfg.get("max_trades_per_day", 4)
        avoid_first = cfg.get("avoid_first_minutes", 15)
        close_str   = cfg.get("hard_close_time", "15:10")
        force_hour  = int(close_str[:2]) + int(close_str[3:]) / 60
        step        = self.sc.get("instruments", {}).get("nifty", {}).get("strike_step", 50)

        trades, daily_pnl, total = [], {}, 0.0
        current = self._intraday_start()
        while current <= self.end_date:
            if get_day_instrument(current) != "NIFTY":
                current += timedelta(days=1); continue
            arr = self._intraday_arrays(current)
            if arr is None:
                current += timedelta(days=1); continue
            ts, o, h, l, c, v = arr
            expiry = get_nifty_weekly_expiry(current)
            t0 = ts[0]
            orb_high = max(h[i] for i in range(len(ts))
                           if (ts[i] - t0).total_seconds() / 60 < orb_mins) if ts else 0
            day_trades, day_pnl, n = [], 0.0, 0
            i = 0
            while i < len(ts) and n < max_trades:
                mins = (ts[i] - t0).total_seconds() / 60
                if mins < max(avoid_first, orb_mins):
                    i += 1; continue
                vol_avg = sum(v[max(0, i - 10):i]) / max(1, len(v[max(0, i - 10):i]))
                bull = (c[i] > orb_high * (1 + buffer_pct)
                        and self._ema(c[:i + 1], 9) > self._ema(c[:i + 1], 21)
                        and v[i] >= vol_ratio * vol_avg)
                if bull:
                    tr, net = self._simulate_option_trade(
                        current, expiry, ts[i], c[i:], 15.0, "CE",
                        budget=budget, target_pct=target_pct, stop_pct=stop_pct,
                        force_exit_hour=force_hour, window_id="PULSE", strike_step=step)
                    if tr:
                        day_trades.append(tr); day_pnl += net; n += 1
                        i += max(1, tr.holding_minutes // 5)
                        continue
                i += 1
            if day_trades:
                trades.extend(day_trades); daily_pnl[str(current)] = day_pnl; total += day_pnl
            current += timedelta(days=1)

        result = BacktestResult(trades=trades, daily_pnl=daily_pnl, daily_pnl_paper={},
                                total_pnl=round(total, 2), total_pnl_paper=0.0,
                                initial_capital=budget)
        result = self._compute_metrics(result)
        log.info(f"ATM Pulse Burst backtest | {len(daily_pnl)} days | "
                 f"P&L {format_inr(result.total_pnl)} | win {result.win_rate:.1f}%")
        return result

    # ── BB Expiry Scalper backtest (expiry-day Bollinger breakout) ────────────

    def run_bb_expiry_scalper(self) -> BacktestResult:
        cfg = self.sc.get("bb_expiry_scalper", {})
        period      = cfg.get("bb_period", 20)
        nstd        = cfg.get("bb_std", 2.0)
        budget      = cfg.get("budget_rs", 10000)
        target_pct  = cfg.get("target_pct", 1.5)
        stop_pct    = cfg.get("stop_pct", 0.375)
        confirm     = cfg.get("confirm_bars", 2)
        max_trades  = cfg.get("max_trades_per_day", 3)
        close_str   = cfg.get("hard_close_time", "15:15")
        force_hour  = int(close_str[:2]) + int(close_str[3:]) / 60
        step        = self.sc.get("instruments", {}).get("nifty", {}).get("strike_step", 50)
        from statistics import pstdev, mean

        trades, daily_pnl, total = [], {}, 0.0
        current = self._intraday_start()
        while current <= self.end_date:
            expiry = get_nifty_weekly_expiry(current)
            if get_day_instrument(current) != "NIFTY" or current != expiry:
                current += timedelta(days=1); continue
            arr = self._intraday_arrays(current)
            if arr is None:
                current += timedelta(days=1); continue
            ts, o, h, l, c, v = arr
            above = below = 0
            day_trades, day_pnl, n = [], 0.0, 0
            i = period
            while i < len(ts) and n < max_trades:
                window = c[i - period:i]
                m, sd = mean(window), pstdev(window)
                upper, lower = m + nstd * sd, m - nstd * sd
                above = above + 1 if c[i] > upper else 0
                below = below + 1 if c[i] < lower else 0
                opt = "CE" if above >= confirm else ("PE" if below >= confirm else None)
                if opt:
                    tr, net = self._simulate_option_trade(
                        current, expiry, ts[i], c[i:], 15.0, opt,
                        budget=budget, target_pct=target_pct, stop_pct=stop_pct,
                        force_exit_hour=force_hour, window_id="BB", strike_step=step)
                    if tr:
                        day_trades.append(tr); day_pnl += net; n += 1
                        above = below = 0
                        i += max(1, tr.holding_minutes // 5); continue
                i += 1
            if day_trades:
                trades.extend(day_trades); daily_pnl[str(current)] = day_pnl; total += day_pnl
            current += timedelta(days=1)

        result = BacktestResult(trades=trades, daily_pnl=daily_pnl, daily_pnl_paper={},
                                total_pnl=round(total, 2), total_pnl_paper=0.0,
                                initial_capital=budget)
        result = self._compute_metrics(result)
        log.info(f"BB Expiry Scalper backtest | {len(daily_pnl)} expiry days | "
                 f"P&L {format_inr(result.total_pnl)} | win {result.win_rate:.1f}%")
        return result

    # ── Black Swan backtest (extreme-move momentum, one trade/day) ────────────

    def run_black_swan(self) -> BacktestResult:
        cfg = self.sc.get("black_swan", {})
        gap_pct     = cfg.get("gap_threshold_pct", 1.5) / 100
        intra_pct   = cfg.get("intraday_threshold_pct", 2.0) / 100
        vix_thr     = cfg.get("vix_threshold", 22)
        vix_pct     = cfg.get("vix_relaxed_pct", 1.2) / 100
        budget      = cfg.get("budget_rs", 20000)
        target_pct  = cfg.get("target_pct", 3.0)
        stop_pct    = cfg.get("stop_pct", 0.40)
        time_stop   = cfg.get("time_stop_minutes", 90)
        close_str   = cfg.get("hard_close_time", "15:20")
        force_hour  = int(close_str[:2]) + int(close_str[3:]) / 60
        step        = self.sc.get("instruments", {}).get("nifty", {}).get("strike_step", 50)
        vix_default = 15.0

        trades, daily_pnl, total = [], {}, 0.0
        prev_close = None
        current = self._intraday_start()
        while current <= self.end_date:
            if get_day_instrument(current) != "NIFTY":
                current += timedelta(days=1); continue
            arr = self._intraday_arrays(current)
            if arr is None:
                current += timedelta(days=1); continue
            ts, o, h, l, c, v = arr
            expiry = get_nifty_weekly_expiry(current)
            day_open = o[0]
            thr = vix_pct if vix_default >= vix_thr else gap_pct
            entry_i, opt = None, None
            # Trigger A: gap from previous close
            if prev_close:
                gap = (day_open - prev_close) / prev_close
                if abs(gap) >= thr:
                    entry_i = 0
                    opt = "CE" if gap > 0 else "PE"
            # Trigger B: intraday move from open
            if entry_i is None:
                for i in range(1, len(ts)):
                    move = (c[i] - day_open) / day_open
                    if abs(move) >= max(intra_pct, vix_pct if vix_default >= vix_thr else intra_pct):
                        entry_i = i
                        opt = "CE" if move > 0 else "PE"
                        break
            if entry_i is not None:
                # cap holding to the 90-min time stop window
                max_bars = entry_i + 1 + time_stop // 5
                path = c[entry_i:max_bars]
                tr, net = self._simulate_option_trade(
                    current, expiry, ts[entry_i], path, vix_default, opt,
                    budget=budget, target_pct=target_pct, stop_pct=stop_pct,
                    force_exit_hour=force_hour, window_id="BLACKSWAN", strike_step=step)
                if tr:
                    if tr.exit_reason == "END_OF_DATA":
                        tr.exit_reason = "TIME_STOP"
                    trades.append(tr); daily_pnl[str(current)] = net; total += net
            prev_close = c[-1]
            current += timedelta(days=1)

        result = BacktestResult(trades=trades, daily_pnl=daily_pnl, daily_pnl_paper={},
                                total_pnl=round(total, 2), total_pnl_paper=0.0,
                                initial_capital=budget)
        result = self._compute_metrics(result)
        log.info(f"Black Swan backtest | {len(daily_pnl)} event days | "
                 f"P&L {format_inr(result.total_pnl)} | win {result.win_rate:.1f}%")
        return result

    # ── Gap Fade backtest (fade a >0.5% gap that reverses in first 30 min) ────

    def run_gap_fade(self) -> BacktestResult:
        cfg = self.sc.get("gap_fade", {})
        gap_min   = cfg.get("gap_min_pct", 0.5) / 100
        confirm   = cfg.get("reversal_confirm_pct", 0.15) / 100
        budget    = cfg.get("trade_budget_rs", 10000)
        target    = cfg.get("target_pct", 0.15)
        stop      = cfg.get("stop_pct", 0.30)
        w_start   = cfg.get("window_start", "09:15")
        w_end     = cfg.get("window_end", "09:45")
        close_str = cfg.get("hard_close_time", "10:15")
        force_h   = int(close_str[:2]) + int(close_str[3:]) / 60
        ws = int(w_start[:2]) * 60 + int(w_start[3:])
        we = int(w_end[:2]) * 60 + int(w_end[3:])
        step = self.sc.get("instruments", {}).get("nifty", {}).get("strike_step", 50)

        trades, daily_pnl, total = [], {}, 0.0
        prev_close = None
        current = self._intraday_start()
        while current <= self.end_date:
            if get_day_instrument(current) != "NIFTY":
                current += timedelta(days=1); continue
            arr = self._intraday_arrays(current)
            if arr is None:
                current += timedelta(days=1); continue
            ts, o, h, l, c, v = arr
            expiry = get_nifty_weekly_expiry(current)
            day_open = o[0]
            if prev_close:
                gap = (day_open - prev_close) / prev_close
                if abs(gap) >= gap_min:
                    # look for a reversal back toward prev_close within the window
                    for i in range(len(ts)):
                        m = ts[i].hour * 60 + ts[i].minute
                        if m < ws or m > we:
                            continue
                        retrace = (day_open - c[i]) / day_open if gap > 0 else (c[i] - day_open) / day_open
                        if retrace >= confirm:
                            opt = "PE" if gap > 0 else "CE"   # fade the gap
                            tr, net = self._simulate_option_trade(
                                current, expiry, ts[i], c[i:], 15.0, opt,
                                budget=budget, target_pct=target, stop_pct=stop,
                                force_exit_hour=force_h, window_id="GAPFADE", strike_step=step)
                            if tr:
                                trades.append(tr); daily_pnl[str(current)] = net; total += net
                            break
            prev_close = c[-1]
            current += timedelta(days=1)

        result = BacktestResult(trades=trades, daily_pnl=daily_pnl, daily_pnl_paper={},
                                total_pnl=round(total, 2), total_pnl_paper=0.0,
                                initial_capital=budget)
        result = self._compute_metrics(result)
        log.info(f"Gap Fade backtest | {len(daily_pnl)} days | "
                 f"P&L {format_inr(result.total_pnl)} | win {result.win_rate:.1f}%")
        return result

    # ── Trend Rider backtest (new 30-min extreme + volume + RSI, hold all day) ─

    def run_trend_rider(self) -> BacktestResult:
        cfg = self.sc.get("trend_rider", {})
        start_after = cfg.get("start_after", "09:45")
        lookback    = cfg.get("extreme_lookback_min", 30) // 5   # 5-min bars
        vol_ratio   = cfg.get("min_volume_ratio", 1.3)
        rsi_ob      = cfg.get("rsi_overbought", 65)
        rsi_os      = cfg.get("rsi_oversold", 35)
        budget      = cfg.get("trade_budget_rs", 15000)
        target      = cfg.get("target_pct", 0.50)
        stop        = cfg.get("stop_pct", 0.10)
        close_str   = cfg.get("hard_close_time", "14:45")
        force_h     = int(close_str[:2]) + int(close_str[3:]) / 60
        sa = int(start_after[:2]) * 60 + int(start_after[3:])
        step = self.sc.get("instruments", {}).get("nifty", {}).get("strike_step", 50)

        def _rsi(closes):
            if len(closes) < 15:
                return 50.0
            g = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
            ls = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
            ag, al = sum(g[-14:]) / 14, sum(ls[-14:]) / 14
            return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

        trades, daily_pnl, total = [], {}, 0.0
        current = self._intraday_start()
        while current <= self.end_date:
            if get_day_instrument(current) != "NIFTY":
                current += timedelta(days=1); continue
            arr = self._intraday_arrays(current)
            if arr is None:
                current += timedelta(days=1); continue
            ts, o, h, l, c, v = arr
            expiry = get_nifty_weekly_expiry(current)
            for i in range(lookback, len(ts)):
                m = ts[i].hour * 60 + ts[i].minute
                if m < sa:
                    continue
                window_hi = max(h[i-lookback:i]); window_lo = min(l[i-lookback:i])
                vol_avg = sum(v[max(0, i-10):i]) / max(1, len(v[max(0, i-10):i]))
                rsi = _rsi(c[:i+1])
                opt = None
                if c[i] > window_hi and v[i] >= vol_ratio * vol_avg and rsi >= rsi_ob:
                    opt = "CE"
                elif c[i] < window_lo and v[i] >= vol_ratio * vol_avg and rsi <= rsi_os:
                    opt = "PE"
                if opt:
                    tr, net = self._simulate_option_trade(
                        current, expiry, ts[i], c[i:], 15.0, opt,
                        budget=budget, target_pct=target, stop_pct=stop,
                        force_exit_hour=force_h, window_id="TREND", strike_step=step)
                    if tr:
                        trades.append(tr); daily_pnl[str(current)] = net; total += net
                    break   # one trade/day, held all day
            current += timedelta(days=1)

        result = BacktestResult(trades=trades, daily_pnl=daily_pnl, daily_pnl_paper={},
                                total_pnl=round(total, 2), total_pnl_paper=0.0,
                                initial_capital=budget)
        result = self._compute_metrics(result)
        log.info(f"Trend Rider backtest | {len(daily_pnl)} days | "
                 f"P&L {format_inr(result.total_pnl)} | win {result.win_rate:.1f}%")
        return result

    # ── VIX Seller backtest (short ATM strangle on high-vol days) ─────────────
    #  SIMPLIFIED MODEL: high-vol days are gated by a prior-day true-range proxy
    #  (intraday VIX history is unavailable in the bar feed). Sells an ATM
    #  strangle at the open and buys it back on target decay / stop / EOD.

    def run_vix_seller(self) -> BacktestResult:
        cfg = self.sc.get("vix_seller", {})
        range_max   = cfg.get("range_max_pct", 1.0) / 100
        otm         = cfg.get("otm_strikes", 2)
        budget      = cfg.get("budget_rs", 50000)
        margin_lot  = cfg.get("margin_per_lot_rs", 90000)
        decay_tgt   = cfg.get("decay_target_pct", 30) / 100
        stop_pct    = cfg.get("stop_pct", 60) / 100
        lot         = self.sc.get("instruments", {}).get("nifty", {}).get("lot_size", 65)
        step        = self.sc.get("instruments", {}).get("nifty", {}).get("strike_step", 50)
        vix_high    = 24.0   # assumed IV on a high-vol day (for pricing)

        trades, daily_pnl, total = [], {}, 0.0
        prev_range = None
        current = self._intraday_start()
        while current <= self.end_date:
            if get_day_instrument(current) != "NIFTY":
                current += timedelta(days=1); continue
            arr = self._intraday_arrays(current)
            if arr is None:
                current += timedelta(days=1); continue
            ts, o, h, l, c, v = arr
            day_range = (max(h) - min(l)) / o[0] if o[0] else 0
            # high-vol day proxy: yesterday's range was large (>= range_max)
            if prev_range is not None and prev_range >= range_max:
                spot0 = o[0]
                ce_k = self.pricer.atm_strike(spot0, step) + otm * step
                pe_k = self.pricer.atm_strike(spot0, step) - otm * step
                T0 = self.pricer.hours_to_expiry(ts[0].hour, ts[0].minute)
                ce0 = self.pricer.price(spot0, ce_k, vix_high, T0, "CE").price
                pe0 = self.pricer.price(spot0, pe_k, vix_high, T0, "PE").price
                prem0 = ce0 + pe0
                if prem0 > 1:
                    lots = max(1, int(budget / margin_lot)); qty = lots * lot
                    tgt_val = prem0 * (1 - decay_tgt)
                    stop_val = prem0 * (1 + stop_pct)
                    exit_val, reason, ex_i = prem0, "EOD", len(ts) - 1
                    for i in range(1, len(ts)):
                        T = self.pricer.hours_to_expiry(ts[i].hour, ts[i].minute)
                        cev = self.pricer.price(c[i], ce_k, vix_high, max(T, 0.01), "CE").price
                        pev = self.pricer.price(c[i], pe_k, vix_high, max(T, 0.01), "PE").price
                        val = cev + pev
                        if val <= tgt_val:
                            exit_val, reason, ex_i = val, "DECAY_TARGET", i; break
                        if val >= stop_val:
                            exit_val, reason, ex_i = val, "STOP_LOSS", i; break
                        exit_val = val
                    gross = (prem0 - exit_val) * qty      # short: profit when premium falls
                    txn   = self._calculate_transaction_cost(prem0, exit_val, qty, "NSE")
                    net   = gross - txn
                    trades.append(BacktestTrade(
                        date=current, window_id="STRANGLE", instrument="NIFTY",
                        direction="NEUTRAL", option_type="SE", strike=self.pricer.atm_strike(spot0, step),
                        entry_price=round(prem0, 2), exit_price=round(exit_val, 2),
                        entry_time=f"{ts[0].hour:02d}:{ts[0].minute:02d}:00",
                        exit_time=f"{ts[ex_i].hour:02d}:{ts[ex_i].minute:02d}:00",
                        pnl_pct=round((prem0 - exit_val) / prem0 * 100, 2),
                        gross_pnl=round(gross, 2), transaction_cost=round(txn, 2),
                        pnl_rupees=round(net, 2), quantity=qty, lot_size=lot,
                        trade_budget=budget, exit_reason=reason,
                        holding_minutes=(ex_i) * 5, is_expiry=(current == get_nifty_weekly_expiry(current)),
                        is_paper=False))
                    daily_pnl[str(current)] = net; total += net
            prev_range = day_range
            current += timedelta(days=1)

        result = BacktestResult(trades=trades, daily_pnl=daily_pnl, daily_pnl_paper={},
                                total_pnl=round(total, 2), total_pnl_paper=0.0,
                                initial_capital=budget)
        result = self._compute_metrics(result)
        log.info(f"VIX Seller backtest | {len(daily_pnl)} high-vol days | "
                 f"P&L {format_inr(result.total_pnl)} | win {result.win_rate:.1f}%")
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
