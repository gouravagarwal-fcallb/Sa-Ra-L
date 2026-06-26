"""
BRAHMASTRA Human Gate
━━━━━━━━━━━━━━━━━━━━━
Controls whether trade signals are auto-executed or held for human approval.

Two modes:
  AUTO         — every confirmed signal is executed immediately
  HUMAN_WATCH  — system analysis + alerts run continuously;
                 signals are queued as PendingSignals and NOT executed
                 until the human approves via:
                   • Dashboard button  POST /api/approve/{instrument}
                   • Terminal          any thread calling gate.approve("NIFTY")
                   • Telegram callback (future: /take NIFTY)

Key invariant: analysis NEVER stops regardless of mode.
Only execution is gated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, Optional

IST = timezone(timedelta(hours=5, minutes=30))


class ExecutionMode(str, Enum):
    AUTO        = "auto"
    HUMAN_WATCH = "human_watch"


@dataclass
class PendingSignal:
    instrument:  str
    hypothesis:  str          # "BULL" / "BEAR"
    entry_price: float
    sl_price:    float
    target1:     float
    target2:     float
    target3:     float
    confidence:  float        # confluence score at time of signal
    lots:        int
    expires_at:  datetime
    armed_at:    datetime = field(default_factory=lambda: datetime.now(IST))

    @property
    def is_expired(self) -> bool:
        return datetime.now(IST) > self.expires_at

    @property
    def minutes_left(self) -> int:
        remaining = (self.expires_at - datetime.now(IST)).total_seconds()
        return max(0, int(remaining / 60))

    def summary(self) -> str:
        return (
            f"{self.instrument} {self.hypothesis}  "
            f"entry≈{self.entry_price:.0f}  SL={self.sl_price:.0f}  "
            f"T1={self.target1:.0f}  conf={self.confidence:.0f}%  "
            f"expires in {self.minutes_left}m"
        )

    def telegram_text(self) -> str:
        arrow = "🟢" if self.hypothesis == "BULL" else "🔴"
        rr = abs(self.target1 - self.entry_price) / max(abs(self.entry_price - self.sl_price), 1)
        return (
            f"{arrow} *BRAHMASTRA SIGNAL — AWAITING YOUR APPROVAL*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Instrument : {self.instrument}\n"
            f"Direction  : {self.hypothesis}\n"
            f"Confidence : {self.confidence:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Entry  : ≈{self.entry_price:.0f}\n"
            f"SL     : {self.sl_price:.0f}\n"
            f"T1     : {self.target1:.0f}  (R/R {rr:.1f}x)\n"
            f"T2     : {self.target2:.0f}\n"
            f"T3     : {self.target3:.0f}\n"
            f"Lots   : {self.lots}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ Expires in {self.minutes_left} min\n"
            f"✅ Approve via dashboard  →  POST /api/approve/{self.instrument}\n"
            f"❌ Reject               →  POST /api/reject/{self.instrument}"
        )


class HumanGate:
    """
    Thread-safe execution mode controller with pending signal queue.

    Usage (in live engine):
        gate = HumanGate(mode=ExecutionMode.AUTO)
        ...
        if gate.check_signal(instrument="NIFTY", hypothesis="BULL", ...):
            trade_engine.execute(...)
        else:
            # HUMAN_WATCH mode — signal queued, alert fired, no execution
            pass
    """

    def __init__(
        self,
        mode:                   ExecutionMode = ExecutionMode.AUTO,
        signal_timeout_minutes: int           = 10,
    ):
        self.mode     = mode
        self._timeout = signal_timeout_minutes
        self._pending: Dict[str, PendingSignal] = {}

    # ── Mode management ───────────────────────────────────────────────────────

    def set_mode(self, mode: ExecutionMode) -> None:
        self.mode = mode

    def toggle(self) -> ExecutionMode:
        """Toggle between AUTO and HUMAN_WATCH. Returns new mode."""
        self.mode = (ExecutionMode.HUMAN_WATCH
                     if self.mode == ExecutionMode.AUTO
                     else ExecutionMode.AUTO)
        return self.mode

    @property
    def is_auto(self) -> bool:
        return self.mode == ExecutionMode.AUTO

    @property
    def is_human_watch(self) -> bool:
        return self.mode == ExecutionMode.HUMAN_WATCH

    # ── Signal gating ─────────────────────────────────────────────────────────

    def check_signal(
        self,
        instrument:  str,
        hypothesis:  str,
        entry_price: float,
        sl_price:    float,
        target1:     float,
        target2:     float,
        target3:     float,
        confidence:  float,
        lots:        int = 1,
    ) -> bool:
        """
        Returns True  → execute immediately (AUTO mode)
        Returns False → signal queued as PendingSignal (HUMAN_WATCH mode)
        """
        if self.mode == ExecutionMode.AUTO:
            return True

        expires = datetime.now(IST) + timedelta(minutes=self._timeout)
        self._pending[instrument.upper()] = PendingSignal(
            instrument  = instrument.upper(),
            hypothesis  = hypothesis,
            entry_price = entry_price,
            sl_price    = sl_price,
            target1     = target1,
            target2     = target2,
            target3     = target3,
            confidence  = confidence,
            lots        = lots,
            expires_at  = expires,
        )
        return False

    # ── Human approval / rejection ────────────────────────────────────────────

    def approve(self, instrument: str) -> Optional[PendingSignal]:
        """
        Human approves the pending signal for this instrument.
        Returns the PendingSignal if it still valid (not expired), else None.
        Removes from pending queue either way.
        """
        sig = self._pending.pop(instrument.upper(), None)
        if sig is None:
            return None
        if sig.is_expired:
            return None   # too late
        return sig

    def reject(self, instrument: str) -> bool:
        """Human rejects the pending signal. Returns True if one existed."""
        return self._pending.pop(instrument.upper(), None) is not None

    # ── Pending signal accessors ──────────────────────────────────────────────

    def pending(self, instrument: str) -> Optional[PendingSignal]:
        return self._pending.get(instrument.upper())

    def all_pending(self) -> Dict[str, PendingSignal]:
        return dict(self._pending)

    def expire_old_signals(self) -> list[str]:
        """Prune expired signals. Returns list of pruned instrument names."""
        expired = [inst for inst, sig in self._pending.items() if sig.is_expired]
        for inst in expired:
            del self._pending[inst]
        return expired

    # ── State summary ─────────────────────────────────────────────────────────

    def status_dict(self) -> dict:
        return {
            "execution_mode": self.mode.value,
            "pending_signals": {
                inst: {
                    "instrument":  sig.instrument,
                    "hypothesis":  sig.hypothesis,
                    "entry_price": sig.entry_price,
                    "sl_price":    sig.sl_price,
                    "target1":     sig.target1,
                    "confidence":  sig.confidence,
                    "lots":        sig.lots,
                    "minutes_left": sig.minutes_left,
                    "armed_at":    sig.armed_at.strftime("%H:%M:%S"),
                    "expires_at":  sig.expires_at.strftime("%H:%M:%S"),
                }
                for inst, sig in self._pending.items()
            },
        }
