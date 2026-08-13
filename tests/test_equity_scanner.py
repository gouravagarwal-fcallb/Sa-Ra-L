"""
Intraday equity scanner — logic tests (pure, no network).

Guards: real signals from bars, an honest no-data empty-state (never fabricated),
and that the ranking is by conviction.
"""
from src.api.equity_scanner import score_symbol, scan_equities


def _breakout_bars():
    return [{"o": 100, "h": 101, "l": 99.5, "c": 100.5, "v": 1000},
            {"o": 100.5, "h": 101, "l": 100, "c": 100.8, "v": 1100},
            {"o": 100.8, "h": 101, "l": 100.5, "c": 100.9, "v": 900},
            {"o": 100.9, "h": 102.5, "l": 100.8, "c": 102.3, "v": 2500},
            {"o": 102.3, "h": 103.5, "l": 102, "c": 103.2, "v": 3000},
            {"o": 103.2, "h": 104.2, "l": 103, "c": 104.0, "v": 3500}]


def _breakdown_bars():
    return [{"o": 100, "h": 100.5, "l": 99.5, "c": 100.0, "v": 1000},
            {"o": 100, "h": 100.2, "l": 99.6, "c": 99.8, "v": 1100},
            {"o": 99.8, "h": 100, "l": 99.5, "c": 99.7, "v": 900},
            {"o": 99.7, "h": 99.8, "l": 98.5, "c": 98.7, "v": 2600},
            {"o": 98.7, "h": 98.9, "l": 97.8, "c": 97.9, "v": 3100},
            {"o": 97.9, "h": 98.0, "l": 96.8, "c": 96.9, "v": 3600}]


def test_breakout_scores_long():
    r = score_symbol("UP", _breakout_bars())
    assert r["orb"] == "BREAKOUT_UP"
    assert r["bias"] == "LONG" and r["score"] > 0
    assert r["vwap_pos"] == "ABOVE"


def test_breakdown_scores_short():
    r = score_symbol("DN", _breakdown_bars())
    assert r["orb"] == "BREAKDOWN"
    assert r["bias"] == "SHORT" and r["score"] < 0


def test_too_few_bars_returns_none():
    assert score_symbol("X", _breakout_bars()[:2]) is None


def test_scan_ranks_by_conviction_and_is_honest_when_empty():
    up, dn = _breakout_bars(), _breakdown_bars()
    data = {"UP": up, "DN": dn, "FLAT": []}
    d = scan_equities(universe=list(data), fetch=lambda s: data[s])
    assert d["status"] == "ok"
    # FLAT (no bars) dropped; UP and DN both present, ranked by |score|
    syms = [r["symbol"] for r in d["watchlist"]]
    assert set(syms) == {"UP", "DN"}
    assert abs(d["watchlist"][0]["score"]) >= abs(d["watchlist"][1]["score"])


def test_scan_no_data_never_fabricates():
    d = scan_equities(universe=["A", "B"], fetch=lambda s: [])
    assert d["status"] == "no_data"
    assert d["watchlist"] == []
    assert d["note"]                     # tells the operator why
