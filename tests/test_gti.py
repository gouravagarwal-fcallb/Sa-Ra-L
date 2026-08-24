"""
Tests for the GTI demand/supply zone research modules (src/research/gti).

Offline only — synthetic candles + a mock Kite client (no network). Validates
zone detection, the no-lookahead backtest, and the fetcher's chunking + cache.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.research.gti.gti_zones import detect_zones, ZoneConfig
from src.research.gti.gti_backtest import run_backtest, BacktestConfig, _synth_intraday
from src.research.gti.kite_data import fetch_history, _MockKite, _date_chunks
from src.research.gti.confluence_ab import (
    run_confluence_ab, run_confluence_select, run_sweep, ConfluenceConfig,
    _trade_direction,
)


def test_zone_detection_synthetic():
    zones = detect_zones(_synth_intraday(days=10), ZoneConfig())
    assert len(zones) > 0
    for z in zones:
        assert z.side in ("demand", "supply")
        assert 0.0 <= z.strength <= 100.0
        assert z.height >= 0.0


def test_backtest_trades_and_no_lookahead():
    res = run_backtest(_synth_intraday(days=25), ZoneConfig(), BacktestConfig())
    assert len(res.trades) > 0
    for t in res.trades:
        # entry must precede (or equal, same-bar) exit — never look ahead
        assert t.entry_time <= t.exit_time
        assert t.exit_reason in ("target", "stop", "eod")
        assert t.risk > 0


def test_confirm_mode_fills_at_close_no_same_bar_exit():
    df = _synth_intraday(days=30)
    res = run_backtest(df, ZoneConfig(), BacktestConfig(entry_mode="confirm"))
    # confirm-mode entries fill at the confirmation candle's close, so no trade
    # may exit on its own entry bar (that would imply an intrabar path we can't see)
    for t in res.trades:
        assert not (t.entry_time == t.exit_time and t.exit_reason in ("target", "stop"))
    # and the two modes are genuinely different code paths
    limit = run_backtest(df, ZoneConfig(), BacktestConfig(entry_mode="limit"))
    assert [t.entry for t in res.trades] != [t.entry for t in limit.trades]


def test_min_strength_filter_reduces_trades():
    df = _synth_intraday(days=25)
    loose = run_backtest(df, ZoneConfig(), BacktestConfig(min_strength=0))
    strict = run_backtest(df, ZoneConfig(), BacktestConfig(min_strength=80))
    assert len(strict.trades) <= len(loose.trades)


def test_kite_chunking_respects_day_cap():
    ch = _date_chunks(datetime(2026, 1, 1), datetime(2026, 6, 30), 90)
    assert ch and all((b - a).days + 1 <= 90 for a, b in ch)


def test_mock_kite_incremental_cache(tmp_path):
    mk = _MockKite()
    d1 = fetch_history(mk, 256265, "2026-05-01", "2026-06-30", "5minute",
                       cache_dir=str(tmp_path), throttle_sec=0.0, verbose=False)
    assert len(d1) > 0 and len(mk.calls) > 0
    # second identical fetch must be fully served from cache — zero new API calls
    mk2 = _MockKite()
    fetch_history(mk2, 256265, "2026-05-01", "2026-06-30", "5minute",
                  cache_dir=str(tmp_path), throttle_sec=0.0, verbose=False)
    assert len(mk2.calls) == 0


def test_trade_direction_resolver():
    import pandas as pd
    df = pd.DataFrame([
        {"option_type": "CE", "action": "buy"},   # bullish
        {"option_type": "PE", "action": "buy"},    # bearish
        {"option_type": "CE", "action": "sell"},   # sell call -> bearish
        {"side": "SHORT"},                          # bearish
        {"foo": "bar"},                             # undeterminable
    ])
    got = [_trade_direction(df.iloc[i], df.columns) for i in range(len(df))]
    assert got == [1, -1, -1, -1, None]


def test_confluence_ab_partitions_and_no_lookahead():
    import numpy as np
    import pandas as pd
    spot = _synth_intraday(days=40)
    rng = np.random.default_rng(3)
    idx = np.sort(rng.choice(len(spot), size=30, replace=False))
    rows = [{"entry_time": spot.index[i],
             "side": "long" if k % 2 == 0 else "short",
             "pnl": float(rng.normal(0, 100))} for k, i in enumerate(idx)]
    res = run_confluence_ab(pd.DataFrame(rows), spot,
                            ConfluenceConfig(veto_pct=0.6, require_fresh=False))
    assert "error" not in res
    # A is the whole set; B (kept) + vetoed must reconcile to A
    assert res["A_all"]["n"] == res["n_total"]
    assert res["B_kept"]["n"] + res["n_vetoed"] == res["n_total"]
    # totals partition exactly (float tolerance)
    assert abs(res["B_kept"]["total"] + res["vetoed"]["total"]
               - res["A_all"]["total"]) < 1e-6


def test_confluence_aligned_and_sweep():
    import numpy as np
    import pandas as pd
    spot = _synth_intraday(days=40)
    rng = np.random.default_rng(5)
    idx = np.sort(rng.choice(len(spot), size=30, replace=False))
    rows = [{"entry_time": spot.index[i],
             "side": "long" if k % 2 == 0 else "short",
             "pnl": float(rng.normal(0, 100))} for k, i in enumerate(idx)]
    trades = pd.DataFrame(rows)
    # aligned selector: aligned + rest must reconcile to all
    sel = run_confluence_select(trades, spot, ConfluenceConfig(veto_pct=0.6, require_fresh=False))
    assert "error" not in sel
    assert sel["aligned"]["n"] + sel["rest"]["n"] == sel["n_total"]
    assert abs(sel["aligned"]["total"] + sel["rest"]["total"] - sel["A_all"]["total"]) < 1e-6
    # sweep: one row per band, vetoed count monotonically non-decreasing with band
    sw = run_sweep(trades, spot, [0.4, 0.8, 1.2], ConfluenceConfig(require_fresh=False))
    assert "error" not in sw and len(sw["bands"]) == 3
    vetoed = [r["n_vetoed"] for r in sw["bands"]]
    assert vetoed == sorted(vetoed)   # wider band never vetoes fewer


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            import inspect
            if "tmp_path" in inspect.signature(t).parameters:
                import tempfile, pathlib
                t(pathlib.Path(tempfile.mkdtemp()))
            else:
                t()
            passed += 1; print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"  FAIL  {t.__name__}: {e}"); traceback.print_exc()
    print(f"\n  {passed} passed, {failed} failed")
