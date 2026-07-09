"""
INRUSD Live/Paper Adapter
─────────────────────────
Wraps the INRUSD currency-futures engine into the standard live-engine interface
(run() loop + cooperative _stop_event + status_callback) so the unified runner
can launch it like any other strategy.

Flow: compute the pre-session bias once (CurrencyBiasEngine), then poll the
USDINR spot each minute, feed it to INRUSDEngine.evaluate(), and report status.
Currency derivatives trade 09:00–17:00 IST; we hard-stop at the configured close.

Now simulates PAPER futures positions: on a LONG/SHORT signal it opens a paper
position (lot = 1000 USD → P&L = price-move × 1000 × lots), monitors it against the
engine's ATR target/stop, and squares off at target/stop/EOD — so INRUSD produces
analysable paper trades. Real NSE-CDS live order routing is still pending; live mode
is accepted for interface parity but places no real currency-futures order yet.
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
        self._lot_size = int(self.cfg.get("instrument", {}).get("lot_size", 1000))
        self._pos = None            # open paper futures position, or None
        self._day_pnl = 0.0

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
            if price is None:
                self._report(last_signal="USDINR quote unavailable (retrying).")
            elif self._pos is not None:
                self._monitor(price)
            else:
                try:
                    sig = engine.evaluate(price)
                    d = str(getattr(sig, "direction", "NO_TRADE")).upper()
                    if d in ("LONG", "SHORT") and getattr(sig, "entry_price", None):
                        self._open(sig, price)
                    else:
                        self._report(last_signal=f"USDINR {price:.4f} → {d}", notable=False)
                except Exception as e:
                    self._report(last_signal=f"eval error: {str(e)[:60]}")

            for _ in range(self.poll_sec):
                if self._stopped():
                    break
                time.sleep(1)

        # square off any open position when the loop ends (EOD / stop)
        if self._pos is not None:
            px = _usdinr_spot() or self._pos["entry"]
            self._close(px, "FORCE_CLOSE")

    # ── Paper futures position (USDINR; lot = 1000 USD → P&L = move × 1000 × lots) ──
    def _open(self, sig, price: float) -> None:
        lots = int(getattr(sig, "lots", 1) or 1)
        self._pos = {
            "dir": str(sig.direction).upper(), "entry": float(sig.entry_price or price),
            "target": sig.target_price, "stop": sig.stop_price, "lots": lots,
            "qty": lots * self._lot_size, "t": datetime.now(IST).strftime("%H:%M:%S"),
        }
        self._report(direction="BULLISH" if self._pos["dir"] == "LONG" else "BEARISH",
                     last_signal=f"ENTER {self._pos['dir']} USDINR @ {self._pos['entry']:.4f} "
                                 f"tgt={sig.target_price} sl={sig.stop_price} lots={lots}",
                     notable=True,
                     trade_event={"event": "ENTRY", "instrument": "USDINR",
                                  "direction": "BULLISH" if self._pos["dir"] == "LONG" else "BEARISH",
                                  "option_type": "FUT", "strike": "", "price": round(self._pos["entry"], 4),
                                  "quantity": self._pos["qty"], "pnl": "", "exit_reason": "",
                                  "window": "INRUSD"})

    def _pnl(self, price: float) -> float:
        p = self._pos
        move = (price - p["entry"]) if p["dir"] == "LONG" else (p["entry"] - price)
        return round(move * self._lot_size * p["lots"], 2)

    def _monitor(self, price: float) -> None:
        p = self._pos
        hit = None
        if p["target"] and ((p["dir"] == "LONG" and price >= p["target"]) or
                            (p["dir"] == "SHORT" and price <= p["target"])):
            hit = "TARGET_HIT"
        elif p["stop"] and ((p["dir"] == "LONG" and price <= p["stop"]) or
                            (p["dir"] == "SHORT" and price >= p["stop"])):
            hit = "STOP_LOSS"
        if hit:
            self._close(price, hit)
        else:
            self._report(last_signal=f"HOLD {p['dir']} USDINR {price:.4f} pnl=Rs.{self._pnl(price):+.0f}")

    def _close(self, price: float, reason: str) -> None:
        p = self._pos
        pnl = self._pnl(price)
        self._day_pnl += pnl
        self._pos = None
        self._report(direction="NEUTRAL",
                     last_signal=f"{reason} USDINR @ {price:.4f} pnl=Rs.{pnl:+.0f}", notable=True,
                     paper_pnl=self._day_pnl,
                     trade_event={"event": reason, "instrument": "USDINR", "direction": "NEUTRAL",
                                  "option_type": "FUT", "strike": "", "price": round(price, 4),
                                  "quantity": p["qty"], "pnl": pnl, "exit_reason": reason,
                                  "window": "INRUSD"})
