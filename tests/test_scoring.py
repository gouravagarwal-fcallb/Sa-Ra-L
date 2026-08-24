"""Unit tests for the analysis-vs-actual scoring engine (Phase 1).

Covers the label logic and — critically — that scoring uses ONLY bars at/after the
analysis timestamp (no look-ahead leakage)."""
from src.api.scoring import _Path, _label, _mod


def _bars(spec):
    """spec: list of (minute_of_day, close). Negligible wicks (realistic index bars
    wick ~0.03%, far below the scoring thresholds)."""
    out = []
    for mo, c in spec:
        out.append({"t": f"2026-06-29 {mo // 60:02d}:{mo % 60:02d}", "o": c,
                    "h": c * 1.0001, "l": c * 0.9999, "c": c, "v": 100})
    return out


def _flat_then_rally():
    # flat 09:15-09:59 at 100, clean +0.6% ramp 10:00-10:30
    spec = [(mo, 100.0) for mo in range(9 * 60 + 15, 10 * 60)]
    spec += [(10 * 60 + i, 100.0 * (1 + 0.006 * i / 30)) for i in range(31)]
    return _Path(_bars(spec))


def test_correct_stand_aside_on_flat():
    p = _flat_then_rally()
    lab, sc = _label("No setup found", p.forward(9 * 60 + 30, 30))
    assert lab == "Correct stand-aside" and sc > 0


def test_over_filtered_when_filter_blocks_real_move():
    p = _flat_then_rally()
    lab, sc = _label("Volatility filter", p.forward(10 * 60, 30))
    assert lab == "Over-filtered" and sc < 0


def test_missed_opportunity_on_no_setup():
    p = _flat_then_rally()
    lab, _ = _label("No setup found", p.forward(10 * 60, 30))
    assert lab == "Missed opportunity"


def test_premature_rejection_on_warming():
    p = _flat_then_rally()
    lab, _ = _label("Warming up / insufficient bars", p.forward(10 * 60, 30))
    assert lab == "Premature rejection"


def test_unverifiable_without_bars():
    lab, sc = _label("No setup found", None)
    assert lab == "Blind / unverifiable" and sc == 0.0


def test_no_lookahead_leakage():
    # A rally happens 09:30-09:45, then flat. A cycle AT 10:00 must NOT see the past
    # rally — its forward window is flat, so it must score Correct stand-aside.
    spec = [(mo, 100.0) for mo in range(9 * 60 + 15, 9 * 60 + 30)]
    spec += [(9 * 60 + 30 + i, 100.0 * (1 + 0.006 * i / 15)) for i in range(16)]   # rally
    spec += [(mo, 100.6) for mo in range(9 * 60 + 46, 11 * 60)]                      # flat after
    p = _Path(_bars(spec))
    lab, _ = _label("No setup found", p.forward(10 * 60, 30))
    assert lab == "Correct stand-aside", f"look-ahead leak: scored {lab}"


def test_mod_parsing():
    assert _mod("10:05:33") == 10 * 60 + 5
    assert _mod("2026-06-29 10:05") == 10 * 60 + 5
