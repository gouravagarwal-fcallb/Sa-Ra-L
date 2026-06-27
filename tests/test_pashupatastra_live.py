"""
Self-test for the PASHUPATASTRA shadow live engine.

Feeds the same synthetic 'trapped short-call wall breaks and covers' squeeze used by
test_oi_recorder and asserts the engine:
  1. opens a shadow trade once the trap score fires (during the break/cover),
  2. buys the candidate option (wall+step), books 2× and rides the runner,
  3. journals a CLOSED trade with positive net P&L and a >=2× peak.

Run:  python tests/test_pashupatastra_live.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brahmastra.options.options_intel import OptionsSnapshot, StrikeOI
from src.brahmastra.options.trap_score import compute_trap_score
from src.brahmastra.live.pashupatastra_live import PashupatastraLive, LiveCfg

IST = timezone(timedelta(hours=5, minutes=30))
STEP = 50


def _snap(spot, wall_oi, wall_chg, cand_ltp, ts):
    strikes = []
    for k in range(24800, 25401, STEP):
        ce_oi = wall_oi if k == 25100 else 1_000_000
        ce_chg = wall_chg if k == 25100 else 0.0
        ce_ltp = cand_ltp if k == 25150 else max(0.5, cand_ltp * (1 - abs(k - 25150) / 800.0))
        strikes.append(StrikeOI(strike=k, call_oi=ce_oi, put_oi=1_000_000,
                                call_iv=11, put_iv=12, call_ltp=round(ce_ltp, 2), put_ltp=20,
                                call_chg_oi=ce_chg, put_chg_oi=0.0))
    s = OptionsSnapshot(instrument="NIFTY", expiry="01JUL2026", spot_price=spot, pcr=0.9,
                        pcr_label="NEUTRAL", max_pain=25000, iv_current=11, iv_percentile=18,
                        iv_label="LOW", oi_buildup="NEUTRAL", oi_trend="",
                        top_call_oi_strikes=[25100], top_put_oi_strikes=[24900], strike_data=strikes)
    s.fetched_at = ts
    s._is_expiry = True
    return s


SEQ = [   # (spot, wall_oi, wall_chg, 25150-CE-ltp)
    (25080, 5_000_000, 0, 8), (25090, 5_050_000, 50_000, 9),
    (25105, 4_800_000, -250_000, 14), (25130, 4_300_000, -500_000, 22),
    (25165, 3_500_000, -800_000, 40), (25200, 2_700_000, -800_000, 70),
    (25235, 2_000_000, -700_000, 110), (25260, 1_600_000, -400_000, 150),
]


def run():
    tmp = tempfile.mkdtemp(prefix="pashu_live_")
    journal = os.path.join(tmp, "shadow_trades.csv")
    cfg = LiveCfg(trap_threshold=45.0)        # v1 score peaks ~67 here
    eng = PashupatastraLive(cfg=cfg, mode="shadow", journal=journal)

    base = datetime(2026, 6, 30, 13, 0, tzinfo=IST)
    prev = None
    opened = False
    for i, (spot, woi, wchg, ltp) in enumerate(SEQ):
        now = base + timedelta(minutes=i + 1)
        snap = _snap(spot, woi, wchg, ltp, now)
        trap = compute_trap_score(snap, prev, strike_step=STEP)
        prev = snap
        evs = eng.on_chain("NIFTY", snap, trap, now)
        for e in evs:
            if e["event"] == "ENTER":
                opened = True
                assert e["strike"] == 25150, f"should buy 25150 CE, got {e['strike']}"
    # force EOD close on the runner
    eng.on_chain("NIFTY", _snap(25260, 1_600_000, -400_000, 150,
                                base + timedelta(hours=2, minutes=12)), trap,
                 base.replace(hour=15, minute=11))

    assert opened, "engine never opened a shadow trade on the squeeze"
    import csv
    rows = list(csv.DictReader(open(journal)))
    assert rows, "no trade journaled"
    r = rows[-1]
    net = float(r["net_pnl"]); peak = float(r["peak_mult"])
    assert net > 0, f"shadow net P&L should be positive, got {net}"
    assert peak >= 2.0, f"peak multiple should be >=2x, got {peak}"

    print(f"OK  shadow ENTER 25150 CE; booked 2× + runner")
    print(f"OK  journaled trade: net=Rs.{net:,.0f}  mult={r['mult_on_capital']}  "
          f"peak={peak}x  exit={r['exit_reason']}")
    print("\nALL PASHUPATASTRA SHADOW-ENGINE TESTS PASSED")


def test_pashupatastra_shadow_engine():    # pytest entry point
    run()


if __name__ == "__main__":
    run()
