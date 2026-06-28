"""
Pashupatastra Live/Shadow Adapter
─────────────────────────────────
Wraps PashupatastraLive into the standard runner interface (run() + _stop_event +
status_callback). Paper status → shadow mode (no real orders, journals would-be
trades); live status → live mode (real orders, once the broker leg is enabled).

The base run_live() loop already polls the NSE option chain and manages trades;
we subclass it to surface scan/trap/trade activity to the dashboard status feed.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.brahmastra.live.pashupatastra_live import PashupatastraLive, load_cfg

IST = timezone(timedelta(hours=5, minutes=30))


class _DashPashupatastra(PashupatastraLive):
    """PashupatastraLive that reports chain scans + trades to a status callback."""
    def __init__(self, *a, status_callback=None, **kw):
        super().__init__(*a, **kw)
        self._cb = status_callback

    def on_chain(self, inst, snap, trap, now):
        # Report the scan/trap read before delegating to the real logic.
        if self._cb:
            try:
                score = getattr(trap, "score", None)
                fires = getattr(trap, "fires", lambda *_: False)(self.cfg.trap_threshold)
                self._cb(last_signal=f"{inst} chain scan · trap={score} "
                                     f"{'⚡ARMED' if fires else 'watching'}",
                         notable=bool(fires))
            except Exception:
                pass
        events = super().on_chain(inst, snap, trap, now)
        if self._cb and events:
            for ev in events:
                try:
                    self._cb(trade_event={
                        "instrument": inst,
                        "event": str(ev.get("action", ev.get("event", "TRADE"))).upper(),
                        "option_type": ev.get("side", ""),
                        "strike": ev.get("strike", ""),
                        "price": ev.get("ltp", ev.get("price", "")),
                        "quantity": ev.get("qty", ""),
                        "pnl": ev.get("net_pnl", ""),
                        "time": now.strftime("%H:%M:%S"),
                    })
                except Exception:
                    pass
        return events


class PashupatastraRunner:
    def __init__(self, strategy_config: dict, broker, mode: str = "paper",
                 status_callback=None):
        self.cfg = strategy_config
        self.broker = broker
        self.mode = mode
        self._cb = status_callback
        self._stop_event = None
        insts = strategy_config.get("instruments", ["NIFTY", "SENSEX"])
        if isinstance(insts, dict):
            insts = list(insts.keys())          # config stores instruments as a dict
        self._instruments = [str(i).upper() for i in insts] or ["NIFTY", "SENSEX"]
        self._interval = int(strategy_config.get("live", {}).get("poll_seconds", 90))

    def run(self) -> None:
        engine_mode = "live" if self.mode == "live" else "shadow"
        if self._cb:
            self._cb(direction="EXPIRY", last_signal=f"Pashupatastra {engine_mode} — "
                     f"watching option chain for trapped sellers.", notable=True)
        try:
            eng = _DashPashupatastra(cfg=load_cfg(), mode=engine_mode,
                                     status_callback=self._cb)
        except Exception as e:
            if self._cb:
                self._cb(state="ERROR", error=str(e)[:100])
            return
        eng._stop_event = self._stop_event
        eng.run_live(instruments=self._instruments, interval_sec=self._interval,
                     market_hours_only=True)
        if self._cb:
            self._cb(last_signal="Pashupatastra session ended.")
