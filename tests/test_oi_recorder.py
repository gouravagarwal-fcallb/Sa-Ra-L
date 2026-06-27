"""
End-to-end self-test for the PASHUPATASTRA OI recorder pipeline.

Live NSE is not reachable in CI/sandbox, so we MOCK the OptionsIntelEngine with a
synthetic 'trapped short-call wall that breaks and gets covered' sequence and assert:
  1. the recorder writes one JSONL line per fetch,
  2. the Seller-Trap Score fires (side=CE) as the wall is covered into a rising spot,
  3. the analyzer measures the candidate option reaching >=2x before EOD.

Run:  python tests/test_oi_recorder.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brahmastra.options.options_intel import OptionsSnapshot, StrikeOI
from src.brahmastra.options.oi_recorder import OIRecorder
from src.brahmastra.options.oi_analyze import load_jsonl, measure_day, summarize

IST = timezone(timedelta(hours=5, minutes=30))
STEP = 50


def _make_snapshot(spot, wall_oi, wall_chg, cand_ltp, ts):
    """Build a NIFTY snapshot: heavy 25100 call wall + a 25150 CE we'd buy."""
    strikes = []
    for k in range(24800, 25401, STEP):
        # call wall concentrated at 25100; everything else ~1M baseline
        ce_oi = wall_oi if k == 25100 else 1_000_000
        ce_chg = wall_chg if k == 25100 else 0.0
        # the candidate 25150 CE LTP rises with the squeeze; others scaled by moneyness
        if k == 25150:
            ce_ltp = cand_ltp
        else:
            ce_ltp = max(0.5, cand_ltp * (1.0 - abs(k - 25150) / 800.0))
        strikes.append(StrikeOI(
            strike=k, call_oi=ce_oi, put_oi=1_000_000,
            call_iv=11.0, put_iv=12.0, call_ltp=round(ce_ltp, 2), put_ltp=20.0,
            call_chg_oi=ce_chg, put_chg_oi=0.0,
        ))
    snap = OptionsSnapshot(
        instrument="NIFTY", expiry="01JUL2026", spot_price=spot,
        pcr=0.9, pcr_label="NEUTRAL", max_pain=25000,
        iv_current=11.0, iv_percentile=18.0, iv_label="LOW",
        oi_buildup="NEUTRAL", oi_trend="", top_call_oi_strikes=[25100],
        top_put_oi_strikes=[24900], strike_data=strikes,
    )
    snap.fetched_at = ts
    return snap


class _MockEngine:
    """Yields a scripted squeeze: spot breaks above 25100, wall covers, 25150 CE 2x+."""
    def __init__(self):
        # (spot, wall_oi, wall_chg_oi, 25150-CE-ltp)
        self.seq = [
            (25080, 5_000_000,        0,   8),   # before: wall intact, building
            (25090, 5_050_000,  +50_000,   9),   # writers still adding (defending)
            (25105, 4_800_000, -250_000,  14),   # BREAK: spot thru wall, covering starts
            (25130, 4_300_000, -500_000,  22),   # covering accelerates
            (25165, 3_500_000, -800_000,  40),   # squeeze
            (25200, 2_700_000, -800_000,  70),   # 2x already cleared from the break entry
            (25235, 2_000_000, -700_000, 110),
            (25260, 1_600_000, -400_000, 150),
        ]
        self.i = 0

    def get_snapshot(self, symbol, expiry="", spot=0.0, force=False):
        if self.i >= len(self.seq):
            return None
        s, woi, wchg, ltp = self.seq[self.i]
        self.i += 1
        base = datetime(2026, 6, 30, 13, 0, tzinfo=IST)
        return _make_snapshot(s, woi, wchg, ltp, base + timedelta(minutes=self.i))


def run():
    eng = _MockEngine()
    tmp = tempfile.mkdtemp(prefix="oi_test_")
    # v1 score peaks ~60-65 mid-squeeze (uncalibrated by design); use 45 for the test.
    rec = OIRecorder(instruments=["NIFTY"], out_dir=tmp, trap_threshold=45.0, engine=eng)

    base = datetime(2026, 6, 30, 13, 0, tzinfo=IST)
    n = 0
    for k in range(len(eng.seq)):
        out = rec.record_once(now=base + timedelta(minutes=k + 1))
        n += len(out)

    path = os.path.join(tmp, "NIFTY", "2026-06-30.jsonl")
    recs = load_jsonl(path)
    assert len(recs) == len(eng.seq), f"expected {len(eng.seq)} lines, got {len(recs)}"

    fired = [r for r in recs if (r["trap"] or {}).get("side") == "CE" and r["trap"]["score"] >= 45]
    assert fired, "trap score never fired CE on the squeeze"
    top = max(recs, key=lambda r: r["trap"]["score"])
    assert top["trap"]["candidate"] == 25150, f"candidate should be 25150, got {top['trap']['candidate']}"
    # the signal must point the RIGHT way: peak score in the covering phase (spot above wall)
    assert top["spot"] > 25100, f"peak score should be during the break/cover, spot={top['spot']}"
    pre_break = recs[0]["trap"]["score"]
    assert top["trap"]["score"] > pre_break, "covering score must exceed pre-break score"

    fires = measure_day(recs, target_mult=2.0, min_score=45.0, cooldown_min=1)
    assert fires, "analyzer found no fires at/above min_score"
    assert any(f["hit_2x"] for f in fires), "no fire reached 2x (pipeline broken)"

    print(f"OK  recorder wrote {len(recs)} snapshots to {path}")
    print(f"OK  trap fired CE; peak score = {top['trap']['score']}  candidate = 25150 CE")
    print(f"OK  analyzer measured {len(fires)} fire(s); "
          f"{sum(1 for f in fires if f['hit_2x'])}/{len(fires)} hit 2x")
    print()
    print(summarize(fires, target_mult=2.0))
    print("\nALL OI-RECORDER PIPELINE TESTS PASSED")


def test_oi_recorder_pipeline():    # pytest entry point
    run()


if __name__ == "__main__":
    run()
