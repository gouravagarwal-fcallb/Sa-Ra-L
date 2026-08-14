"""
Next-day follow-up grader — logic tests (pure, no network).

Guards: LONG hits when the stock rose, SHORT hits when it fell; the in-direction
return is signed to the call; NEUTRAL picks are excluded from the hit-rate;
picks with no next-day close are skipped; and the whole flow (snapshot → grade)
works through injectable fetchers with an honest empty-state.
"""
import json
import os

from src.api.equity_scanner import (
    grade_followup, save_scan_snapshot, followup_analysis, list_snapshots,
)


def _picks():
    return [
        {"symbol": "UP", "bias": "LONG", "score": 60, "ltp": 100.0},
        {"symbol": "DN", "bias": "SHORT", "score": -55, "ltp": 200.0},
        {"symbol": "FLAT", "bias": "NEUTRAL", "score": 5, "ltp": 50.0},
    ]


def test_long_hits_when_price_rises():
    r = grade_followup(_picks(), {"UP": 105.0, "DN": 200.0, "FLAT": 50.0})
    up = next(p for p in r["picks"] if p["symbol"] == "UP")
    assert up["hit"] is True
    assert up["dir_return_pct"] == 5.0          # +5% in the LONG direction


def test_short_hits_when_price_falls():
    r = grade_followup(_picks(), {"UP": 100.0, "DN": 190.0, "FLAT": 50.0})
    dn = next(p for p in r["picks"] if p["symbol"] == "DN")
    assert dn["hit"] is True                     # fell 5% → a winning short
    assert dn["dir_return_pct"] == 5.0           # signed to the call = positive


def test_short_misses_when_price_rises():
    r = grade_followup(_picks(), {"UP": 100.0, "DN": 210.0, "FLAT": 50.0})
    dn = next(p for p in r["picks"] if p["symbol"] == "DN")
    assert dn["hit"] is False
    assert dn["dir_return_pct"] == -5.0


def test_neutral_excluded_from_hit_rate():
    r = grade_followup(_picks(), {"UP": 105.0, "DN": 190.0, "FLAT": 60.0})
    assert r["directional"] == 2                  # only LONG + SHORT counted
    assert r["hits"] == 2 and r["hit_rate_pct"] == 100.0


def test_missing_next_close_is_skipped():
    r = grade_followup(_picks(), {"UP": 105.0})   # DN + FLAT have no close
    assert r["graded"] == 1 and r["directional"] == 1


def test_snapshot_and_followup_roundtrip(tmp_path):
    snap_dir = str(tmp_path)
    scan = {
        "status": "ok", "generated_at": "2026-08-13T15:20:00+05:30",
        "watchlist": [
            {"symbol": "UP", "bias": "LONG", "score": 60, "ltp": 100.0,
             "orb": "BREAKOUT_UP", "vwap_pos": "ABOVE", "reason": "x"},
            {"symbol": "DN", "bias": "SHORT", "score": -55, "ltp": 200.0,
             "orb": "BREAKDOWN", "vwap_pos": "BELOW", "reason": "y"},
        ],
    }
    path = save_scan_snapshot(scan, snap_dir=snap_dir)
    assert os.path.isfile(path)
    assert list_snapshots(snap_dir) == ["2026-08-13"]

    out = followup_analysis(
        snap_dir=snap_dir,
        fetch_next=lambda syms, day: {"UP": 104.0, "DN": 196.0})
    assert out["status"] == "ok"
    assert out["snapshot_date"] == "2026-08-13"
    assert out["hit_rate_pct"] == 100.0          # both calls paid off


def test_followup_honest_when_no_snapshots(tmp_path):
    out = followup_analysis(snap_dir=str(tmp_path))
    assert out["status"] == "no_snapshots"
    assert out["picks"] == [] and out["note"]


def test_followup_pending_when_next_day_absent(tmp_path):
    snap_dir = str(tmp_path)
    save_scan_snapshot({
        "status": "ok", "generated_at": "2026-08-13T15:20:00+05:30",
        "watchlist": [{"symbol": "UP", "bias": "LONG", "score": 60, "ltp": 100.0}],
    }, snap_dir=snap_dir)
    out = followup_analysis(snap_dir=snap_dir, fetch_next=lambda syms, day: {})
    assert out["status"] == "pending" and out["picks"] == []
