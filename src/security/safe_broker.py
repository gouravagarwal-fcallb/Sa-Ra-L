"""
SafeBrokerGuard — the audited Broker Interface (audit controls EXEC-1 / KILL-1).
────────────────────────────────────────────────────────────────────────────────
The platform's `BaseBroker` (place_order / get_ltp / get_positions / cancel_order)
is the transport contract. This guard WRAPS a BaseBroker and is the ONLY object a
strategy is given, so every order is forced through four checks before it can
reach the market:

    1. KILL SWITCH   — if the heartbeat/kill-switch is tripped, NEW entries (BUY)
                       are rejected. Exits (SELL) are always allowed so the system
                       can flatten. (KILL-1)
    2. HUMAN-IN-LOOP — a LIVE entry requires a valid single-use arm token supplied
                       by the operator's arm+confirm ritual. Paper never needs one.
    3. SIZING        — the 10%-capital cap is asserted at the gate, not trusted to
                       the caller. (defence in depth for RISK-1)
    4. AUDIT         — intent, order_id, fill and SLIPPAGE are written to the
                       EXECUTION channel for every attempt, pass or fail.

This module deliberately contains NO strategy logic and does NOT decide WHEN to
trade — it only decides whether an already-formed order is ALLOWED, and records
it. Execution wiring (the runner injecting a real KiteBroker) is the LAST layer,
built after this guard and the vault are in place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from src.broker.base import BaseBroker, Order
from src.security.audit_log import log_execution, log_system


class BrokerGuardError(RuntimeError):
    """Order rejected at the gate. Carries a machine-readable reason code."""
    def __init__(self, code: str, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code


@runtime_checkable
class RiskGate(Protocol):
    """Wired by the runner — the guard consults it, never owns the state."""
    def kill_switch_active(self) -> bool: ...
    def is_armed(self, strategy: str, token: Optional[str]) -> bool: ...
    def capital_for(self, strategy: str) -> float: ...


@dataclass
class GuardResult:
    allowed: bool
    order_id: Optional[str]
    reason: str
    intended_price: Optional[float]
    fill_price: Optional[float]
    slippage_pts: Optional[float]


class SafeBrokerGuard:
    def __init__(self, broker: BaseBroker, gate: RiskGate,
                 lot_size: int = 65, capital_per_trade_pct: float = 0.10):
        self._broker = broker
        self._gate = gate
        self.lot_size = lot_size
        self.capital_per_trade_pct = capital_per_trade_pct

    # ── the single guarded entry point strategies must use ────────────────────
    def place(self, order: Order, *, mode: str, strategy: str,
              intended_price: float, arm_token: Optional[str] = None) -> GuardResult:
        is_entry = order.transaction.upper() == "BUY"

        # 1. kill switch — blocks new entries only; exits may always proceed
        if is_entry and self._gate.kill_switch_active():
            log_execution("REJECTED_KILL_SWITCH", strategy=strategy,
                          symbol=order.symbol, txn=order.transaction, qty=order.quantity)
            raise BrokerGuardError("KILL", "kill switch active — new entries blocked")

        # 2. human-in-the-loop — live entries require a valid arm token
        if is_entry and mode == "live" and not self._gate.is_armed(strategy, arm_token):
            log_execution("REJECTED_NOT_ARMED", strategy=strategy, symbol=order.symbol)
            raise BrokerGuardError("ARM", "live entry without a valid arm token")

        # 3. sizing — enforce the 10% cap at the gate (defence in depth)
        if is_entry:
            budget = self._gate.capital_for(strategy) * self.capital_per_trade_pct
            cost = intended_price * order.quantity
            if cost > budget * 1.001:          # tiny tolerance for rounding
                log_execution("REJECTED_OVERSIZE", strategy=strategy,
                              cost=round(cost, 2), budget=round(budget, 2))
                raise BrokerGuardError("SIZE", f"order cost {cost:.0f} > 10% cap {budget:.0f}")

        # 4. place + audit (intent → order_id → fill → slippage)
        log_execution("INTENT", strategy=strategy, mode=mode, symbol=order.symbol,
                      txn=order.transaction, qty=order.quantity, intended=intended_price)
        try:
            order_id = self._broker.place_order(order)
        except Exception as e:
            log_system("ORDER_PLACE_FAILED", strategy=strategy, symbol=order.symbol,
                       error=type(e).__name__)
            raise

        fill = self._safe_fill_price(order, order_id)
        slip = round(abs(fill - intended_price), 2) if fill is not None else None
        log_execution("FILLED", strategy=strategy, symbol=order.symbol,
                      order_id=order_id, intended=intended_price, fill=fill, slippage=slip)
        return GuardResult(True, order_id, "ok", intended_price, fill, slip)

    # ── read-through (no gating — quotes/positions are read-only) ──────────────
    def get_ltp(self, *a, **k):
        return self._broker.get_ltp(*a, **k)

    def get_positions(self, *a, **k):
        return self._broker.get_positions(*a, **k)

    def get_order_status(self, *a, **k):
        return self._broker.get_order_status(*a, **k)

    def get_tradingsymbol(self, *a, **k):
        return self._broker.get_tradingsymbol(*a, **k)

    def cancel_order(self, *a, **k):
        # exits/cancels are always allowed (needed to flatten under a kill)
        return self._broker.cancel_order(*a, **k)

    def _safe_fill_price(self, order: Order, order_id: str) -> Optional[float]:
        """Best-effort fill price for slippage accounting; never raises."""
        try:
            status = self._broker.get_order_status(order_id)
            if isinstance(status, dict):
                for k in ("average_price", "fill_price", "price"):
                    if status.get(k):
                        return float(status[k])
        except Exception:
            pass
        return None
