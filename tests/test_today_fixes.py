"""
Regression tests for the 2026-07-01 stabilisation fixes.

Locks in the behaviour of: trade-accounting lifecycle, the telemetry analysis
trail, portfolio-risk math, 1D/1W weekly resampling, FII-net parsing, the
IPv4-preference network shim, and the equity-order guards. Pure/offline — no
Kite, no network. Run with `pytest tests/test_today_fixes.py` or directly.
"""
import os
import sys
import socket

# Make `src` importable whether run via pytest or directly (python tests/...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Trade accounting: ENTRY → open → EXIT → closed with computed P&L ──────────
def test_trade_accounting_nifty_contract():
    from src.brahmastra.api.state import BrahmastraState
    st = BrahmastraState()
    st.record_trade_event({"event": "ENTRY", "instrument": "NIFTY", "option_type": "CE",
                           "strike": 25000, "price": 100.0, "quantity": 75, "direction": "BULL"})
    assert len(st.open_trades) == 1
    st.record_trade_event({"event": "MARK", "instrument": "NIFTY", "option_type": "CE",
                           "strike": 25000, "price": 118.0})
    pos = list(st.open_trades.values())[0]
    assert abs(pos.unrealised_pnl - (118 - 100) * 75) < 1e-6
    st.record_trade_event({"event": "TARGET", "instrument": "NIFTY", "option_type": "CE",
                           "strike": 25000, "price": 130.0, "quantity": 75, "pnl": 2250.0})
    assert len(st.open_trades) == 0
    c = list(st.closed_trades)[-1]
    assert c["entry_price"] == 100.0 and c["exit_price"] == 130.0 and c["net_pnl"] == 2250.0


def test_trade_accounting_bbexpiry_contract():
    """bb_expiry uses action/symbol/ltp and drops strike on close — the exit must
    still match the open by instrument and inherit its labels."""
    from src.brahmastra.api.state import BrahmastraState
    st = BrahmastraState()
    st.record_trade_event({"action": "open", "symbol": "SENSEX", "strike": 81000,
                           "ltp": 50.0, "qty": 20, "direction": "BEAR"})
    assert list(st.open_trades.values())[0].entry_price == 50.0
    st.record_trade_event({"action": "close", "symbol": "SENSEX", "reason": "SL",
                           "pnl": -600.0, "exit_ltp": 20.0})
    c = list(st.closed_trades)[-1]
    assert c["entry_price"] == 50.0 and c["exit_price"] == 20.0
    assert c["net_pnl"] == -600.0 and str(c["strike"]) == "81000"
    assert len(st.open_trades) == 0


def test_trade_accounting_engine_pnl_wins():
    from src.brahmastra.api.state import BrahmastraState
    st = BrahmastraState()
    st.record_trade_event({"event": "ENTRY", "instrument": "NIFTY", "option_type": "PE",
                           "strike": 24000, "price": 80.0, "quantity": 75})
    st.record_trade_event({"event": "SL", "instrument": "NIFTY", "option_type": "PE",
                           "strike": 24000, "price": 60.0, "quantity": 75, "pnl": -1500.0})
    assert list(st.closed_trades)[-1]["net_pnl"] == -1500.0


# ── Telemetry: signal OR last_signal counts, throttled; notable is immediate ──
def _fake_runner():
    import threading
    from src.api.runner import ApiPortfolioRunner
    r = ApiPortfolioRunner.__new__(ApiPortfolioRunner)
    r._last_analysis_log = {}
    r._lock = threading.Lock()
    r._statuses = {}
    return r


class _FakeState:
    def __init__(self): self.logs = []
    def update_session(self, **k): pass
    def add_log(self, cat, msg): self.logs.append((cat, msg))
    def record_trade_event(self, ev): pass


def _analysis_count(st):
    return sum(1 for c, _ in st.logs if c == "ANALYSIS")


def test_telemetry_last_signal_throttled():
    r, st = _fake_runner(), _FakeState()
    r._mirror_to_state(st, "INRUSD_v1", {"last_signal": "USDINR 95.04 -> HOLD", "notable": False})
    r._mirror_to_state(st, "INRUSD_v1", {"last_signal": "USDINR 95.05 -> HOLD", "notable": False})
    assert _analysis_count(st) == 1              # 2nd within window is throttled
    r._last_analysis_log["INRUSD_v1"] -= 60      # advance past the throttle window
    r._mirror_to_state(st, "INRUSD_v1", {"last_signal": "USDINR 95.06 -> HOLD", "notable": False})
    assert _analysis_count(st) == 2


def test_telemetry_notable_immediate():
    r, st = _fake_runner(), _FakeState()
    r._mirror_to_state(st, "X", {"last_signal": "watching", "notable": False})
    r._mirror_to_state(st, "X", {"signal": "BUY setup", "notable": True})
    assert _analysis_count(st) == 2              # notable logs even inside the window


# ── Portfolio risk ───────────────────────────────────────────────────────────
def test_portfolio_risk_math():
    from src.api import portfolio_risk as pr
    import src.api.market as market
    _orig = market.get_market_summary
    market.get_market_summary = lambda: {"nifty": 25000.0, "sensex": 82000.0, "vix": 13.5}
    try:
        class FS:
            def __init__(self, ot): self._ot = ot
            def snapshot(self): return {"open_trades": self._ot}
        class Multi:
            def names(self): return ["S1", "S2"]
            def get(self, n):
                return FS({"a": {"instrument": "NIFTY", "strike": 25000, "option_type": "CE",
                                 "quantity": 75, "ltp": 120.0}}) if n == "S1" else \
                       FS({"b": {"instrument": "SENSEX", "strike": 81000, "option_type": "PE",
                                 "quantity": 20, "ltp": 90.0}})
        class Runner:
            def is_running(self, n): return True
        out = pr.compute_portfolio_risk(Multi(), Runner())
    finally:
        market.get_market_summary = _orig
    assert out["positions"] == 2
    assert abs(out["premium_at_risk"] - (75 * 120 + 20 * 90)) < 1.0     # exact max loss
    assert out["directional_lean"] in ("BULLISH", "BEARISH", "NEUTRAL")
    assert out["var_1d_95"] >= 0
    # A long CE profits on an up move; a long PE profits on a down move.
    up5 = next(s["pnl"] for s in out["stress"] if s["move_pct"] == 5.0)
    dn5 = next(s["pnl"] for s in out["stress"] if s["move_pct"] == -5.0)
    assert up5 > 0 and dn5 > 0        # each leg wins on its own side


def test_portfolio_risk_empty_book():
    from src.api import portfolio_risk as pr
    import src.api.market as market
    _orig = market.get_market_summary
    market.get_market_summary = lambda: {"nifty": 25000.0, "vix": 13.0}
    try:
        class Multi:
            def names(self): return []
            def get(self, n): raise KeyError
        class Runner:
            def is_running(self, n): return False
        out = pr.compute_portfolio_risk(Multi(), Runner())
    finally:
        market.get_market_summary = _orig
    assert out["positions"] == 0 and out["premium_at_risk"] == 0


# ── Charts: daily → weekly resample ──────────────────────────────────────────
def test_weekly_resample():
    from src.api.charts import _resample_daily_to_weekly
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05",   # week 1
            "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12",   # week 2
            "2026-06-15", "2026-06-16"]                                              # partial week 3
    bars = [{"t": d, "o": 100 + i, "h": 100 + i + 2, "l": 100 + i - 2, "c": 100 + i + 1, "v": 1000}
            for i, d in enumerate(days)]
    wk = _resample_daily_to_weekly(bars)
    assert len(wk) == 3
    assert wk[0]["o"] == 100 and wk[0]["c"] == 105 and wk[0]["h"] == 106 and wk[0]["v"] == 5000
    assert wk[2]["v"] == 2000        # partial week keeps only its 2 days


def test_weekly_resample_kite_datetime_format():
    """Kite daily bars carry 't' as 'YYYY-MM-DD 00:00' — must still parse."""
    from src.api.charts import _resample_daily_to_weekly
    bars = [{"t": "2026-06-01 00:00", "o": 10, "h": 12, "l": 9, "c": 11, "v": 5},
            {"t": "2026-06-02 00:00", "o": 11, "h": 13, "l": 10, "c": 12, "v": 5}]
    wk = _resample_daily_to_weekly(bars)
    assert len(wk) == 1 and wk[0]["o"] == 10 and wk[0]["c"] == 12


# ── FII net parsing ──────────────────────────────────────────────────────────
def test_fii_parse():
    from src.brahmastra.data.fetchers.premarket_fetch import _parse_fii_net
    data = [
        {"category": "DII **", "netValue": "999.89"},
        {"category": "FII/FPI **", "netValue": "1,820.45"},   # thousands separator
    ]
    assert _parse_fii_net(data) == 1820.45
    assert _parse_fii_net([{"category": "FII/FPI **", "netValue": "-2557.00"}]) == -2557.0
    assert _parse_fii_net([{"category": "DII **", "netValue": "5"}]) is None   # no FII row
    assert _parse_fii_net([]) is None


# ── IPv4 preference shim ──────────────────────────────────────────────────────
def test_prefer_ipv4():
    from src.utils.net import prefer_ipv4
    try:
        prefer_ipv4(True)
        res = socket.getaddrinfo("example.com", 443, type=socket.SOCK_STREAM)
        assert res and all(r[0] == socket.AF_INET for r in res)
    finally:
        prefer_ipv4(False)      # restore OS default for other tests


# ── Equity order guards (needs kiteconnect — skipped if absent) ───────────────
def test_equity_order_rejects_bad_price():
    try:
        import kiteconnect  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("kiteconnect not installed in this environment")
    from src.broker.kite_broker import KiteBroker
    b = KiteBroker.__new__(KiteBroker)

    class _Kite:
        def ltp(self, keys): return {}          # no quote → get_equity_ltp raises
        def place_order(self, **kw): return "1"
    b._kite = _Kite()
    import pytest
    with pytest.raises(ValueError):
        b.place_equity_order("TATAPOWER", order_type="MARKET")   # LTP missing
    with pytest.raises(ValueError):
        b.place_equity_order("TATAPOWER", order_type="LIMIT", price=0)  # zero limit


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = skipped = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except BaseException as e:      # pytest's Skipped subclasses BaseException
            if e.__class__.__name__ == "Skipped":
                skipped += 1
                print(f"  SKIP  {t.__name__}: {e}")
            else:
                failed += 1
                print(f"  FAIL  {t.__name__}: {e}")
                traceback.print_exc()
    print(f"\n  {passed} passed, {failed} failed, {skipped} skipped")
