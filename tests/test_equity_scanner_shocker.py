"""
Volume-shocker tests for the Equity Scanner — pure `score_symbol` / `scan_equities`
math, offline (no network). A "shocker" = this bar's volume >= 2x the session's
average bar volume, graded ELEVATED (1.5x) / HIGH (2x) / EXTREME (3x).

    python -m pytest tests/test_equity_scanner_shocker.py -q
"""
from src.api import equity_scanner as es


def _bars(vols, base=100.0):
    """Simple valid intraday bars carrying the given per-bar volumes."""
    out = []
    for i, v in enumerate(vols):
        c = base + i * 0.1
        out.append({"o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": v})
    return out


def test_shocker_tiers_and_reason():
    extreme = es.score_symbol("X", _bars([100, 100, 100, 100, 100, 300]))  # 3.0x
    assert extreme["vol_ratio"] == 3.0
    assert extreme["vol_shocker"] is True
    assert extreme["vol_tier"] == "EXTREME"
    assert "🔥 VOL SHOCKER" in extreme["reason"]

    normal = es.score_symbol("X", _bars([100, 100, 100, 100, 100, 100]))   # 1.0x
    assert normal["vol_shocker"] is False
    assert normal["vol_tier"] == "NORMAL"
    assert "🔥" not in normal["reason"]


def test_shocker_thresholds():
    high = es.score_symbol("X", _bars([100, 100, 100, 100, 100, 200]))     # 2.0x
    assert high["vol_ratio"] == 2.0 and high["vol_tier"] == "HIGH" and high["vol_shocker"] is True
    # 1.5x is ELEVATED but NOT a shocker (the shocker line is 2x)
    elev = es.score_symbol("X", _bars([100, 100, 100, 100, 100, 150]))     # 1.5x
    assert elev["vol_ratio"] == 1.5 and elev["vol_tier"] == "ELEVATED" and elev["vol_shocker"] is False


def test_scan_ranks_by_volume_shocker():
    data = {
        "AAA": _bars([100, 100, 100, 100, 100, 120]),   # 1.2x  (not a shocker)
        "BBB": _bars([100, 100, 100, 100, 100, 350]),   # 3.5x  (biggest)
        "CCC": _bars([100, 100, 100, 100, 100, 220]),   # 2.2x
    }
    scan = es.scan_equities(universe=list(data), fetch=lambda s: data[s],
                            rank_by="volume_shocker")
    order = [r["symbol"] for r in scan["watchlist"]]
    assert order[0] == "BBB"                 # highest vol_ratio ranked first
    assert scan["rank_by"] == "volume_shocker"
    assert scan["shockers"] == 2             # BBB + CCC are >= 2x

    # default ranking (by conviction) is unchanged
    scan2 = es.scan_equities(universe=list(data), fetch=lambda s: data[s])
    assert scan2["rank_by"] == "score"
