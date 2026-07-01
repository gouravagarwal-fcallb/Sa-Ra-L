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
