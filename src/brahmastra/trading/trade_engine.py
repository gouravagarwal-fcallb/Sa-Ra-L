"""
BRAHMASTRA Trade Engine — Phase 3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Converts CONFIRMED scenarios into real/paper trades.

Responsibilities:
  1. Entry gates (circuit-breakers before placing any order)
  2. Kelly Criterion position sizing (half-Kelly, max 15% capital)
  3. Order placement + fill confirmation
  4. Trailing SL management (ATR-based ratchet)
  5. Partial booking at T1/T2 (50%/25% of position)
  6. Full exit at T3 or SL
  7. EOD forced exit if position still open at 3:20 PM IST

Entry gates (ALL must pass):
  G1. Confidence ≥ 85% for 2 consecutive bars
  G2. ATR available and meaningful (> 0)
  G3. No existing ACTIVE scenario for same instrument (no pyramid)
  G4. Capital available (free margin ≥ required margin)
  G5. Time gate: between 9:20 AM and 3:10 PM IST (no late entries)
  G6. VIX gate: India VIX ≤ max_vix (default 22)
  G7. Mode gate: shadow/alert_only modes → log but do not place order

Trailing SL:
  - After T1 hit: SL moves to entry (free trade)
  - After T2 hit: SL moves to T1
  - ATR-based trail: SL = high_since_entry − 2×ATR (bull)
                           low_since_entry  + 2×ATR (bear)
  - Always take the more protective of the two
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable
from enum import Enum

from src.brahmastra.scenarios.scenario_engine import Scenario, ScenarioState

IST = timezone(timedelta(hours=5, minutes=30))


class TradeState(str, Enum):
    PENDING  = "PENDING"   # order placed, awaiting fill
    OPEN     = "OPEN"      # filled, position live
    PARTIAL  = "PARTIAL"   # T1/T2 partially booked
    CLOSED   = "CLOSED"    # fully exited


@dataclass
class TradeRecord:
    trade_id:        str
    scenario_id:     int
    instrument:      str
    hypothesis:      str
    option_type:     str          # 'CE' | 'PE'
    strike:          int
    expiry:          str
    quantity:        int
    lots:            int
    lot_size:        int
    entry_price:     Optional[float] = None
    exit_price:      Optional[float] = None
    sl_price:        Optional[float] = None
    target1:         Optional[float] = None
    target2:         Optional[float] = None
    target3:         Optional[float] = None
    trailing_sl:     Optional[float] = None
    current_price:   Optional[float] = None
    unrealised_pnl:  float = 0.0
    realised_pnl:    float = 0.0
    booked_qty:      int   = 0
    state:           TradeState = TradeState.PENDING
    buy_order_id:    Optional[str] = None
    sell_order_id:   Optional[str] = None
    opened_at:       Optional[datetime] = None
    closed_at:       Optional[datetime] = None
    exit_reason:     str = ""
    high_since_entry: float = 0.0
    low_since_entry:  float = 1e9
    capital_used:     float = 0.0
    confidence_at_entry: float = 0.0
    theta_per_min:    float = 0.0
    action_rec:       str   = "HOLD"

    @property
    def is_open(self) -> bool:
        return self.state in (TradeState.OPEN, TradeState.PARTIAL, TradeState.PENDING)

    @property
    def net_pnl(self) -> float:
        return self.realised_pnl + self.unrealised_pnl

    def summary_line(self) -> str:
        pnl = f"P&L={self.net_pnl:+.0f}" if self.net_pnl != 0 else ""
        sl  = f"SL={self.trailing_sl or self.sl_price:.0f}" if (self.trailing_sl or self.sl_price) else ""
        return (f"  TRADE {self.trade_id} | {self.hypothesis} "
                f"{self.instrument} {self.strike}{self.option_type} "
                f"qty={self.quantity} entry={self.entry_price or '?':.2f} "
                f"{sl}  {pnl}  [{self.state.value}]")


def _kelly_lots(
    win_prob: float,
    avg_win_r: float,    # avg win in multiples of risk
    avg_loss_r: float,   # avg loss in multiples of risk (usually 1.0)
    capital: float,
    risk_per_lot: float, # Rs. at risk per 1 lot (SL distance × lot_size)
    max_capital_pct: float = 0.15,
    half_kelly: bool = True,
) -> int:
    """
    Half-Kelly position sizing.
    Returns number of lots to trade.

    Kelly% = (p×b − q) / b   where b=win/loss ratio, p=win prob, q=1-p
    """
    if risk_per_lot <= 0 or capital <= 0:
        return 1

    q = 1 - win_prob
    b = avg_win_r / avg_loss_r if avg_loss_r > 0 else avg_win_r
    kelly = (win_prob * b - q) / b if b > 0 else 0

    if kelly <= 0:
        return 1

    if half_kelly:
        kelly *= 0.5

    max_risk = capital * min(kelly, max_capital_pct)
    lots     = int(max_risk / risk_per_lot)
    return max(1, lots)


class EntryGate:
    """
    Stateless gate checker. All gates must return True for entry.
    Returns (allowed: bool, reason: str).
    """

    @staticmethod
    def check(
        scenario:        Scenario,
        current_price:   float,
        atr_value:       Optional[float],
        india_vix:       Optional[float],
        capital_free:    float,
        margin_required: float,
        mode:            str,          # 'live'|'paper'|'shadow'|'alert_only'
        max_vix:         float = 22.0,
        now:             Optional[datetime] = None,
        existing_active: bool = False,
    ) -> tuple[bool, str]:

        if now is None:
            now = datetime.now(IST)

        # G1: Confidence
        if not scenario.is_entry_ready:
            return False, f"G1 FAIL: not CONFIRMED (state={scenario.state.value})"

        # G2: ATR
        if not atr_value or atr_value <= 0:
            return False, "G2 FAIL: ATR not available"

        # G3: No pyramid
        if existing_active:
            return False, "G3 FAIL: position already active for this instrument"

        # G4: Capital
        if capital_free < margin_required:
            return False, f"G4 FAIL: insufficient capital free={capital_free:.0f} need={margin_required:.0f}"

        # G5: Time
        hm = now.hour * 60 + now.minute
        if hm < 9 * 60 + 20:
            return False, "G5 FAIL: too early (before 9:20 AM)"
        if hm > 15 * 60 + 10:
            return False, "G5 FAIL: too late (after 3:10 PM)"

        # G6: VIX
        if india_vix and india_vix > max_vix:
            return False, f"G6 FAIL: VIX={india_vix:.1f} > max={max_vix}"

        # G7: Mode
        if mode in ("shadow", "alert_only"):
            return False, f"G7 SKIP: mode={mode} — logged but no order"

        return True, "ALL GATES PASS"


class TradeEngine:
    """
    Manages the full lifecycle of all trades for one instrument.

    Usage:
        engine = TradeEngine(instrument="NIFTY", lot_size=75, config=config, broker=broker)
        engine.on_scenario_confirmed(scenario, bar)
        engine.on_tick(price, atr, india_vix, now)
        engine.force_exit_all()   # called at 3:20 PM
    """

    def __init__(
        self,
        instrument:   str,
        lot_size:     int,
        config:       dict,
        broker,
        mode:         str = "paper",
        on_trade_event: Optional[Callable] = None,
        log = None,
    ):
        self.instrument   = instrument
        self.lot_size     = lot_size
        self._config      = config
        self._broker      = broker
        self._mode        = mode
        self._on_event    = on_trade_event
        self._log         = log

        self._trades:      dict[str, TradeRecord] = {}
        self._trade_count: int = 0
        self._session_pnl: float = 0.0

        tc  = config.get("trading", {})
        self._max_vix     = tc.get("max_vix_for_entry", 22.0)
        self._sl_atr_mult = tc.get("sl_atr_multiplier", 2.0)
        self._max_lots    = tc.get("max_lots_per_trade", 3)
        self._book_t1_pct = tc.get("book_at_t1_pct", 0.50)
        self._book_t2_pct = tc.get("book_at_t2_pct", 0.25)

    def _new_trade_id(self) -> str:
        self._trade_count += 1
        ts = datetime.now(IST).strftime("%H%M%S")
        return f"B{ts}{self._trade_count:03d}"

    def _log_msg(self, level: str, msg: str) -> None:
        if self._log:
            fn = getattr(self._log, level, None)
            if fn:
                fn(msg)

    def on_scenario_confirmed(
        self,
        scenario:      Scenario,
        current_price: float,
        atr_value:     float,
        india_vix:     Optional[float],
        capital_free:  float,
        strike:        int,
        expiry:        str,
        now:           Optional[datetime] = None,
    ) -> Optional[TradeRecord]:
        """
        Called when a scenario reaches CONFIRMED state.
        Runs entry gates; if all pass, places the order.
        Returns the TradeRecord or None if gates blocked entry.
        """
        if now is None:
            now = datetime.now(IST)

        existing_active = any(
            t.is_open for t in self._trades.values()
            if t.instrument == self.instrument
        )

        margin_per_lot   = current_price * self.lot_size * 0.15   # approx 15% margin
        win_prob         = scenario.confidence / 100
        risk_per_lot_sl  = abs(current_price - (scenario.sl or current_price * 0.98))
        risk_per_lot_rs  = risk_per_lot_sl * self.lot_size
        lots             = _kelly_lots(
            win_prob=win_prob,
            avg_win_r=2.0,
            avg_loss_r=1.0,
            capital=capital_free,
            risk_per_lot=risk_per_lot_rs,
            max_capital_pct=self._config.get("trading", {}).get("max_capital_pct", 0.15),
        )
        lots = min(lots, self._max_lots)
        qty  = lots * self.lot_size

        allowed, gate_reason = EntryGate.check(
            scenario        = scenario,
            current_price   = current_price,
            atr_value       = atr_value,
            india_vix       = india_vix,
            capital_free    = capital_free,
            margin_required = margin_per_lot * lots,
            mode            = self._mode,
            max_vix         = self._max_vix,
            now             = now,
            existing_active = existing_active,
        )

        option_type = "CE" if scenario.hypothesis == "BULL" else "PE"
        trade_id    = self._new_trade_id()

        self._log_msg("gate",
            f"GATE CHECK | {trade_id} {self.instrument} {strike}{option_type} "
            f"lots={lots} qty={qty} | {gate_reason}")

        if not allowed:
            return None

        # Place order
        record = TradeRecord(
            trade_id   = trade_id,
            scenario_id = scenario.id,
            instrument = self.instrument,
            hypothesis = scenario.hypothesis,
            option_type = option_type,
            strike     = strike,
            expiry     = expiry,
            quantity   = qty,
            lots       = lots,
            lot_size   = self.lot_size,
            sl_price   = scenario.sl,
            target1    = scenario.target1,
            target2    = scenario.target2,
            target3    = scenario.target3,
            trailing_sl = scenario.sl,
            capital_used = margin_per_lot * lots,
            confidence_at_entry = scenario.confidence,
            opened_at  = now,
        )

        try:
            from src.broker.base import Order
            order = Order(
                symbol      = self.instrument,
                exchange    = "NFO",
                option_type = option_type,
                strike      = strike,
                expiry      = expiry,
                transaction = "BUY",
                quantity    = qty,
                product     = "MIS",
                order_type  = "MARKET",
            )
            order_id = self._broker.place_order(order)
            record.buy_order_id = str(order_id)
            record.state        = TradeState.PENDING

            self._log_msg("order",
                f"BUY PLACED | {trade_id} order_id={order_id} "
                f"{self.instrument} {strike}{option_type} "
                f"qty={qty} lots={lots} | conf={scenario.confidence:.1f}%")

        except Exception as e:
            self._log_msg("order", f"BUY FAILED | {trade_id} — {e}")
            return None

        # Confirm fill (poll up to 30 seconds)
        fill_price = self._poll_fill(record.buy_order_id, timeout=30)
        if fill_price:
            record.entry_price = fill_price
            record.high_since_entry = fill_price
            record.low_since_entry  = fill_price
            record.state = TradeState.OPEN
            scenario.mark_active(fill_price, atr_value)
            self._log_msg("trade",
                f"BUY FILLED | {trade_id} @ Rs.{fill_price:.2f} "
                f"SL={record.sl_price:.0f} T1={record.target1:.0f} "
                f"T2={record.target2:.0f} T3={record.target3:.0f}")
        else:
            record.state = TradeState.PENDING
            self._log_msg("order", f"FILL TIMEOUT | {trade_id} — monitoring continues")

        self._trades[trade_id] = record
        if self._on_event:
            self._on_event("ENTRY", record)
        return record

    def _poll_fill(self, order_id: str, timeout: int = 30) -> Optional[float]:
        import time
        for _ in range(timeout):
            try:
                orders = self._broker.orders()
                for o in orders:
                    oid = str(o.get("order_id") or o.get("id") or "")
                    if oid == str(order_id):
                        if (o.get("status") or "").upper() == "COMPLETE":
                            return float(o.get("average_price") or o.get("price") or 0)
            except Exception:
                pass
            time.sleep(1)
        return None

    def on_tick(
        self,
        price:       float,
        atr_value:   Optional[float],
        india_vix:   Optional[float] = None,
        now:         Optional[datetime] = None,
    ) -> None:
        """
        Called on every price tick for open trades.
        Updates unrealised P&L, trailing SL, and checks exit triggers.
        """
        if now is None:
            now = datetime.now(IST)

        for trade_id, trade in list(self._trades.items()):
            if not trade.is_open or trade.state == TradeState.PENDING:
                continue
            if trade.entry_price is None:
                continue

            trade.current_price = price

            # Track high/low since entry
            if price > trade.high_since_entry:
                trade.high_since_entry = price
            if price < trade.low_since_entry:
                trade.low_since_entry = price

            # Unrealised P&L
            remaining_qty = trade.quantity - trade.booked_qty
            if trade.hypothesis == "BULL":
                trade.unrealised_pnl = (price - trade.entry_price) * remaining_qty
            else:
                trade.unrealised_pnl = (trade.entry_price - price) * remaining_qty

            # Trailing SL update (ATR-based ratchet)
            if atr_value:
                if trade.hypothesis == "BULL":
                    new_trail = round(trade.high_since_entry - 2 * atr_value, 2)
                    if trade.trailing_sl is None or new_trail > trade.trailing_sl:
                        trade.trailing_sl = new_trail
                else:
                    new_trail = round(trade.low_since_entry + 2 * atr_value, 2)
                    if trade.trailing_sl is None or new_trail < trade.trailing_sl:
                        trade.trailing_sl = new_trail

            active_sl = trade.trailing_sl or trade.sl_price

            # SL hit check
            sl_hit = (
                (trade.hypothesis == "BULL" and active_sl and price <= active_sl) or
                (trade.hypothesis == "BEAR" and active_sl and price >= active_sl)
            )
            if sl_hit:
                self._exit_trade(trade, price, "SL_HIT", now)
                continue

            # Target checks — partial booking
            if (trade.target1 and trade.booked_qty == 0 and
                    ((trade.hypothesis == "BULL" and price >= trade.target1) or
                     (trade.hypothesis == "BEAR" and price <= trade.target1))):
                book_qty = int(trade.quantity * self._book_t1_pct)
                self._partial_book(trade, price, book_qty, "T1", now)
                # Move SL to entry after T1
                trade.trailing_sl = trade.entry_price

            elif (trade.target2 and trade.booked_qty == int(trade.quantity * self._book_t1_pct) and
                      ((trade.hypothesis == "BULL" and price >= trade.target2) or
                       (trade.hypothesis == "BEAR" and price <= trade.target2))):
                book_qty = int(trade.quantity * self._book_t2_pct)
                self._partial_book(trade, price, book_qty, "T2", now)
                trade.trailing_sl = trade.target1

            elif (trade.target3 and
                      ((trade.hypothesis == "BULL" and price >= trade.target3) or
                       (trade.hypothesis == "BEAR" and price <= trade.target3))):
                self._exit_trade(trade, price, "T3_HIT", now)
                continue

            # Update theta and action recommendation on each tick
            eod_min = 15 * 60 + 20
            now_min = now.hour * 60 + now.minute
            minutes_to_expiry = max(float(eod_min - now_min), 1.0)
            trade.theta_per_min = self.theta_per_minute(trade, price, minutes_to_expiry)

    def _partial_book(
        self, trade: TradeRecord, price: float,
        qty: int, label: str, now: datetime
    ) -> None:
        if qty <= 0:
            return
        pnl = (price - trade.entry_price) * qty if trade.hypothesis == "BULL" \
              else (trade.entry_price - price) * qty
        trade.booked_qty    += qty
        trade.realised_pnl  += pnl
        trade.state          = TradeState.PARTIAL
        self._session_pnl   += pnl
        self._log_msg("trade",
            f"PARTIAL BOOK | {trade.trade_id} {label} @ Rs.{price:.2f} "
            f"qty={qty} pnl=Rs.{pnl:+.0f} | total_booked={trade.booked_qty}/{trade.quantity}")

        try:
            from src.broker.base import Order
            order = Order(
                symbol      = trade.instrument,
                exchange    = "NFO",
                option_type = trade.option_type,
                strike      = trade.strike,
                expiry      = trade.expiry,
                transaction = "SELL",
                quantity    = qty,
                product     = "MIS",
                order_type  = "MARKET",
            )
            order_id = self._broker.place_order(order)
            self._log_msg("order",
                f"PARTIAL SELL PLACED | {trade.trade_id} order_id={order_id} qty={qty}")
        except Exception as e:
            self._log_msg("order", f"PARTIAL SELL FAILED | {trade.trade_id} — {e}")

        if self._on_event:
            self._on_event(f"PARTIAL_{label}", trade)

    def _exit_trade(
        self, trade: TradeRecord, price: float,
        reason: str, now: datetime
    ) -> None:
        remaining = trade.quantity - trade.booked_qty
        if remaining <= 0:
            trade.state     = TradeState.CLOSED
            trade.closed_at = now
            return

        pnl = (price - trade.entry_price) * remaining if trade.hypothesis == "BULL" \
              else (trade.entry_price - price) * remaining
        trade.realised_pnl  += pnl
        trade.unrealised_pnl = 0.0
        trade.exit_price     = price
        trade.exit_reason    = reason
        trade.state          = TradeState.CLOSED
        trade.closed_at      = now
        self._session_pnl   += pnl

        self._log_msg("trade",
            f"EXIT | {trade.trade_id} {reason} @ Rs.{price:.2f} "
            f"remaining_qty={remaining} pnl=Rs.{pnl:+.0f} "
            f"total_pnl=Rs.{trade.net_pnl:+.0f}")

        try:
            from src.broker.base import Order
            order = Order(
                symbol      = trade.instrument,
                exchange    = "NFO",
                option_type = trade.option_type,
                strike      = trade.strike,
                expiry      = trade.expiry,
                transaction = "SELL",
                quantity    = remaining,
                product     = "MIS",
                order_type  = "MARKET",
            )
            order_id = self._broker.place_order(order)
            trade.sell_order_id = str(order_id)
            self._log_msg("order",
                f"EXIT SELL PLACED | {trade.trade_id} order_id={order_id} qty={remaining}")
        except Exception as e:
            self._log_msg("order", f"EXIT SELL FAILED | {trade.trade_id} — {e}")

        if self._on_event:
            self._on_event("EXIT", trade)

    def force_exit_all(self, price: float, now: Optional[datetime] = None) -> None:
        """Force-exit all open trades. Called at 3:20 PM IST."""
        if now is None:
            now = datetime.now(IST)
        for trade in list(self._trades.values()):
            if trade.is_open:
                self._exit_trade(trade, price, "EOD_FORCE_EXIT", now)

    def session_summary(self) -> dict:
        closed = [t for t in self._trades.values() if t.state == TradeState.CLOSED]
        wins   = [t for t in closed if t.net_pnl > 0]
        losses = [t for t in closed if t.net_pnl < 0]
        return {
            "total_trades":    len(closed),
            "wins":            len(wins),
            "losses":          len(losses),
            "win_rate":        round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "session_pnl":     round(self._session_pnl, 2),
            "best_trade":      max((t.net_pnl for t in closed), default=0),
            "worst_trade":     min((t.net_pnl for t in closed), default=0),
        }

    def theta_per_minute(
        self,
        trade: TradeRecord,
        current_price: float,
        minutes_to_expiry: float,
    ) -> float:
        """
        Approximate theta decay in Rs/minute for an open option position.

        Simplified Black-Scholes theta proxy:
            daily_theta ≈ premium × 0.5 / sqrt(days_to_expiry)
        Adjusted by lot_size and divided by 375 trading minutes per day.
        Near-expiry acceleration: < 2 trading days (< 750 min) → 1.5× multiplier.
        Returns positive float = Rs lost per minute to time decay.
        """
        if current_price <= 0 or minutes_to_expiry <= 0:
            return 0.0

        trading_minutes_per_day = 375.0
        days_to_expiry = minutes_to_expiry / trading_minutes_per_day

        import math as _math
        daily_theta = current_price * 0.5 / _math.sqrt(max(days_to_expiry, 0.01))

        if minutes_to_expiry < 750:
            daily_theta *= 1.5

        theta_per_min = daily_theta * trade.lot_size / trading_minutes_per_day
        return round(max(theta_per_min, 0.0), 4)

    def get_action_recommendation(
        self,
        trade: TradeRecord,
        current_price: float,
        scenario_confidence: float,
    ) -> str:
        """
        Returns: 'HOLD' | 'REDUCE' | 'EXIT' | 'ADD'

        ADD:    confidence > 90% AND unrealised_pnl > 0 AND no T2 booked yet
        REDUCE: approaching SL — < 30% of original SL distance remaining
        EXIT:   confidence < 55% OR momentum SL trigger (price reversed past entry + 0.5%)
        HOLD:   otherwise
        """
        if not trade.is_open or trade.entry_price is None:
            return "HOLD"

        active_sl = trade.trailing_sl or trade.sl_price

        if scenario_confidence > 90 and trade.unrealised_pnl > 0:
            t2_expected_qty = int(trade.quantity * 0.50)
            if trade.booked_qty < t2_expected_qty:
                return "ADD"

        if active_sl and trade.entry_price:
            original_sl_dist = abs(trade.entry_price - (trade.sl_price or trade.entry_price))
            if original_sl_dist > 0:
                current_sl_dist = abs(current_price - active_sl)
                if current_sl_dist < original_sl_dist * 0.30:
                    return "REDUCE"

        if scenario_confidence < 55:
            return "EXIT"

        if trade.entry_price:
            reversal_threshold = trade.entry_price * 0.005
            if trade.hypothesis == "BULL" and current_price < trade.entry_price - reversal_threshold:
                return "EXIT"
            elif trade.hypothesis == "BEAR" and current_price > trade.entry_price + reversal_threshold:
                return "EXIT"

        return "HOLD"

    @property
    def open_trades(self) -> list[TradeRecord]:
        return [t for t in self._trades.values() if t.is_open]

    @property
    def session_pnl(self) -> float:
        return self._session_pnl
