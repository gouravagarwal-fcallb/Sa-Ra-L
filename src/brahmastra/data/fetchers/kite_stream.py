"""
BRAHMASTRA Kite WebSocket Tick Stream
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Connects to Kite Connect WebSocket API.
Streams live ticks for all subscribed instruments.
Routes ticks to BarBuilder and Logger.

Kite WebSocket delivers 1–3 ticks/second per instrument.
Processing latency target: < 100ms from receipt to BarBuilder update.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

IST = timezone(timedelta(hours=5, minutes=30))

# Kite instrument tokens for major indices
# These are fixed tokens for NSE indices — do not change
INSTRUMENT_TOKENS = {
    "NIFTY":        256265,   # NSE:NIFTY 50
    "SENSEX":       265,      # BSE:SENSEX
    "BANKNIFTY":    260105,   # NSE:BANKNIFTY
    "INDIA_VIX":    264969,   # NSE:INDIA VIX
    "NIFTY_FUT":    None,     # filled dynamically from instruments list
}


class KiteTick:
    """Normalised tick data from Kite WebSocket."""
    __slots__ = ("instrument_token", "instrument", "last_price",
                 "volume", "timestamp", "mode")

    def __init__(self, instrument_token: int, instrument: str,
                 last_price: float, volume: float = 0,
                 timestamp: Optional[datetime] = None,
                 mode: str = "quote"):
        self.instrument_token = instrument_token
        self.instrument       = instrument
        self.last_price       = last_price
        self.volume           = volume
        self.timestamp        = timestamp or datetime.now(IST)
        self.mode             = mode


class KiteTickStream:
    """
    Manages live Kite WebSocket connection.

    Usage:
        stream = KiteTickStream(kite_api, instruments=["NIFTY", "SENSEX"])
        stream.add_tick_handler(my_callback)   # called on every tick
        stream.start()
        ...
        stream.stop()
    """

    def __init__(
        self,
        kite_api,                        # kiteconnect.KiteConnect instance
        instruments: list[str],
        on_tick: Optional[Callable[[KiteTick], None]] = None,
        on_connect: Optional[Callable[[], None]] = None,
        on_disconnect: Optional[Callable[[Exception], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        mode: str = "quote",             # quote | full | ltp
    ):
        self._kite        = kite_api
        self._instruments = instruments
        self._on_tick     = on_tick
        self._on_connect  = on_connect
        self._on_disconnect = on_disconnect
        self._on_error    = on_error
        self._mode        = mode

        self._ticker      = None
        self._running     = False
        self._token_map: dict[int, str] = {}   # token → instrument name
        self._thread: Optional[threading.Thread] = None

    def _build_token_map(self) -> list[int]:
        """Resolve instrument names to Kite instrument tokens."""
        tokens = []
        for inst in self._instruments:
            token = INSTRUMENT_TOKENS.get(inst.upper())
            if token:
                self._token_map[token] = inst
                tokens.append(token)
            else:
                # Try to look up from Kite instruments list
                try:
                    inst_list = self._kite.instruments("NSE")
                    matches = [i for i in inst_list
                               if i["tradingsymbol"] == inst.upper()]
                    if matches:
                        t = matches[0]["instrument_token"]
                        self._token_map[t] = inst
                        tokens.append(t)
                except Exception:
                    pass
        return tokens

    def _on_ticks_kite(self, ws, ticks: list[dict]) -> None:
        """Called by KiteTicker on each batch of ticks from WebSocket."""
        now = datetime.now(IST)
        for tick_data in ticks:
            token = tick_data.get("instrument_token")
            inst  = self._token_map.get(token, str(token))
            price = tick_data.get("last_price") or tick_data.get("close", 0)
            vol   = tick_data.get("volume_traded") or tick_data.get("volume", 0)
            tick_ts = tick_data.get("exchange_timestamp") or now
            if not isinstance(tick_ts, datetime):
                tick_ts = now

            tick = KiteTick(
                instrument_token = token,
                instrument       = inst,
                last_price       = float(price),
                volume           = float(vol),
                timestamp        = tick_ts,
                mode             = self._mode,
            )
            if self._on_tick:
                try:
                    self._on_tick(tick)
                except Exception as e:
                    if self._on_error:
                        self._on_error(e)

    def _on_connect_kite(self, ws, response) -> None:
        tokens = self._build_token_map()
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_QUOTE if self._mode == "quote" else ws.MODE_FULL, tokens)
        if self._on_connect:
            self._on_connect()

    def _on_close_kite(self, ws, code, reason) -> None:
        self._running = False
        if self._on_disconnect:
            self._on_disconnect(Exception(f"WebSocket closed: {code} {reason}"))

    def _on_error_kite(self, ws, code, reason) -> None:
        if self._on_error:
            self._on_error(Exception(f"WebSocket error: {code} {reason}"))

    def start(self) -> None:
        """Start WebSocket connection in background thread."""
        try:
            from kiteconnect import KiteTicker
        except ImportError:
            raise ImportError(
                "kiteconnect not installed. Run: pip install kiteconnect"
            )

        self._ticker = KiteTicker(
            self._kite.api_key,
            self._kite.access_token,
        )
        self._ticker.on_ticks      = self._on_ticks_kite
        self._ticker.on_connect    = self._on_connect_kite
        self._ticker.on_close      = self._on_close_kite
        self._ticker.on_error      = self._on_error_kite

        self._running = True
        self._thread  = threading.Thread(
            target=self._ticker.connect,
            kwargs={"threaded": True},
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass

    @property
    def is_running(self) -> bool:
        return self._running

    def add_instrument(self, instrument: str, token: int) -> None:
        """Dynamically add an instrument during live session (e.g. ATM option)."""
        self._token_map[token] = instrument
        if self._ticker and self._running:
            self._ticker.subscribe([token])
            self._ticker.set_mode(
                self._ticker.MODE_QUOTE if self._mode == "quote"
                else self._ticker.MODE_FULL,
                [token]
            )

    def remove_instrument(self, token: int) -> None:
        """Remove instrument from subscription."""
        self._token_map.pop(token, None)
        if self._ticker and self._running:
            try:
                self._ticker.unsubscribe([token])
            except Exception:
                pass


class MockTickStream:
    """
    Simulates tick stream using yfinance data for paper trading / development.
    Replays recent 1m bars as live ticks at configurable speed.
    Used when Kite credentials are not available.
    """

    def __init__(
        self,
        instruments: list[str],
        on_tick: Optional[Callable[[KiteTick], None]] = None,
        replay_speed: float = 1.0,   # 1.0 = real time, 60.0 = 60x speed
    ):
        self._instruments = instruments
        self._on_tick     = on_tick
        self._speed       = replay_speed
        self._running     = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(target=self._replay_loop, daemon=True)
        self._thread.start()

    def _replay_loop(self) -> None:
        try:
            import yfinance as yf
        except ImportError:
            return

        now  = datetime.now(IST)
        bars = {}
        for inst in self._instruments:
            sym_map = {"NIFTY": "^NSEI", "SENSEX": "^BSESN", "BANKNIFTY": "^NSEBANK"}
            sym = sym_map.get(inst, inst)
            try:
                ticker = yf.Ticker(sym)
                hist   = ticker.history(period="5d", interval="1m")
                if not hist.empty:
                    bars[inst] = hist
            except Exception:
                pass

        while self._running:
            for inst, hist in bars.items():
                if hist.empty:
                    continue
                row = hist.iloc[-1]
                price = float(row["Close"])
                vol   = float(row.get("Volume", 0))
                tick  = KiteTick(
                    instrument_token = INSTRUMENT_TOKENS.get(inst, 0),
                    instrument       = inst,
                    last_price       = price,
                    volume           = vol,
                    timestamp        = datetime.now(IST),
                )
                if self._on_tick:
                    try:
                        self._on_tick(tick)
                    except Exception:
                        pass
            time.sleep(60.0 / self._speed)

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running
