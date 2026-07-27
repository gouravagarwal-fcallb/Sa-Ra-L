"""
Strategy fidelity auditor — unit tests for the static source scan + metric flags.

Guards the auditor's own honesty: it must catch the known bug classes (flat VIX,
missing phantom guard, target overshoot incl. tuple/seller forms), recognise the
already-fixed clamps and the shared-pricer delegation, and grade sensibly.
"""
from src.api.strategy_audit import scan_source, metric_flags, audit_one


# ── static scan ───────────────────────────────────────────────────────────────
def test_scan_flags_flat_vix_without_real_load():
    src = "def f():\n    VIX = 15.0\n    x = self.pricer.price(s, k, VIX, t, o)\n"
    assert scan_source(src)["flat_vix"] is True


def test_scan_clears_flat_vix_when_real_daily_loaded():
    src = ('def f():\n    VIX = 15.0\n    _vdf = load_daily("vix", a, b)\n'
           '    VIX = vix_by_day.get(d, 15.0)\n')
    assert scan_source(src)["flat_vix"] is False


def test_scan_flags_missing_phantom_guard():
    src = "def f():\n    exp = get_sensex_weekly_expiry(current)\n"
    assert scan_source(src)["phantom_guard_missing"] is True


def test_scan_clears_phantom_guard_when_present():
    src = ("def f():\n    if not _weekly_options_exist(inst, current):\n        continue\n"
           "    exp = get_sensex_weekly_expiry(current)\n")
    assert scan_source(src)["phantom_guard_missing"] is False


def test_scan_flags_target_overshoot_inline():
    src = ("def f():\n    if flt >= tgt:\n        pass\n"
           "    if ltp >= target_price:\n        exit_reason = 'TARGET_HIT'\n"
           "        exit_p = ltp * (1 - slip)\n")
    s = scan_source(src)
    assert s["overshoot_state"] == "REVIEW"
    assert s["overshoot_lines"]


def test_scan_sees_clamp_marker():
    src = ("def f():\n    if flt >= tgt:\n        # Limit fills AT the target\n"
           "        exit_p = tgt * (1 - slip)\n")
    assert scan_source(src)["overshoot_state"] == "CLAMPED"


def test_scan_catches_tuple_seller_decay_overshoot():
    # VIX_SELLER form: books `val` at a DECAY_TARGET, not the target level.
    src = ("def f():\n    if val <= tgt_val:\n"
           "        exit_val, reason, ex_i = val, 'DECAY_TARGET', i\n")
    # the exit line itself lacks tgt_val in its 3-line window → REVIEW
    s = scan_source(src)
    assert s["overshoot_state"] == "REVIEW"


def test_scan_marks_pricer_delegation():
    src = "def f():\n    tr, net = self._simulate_option_trade(a, b, window_id='X')\n"
    assert scan_source(src)["overshoot_state"] == "DELEGATED_PRICER"


# ── metric flags ──────────────────────────────────────────────────────────────
def test_metric_flags_thin_edge_and_frequency():
    summ = {"profit_factor": 1.1, "total_trades": 30, "total_pnl": 5000,
            "period": {"start": "2019-01-01", "end": "2025-12-31"}}
    f = metric_flags(summ)["flags"]
    assert any("thin/no edge" in x for x in f)
    assert any("low frequency" in x for x in f)


def test_metric_flags_concentration():
    summ = {"profit_factor": 3.0, "total_trades": 200, "total_pnl": 100000,
            "period": {"start": "2023-01-01", "end": "2025-12-31"},
            "by_window": [{"window": "W3", "pnl": 90000}, {"window": "W1", "pnl": 10000}]}
    assert any("concentration" in x for x in metric_flags(summ)["flags"])


# ── grading ───────────────────────────────────────────────────────────────────
def test_audit_one_grades_fix_needed_on_static_flag(monkeypatch):
    import src.api.strategy_audit as sa
    monkeypatch.setattr(sa, "_engine_source",
                        lambda m: "def f():\n    vix = 15.0\n    x=1\n")
    a = audit_one("X_v1", {"type": "range_scalper", "status": "paper"},
                  {"summary": {"profit_factor": 2.0, "total_trades": 100,
                               "period": {"start": "2023", "end": "2025"}}})
    assert a["grade"] == "FIX_NEEDED"
    assert any("flat VIX" in i for i in a["issues"])


def test_model_based_summary_is_graded_model_only():
    # A synthetic/Monte-Carlo backtest (PASHUPATASTRA-style) must never be graded as
    # a real edge, whatever its PF — it's a feasibility projection.
    a = audit_one("PASHUPATASTRA_v1", {"type": "pashupatastra", "status": "paper"},
                  {"summary": {"run_kind": "synthetic_montecarlo", "profit_factor": 1.84,
                               "total_trades": 358, "period": {"start": "2020", "end": "2026"},
                               "caveat": "Model-based, NOT real market data"}})
    assert a["grade"] == "MODEL_ONLY"
    assert a["model_based"] is True
    assert any("MODEL-BASED" in i for i in a["issues"])


def test_real_data_summary_not_flagged_model():
    a = audit_one("EXPIRY_SCALPER_v1", {"type": "expiry_scalper", "status": "live"},
                  {"summary": {"run_kind": "backtest", "profit_factor": 2.5,
                               "total_trades": 195, "period": {"start": "2019", "end": "2025"}}})
    assert a["grade"] != "MODEL_ONLY"
    assert a["model_based"] is False
