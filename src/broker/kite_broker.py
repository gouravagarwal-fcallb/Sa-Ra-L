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

        self._kite = KiteConnect(api_key=api_key)
        self._kite.set_access_token(access_token)

        # Instrument cache: loaded once per day per exchange
        self._instruments_nfo: list[dict] = []
        self._instruments_bfo: list[dict] = []
        self._inst_cache_date: Optional[date] = None

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


def create_kite_broker(settings: dict) -> KiteBroker:
    """Build KiteBroker from settings dict or environment variables."""
    kite_cfg = settings.get("broker", {}).get("kite", {})
    api_key      = os.environ.get("KITE_API_KEY")      or kite_cfg.get("api_key", "")
    access_token = os.environ.get("KITE_ACCESS_TOKEN") or kite_cfg.get("access_token", "")

    if not api_key or api_key == "YOUR_KITE_API_KEY":
        raise ValueError(
            "Kite API key not set.\n"
            "  Set env var:  export KITE_API_KEY=your_key\n"
            "  Or update:    config/settings.yaml → broker.kite.api_key"
        )
    if not access_token:
        raise ValueError(
            "Kite access token not set.\n"
            "  Generate it:  python main.py --mode login\n"
            "  Then set:     export KITE_ACCESS_TOKEN=your_token"
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
