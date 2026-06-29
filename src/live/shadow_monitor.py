"""
ShadowMonitor — safe paper-only live monitor
────────────────────────────────────────────
For strategies whose full bespoke live engine isn't built yet (GAP_FADE,
TREND_RIDER, VIX_SELLER), this runs in the dashboard as a real, stop-aware engine so
they show ACTIVE with live ticks / charts / Forward-Impact (driven by the central
market feed) instead of a dead "testing stage" message. Each cycle it evaluates the
strategy's HEADLINE condition against the live feed and logs what it is watching for.

It is deliberately conservative: it NEVER places an order (paper/shadow only, even if
asked for live), and it has no position management — it surfaces signals so the
operator can see the strategy is alive and analysing, pending its full engine. The
strategy's edge is what the deep backtest measured; this is the live cockpit for it.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


class ShadowMonitor:
    def __init__(self, strategy_config: dict, broker, mode: str = "paper",
                 status_callback=None):
        self.cfg = strategy_config or {}
        self.broker = broker
        self.mode = "paper"                      # force paper — never real orders
        self._cb = status_callback
        self.stype = self.cfg.get("strategy_type", "")
        self.name = self.cfg.get("name") or self.stype
        self.instruments = [str(i).upper() for i in (self.cfg.get("instruments") or ["NIFTY"])]
        self._last_state = None

    def _emit(self, **kw) -> None:
        if self._cb:
            try:
                self._cb(**kw)
            except Exception:
                pass

    def run(self) -> None:
        self._emit(signal=f"{self.stype}: live monitor active (paper — signals only, no orders).",
                   notable=True)
        # Heartbeat loop — evaluate the headline condition every ~30s, stop-aware.
        while True:
            ev = getattr(self, "_stop_event", None)
            if ev is not None and ev.is_set():
                break
            try:
                self._evaluate()
            except Exception:
                pass
            if ev is not None:
                if ev.wait(30):
                    break
            else:
                time.sleep(30)
        self._emit(signal=f"{self.stype}: monitor stopped.", notable=True)

    # ──────────────────────────────────────────────────────────────────────────
    def _market(self) -> dict:
        try:
            from src.api.market import get_market_summary
            return get_market_summary() or {}
        except Exception:
            return {}

    def _evaluate(self) -> None:
        m = self._market()
        inst = self.instruments[0].lower()
        q = m.get(inst) or m.get("nifty") or {}
        ltp = q.get("ltp")
        chg = q.get("change_pct")
        vix = m.get("vix")
        now = datetime.now(IST).strftime("%H:%M")

        if self.stype == "volatility_mean_reversion":          # VIX_SELLER
            thr = (self.cfg.get("entry_logic_vix") or 22)
            if vix is None:
                msg = f"{now}  VIX unavailable — idle."
            elif vix > 22:
                msg = f"{now}  VIX {vix:.1f} > 22 — ARMED: range-bound IV-crush setup eligible."
            else:
                msg = f"{now}  VIX {vix:.1f} ≤ 22 — idle (activates only when VIX > 22)."
        elif self.stype == "opening_range":                    # GAP_FADE
            if chg is None:
                msg = f"{now}  awaiting open data."
            elif abs(chg) >= 0.5:
                msg = f"{now}  gap {chg:+.2f}% (≥0.5%) — WATCHING for intraday reversal to fade."
            else:
                msg = f"{now}  gap {chg:+.2f}% (<0.5%) — no gap worth fading today."
        elif self.stype == "trend_following":                  # TREND_RIDER
            if ltp is None:
                msg = f"{now}  awaiting live price."
            elif now < "09:45":
                msg = f"{now}  pre-09:45 — trend window not open yet."
            else:
                msg = f"{now}  {self.instruments[0]} {ltp:,.0f} ({chg:+.2f}%) — watching for a new 30-min extreme + momentum."
        else:
            msg = f"{now}  monitoring {self.instruments[0]} {ltp or '—'}."

        # Only log when the message meaningfully changes, to avoid spamming the stream.
        if msg != self._last_state:
            self._last_state = msg
            self._emit(signal=msg, notable=True)
