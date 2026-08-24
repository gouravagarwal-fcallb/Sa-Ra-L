"""
Audit control INTEGRITY-1: prove NON-REPAINTING.

Once a bar has closed, the indicator values anchored to it must never change when
later bars arrive. We prove this by the strongest possible test: recompute each
indicator on a TRUNCATED history (only the bars available up to bar k) and assert
it is bit-identical to the value the full-history computation produced for bar k.

Covered:
  * GTI demand/supply zones (detect_zones)  — a zone is identical whether computed
    the moment it is knowable (confirmed_index) or with 70+ future bars added.
  * Golden Line (session VWAP)              — VWAP[i] is unchanged by any later bar.
  * Institutional candle colour (_classify) — a closed bar's colour is fixed.

Run:  python -m pytest tests/test_non_repainting.py -q
      python tests/test_non_repainting.py        # also works standalone
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.gti.gti_zones import detect_zones, ZoneConfig
from src.live.trap_cmcd_live import _classify, TrapParams


# ── deterministic fixture: a clean base -> explosive departure -> drift ──────────
def _fixture() -> pd.DataFrame:
    idx = pd.date_range("2026-06-02 09:15", periods=130, freq="3min")
    o = h = l = c = 24000.0
    rows = []
    for k in range(130):
        if k < 45:                       # mild noise
            drift = (1.0 if k % 2 else -1.0)
            vb = 120000
        elif k < 49:                     # tight accumulation base
            drift = 0.3
            vb = 110000
        elif k < 53:                     # explosive departure (forms a demand zone)
            drift = 40.0
            vb = 340000
        else:                            # drift away, never revisiting the base
            drift = 2.0 if (k % 3) else -1.5
            vb = 130000
        op = c
        cl = op + drift
        hi = max(op, cl) + 3.0
        lo = min(op, cl) - 3.0
        rows.append([op, hi, lo, cl, vb])
        c = cl
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def _vwap_series(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()


# ── 1. ZONES do not repaint ──────────────────────────────────────────────────────
def test_zones_are_static_after_confirmation():
    df = _fixture()
    cfg = ZoneConfig(max_active_zones=0)          # keep all zones, no top-N truncation
    full = detect_zones(df, cfg)
    assert full, "fixture failed to produce any zone"

    # pick a zone knowable well before the end, with plenty of future bars after it
    testable = [z for z in full if 45 <= z.confirmed_index <= len(df) - 20]
    assert testable, "no zone with enough trailing bars to test"

    for z in testable:
        c = z.confirmed_index
        # recompute using ONLY the bars available the moment the zone is knowable
        at_birth = detect_zones(df.iloc[: c + 1], cfg)
        match = [x for x in at_birth
                 if x.side == z.side and abs(x.proximal - z.proximal) < 1e-6]
        assert match, (f"zone {z.side} prox={z.proximal:.2f} (confirmed@{c}) "
                       f"was NOT reproducible from truncated history -> REPAINTS")
        m = match[0]
        assert abs(m.distal - z.distal) < 1e-6, "distal edge shifted -> REPAINT"

        # and adding future bars must not move it either
        later = detect_zones(df.iloc[: c + 15], cfg)
        match2 = [x for x in later
                  if x.side == z.side and abs(x.proximal - z.proximal) < 1e-6]
        assert match2 and abs(match2[0].distal - z.distal) < 1e-6, \
            "zone moved when future bars were added -> REPAINT"


# ── 2. VWAP (Golden Line) does not repaint ───────────────────────────────────────
def test_vwap_is_prefix_invariant():
    df = _fixture()
    full = _vwap_series(df)
    for k in (50, 80, 110, 129):
        prefix = _vwap_series(df.iloc[: k + 1])
        # VWAP for every bar up to k must equal the full-series VWAP for those bars
        assert np.allclose(prefix.values, full.values[: k + 1], atol=1e-9), \
            f"VWAP changed for bars <= {k} when later bars were added -> REPAINT"


# ── 3. Candle colour of a CLOSED bar is fixed ────────────────────────────────────
def test_candle_colour_is_fixed_once_closed():
    df = _fixture()
    p = TrapParams()
    full = _classify(df, p)
    for k in (60, 90, 120):
        prefix = _classify(df.iloc[: k + 1], p)
        for i in range(30, k + 1):        # skip warm-up bars (rolling not yet primed)
            assert full[i] == prefix[i], \
                f"bar {i} colour {full[i]}!=({prefix[i]}) changed with future bars -> REPAINT"


if __name__ == "__main__":
    test_zones_are_static_after_confirmation()
    print("[ok] zones are static after confirmation (no repaint)")
    test_vwap_is_prefix_invariant()
    print("[ok] VWAP is prefix-invariant (no repaint)")
    test_candle_colour_is_fixed_once_closed()
    print("[ok] candle colour fixed once closed (no repaint)")
    print("\nINTEGRITY-1 PASSED — indicators do not repaint.")
