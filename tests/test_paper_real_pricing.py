"""
Guard for the paper-pricing fix (2026-07-07).

Before the fix, paper strategies priced option fills with a Black-Scholes model
instead of the real market quote — badly overpricing cheap OTM/expiry options
(a 24600 CE "entered" at ₹26.16 when its real whole-day range was ₹3.25-9.90,
producing a fantasy +₹45,709 expiry day). The fix: PaperBroker serves real,
read-only Kite quotes, and each engine prices from the broker in paper too, with
the model as an offline fallback only.

These tests assert:
  1. PaperBroker returns the real quote when a quote source is wired.
  2. PaperBroker falls back to 0.0 (→ caller's model) when the quote fails.
  3. An engine's _get_ltp uses the quoted price in PAPER mode (not the model).
"""
import pytest

from src.broker.paper_broker import PaperBroker


class _FakeQuoteBroker:
    """Stand-in for a read-only KiteBroker: real tradingsymbol + real LTP."""
    def __init__(self, ltp=5.75, fail=False):
        self._ltp = ltp
        self._fail = fail
        self.ltp_calls = []

    def get_tradingsymbol(self, instrument, expiry, strike, option_type):
        return f"{instrument}{strike}{option_type}", ("NFO" if instrument == "NIFTY" else "BFO")

    def get_ltp(self, symbol, exchange, strike, option_type, expiry):
        self.ltp_calls.append((symbol, exchange, strike, option_type, expiry))
        if self._fail:
            raise RuntimeError("quote unavailable")
        return self._ltp


def test_paperbroker_returns_real_quote():
    q = _FakeQuoteBroker(ltp=5.75)
    pb = PaperBroker(quote_broker=q)
    sym, exch = pb.get_tradingsymbol("NIFTY", "20260707", 24600, "CE")
    px = pb.get_ltp(sym, exch, 24600, "CE", "20260707")
    assert px == 5.75                         # the REAL price, not a ₹26 model value
    assert q.ltp_calls                         # it actually queried the quote source


def test_paperbroker_failed_quote_falls_back_to_zero():
    pb = PaperBroker(quote_broker=_FakeQuoteBroker(fail=True))
    px = pb.get_ltp("NIFTY24600CE", "NFO", 24600, "CE", "20260707")
    assert px == 0.0                           # 0 → caller uses its own model


def test_paperbroker_no_quote_source_does_not_query():
    # With no quote source the tradingsymbol is a bare index (model-only path).
    pb = PaperBroker(quote_broker=None)
    sym, exch = pb.get_tradingsymbol("NIFTY", "20260707", 24600, "CE")
    assert sym == "NIFTY" and exch == "NFO"


def test_expiry_engine_prices_from_broker_in_paper():
    """The EXPIRY_SCALPER engine must return the broker's real quote in PAPER
    mode, not the Black-Scholes model (which produced the ₹26.16 fantasy)."""
    from src.live.expiry_scalper_live import ExpiryScalperLive
    from datetime import date

    q = _FakeQuoteBroker(ltp=5.75)
    pb = PaperBroker(quote_broker=q)
    eng = ExpiryScalperLive.__new__(ExpiryScalperLive)   # skip heavy __init__
    eng.mode = "paper"
    eng.broker = pb
    eng.instrument = "NIFTY"
    eng.expiry = date(2026, 7, 7)

    # spot far from the strike → a model would price this OTM call very differently;
    # the engine must instead return the broker's real 5.75.
    px = eng._get_ltp(24504.0, 24600, "CE")
    assert px == 5.75
    assert q.ltp_calls


def test_rams_engine_prices_from_broker_in_paper():
    """RAMS runs on LiveEngine — it too must return the broker's real quote in
    PAPER mode. (An ITM SENSEX PE was once modelled below its intrinsic value,
    giving fantasy P&L.)"""
    from src.live.live_engine import LiveEngine
    from datetime import date

    q = _FakeQuoteBroker(ltp=305.0)
    pb = PaperBroker(quote_broker=q)
    eng = LiveEngine.__new__(LiveEngine)                  # skip heavy __init__
    eng.mode = "paper"
    eng.broker = pb

    # 77200 PE with SENSEX ~76995 is ITM by ~205 pts; the broker's real 305 must
    # win over any model (which had priced it ~110, below intrinsic).
    px = eng._get_ltp("SENSEX", date(2026, 7, 9), 77200, "PE")
    assert px == 305.0
    assert q.ltp_calls
