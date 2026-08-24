"""
Flow-metrics unit tests — pure VWAP/RVOL math + the honest no-volume guard.
Offline-runnable (no Kite, no network): exercises only the pure functions.

    python -m pytest tests/test_flow_metrics.py -q
"""
from src.api import flow_metrics as fm


def _bars():
    # typical price (H+L+C)/3 = 100, 102, 104 ; volume 100, 200, 300
    return [
        {"t": "2026-08-14 09:15", "o": 100, "h": 102, "l": 98, "c": 100, "v": 100},
        {"t": "2026-08-14 09:20", "o": 100, "h": 104, "l": 100, "c": 102, "v": 200},
        {"t": "2026-08-14 09:25", "o": 102, "h": 106, "l": 102, "c": 104, "v": 300},
    ]


def test_session_vwap_value():
    vw = fm.session_vwap(_bars())
    assert vw is not None
    # (100*100 + 102*200 + 104*300) / 600 = 102.666…
    assert abs(vw["vwap"] - 102.67) < 0.02
    assert vw["n"] == 3
    assert vw["last"] == 104


def test_spot_index_zero_volume_is_unavailable():
    """Spot indices carry volume=0 — must report unavailable, never a fake VWAP."""
    spot = [dict(b, v=0) for b in _bars()]
    assert fm.session_vwap(spot) is None
    blk = fm.compute_flow(spot, "NIFTY (spot)")
    assert blk["available"] is False
    assert "volume" in blk["note"].lower()
    # no fabricated numbers leaked through
    assert "vwap" not in blk


def test_relative_volume_and_state():
    # last=300 vs mean(prior positive [100,200])=150 -> rvol 2.0 -> HIGH
    rvol = fm.relative_volume(_bars())
    assert abs(rvol - 2.0) < 1e-9
    assert fm._rvol_state(rvol) == "HIGH"
    assert fm._rvol_state(0.5) == "LOW"
    assert fm._rvol_state(1.0) == "NORMAL"
    assert fm._rvol_state(None) is None


def test_relative_volume_needs_a_positive_baseline():
    assert fm.relative_volume([{"c": 100, "v": 0}]) is None          # single bar
    assert fm.relative_volume([{"c": 1, "v": 0}, {"c": 1, "v": 0}]) is None  # all zero


def test_compute_flow_available_block():
    blk = fm.compute_flow(_bars(), "NIFTY FUT · 5m")
    assert blk["available"] is True
    assert blk["position"] == "ABOVE"          # last 104 > vwap 102.67
    assert blk["vs_vwap_bps"] > 0
    assert blk["rvol_state"] == "HIGH"
    assert blk["n_bars"] == 3
    assert blk["source"] == "NIFTY FUT · 5m"


def test_position_below_and_at():
    below = [{"h": 100, "l": 100, "c": 100, "v": 100},
             {"h": 100, "l": 100, "c": 100, "v": 100},
             {"h": 90, "l": 90, "c": 90, "v": 100}]
    assert fm.compute_flow(below, "x")["position"] == "BELOW"
    flat = [{"h": 100, "l": 100, "c": 100, "v": 100},
            {"h": 100, "l": 100, "c": 100, "v": 100}]
    assert fm.compute_flow(flat, "x")["position"] == "AT"


def test_session_date_marks_stale():
    """Weekend/holiday fallback: a past session's data is flagged stale."""
    blk = fm.compute_flow(_bars(), "NIFTY FUT · 2026-08-14 session", session_date="2026-08-14")
    assert blk["available"] is True
    assert blk["stale"] is True
    assert blk["session_date"] == "2026-08-14"
    # today's live read is not stale
    live = fm.compute_flow(_bars(), "NIFTY FUT · 5m")
    assert live["stale"] is False
    assert live["session_date"] is None


def test_bar_date_extraction():
    assert fm._bar_date({"t": "2026-08-14 09:20"}) == "2026-08-14"
    assert fm._bar_date({}) == ""
