"""
Kite Connect Broker
────────────────────
Implements BaseBroker using Zerodha Kite Connect API.

Setup:
  1. Generate daily access token:  python main.py --mode login
  2. Set env vars before running:
       export KITE_API_KEY=your_key
       export KITE_ACCESS_TOKEN=your_daily_token
     OR store in config/settings.yaml under broker.kite.*

Symbol format for NSE/BSE F&O options:
  The correct tradingsymbol is looked up from Kite's instrument dump
  (cached per day) to avoid format guessing.

Product type: MIS (Margin Intraday Square-off) — all positions auto-closed by 3:20 PM.
"""

from __future__ import annotations
import os
import time
from datetime import date, datetime
from typing import Optional
from dataclasses import dataclass

from src.broker.base import BaseBroker, Order
from src.utils.logger import setup_logger

log = setup_logger("kite_broker")


class KiteBroker(BaseBroker):
    def __init__(self, api_key: str, access_token: str):
        try:
            from kiteconnect import KiteConnect
        except ImportError:
            raise RuntimeError("kiteconnect package not installed. Run: pip install kiteconnect")

        self._api_key      = api_key
        self._access_token = access_token
        self._kite = KiteConnect(api_key=api_key)
        self._kite.set_access_token(access_token)

        # Instrument cache: loaded once per day per exchange
        self._instruments_nfo: list[dict] = []
        self._instruments_bfo: list[dict] = []
        self._inst_cache_date: Optional[date] = None
        self._ticker = None

        log.info("KiteBroker initialised — verifying connection")
        try:
            profile = self._kite.profile()
            log.info(f"Connected as: {profile.get('user_name', 'Unknown')}")
        except Exception as e:
            raise RuntimeError(f"Kite connection failed: {e}") from e

    # ── Instrument lookup ──────────────────────────────────────────────────────

    def _refresh_instruments(self) -> None:
        today = date.today()
        if self._inst_cache_date == today:
            return
        log.info("Refreshing Kite instrument list (NFO + BFO)...")
        try:
            self._instruments_nfo = self._kite.instruments("NFO")
            self._instruments_bfo = self._kite.instruments("BFO")
            self._inst_cache_date = today
            log.info(
                f"Loaded {len(self._instruments_nfo)} NFO + "
                f"{len(self._instruments_bfo)} BFO instruments"
            )
        except Exception as e:
            log.error(f"Instrument refresh failed: {e}")

    def get_tradingsymbol(
        self,
        instrument: str,
        expiry: date,
        strike: int,
        opt_type: str,
    ) -> tuple[str, str]:
        """
        Return (tradingsymbol, exchange) for the given option.
        Looks up live instrument list for exact match.
        Falls back to constructed symbol if lookup fails.
        """
        self._refresh_instruments()
        exchange = "BFO" if instrument == "SENSEX" else "NFO"
        instruments = self._instruments_bfo if exchange == "BFO" else self._instruments_nfo

        for inst in instruments:
            if (
                inst.get("name", "").upper() == instrument
                and inst.get("instrument_type", "") == opt_type
                and int(inst.get("strike", -1)) == strike
                and inst.get("expiry") == expiry
            ):
                return inst["tradingsymbol"], exchange

        # Fallback: construct symbol (works for most standard weekly options)
        exp_str = expiry.strftime("%d%b%y").upper()
        symbol = f"{instrument}{exp_str}{strike}{opt_type}"
        log.warning(f"Instrument lookup failed — using constructed symbol: {symbol}")
        return symbol, exchange

    # ── BaseBroker implementation ──────────────────────────────────────────────

    def place_order(self, order: Order) -> str:
        try:
            from kiteconnect import KiteConnect as _KC
            tx = _KC.TRANSACTION_TYPE_BUY if order.transaction == "BUY" \
                else _KC.TRANSACTION_TYPE_SELL
            order_id = self._kite.place_order(
                variety=_KC.VARIETY_REGULAR,
                exchange=order.exchange,
                tradingsymbol=order.symbol,
                transaction_type=tx,
                quantity=order.quantity,
                product=_KC.PRODUCT_MIS,       # Intraday — auto-squared by broker
                order_type=_KC.ORDER_TYPE_MARKET,
            )
            order.order_id = str(order_id)
            order.status = "PENDING"
            log.info(
                f"[LIVE] {order.transaction} {order.quantity} × "
                f"{order.symbol} | order_id={order_id}"
            )
            return str(order_id)
        except Exception as e:
            log.error(f"place_order failed: {e}")
            order.status = "FAILED"
            return ""

    def place_equity_order(self, tradingsymbol: str, exchange: str = "NSE",
                           transaction: str = "BUY", quantity: int = 1,
                           product: str = "CNC", order_type: str = "MARKET",
                           price: float = 0.0) -> str:
        """Place a raw CASH-segment (equity) order — e.g. buy 1 share of TATAPOWER
        for delivery (CNC). Separate from place_order(), which is option-specific
        (MIS + option tradingsymbol lookup). Returns the Kite order_id, or "" on
        failure. Used only by the operator's explicit live-test button.

        product   : "CNC" (delivery) | "MIS" (intraday)
        order_type: "MARKET" | "LIMIT" (LIMIT requires price > 0)

        Zerodha REJECTS naked MARKET equity orders via the API ("market orders
        without market protection are not allowed"). So a MARKET request is
        placed as a *marketable LIMIT*: a limit priced ~1% through the LTP so it
        fills immediately like a market order, with that 1% acting as the price
        cap (the protection). Pass order_type="LIMIT" with an explicit price to
        override.
        """
        from kiteconnect import KiteConnect as _KC
        tx = _KC.TRANSACTION_TYPE_BUY if str(transaction).upper() == "BUY" \
            else _KC.TRANSACTION_TYPE_SELL
        prod = {"CNC": _KC.PRODUCT_CNC, "MIS": _KC.PRODUCT_MIS}.get(
            str(product).upper(), _KC.PRODUCT_CNC)
        if int(quantity) <= 0:
            raise ValueError(f"invalid quantity {quantity}")
        req = str(order_type).upper()
        limit_price = float(price)
        if req == "MARKET":
            # Convert to a marketable limit ~1% through the LTP (BUY: above,
            # SELL: below), rounded to the ₹0.05 tick.
            ltp = self.get_equity_ltp(tradingsymbol, exchange)
            buf = 1.01 if str(transaction).upper() == "BUY" else 0.99
            limit_price = round(round(ltp * buf / 0.05) * 0.05, 2)
            otype = _KC.ORDER_TYPE_LIMIT
        elif req == "LIMIT":
            otype = _KC.ORDER_TYPE_LIMIT
        else:
            otype = _KC.ORDER_TYPE_MARKET
        # A limit order must carry a positive price — never send a zero/negative
        # limit to the broker (would be rejected, or worse, fill oddly).
        if otype == _KC.ORDER_TYPE_LIMIT and (limit_price is None or limit_price <= 0):
            raise ValueError(f"refusing to place a LIMIT order with non-positive price ({limit_price}) "
                             f"for {tradingsymbol} — LTP unavailable or price not supplied")
        kw = dict(variety=_KC.VARIETY_REGULAR, exchange=exchange.upper(),
                  tradingsymbol=tradingsymbol.upper(), transaction_type=tx,
                  quantity=int(quantity), product=prod, order_type=otype)
        if otype == _KC.ORDER_TYPE_LIMIT:
            kw["price"] = limit_price
        order_id = self._kite.place_order(**kw)
        log.info(f"[LIVE-EQUITY] {transaction} {quantity} × {tradingsymbol} "
                 f"{product}/{order_type}"
                 f"{f' (marketable limit @{limit_price})' if req == 'MARKET' else ''} "
                 f"| order_id={order_id}")
        return str(order_id)

    def get_equity_ltp(self, tradingsymbol: str, exchange: str = "NSE") -> float:
        """Last traded price for a cash-segment symbol (e.g. NSE:TATAPOWER).
        Raises ValueError if no usable quote is returned."""
        key = f"{exchange.upper()}:{tradingsymbol.upper()}"
        data = self._kite.ltp([key]) or {}
        rec = data.get(key) or {}
        ltp = rec.get("last_price")
        if ltp is None or float(ltp) <= 0:
            raise ValueError(f"no last price for {key} (market closed or unknown symbol)")
        return float(ltp)

    def get_order_status(self, order_id: str) -> Optional[Order]:
        try:
            history = self._kite.order_history(order_id)
            if not history:
                return None
            last = history[-1]
            order = Order(
                symbol=last.get("tradingsymbol", ""),
                exchange=last.get("exchange", ""),
                option_type="",
                strike=0,
                expiry="",
                transaction=last.get("transaction_type", ""),
                quantity=last.get("quantity", 0),
                order_id=order_id,
                status=last.get("status", "UNKNOWN"),
                avg_price=float(last.get("average_price") or 0),
            )
            return order
        except Exception as e:
            log.error(f"get_order_status failed for {order_id}: {e}")
            return None

    def get_ltp(
        self,
        symbol: str,
        exchange: str,
        strike: int,
        option_type: str,
        expiry: str,
    ) -> float:
        try:
            instrument_key = f"{exchange}:{symbol}"
            data = self._kite.ltp([instrument_key])
            price = data.get(instrument_key, {}).get("last_price", 0.0)
            return float(price)
        except Exception as e:
            log.error(f"get_ltp failed for {symbol}: {e}")
            return 0.0

    def get_positions(self) -> list[Order]:
        try:
            raw = self._kite.positions().get("day", [])
            orders = []
            for pos in raw:
                if pos.get("quantity", 0) != 0:
                    orders.append(Order(
                        symbol=pos.get("tradingsymbol", ""),
                        exchange=pos.get("exchange", ""),
                        option_type="",
                        strike=0,
                        expiry="",
                        transaction="BUY" if pos["quantity"] > 0 else "SELL",
                        quantity=abs(pos["quantity"]),
                        status="OPEN",
                        avg_price=float(pos.get("average_price") or 0),
                    ))
            return orders
        except Exception as e:
            log.error(f"get_positions failed: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        try:
            from kiteconnect import KiteConnect as _KC
            self._kite.cancel_order(variety=_KC.VARIETY_REGULAR, order_id=order_id)
            return True
        except Exception as e:
            log.error(f"cancel_order failed for {order_id}: {e}")
            return False

    def place_bracket_order_exit(self, order_id: str, exit_price: float) -> str:
        """Square off an open MIS position at market price."""
        try:
            status_order = self.get_order_status(order_id)
            if not status_order or status_order.status != "COMPLETE":
                log.warning(f"Cannot exit — order {order_id} not in COMPLETE state")
                return ""
            from kiteconnect import KiteConnect as _KC
            exit_id = self._kite.place_order(
                variety=_KC.VARIETY_REGULAR,
                exchange=status_order.exchange,
                tradingsymbol=status_order.symbol,
                transaction_type=_KC.TRANSACTION_TYPE_SELL,
                quantity=status_order.quantity,
                product=_KC.PRODUCT_MIS,
                order_type=_KC.ORDER_TYPE_MARKET,
            )
            log.info(f"[LIVE EXIT] Squared off {order_id} → exit_order={exit_id}")
            return str(exit_id)
        except Exception as e:
            log.error(f"Exit order failed: {e}")
            return ""


    # ── 1-min historical data ──────────────────────────────────────────────────

    @staticmethod
    def get_index_token(instrument: str) -> int:
        """Kite instrument token for NSE/BSE index spot."""
        return {"NIFTY": 256265, "SENSEX": 265}.get(instrument.upper(), 256265)

    def _nse_equity_token(self, symbol: str) -> Optional[int]:
        """Kite instrument_token for an NSE cash-equity tradingsymbol (e.g.
        RELIANCE). Cached once per day."""
        today = date.today()
        if getattr(self, "_nse_map_date", None) != today:
            token_map: dict = {}
            try:
                for i in self._kite.instruments("NSE"):
                    if i.get("instrument_type") == "EQ":
                        token_map[i.get("tradingsymbol")] = i.get("instrument_token")
            except Exception as e:
                log.error(f"NSE instrument refresh failed: {e}")
            self._nse_token_map = token_map
            self._nse_map_date = today
        return self._nse_token_map.get(symbol)

    def get_equity_intraday_bars(self, symbol: str, interval: str = "5minute") -> list:
        """Today's intraday OHLCV for an NSE cash equity via Kite historical API —
        REAL-TIME (no 15-min delay). Returns scanner-shaped dicts
        [{t,o,h,l,c,v}], or [] when the symbol/token or data isn't available."""
        from datetime import datetime, timedelta, timezone
        IST   = timezone(timedelta(hours=5, minutes=30))
        now   = datetime.now(IST)
        from_ = now.replace(hour=9, minute=15, second=0, microsecond=0)
        token = self._nse_equity_token(symbol)
        if not token:
            return []
        try:
            raw = self._kite.historical_data(token, from_, now, interval, continuous=False)
        except Exception as e:
            log.warning(f"equity intraday fetch failed for {symbol}: {type(e).__name__}")
            return []
        return [
            {"t": r["date"].strftime("%Y-%m-%d %H:%M"),
             "o": float(r["open"]), "h": float(r["high"]),
             "l": float(r["low"]),  "c": float(r["close"]),
             "v": int(r["volume"]) if r.get("volume") else 0}
            for r in (raw or [])
        ]

    def get_1min_bars(self, instrument: str, n: int = 60) -> list:
        """
        Fetch today's 1-min OHLCV from Kite historical API.
        Returns list of Candle objects — real-time, no delay.
        Retries up to 3 times with exponential backoff on connection errors.
        """
        from src.data.candle_builder import Candle
        from datetime import datetime, timedelta, timezone
        IST   = timezone(timedelta(hours=5, minutes=30))
        now   = datetime.now(IST)
        from_ = now.replace(hour=9, minute=15, second=0, microsecond=0)
        token = self.get_index_token(instrument)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                raw = self._kite.historical_data(
                    token, from_, now, "minute", continuous=False
                )
                if not raw:
                    return []
                return [
                    Candle(
                        timestamp=r["date"],
                        open=float(r["open"]),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                        volume=int(r["volume"]) if r["volume"] > 0 else 1,
                    )
                    for r in raw[-n:]
                ]
            except Exception as e:
                is_last = (attempt == max_retries - 1)
                wait    = 2 ** (attempt + 1)   # 2, 4, 8 seconds
                if is_last:
                    log.error(f"get_1min_bars failed for {instrument} after {max_retries} attempts: {e}")
                    return []
                log.warning(
                    f"get_1min_bars attempt {attempt + 1}/{max_retries} failed for {instrument}"
                    f" — retrying in {wait}s: {type(e).__name__}"
                )
                time.sleep(wait)
                # Re-seat the access token so the next HTTP call starts fresh
                try:
                    self._kite.set_access_token(self._access_token)
                except Exception:
                    pass
        return []

    # ── WebSocket ticker ───────────────────────────────────────────────────────

    def start_ticker(self, tokens: list[int], on_tick) -> None:
        """
        Start KiteTicker WebSocket in a background thread.
        `on_tick(ticks)` is called for each tick batch from Kite.
        """
        try:
            from kiteconnect import KiteTicker
        except ImportError:
            log.error("kiteconnect not installed — cannot start WebSocket")
            return

        self._ticker = KiteTicker(self._api_key, self._access_token)

        def _on_ticks(ws, ticks):
            on_tick(ticks)

        def _on_connect(ws, response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)
            log.info(f"WebSocket subscribed to tokens: {tokens}")

        def _on_error(ws, code, reason):
            log.error(f"WebSocket error {code}: {reason}")

        def _on_close(ws, code, reason):
            log.info(f"WebSocket closed {code}: {reason}")

        self._ticker.on_ticks    = _on_ticks
        self._ticker.on_connect  = _on_connect
        self._ticker.on_error    = _on_error
        self._ticker.on_close    = _on_close
        self._ticker.connect(threaded=True)
        log.info("KiteTicker WebSocket started")

    def stop_ticker(self) -> None:
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass
            self._ticker = None
            log.info("KiteTicker stopped")


def create_kite_broker(settings: dict) -> KiteBroker:
    """
    Build KiteBroker from (in priority order):
      1. KITE_API_KEY / KITE_ACCESS_TOKEN env vars
      2. config/.kite_token  (written by --mode autologin each morning)
      3. config/settings.yaml broker.kite.*
    """
    from src.broker.kite_auto_login import load_cached_token

    kite_cfg = settings.get("broker", {}).get("kite", {})
    api_key  = os.environ.get("KITE_API_KEY") or kite_cfg.get("api_key", "")

    # Token: env var → daily cache file → settings.yaml (static, will be stale)
    access_token = (
        os.environ.get("KITE_ACCESS_TOKEN")
        or load_cached_token()
        or kite_cfg.get("access_token", "")
    )

    if not api_key or api_key == "YOUR_KITE_API_KEY":
        raise ValueError(
            "Kite API key not set.\n"
            "  Option A (recommended): add to config/settings.local.yaml\n"
            "                          then run: python main.py --mode autologin\n"
            "  Option B: set env var:  export KITE_API_KEY=your_key"
        )
    if not access_token:
        raise ValueError(
            "Kite access token not found.\n"
            "  Run automated login:  python main.py --mode autologin\n"
            "  Or manual login:      python main.py --mode login\n"
            "  Then set env var:     export KITE_ACCESS_TOKEN=your_token"
        )

    return KiteBroker(api_key=api_key, access_token=access_token)


def kite_login_flow(settings: dict) -> str:
    """
    Interactive Kite OAuth login — call once each morning.
    Prints the access token for the user to store.
    """
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise RuntimeError("Run: pip install kiteconnect")

    kite_cfg = settings.get("broker", {}).get("kite", {})
    api_key    = os.environ.get("KITE_API_KEY")    or kite_cfg.get("api_key", "")
    api_secret = os.environ.get("KITE_API_SECRET") or kite_cfg.get("api_secret", "")

    if not api_key or api_key == "YOUR_KITE_API_KEY":
        raise ValueError("Set KITE_API_KEY env var or config/settings.yaml before login.")

    kite = KiteConnect(api_key=api_key)
    print(f"\n{'='*60}")
    print("  Kite Connect Login")
    print(f"{'='*60}")
    print(f"\n1. Open this URL in your browser:\n\n   {kite.login_url()}\n")
    print("2. Login with your Zerodha credentials.")
    print("3. After redirect, copy the 'request_token' from the URL.\n")

    request_token = input("Paste request_token here: ").strip()
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    print(f"\n{'='*60}")
    print(f"  Access token:  {access_token}")
    print(f"{'='*60}")
    print("\nTo use this session, run:")
    print(f"  export KITE_ACCESS_TOKEN={access_token}")
    print("  python main.py --mode paper    # paper trading")
    print("  python main.py --mode live     # real trading\n")

    return access_token
