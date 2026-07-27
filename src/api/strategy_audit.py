"""
Strategy fidelity auditor — one pass over every strategy.
════════════════════════════════════════════════════════

Built after we found the same modelling bugs by hand in strategy after strategy
(intrabar target-overshoot, flat VIX pricing, phantom pre-launch weekly options).
This turns that manual sweep into a repeatable report so each strategy can be made
"finer" independently, and so a newly-introduced regression is caught next run.

It combines TWO honest signals — neither is trusted blindly:

  1. STATIC source scan of the strategy's backtest method (inspect.getsource):
       • flat_vix        — prices at a hardcoded VIX=15.0 with no real daily VIX
       • phantom_guard   — trades weekly expiries with no instrument-existence guard
       • overshoot       — a target exit that books the live/overshoot price, not
                           the target LIMIT (state: CLAMPED / REVIEW / N/A)

  2. METRIC flags from the strategy's summary.json (when a backtest has been run):
       • thin/no edge (PF < 1.3), low frequency (<20 trades/yr),
       • concentration (one window / instrument / VIX band holds most of the P&L),
       • avg_win far above the clean target-implied win (corroborates overshoot).

Each strategy gets a grade + a concrete next action. Nothing here places orders or
needs Kite; it reads source + the last summary.json. The overshoot check reports
REVIEW (not "BUG") when it cannot prove a clamp — the tool surfaces, a human
confirms — so the auditor itself never emits improper data.
"""
from __future__ import annotations

import inspect
import re

# config strategy_type → BacktestEngine method (mirrors main._dispatch_backtest).
_TYPE_TO_METHOD = {
    "expiry_scalper":            "run_expiry_scalper",
    "range_scalper":             "run_range_scalper",
    "nifty_intraday":            "run_nifty_intraday",
    "atm_pulse_burst":           "run_atm_pulse_burst",
    "bb_expiry_scalper":         "run_bb_expiry_scalper",
    "black_swan":                "run_black_swan",
    "gap_fade":                  "run_gap_fade",
    "trend_following":           "run_trend_rider",
    "volatility_mean_reversion": "run_vix_seller",
}
# Types with no in-engine backtest (standalone or analysis-only / shadow).
_STANDALONE = {"inrusd_futures": "INRUSD standalone backtest (src/inrusd/…)",
               "pashupatastra":  "PASHUPATASTRA standalone backtest",
               "brahmastra":     "BRAHMASTRA standalone backtest",
               "intraday_1min":  "run_1min path (RAMS) — OI-dependent, forward-only",
               "5min_fixed_quantity": "archived / superseded"}

# Live-price variables that, booked at a target hit, mean intrabar overshoot.
_PRICE_VARS = {"flt", "ltp", "val", "cev", "pev", "spot_close", "close_ltp", "cur", "fspot"}


# ── static source scan ────────────────────────────────────────────────────────
def _engine_source(method_name: str):
    try:
        from src.backtest.engine import BacktestEngine
        m = getattr(BacktestEngine, method_name, None)
        return inspect.getsource(m) if m else None
    except Exception:
        return None


def scan_source(src: str) -> dict:
    """Return the static fidelity findings for one backtest method's source."""
    out = {"flat_vix": False, "phantom_guard_missing": False,
           "overshoot_state": "N/A", "overshoot_lines": [], "notes": []}
    if not src:
        out["notes"].append("no engine backtest method")
        return out

    # 1) flat VIX pricing (constant 15.0 and never loads the real daily series)
    has_flat  = bool(re.search(r"\b[vV][iI][xX]\s*=\s*15\.0\b", src))
    loads_vix = ('load_daily("vix"' in src) or ("load_daily('vix'" in src)
    out["flat_vix"] = bool(has_flat and not loads_vix)

    # 2) phantom instrument guard
    trades_weekly = ("get_sensex_weekly_expiry" in src) or ("get_nifty_weekly_expiry" in src)
    out["phantom_guard_missing"] = bool(trades_weekly and "_weekly_options_exist" not in src)

    # 3) target-overshoot. Two cases:
    #    (a) exit booked inline in this method — scan for the anti-pattern;
    #    (b) exit DELEGATED to the shared pricer (simulate_trade) — can't see it from
    #        here, so report DELEGATED_PRICER rather than a false "clean".
    delegates = ("simulate_trade" in src) or ("_simulate_option_trade" in src)
    clamp_marker = "fills AT the target" in src
    lines = src.splitlines()
    suspect = False
    for i, ln in enumerate(lines):
        is_target = ("TARGET" in ln) or ("DECAY_TARGET" in ln) or \
            re.search(r"(>=|<=)\s*[\w\.\[\]\"']*t(gt|arget)", ln)
        if not is_target:
            continue
        window = " ".join(lines[i:i + 3])
        # allow a tuple LHS (exit_val, reason, ex_i = val, ...)
        m = re.search(r"exit_\w*[\w,\s]*=\s*([a-zA-Z_]\w*)", window)
        if not m:
            continue
        var = m.group(1)
        local_clamp = bool(re.search(r"\b(tgt|target|target_price|target_px|tgt_val)\b", window))
        if var in _PRICE_VARS and not local_clamp:
            suspect = True
            out["overshoot_lines"].append(lines[i].strip()[:90])
    if suspect:
        out["overshoot_state"] = "CLAMPED_PARTIAL" if clamp_marker else "REVIEW"
    elif delegates:
        out["overshoot_state"] = "DELEGATED_PRICER"
        out["notes"].append("exit in option_pricer.simulate_trade (shared) — audited/clamped 2026-07-23")
    elif clamp_marker:
        out["overshoot_state"] = "CLAMPED"
    else:
        out["overshoot_state"] = "N/A"
    return out


# ── metric flags from summary.json ────────────────────────────────────────────
def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _years(period: dict):
    if not isinstance(period, dict):
        return None
    s, e = str(period.get("start") or "")[:4], str(period.get("end") or "")[:4]
    if s.isdigit() and e.isdigit():
        return max(int(e) - int(s), 0) + 1
    return None


def metric_flags(summary: dict) -> dict:
    """Derive honest red-flags from a summary.json dict (or {} if none)."""
    out = {"has_backtest": bool(summary), "flags": []}
    if not summary:
        return out
    pf     = _num(summary.get("profit_factor"))
    sharpe = _num(summary.get("sharpe"))
    trades = _num(summary.get("total_trades")) or 0
    pnl    = _num(summary.get("total_pnl")) or 0.0
    yrs    = _years(summary.get("period")) or 1
    out.update({"pf": pf, "sharpe": sharpe, "trades": int(trades),
                "pnl": pnl, "years": yrs, "trades_per_year": round(trades / yrs, 1)})

    if trades == 0:
        out["flags"].append("zero backtest trades (OI-dependent / gate never fires / no order path)")
        return out
    if pf is not None and pf < 1.3:
        out["flags"].append(f"thin/no edge — PF {pf:.2f} < 1.3")
    if trades / yrs < 20:
        out["flags"].append(f"low frequency — ~{trades/yrs:.0f} trades/yr")

    # concentration: does one bucket hold most of the profit?
    def _conc(rows, key, label):
        if not rows or pnl <= 0:
            return
        pos = [(_num(r.get("pnl")) or 0, r.get(key)) for r in rows]
        top = max(pos, key=lambda x: x[0], default=(0, None))
        if top[0] > 0.6 * pnl:
            out["flags"].append(f"concentration — {label} '{top[1]}' holds "
                                f"{top[0]/pnl*100:.0f}% of profit")

    _conc(summary.get("by_window"), "window", "window")
    _conc(summary.get("by_instrument"), "instrument", "instrument")
    _conc(summary.get("by_vix_bucket"), "vix_band", "VIX band")

    # avg_win far above a plausible clean win → corroborates overshoot inflation.
    aw = _num(summary.get("avg_win"))
    if aw and aw > 40000:      # >4x a 10k budget is a soft tripwire; report, don't assert
        out["flags"].append(f"avg_win ₹{aw:,.0f} is large — confirm it isn't target overshoot")
    return out


# ── per-strategy audit + grade ────────────────────────────────────────────────
def audit_one(name: str, entry: dict, summary_row: dict) -> dict:
    stype  = (entry.get("type") or entry.get("strategy_type") or "").strip()
    status = entry.get("status")
    method = _TYPE_TO_METHOD.get(stype)
    src    = _engine_source(method) if method else None
    static = scan_source(src)
    summ   = summary_row.get("summary") if isinstance(summary_row, dict) else None
    metrics = metric_flags(summ or {})

    # Model/synthetic backtest? A PF from a Monte-Carlo / assumed-skill model (e.g.
    # PASHUPATASTRA's synthetic_montecarlo with filter_skill=0.50) is a FEASIBILITY
    # number, not a real track record — it must never sit next to real-data edges
    # unlabelled. Detect from the summary's own honest flags.
    run_kind  = str((summ or {}).get("run_kind") or "").lower()
    data_basis = (summ or {}).get("data_basis") or (summ or {}).get("caveat") or ""
    dq = (summary_row.get("data_quality") if isinstance(summary_row, dict) else None)
    model_based = bool(("synthetic" in run_kind) or ("montecarlo" in run_kind)
                       or (dq == "model") or ("synthetic" in str(data_basis).lower())
                       or ("model-based" in str(data_basis).lower()))

    issues = []
    if model_based:
        issues.append("MODEL-BASED backtest (synthetic / assumed-skill) — a feasibility "
                      "projection, NOT a real-data track record")
    if static["flat_vix"]:
        issues.append("flat VIX=15.0 pricing (wire real daily VIX)")
    if static["phantom_guard_missing"]:
        issues.append("no phantom-instrument guard (weekly options pre-launch)")
    if static["overshoot_state"] in ("REVIEW", "CLAMPED_PARTIAL"):
        issues.append("target exit may book the overshoot — REVIEW the exit clamp")
    issues.extend(metrics["flags"])

    # grade (priority order)
    if model_based:
        # A synthetic/assumed-skill model is never a validated edge, whatever its PF.
        grade = "MODEL_ONLY"
    elif not method and stype in _STANDALONE:
        grade = "CANT_AUDIT_HERE"
    elif status in ("archived",):
        grade = "ARCHIVED"
    elif static["flat_vix"] or static["phantom_guard_missing"] or static["overshoot_state"] in ("REVIEW", "CLAMPED_PARTIAL"):
        grade = "FIX_NEEDED"
    elif not metrics["has_backtest"] or metrics.get("trades", 0) == 0:
        grade = "NO_BACKTEST_DATA"
    elif metrics.get("pf") is not None and metrics["pf"] < 1.3:
        grade = "NO_EDGE"
    elif any(f.startswith(("low frequency", "concentration")) for f in metrics["flags"]):
        grade = "THIN_EDGE"
    else:
        grade = "CLEAN"

    return {
        "name": name, "strategy_type": stype, "status": status,
        "backtest_method": method or _STANDALONE.get(stype, "—"),
        "grade": grade, "issues": issues,
        "static": static, "metrics": metrics,
        "model_based": model_based, "run_kind": run_kind or None,
        "next_action": _next_action(grade, static, metrics),
    }


def _next_action(grade, static, metrics):
    if grade == "FIX_NEEDED":
        bits = []
        if static["flat_vix"]:
            bits.append("wire real daily VIX")
        if static["phantom_guard_missing"]:
            bits.append("add _weekly_options_exist guard")
        if static["overshoot_state"] in ("REVIEW", "CLAMPED_PARTIAL"):
            bits.append("clamp target exit to the limit, then re-backtest")
        return "; ".join(bits) or "review flagged fidelity issues"
    if grade == "NO_EDGE":
        return "retire / paper-park — edge is < costs on honest data"
    if grade == "THIN_EDGE":
        return "keep paper; investigate concentration / low frequency before real capital"
    if grade == "MODEL_ONLY":
        return ("forward-paper to confirm the ASSUMED signal skill on real OI — the PF is a "
                "synthetic feasibility projection, not a validated edge; don't fund on it")
    if grade == "NO_BACKTEST_DATA":
        return "run a backtest (or forward-paper if OI-dependent) to get honest metrics"
    if grade == "CANT_AUDIT_HERE":
        return "audit its standalone backtest separately (not in the shared engine)"
    if grade == "ARCHIVED":
        return "none — archived/intentional"
    return "clean on current signals — safe to refine features"


_GRADE_ORDER = {"FIX_NEEDED": 0, "NO_EDGE": 1, "MODEL_ONLY": 2, "THIN_EDGE": 3,
                "NO_BACKTEST_DATA": 4, "CANT_AUDIT_HERE": 5, "CLEAN": 6, "ARCHIVED": 7}


def audit_all(registry: dict) -> list:
    from src.api.backtests import load_summary
    rows = []
    for name, entry in registry.items():
        try:
            summ = load_summary(name, entry)
        except Exception:
            summ = {}
        rows.append(audit_one(name, entry, summ))
    rows.sort(key=lambda a: (_GRADE_ORDER.get(a["grade"], 9), a["name"]))
    return rows


# ── markdown report ───────────────────────────────────────────────────────────
def to_markdown(audits: list, *, generated_at: str = "") -> str:
    L = ["# Strategy Fidelity Audit", ""]
    if generated_at:
        L.append(f"_generated {generated_at}_\n")
    counts = {}
    for a in audits:
        counts[a["grade"]] = counts.get(a["grade"], 0) + 1
    L.append("## Summary")
    L.append(" · ".join(f"**{g}**: {n}" for g, n in
                        sorted(counts.items(), key=lambda x: _GRADE_ORDER.get(x[0], 9))))
    L.append("")
    L.append("| Strategy | Grade | PF | Trades/yr | Key issues | Next action |")
    L.append("|---|---|---|---|---|---|")
    for a in audits:
        m = a["metrics"]
        pf = f"{m['pf']:.2f}" if m.get("pf") is not None else "—"
        tpy = m.get("trades_per_year", "—")
        iss = "; ".join(a["issues"][:2]) or "—"
        L.append(f"| {a['name']} | {a['grade']} | {pf} | {tpy} | {iss} | {a['next_action']} |")
    L.append("")
    # detail per FIX_NEEDED / NO_EDGE
    for a in audits:
        if a["grade"] not in ("FIX_NEEDED", "NO_EDGE", "THIN_EDGE"):
            continue
        L.append(f"### {a['name']} — {a['grade']}")
        L.append(f"- type `{a['strategy_type']}` · status `{a['status']}` · "
                 f"backtest `{a['backtest_method']}`")
        if a["issues"]:
            for it in a["issues"]:
                L.append(f"  - {it}")
        if a["static"]["overshoot_lines"]:
            L.append(f"  - overshoot suspect line(s): `{a['static']['overshoot_lines'][0]}`")
        L.append(f"  - **next:** {a['next_action']}")
        L.append("")
    L.append("> Static scan reads each backtest method's source (flat VIX, phantom "
             "guard, target-overshoot clamp). Metrics (PF, trades/yr, concentration) "
             "come from each strategy's LAST summary.json on disk — re-run its backtest "
             "for fresh numbers. 'REVIEW' means the clamp couldn't be proven from source "
             "— confirm by hand. 'DELEGATED_PRICER' = exit lives in "
             "option_pricer.simulate_trade (shared, already clamped).")
    return "\n".join(L)
