"""
Trap Alerter — PASHUPATASTRA real-time pings
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Sends a phone alert (Telegram/WhatsApp via BrahmastraNotifier) the moment the
Seller-Trap Score fires on the live chain — so you can eyeball the signal against
the tape while the recorder accrues data. Independent of the shadow engine's own
entry/exit pings: this fires on the SCORE crossing, even on days you don't trade.

Anti-spam: one ping per (instrument, wall, side); re-pings only after `cooldown_min`
OR when the score ESCALATES by `escalate_delta` (the squeeze intensifying).

Decoupled: works with BrahmastraNotifier (preferred) or any object exposing
send_message(text)/send(text); with no notifier it just logs (and returns the alert),
so it is fully testable offline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


class TrapAlerter:
    def __init__(self, notifier=None, threshold: float = 75.0,
                 cooldown_min: int = 15, escalate_delta: float = 8.0, logger=None):
        self.notifier = notifier
        self.threshold = threshold
        self.cooldown = timedelta(minutes=cooldown_min)
        self.escalate_delta = escalate_delta
        self.log = logger or _default_logger()
        self._last: dict[tuple, tuple] = {}      # (inst,wall,side) -> (ts, score)

    def on_chain(self, inst: str, snap, trap, now: Optional[datetime] = None) -> Optional[dict]:
        now = now or datetime.now(IST)
        if not getattr(trap, "side", None) or trap.score < self.threshold:
            return None
        key = (inst, trap.wall_strike, trap.side)
        prev = self._last.get(key)
        escalation = False
        if prev is not None:
            prev_ts, prev_score = prev
            fresh = (now - prev_ts) >= self.cooldown
            escalation = trap.score >= prev_score + self.escalate_delta
            if not (fresh or escalation):
                return None                       # within cooldown, no escalation -> stay quiet
        self._last[key] = (now, trap.score)

        alert = {
            "instrument": inst, "side": trap.side, "score": trap.score,
            "spot": round(float(getattr(snap, "spot_price", 0) or 0)),
            "wall_strike": trap.wall_strike, "candidate_strike": trap.candidate_strike,
            "factors": getattr(trap, "factors", {}) or {},
            "time_str": now.strftime("%H:%M"), "escalation": escalation,
        }
        self._dispatch(alert)
        return alert

    def _dispatch(self, alert: dict):
        tag = "ESCALATING" if alert["escalation"] else "TRAP"
        self.log.info(f"[trap_alert] 🎯 {tag} {alert['instrument']} {alert['side']} "
                      f"score={alert['score']} wall={alert['wall_strike']} "
                      f"buy={alert['candidate_strike']} spot={alert['spot']}")
        n = self.notifier
        if n is None:
            return
        try:
            if hasattr(n, "send_trap_alert"):
                n.send_trap_alert(**alert)
            else:
                from src.brahmastra.notifications.notifier import format_trap_alert
                msg = format_trap_alert(**alert)
                send = getattr(n, "send_message", None) or getattr(n, "send", None)
                if send:
                    send(msg)
        except Exception as e:
            self.log.warning(f"[trap_alert] dispatch failed: {e}")


def build_default_alerter(threshold: float = 75.0, cooldown_min: int = 15,
                          settings_paths=("config/settings.local.yaml", "config/settings.yaml")):
    """Construct a TrapAlerter wired to BrahmastraNotifier from settings, if available.
    Falls back to a log-only alerter (notifier=None) when settings/notifier are absent —
    so callers never crash for lack of a Telegram token."""
    notifier = None
    try:
        import os
        import yaml
        settings = {}
        for p in settings_paths:
            if os.path.exists(p):
                with open(p) as f:
                    settings = yaml.safe_load(f) or {}
                break
        from src.brahmastra.notifications.notifier import BrahmastraNotifier
        n = BrahmastraNotifier(settings)
        notifier = n if n.any_enabled else None       # any_enabled is a property, not a method
    except Exception:
        notifier = None
    return TrapAlerter(notifier=notifier, threshold=threshold, cooldown_min=cooldown_min)


def _default_logger():
    try:
        from src.utils.logger import setup_logger
        return setup_logger("trap_alert")
    except Exception:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        return logging.getLogger("trap_alert")
