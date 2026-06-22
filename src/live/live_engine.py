"""
Live / Paper Trading Engine
─────────────────────────────
Real-time counterpart to BacktestEngine.

Runs in two modes:
  paper — Uses PaperBroker (Black-Scholes pricing, no real orders).
           Prints signals to terminal for manual execution.
  live  — Uses KiteBroker (real Kite Connect orders, real LTP).

Architecture mirrors BacktestEngine:
  - Pre-market: evaluate Dow + Gift Nifty + VIX → sets budget only
    (NEVER gates the day; NEUTRAL score → min budget, day still runs)
  - Market hours: 5-min tick loop
    → slot scheduler (same OW0/T1/OW1/... slots as backtest)
    → per-candle intraday direction re-evaluation (can flip CE ↔ PE)
    → continuous re-entry within windows
    → off-window slots → paper trades regardless of mode
    → 15:20 force-close of all positions

Intraday direction logic:
  At each 5-min tick, evaluate_intraday() scores:
    Spot % chg from prev close  : -2 to +2
    Momentum vs day open        : -1 to +1
    VIX level                   : -2 to +1
  Score ≥ 2 → BULLISH, ≤ -2 → BEARISH, else NEUTRAL (skip candle)

Position sizing:
  qty = floor(budget / LTP / lot_size) × lot_size  (same as backtest)
  Budget scales with pre-market signal strength (Rs. 7L–15L).
"""

from __future__ import annotations
import time
import os
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from src.strategy.direction_engine import DirectionEngine, DirectionInputs, Direction
from src.strategy.exit_logic import ExitLogic, ExitReason
from src.strategy.position_manager import PositionManager, Trade
from src.broker.base import BaseBroker, Order
from src.backtest.option_pricer import OptionPricer
from src.data.market_data import (
    get_spot_price, get_india_vix, get_previous_close,
    get_dow_jones_change_pct, get_recent_1min_bars,
)
from src.data.candle_builder import Candle, compute_vwap, build_5min_from_1min
from src.data.gift_nifty import get_gift_nifty_premium
from src.data.option_chain import synthetic_option_data
from src.utils.market_calendar import (
    get_day_instrument,
    get_nifty_weekly_expiry,
    get_sensex_weekly_expiry,
    is_trading_day,
)
from src.utils.helpers import round_to_strike, format_inr
from src.utils.logger import setup_logger

log = setup_logger("live_engine")

IST = timezone(timedelta(hours=5, minutes=30))

# Slot definitions — identical to backtest engine
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


@dataclass
class LiveTrade:
    """Augments PositionManager.Trade with live-session metadata."""
    trade: Trade
    slot_id: str
    is_paper: bool
    direction: str
    option_type: str
    expiry: date
    exchange: str
    kite_order_id: str = ""
    peak_price: float = 0.0        # highest LTP seen while position open
    window_end_abs: int = 0        # window close time in minutes from midnight (e.g. 13*60+20=800 for T3)


@dataclass
class DayStats:
    instrument: str = ""
    expiry: date = field(default_factory=date.today)
    is_expiry: bool = False
    pre_market_direction: str = "NEUTRAL"
    pre_market_score: int = 0
    budget: float = 0.0
    spot_open: float = 0.0
    spot_prev: float = 0.0
    real_pnl: float = 0.0
    paper_pnl: float = 0.0
    day_stopped: bool = False


class LiveEngine:

    def __init__(self, strategy_config: dict, broker: BaseBroker, mode: str = "paper"):
        self.sc     = strategy_config
        self.broker = broker
        self.mode   = mode          # "paper" | "live"
        self.pricer = OptionPricer(
            risk_free_rate=strategy_config.get("backtest", {}).get("risk_free_rate", 0.065)
        )
        self.direction_engine = DirectionEngine(strategy_config)
        self.exit_logic       = ExitLogic(strategy_config)
        self.position_manager = PositionManager(strategy_config)

        bt = strategy_config.get("backtest", {})
        self.slippage_pct = bt.get("slippage_pct", 0.1) / 100

        inst_n = strategy_config.get("instruments", {}).get("nifty", {})
        self.nifty_lot_size    = inst_n.get("lot_size", 75)
        self.nifty_strike_step = inst_n.get("strike_step", 50)

        inst_s = strategy_config.get("instruments", {}).get("sensex", {})
        self.sensex_lot_size    = inst_s.get("lot_size", 20)
        self.sensex_strike_step = inst_s.get("strike_step", 100)

        ps = strategy_config.get("position_sizing", {})
        self.budget_min        = ps.get("trade_budget_min", 700_000)
        self.budget_max        = ps.get("trade_budget_max", 1_500_000)
        self.min_score_for_max = ps.get("min_score_for_max_budget", 5)

        risk = strategy_config.get("risk", {})
        self.daily_loss_limit  = risk.get("daily_loss_limit", 1_000_000)
        self.base_trades       = risk.get("base_trades_per_day", 10)
        self.per_trade_max_loss = self.daily_loss_limit / self.base_trades

        self.exit_target_pct   = strategy_config.get("exit", {}).get("profit_target_pct", 27.5) / 100
        self.max_vix           = strategy_config.get("option_chain", {}).get("max_vix_for_entry", 22.0)

        self.live_trades: list[LiveTrade] = []
        self.day: Optional[DayStats] = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _now_ist(self) -> datetime:
        return datetime.now(IST)

    def _signal_to_budget(self, score: int) -> float:
        t = max(0.0, (min(abs(score), self.min_score_for_max) - 3) / max(self.min_score_for_max - 3, 1))
        return self.budget_min + t * (self.budget_max - self.budget_min)

    def _calculate_quantity(self, budget: float, ltp: float, lot_size: int) -> int:
        if ltp < 0.5:
            return 0
        return max(1, int(budget / (ltp * lot_size))) * lot_size

    def _per_trade_stop_pct(self, entry_price: float, quantity: int) -> float:
        pv = entry_price * quantity
        return min(max(self.per_trade_max_loss / pv if pv > 0 else 0.15, 0.05), 0.30)

    def _T_hours(self, expiry: date, h: int, m: int) -> float:
        expiry_dt = datetime(expiry.year, expiry.month, expiry.day, 15, 30, tzinfo=IST)
        now_dt    = self._now_ist().replace(hour=h, minute=m, second=0, microsecond=0)
        return max((expiry_dt - now_dt).total_seconds() / 3600, 0.05)

    def _get_ltp(self, instrument: str, expiry: date, strike: int, opt_type: str) -> float:
        """Get current option LTP. Uses Black-Scholes in paper mode, Kite API in live mode."""
        if self.mode == "paper":
            spot = get_spot_price(instrument)
            vix  = get_india_vix()
            now  = self._now_ist()
            T_hrs = self._T_hours(expiry, now.hour, now.minute)
            opt_data = synthetic_option_data(spot, strike, opt_type, T_hrs, vix)
            return opt_data.ltp
        else:
            from src.broker.kite_broker import KiteBroker
            kb = self.broker
            if isinstance(kb, KiteBroker):
                symbol, exchange = kb.get_tradingsymbol(instrument, expiry, strike, opt_type)
                return kb.get_ltp(symbol, exchange, strike, opt_type, str(expiry))
            return 0.0

    def _get_current_slot(self, h: int, m: int, slots: list) -> Optional[tuple]:
        """Return (slot_id, sh, sm, eh, em, is_real) for the current time, or None."""
        abs_min = h * 60 + m
        for sid, (sh, sm), (eh, em), is_real in slots:
            if sh * 60 + sm <= abs_min < eh * 60 + em:
                return sid, sh, sm, eh, em, is_real
        return None

    def _sleep_to_next_1min(self) -> None:
        """Sleep until the next 1-minute boundary + 3 seconds (candle close buffer)."""
        now  = self._now_ist()
        wake = now.replace(second=0, microsecond=0) + timedelta(minutes=1, seconds=3)
        sleep_secs = max((wake - now).total_seconds(), 3)
        log.debug(f"Sleeping {sleep_secs:.0f}s until {wake.strftime('%H:%M:%S')}")
        time.sleep(sleep_secs)

    def _fetch_1min_bars(self, instrument: str) -> list[Candle]:
        """
        Fetch today's completed 1-min bars.
        Live mode: Kite historical API (real-time, no delay).
        Paper mode: yfinance (up to ~15-min delay but sufficient for paper).
        """
        if self.mode == "live":
            from src.broker.kite_broker import KiteBroker
            if isinstance(self.broker, KiteBroker):
                bars = self.broker.get_1min_bars(instrument, n=60)
                if bars:
                    return bars
        return get_recent_1min_bars(instrument, n=60)

    def _print_banner(self, day: DayStats) -> None:
        lines = [
            "",
            "═" * 65,
            f"  Sa-Ra-L  |  {day.instrument}  |  "
            f"{date.today().strftime('%A, %d %b %Y')}",
            f"  Expiry   : {day.expiry}  "
            f"{'[EXPIRY DAY]' if day.is_expiry else ''}",
            f"  Mode     : {self.mode.upper()}",
            f"  Direction: {day.pre_market_direction}  "
            f"(score={day.pre_market_score})",
            f"  Budget   : Rs.{day.budget/1e5:.1f}L",
            f"  Prev Close: {day.spot_prev:.2f}",
            "═" * 65,
            "",
        ]
        print("\n".join(lines))

    def _print_tick(self, h: int, m: int, spot: float, vix: float, vwap: float,
                    slot_id: str, intra_dir: Direction, is_real: bool,
                    reason: str = "") -> None:
        tag = "[REAL]" if is_real else "[PAPER]"
        open_count = len(self.position_manager.open_trades)
        vwap_side  = "▲" if spot >= vwap else "▼"
        print(
            f"  {h:02d}:{m:02d}  {slot_id} {tag:8s}  "
            f"Spot={spot:.0f} {vwap_side}VWAP={vwap:.0f}  VIX={vix:.1f}  "
            f"Dir={intra_dir.value:8s}  "
            f"Pos={open_count}  "
            f"PnL={format_inr(self.position_manager.realised_pnl)}"
            + (f"\n         └ {reason}" if reason and intra_dir != Direction.NEUTRAL else "")
        )

    # ── Pre-market ────────────────────────────────────────────────────────────

    def _pre_market_analysis(self) -> Optional[DayStats]:
        """
        Evaluate pre-market signals. Returns DayStats or None only if today is
        not a trading day (Wednesday or market holiday). Pre-market direction
        NEVER gates the day — it only sets trade budget (min budget if NEUTRAL).
        """
        today = date.today()
        instrument = get_day_instrument(today)

        if not instrument:
            log.info(f"{today} is not a trading day or is Wednesday — skipping")
            return None

        expiry     = get_nifty_weekly_expiry(today) if instrument == "NIFTY" \
                     else get_sensex_weekly_expiry(today)
        is_expiry  = (today == expiry) or (instrument == "SENSEX")
        spot_prev  = get_previous_close(instrument)
        vix        = get_india_vix()
        if vix == 0.0:
            import time as _time; _time.sleep(3)
            vix = get_india_vix()
        if vix == 0.0:
            vix = 15.0   # neutral assumption if VIX still unavailable
            log.warning("VIX fetch failed — defaulting to 15.0 (neutral)")

        dow_chg = get_dow_jones_change_pct()
        log.info(f"Dow Jones change: {dow_chg:+.2f}%")

        log.info("Fetching Gift Nifty premium...")
        gift_prem = get_gift_nifty_premium()
        if gift_prem == 0.0:
            # Scraping sources unavailable — use DOW as proxy (same as backtest)
            gift_prem = spot_prev * dow_chg * 0.6 / 100
            log.info(f"Gift Nifty unavailable — DOW proxy: {gift_prem:+.1f} pts")
        else:
            log.info(f"Gift Nifty premium: {gift_prem:+.1f} pts")

        dir_inputs = DirectionInputs(
            dow_change_pct=dow_chg,
            gift_nifty_premium=gift_prem,
            india_vix=vix,
            sensex_change_pct=dow_chg * 0.55,
            nifty_prev_close=spot_prev,
        )
        dir_result = self.direction_engine.evaluate(dir_inputs)

        # Pre-market NEUTRAL → trade with min budget; day still runs
        budget = self._signal_to_budget(dir_result.score)  # returns budget_min if neutral
        log.info(
            f"Pre-market: {dir_result.direction.value} (score={dir_result.score})  "
            f"Budget=Rs.{budget:,.0f}"
        )

        if vix > self.max_vix:
            log.warning(f"VIX {vix:.1f} > max {self.max_vix} — all real-slot entries will be paper")

        day = DayStats(
            instrument=instrument,
            expiry=expiry,
            is_expiry=is_expiry,
            pre_market_direction=dir_result.direction.value,
            pre_market_score=dir_result.score,
            budget=budget,
            spot_prev=spot_prev,
        )
        return day

    # ── Entry logic ───────────────────────────────────────────────────────────

    def _try_enter(
        self,
        day: DayStats,
        slot_id: str,
        eh: int, em: int,
        intra_dir: Direction,
        spot: float,
        vix: float,
        is_real_slot: bool,
    ) -> None:
        """Attempt to open a position if all entry conditions are met."""
        if not day or intra_dir == Direction.NEUTRAL:
            return
        if self.position_manager.open_trades:
            return  # Position already open — wait for exit

        # VIX gate: high volatility → paper only (market too unpredictable).
        # CE vs PE is decided by live intraday conditions at THIS moment,
        # independent of pre-market direction.
        high_vix = vix > self.max_vix
        if high_vix:
            log.info(f"[{slot_id}] VIX {vix:.1f} > {self.max_vix} — paper only")

        is_paper = (not is_real_slot) or day.day_stopped or high_vix

        lot_size    = self.nifty_lot_size    if day.instrument == "NIFTY" else self.sensex_lot_size
        strike_step = self.nifty_strike_step if day.instrument == "NIFTY" else self.sensex_strike_step
        opt_type    = "CE" if intra_dir == Direction.BULLISH else "PE"
        atm         = round_to_strike(spot, strike_step)

        ltp = self._get_ltp(day.instrument, day.expiry, atm, opt_type)
        if ltp < 0.5:
            log.info(f"[{slot_id}] LTP ₹{ltp:.2f} too low — skip")
            return

        qty = self._calculate_quantity(day.budget, ltp, lot_size)
        if qty == 0:
            log.info(f"[{slot_id}] Qty=0 at LTP ₹{ltp:.2f} — skip")
            return

        entry_price = ltp * (1 + self.slippage_pct)
        exchange    = "NFO" if day.instrument == "NIFTY" else "BFO"
        kite_order_id = ""

        if not is_paper and self.mode == "live":
            from src.broker.kite_broker import KiteBroker
            if isinstance(self.broker, KiteBroker):
                symbol, exch = self.broker.get_tradingsymbol(
                    day.instrument, day.expiry, atm, opt_type
                )
                order = Order(
                    symbol=symbol, exchange=exch,
                    option_type=opt_type, strike=atm,
                    expiry=str(day.expiry),
                    transaction="BUY", quantity=qty,
                    price=ltp,
                )
                kite_order_id = self.broker.place_order(order)

        trade = self.position_manager.open_trade(
            window_id=slot_id,
            symbol=day.instrument,
            option_type=opt_type,
            strike=atm,
            quantity=qty,
            lot_size=lot_size,
            entry_price=entry_price,
        )

        live_trade = LiveTrade(
            trade=trade,
            slot_id=slot_id,
            is_paper=is_paper,
            direction=intra_dir.value,
            option_type=opt_type,
            expiry=day.expiry,
            exchange=exchange,
            kite_order_id=kite_order_id,
            peak_price=entry_price,
            window_end_abs=eh * 60 + em,
        )
        self.live_trades.append(live_trade)

        tag = "[PAPER]" if is_paper else "[LIVE]"
        print(
            f"\n  ▶ ENTRY {tag}  {slot_id}  {intra_dir.value}  "
            f"{day.instrument} {atm}{opt_type}  "
            f"Qty={qty}  LTP=₹{ltp:.2f}  "
            f"Fill=₹{entry_price:.2f}  "
            f"Target=₹{ltp*(1+self.exit_target_pct):.2f}"
        )

    # ── Exit logic ────────────────────────────────────────────────────────────

    def _try_exit(self, day: DayStats, force_close: bool = False) -> None:
        """Check all open positions for exit conditions."""
        for lt in [x for x in self.live_trades if x.trade.status.value == "OPEN"]:
            trade = lt.trade
            ltp = self._get_ltp(day.instrument, lt.expiry, trade.strike, lt.option_type)
            if ltp <= 0:
                continue

            lt.peak_price = max(lt.peak_price, ltp)
            stop_pct      = self._per_trade_stop_pct(trade.entry_price, trade.quantity)

            exit_signal = self.exit_logic.check_exit(
                entry_price=trade.entry_price,
                current_price=ltp,
                quantity=trade.quantity,
                lot_size=trade.quantity // max(1, trade.lots) if hasattr(trade, "lots") else self.nifty_lot_size,
                cumulative_day_pnl=self.position_manager.realised_pnl,
                peak_trade_pnl=max(0, (lt.peak_price - trade.entry_price) * trade.quantity),
                force_close=force_close,
            )

            # Also check per-trade stop loss (not in ExitLogic, computed here)
            if not exit_signal.should_exit:
                price_chg_pct = (ltp - trade.entry_price) / trade.entry_price
                if price_chg_pct <= -stop_pct:
                    exit_signal.should_exit   = True
                    exit_signal.reason        = ExitReason.FORCE_CLOSE
                    exit_signal.exit_price    = ltp
                    exit_signal.description   = f"STOP_LOSS {price_chg_pct*100:.1f}%"

            if not exit_signal.should_exit:
                continue

            exit_price = exit_signal.exit_price * (1 - self.slippage_pct)
            gross_pnl  = (exit_price - trade.entry_price) * trade.quantity

            if not lt.is_paper and self.mode == "live":
                from src.broker.kite_broker import KiteBroker
                if isinstance(self.broker, KiteBroker) and lt.kite_order_id:
                    self.broker.place_bracket_order_exit(lt.kite_order_id, exit_price)

            self.position_manager.close_trade(trade, exit_price, exit_signal.reason.value)

            if lt.is_paper:
                day.paper_pnl += gross_pnl
            else:
                day.real_pnl  += gross_pnl
                if day.real_pnl <= -self.daily_loss_limit:
                    day.day_stopped = True

            tag = "[PAPER]" if lt.is_paper else "[LIVE]"
            print(
                f"\n  ◀ EXIT  {tag}  {lt.slot_id}  "
                f"{day.instrument} {trade.strike}{lt.option_type}  "
                f"Exit=₹{exit_price:.2f}  "
                f"P&L={format_inr(gross_pnl)}  "
                f"({exit_signal.description})"
            )

            if day.day_stopped:
                log.warning(
                    f"Daily loss limit Rs.{self.daily_loss_limit/1e5:.0f}L hit — "
                    "all further trades will be PAPER"
                )

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """
        Full day runner — 1-min candle resolution.
          Pre-market  → direction + budget
          9:15–15:30  → 1-min loop: fetch bars → VWAP → evaluate_1min → entry/exit
        Data source:
          Live mode : Kite historical API (real-time 1-min)
          Paper mode: yfinance 1-min (slight delay, fine for paper)
        """
        print(f"\nSa-Ra-L Live Engine starting — Mode: {self.mode.upper()}")
        print(f"Date: {date.today()}  Time: {self._now_ist().strftime('%H:%M IST')}\n")

        log.info("Running pre-market analysis...")
        day = self._pre_market_analysis()
        if day is None:
            print("No trading today. Exiting.")
            return

        self.day = day
        self._print_banner(day)
        slots = _SLOTS_EXPIRY if day.is_expiry else _SLOTS_NORMAL
        spot_open_set = False

        try:
            while True:
                now = self._now_ist()
                h, m = now.hour, now.minute

                # ── EOD ───────────────────────────────────────────────────────
                if h > 15 or (h == 15 and m >= 30):
                    log.info("Market closed (15:30). Day complete.")
                    break

                # ── 15:20 force close ─────────────────────────────────────────
                if h == 15 and m >= 20:
                    if self.position_manager.open_trades:
                        log.info("15:20 — squaring off all positions")
                        self._try_exit(day, force_close=True)
                    self._sleep_to_next_1min()
                    continue

                # ── Pre-open wait ─────────────────────────────────────────────
                if h < 9 or (h == 9 and m < 15):
                    self._sleep_to_next_1min()
                    continue

                # ── Fetch 1-min bars ──────────────────────────────────────────
                bars_1m = self._fetch_1min_bars(day.instrument)
                if not bars_1m:
                    log.warning("No 1-min bars — retrying next minute")
                    self._sleep_to_next_1min()
                    continue

                # ── Record day open ───────────────────────────────────────────
                if not spot_open_set:
                    day.spot_open = bars_1m[0].open
                    spot_open_set = True
                    log.info(f"Day open: {day.instrument}={day.spot_open:.2f}")

                # ── Compute VWAP and current spot ─────────────────────────────
                vwap = compute_vwap(bars_1m)
                spot = bars_1m[-1].close
                vix  = get_india_vix() or 15.0

                # ── Build 5-min bars ──────────────────────────────────────────
                bars_5m = build_5min_from_1min(bars_1m)

                # ── Current slot ──────────────────────────────────────────────
                slot = self._get_current_slot(h, m, slots)

                # ── Window-end force close ────────────────────────────────────
                current_abs = h * 60 + m
                for lt in [x for x in self.live_trades
                           if x.trade.status.value == "OPEN"]:
                    if lt.window_end_abs and current_abs >= lt.window_end_abs:
                        log.info(
                            f"[{lt.slot_id}] Window closed — force-exiting position"
                        )
                        self._try_exit(day, force_close=True)
                        break

                # ── Target / stop monitoring ──────────────────────────────────
                self._try_exit(day, force_close=False)

                if not slot:
                    self._sleep_to_next_1min()
                    continue

                slot_id, sh, sm, eh, em, is_real = slot

                # ── 1-min direction evaluation ────────────────────────────────
                dir_result = self.direction_engine.evaluate_1min(
                    bars_1m, bars_5m, vix,
                    day.spot_open, day.spot_prev, vwap,
                )

                self._print_tick(
                    h, m, spot, vix, vwap, slot_id,
                    dir_result.direction, is_real, dir_result.reason,
                )

                # ── Entry attempt ─────────────────────────────────────────────
                if not self.position_manager.open_trades:
                    self._try_enter(
                        day, slot_id, eh, em,
                        dir_result.direction, spot, vix, is_real,
                    )

                self._sleep_to_next_1min()

        except KeyboardInterrupt:
            print("\n\nStopped by user.")
            if self.position_manager.open_trades:
                print("Force-closing open positions...")
                self._try_exit(day, force_close=True)

        self._print_eod_summary(day)

    # ── End of day ────────────────────────────────────────────────────────────

    def _print_eod_summary(self, day: DayStats) -> None:
        real_trades  = [lt for lt in self.live_trades if not lt.is_paper]
        paper_trades = [lt for lt in self.live_trades if lt.is_paper]
        closed_real  = [lt for lt in real_trades  if lt.trade.status.value == "CLOSED"]

        print(f"\n{'═'*65}")
        print(f"  EOD Summary — {date.today()}  |  {day.instrument}")
        print(f"{'═'*65}")
        print(f"  Pre-market direction : {day.pre_market_direction} (score={day.pre_market_score})")
        print(f"  Real trades          : {len(real_trades)}")
        print(f"  Paper trades         : {len(paper_trades)}")
        print(f"  Real P&L (gross)     : {format_inr(day.real_pnl)}")
        print(f"  Paper P&L (gross)    : {format_inr(day.paper_pnl)}")

        if closed_real:
            wins = [lt for lt in closed_real if lt.trade.pnl > 0]
            print(f"  Win rate (real)      : {len(wins)/len(closed_real)*100:.0f}%")

        if day.day_stopped:
            print(f"  ⚠  Daily loss limit hit — trading stopped early")
        print(f"{'═'*65}\n")
