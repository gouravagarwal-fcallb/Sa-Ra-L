"""
INRUSD Live/Paper Adapter
─────────────────────────
Wraps the INRUSD currency-futures engine into the standard live-engine interface
(run() loop + cooperative _stop_event + status_callback) so the unified runner
can launch it like any other strategy.

Flow: compute the pre-session bias once (CurrencyBiasEngine), then poll the
USDINR spot each minute, feed it to INRUSDEngine.evaluate(), and report status.
Currency derivatives trade 09:00–17:00 IST; we hard-stop at the configured close.

Note: paper-only here (no real broker order routing for NSE-CDS yet) — `mode`
is accepted for interface parity and reported, but orders are not placed.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def _usdinr_spot() -> float | None:
    """Latest USD/INR via yfinance (USDINR=X). Returns None on failure."""
    try:
        import yfinance as yf
        d = yf.Ticker("USDINR=X").history(period="1d", interval="1m")
        if d is not None and not d.empty:
            return float(d["Close"].iloc[-1])
    except Exception:
        pass
    return None


class INRUSDLive:
    def __init__(self, strategy_config: dict, broker, mode: str = "paper",
                 status_callback=None):
        self.cfg = strategy_config
        self.broker = broker
        self.mode = mode
        self._cb = status_callback
        self._stop_event = None
        ex = self.cfg.get("exit", {})
        close = str(self.cfg.get("trading", {}).get("hard_close", "16:45"))
        self.close_h, self.close_m = int(close[:2]), int(close[3:5])
        self.poll_sec = int(self.cfg.get("live", {}).get("poll_seconds", 60))

    def _report(self, **kw):
        if self._cb:
            try: self._cb(**kw)
            except Exception: pass

    def _stopped(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    def run(self) -> None:
        # ── Pre-session bias ──────────────────────────────────────────────────
        bdir, bscore = "NEUTRAL", 0
        try:
            from src.inrusd.data_fetcher import fetch_pre_session_snapshot
            from src.inrusd.premarket_bias import CurrencyBiasEngine
            snap = fetch_pre_session_snapshot(verbose=False)
            bias = CurrencyBiasEngine(self.cfg).compute(snap)
            bdir, bscore = bias.direction, bias.score
        except Exception as e:
            self._report(last_signal=f"Bias fetch failed: {str(e)[:60]} — NEUTRAL", notable=True)

        self._report(direction=bdir, score=int(bscore),
                     last_signal=f"Pre-session USD/INR bias {bdir} ({bscore:+d})", notable=True)

        if str(bdir).upper() == "NEUTRAL":
            self._report(last_signal="Bias NEUTRAL — standing aside, monitoring only.", notable=True)

        from src.inrusd.inrusd_engine import INRUSDEngine
        try:
            engine = INRUSDEngine(self.cfg, bdir, int(bscore))
        except Exception as e:
            self._report(state="ERROR", error=str(e)[:100]); return

        # ── Intraday poll loop ───────────────────────────────────────────────
        while True:
            if self._stopped():
                self._report(last_signal="Stop requested — INRUSD halted."); break
            now = datetime.now(IST)
            if (now.hour, now.minute) >= (self.close_h, self.close_m):
                self._report(last_signal=f"Hard close {self.close_h:02d}:{self.close_m:02d} — done."); break

            price = _usdinr_spot()
            if price is not None:
                try:
                    sig = engine.evaluate(price)
                    action = getattr(sig, "action", None) or getattr(sig, "signal", "—")
                    self._report(last_signal=f"USDINR {price:.4f} → {action}",
                                 notable=bool(action and str(action).upper() not in ("HOLD", "NONE", "—")))
                except Exception as e:
                    self._report(last_signal=f"eval error: {str(e)[:60]}")
            else:
                self._report(last_signal="USDINR quote unavailable (retrying).")

            for _ in range(self.poll_sec):
                if self._stopped():
                    break
                time.sleep(1)
