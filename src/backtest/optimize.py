"""
Strategy parameter optimisation + anti-overfit walk-forward harness.
─────────────────────────────────────────────────────────────────────
Goal: find parameter settings that show a GENUINE, ROBUST edge — not curve-fit
noise. The discipline that separates the two is an **out-of-sample split**:

    1. Sweep a small, sensible grid of parameters (conservative by default).
    2. For each combo, backtest on a TRAIN window (in-sample) AND on a later,
       UNSEEN TEST window (out-of-sample), each NET OF REALISTIC COSTS.
    3. Rank by the TEST (out-of-sample) result — never the train result.
    4. Flag "overfit" combos: great in-sample, poor out-of-sample (big gap).

A combo only earns trust if it is positive-after-costs OUT-OF-SAMPLE, with a
small train→test gap. Everything here is cost-aware; a gross edge that dies at
costs is not a business (the GTI lesson).

Runs on a machine with Kite history (backtests need data):
    python -m src.backtest.optimize --strategy expiry_scalper \
        --config strategies/EXPIRY_SCALPER_v1/config.yaml \
        --from 2023-01-01 --to 2026-06-30 --train-frac 0.65 --cost 150

Offline self-test of the pure logic (no Kite needed):
    python -m src.backtest.optimize --selftest
"""
from __future__ import annotations

import argparse
import copy
import itertools
from dataclasses import dataclass
from datetime import date, timedelta


# --------------------------------------------------------------------------- #
# Parameter grids — CONSERVATIVE by default (anti-overfit: few, sensible knobs)
# --------------------------------------------------------------------------- #
def _set_all_windows(cfg: dict, key: str, val) -> None:
    for w in cfg.get("expiry_scalper", {}).get("windows", []):
        w[key] = val


def _set_es(cfg: dict, key: str, val) -> None:
    cfg.setdefault("expiry_scalper", {})[key] = val


def _set_bb(cfg: dict, key: str, val) -> None:
    cfg.setdefault("bb_expiry_scalper", {})[key] = val


# knob = (name, [values], setter). The full grid is the Cartesian product.
_KNOBS = {
    "expiry_scalper": {
        "conservative": [
            ("mom_thr", [0.15, 0.20, 0.25], lambda c, v: _set_all_windows(c, "momentum_threshold_pct", v)),
            ("target",  [2.0, 2.5, 3.0],    lambda c, v: _set_all_windows(c, "target_multiplier", v)),
            ("stop",    [30, 35, 40],       lambda c, v: _set_all_windows(c, "stop_loss_pct", v)),
        ],
        "full": [
            ("mom_thr", [0.15, 0.20, 0.25], lambda c, v: _set_all_windows(c, "momentum_threshold_pct", v)),
            ("target",  [2.0, 2.5, 3.0, 3.5], lambda c, v: _set_all_windows(c, "target_multiplier", v)),
            ("stop",    [30, 35, 40, 45],   lambda c, v: _set_all_windows(c, "stop_loss_pct", v)),
            ("otm",     [1, 2, 3],          lambda c, v: _set_es(c, "otm_strikes", v)),
            ("vol",     [1.2, 1.3, 1.5],    lambda c, v: _set_es(c, "volume_surge_multiplier", v)),
        ],
    },
    "bb_expiry_scalper": {
        # Flat params under bb_expiry_scalper:. Key levers = entry score bar,
        # what counts as a squeeze, and the stop. `score` moves both the Mode-A
        # min and the generic score_entry together; `stop` moves both modes' stops.
        "conservative": [
            ("score", [60, 65, 70],    lambda c, v: (_set_bb(c, "mode_a_min_score", v), _set_bb(c, "score_entry", v))),
            ("sqz",   [0.4, 0.5, 0.6],  lambda c, v: _set_bb(c, "bb_squeeze_threshold", v)),
            ("stop",  [30, 35, 40],     lambda c, v: (_set_bb(c, "mode_a_stop_pct", v), _set_bb(c, "mode_b_stop_pct", v))),
        ],
        "full": [
            ("score",  [60, 65, 70],   lambda c, v: (_set_bb(c, "mode_a_min_score", v), _set_bb(c, "score_entry", v))),
            ("sqz",    [0.4, 0.5, 0.6], lambda c, v: _set_bb(c, "bb_squeeze_threshold", v)),
            ("stop",   [30, 35, 40],    lambda c, v: (_set_bb(c, "mode_a_stop_pct", v), _set_bb(c, "mode_b_stop_pct", v))),
            ("target", [2.0, 2.5, 3.0], lambda c, v: (_set_bb(c, "mode_a_target_mult", v), _set_bb(c, "mode_b_target_mult", v + 0.5))),
            ("vol",    [1.1, 1.2, 1.4], lambda c, v: _set_bb(c, "min_volume_surge", v)),
        ],
    },
}


def iter_combos(strategy: str, grid: str = "conservative"):
    """Yield (label:str, {knob:value}) for every combination in the grid."""
    knobs = _KNOBS[strategy][grid]
    names = [k[0] for k in knobs]
    value_lists = [k[1] for k in knobs]
    for combo in itertools.product(*value_lists):
        mapping = dict(zip(names, combo))
        label = " ".join(f"{n}={v}" for n, v in mapping.items())
        yield label, mapping


def apply_combo(strategy_config: dict, strategy: str, combo: dict, grid: str = "conservative") -> dict:
    """Return a DEEP COPY of strategy_config with the combo's params applied."""
    cfg = copy.deepcopy(strategy_config)
    setters = {k[0]: k[2] for k in _KNOBS[strategy][grid]}
    for name, val in combo.items():
        setters[name](cfg, val)
    return cfg


# --------------------------------------------------------------------------- #
# Cost-aware scorecard metrics (pure — testable without Kite)
# --------------------------------------------------------------------------- #
@dataclass
class Score:
    n: int = 0
    win_rate: float = 0.0
    total: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0     # rupees (positive number = worst peak-to-trough)
    sharpe: float = 0.0
    max_consec_loss: int = 0
    per_year: float = 0.0


def score_trades(trades: list, cost_per_trade: float, years: float = 1.0) -> Score:
    """Compute a cost-adjusted scorecard from a list of (date, pnl_rupees) tuples.
    `date` is any hashable day key (used to aggregate daily returns for Sharpe)."""
    import math
    if not trades:
        return Score()
    net = [(d, p - cost_per_trade) for d, p in trades]
    pnls = [p for _, p in net]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_w = sum(wins)
    gross_l = -sum(losses)

    # max drawdown on the cumulative equity curve
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    # max consecutive losses
    mcl = cur = 0
    for p in pnls:
        cur = cur + 1 if p <= 0 else 0
        mcl = max(mcl, cur)

    # Sharpe from DAILY aggregated net pnl
    daily: dict = {}
    for d, p in net:
        daily[d] = daily.get(d, 0.0) + p
    dv = list(daily.values())
    if len(dv) > 1:
        mean = sum(dv) / len(dv)
        var = sum((x - mean) ** 2 for x in dv) / (len(dv) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd * math.sqrt(252)) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    return Score(
        n=len(pnls),
        win_rate=round(len(wins) / len(pnls) * 100, 1),
        total=round(sum(pnls), 2),
        avg_win=round(gross_w / len(wins), 2) if wins else 0.0,
        avg_loss=round(-gross_l / len(losses), 2) if losses else 0.0,
        profit_factor=round(gross_w / gross_l, 2) if gross_l > 0 else float("inf"),
        max_drawdown=round(max_dd, 2),
        sharpe=round(sharpe, 2),
        max_consec_loss=mcl,
        per_year=round(len(pnls) / years, 1) if years else float(len(pnls)),
    )


def _result_to_trades(result) -> list:
    """Extract (date, net_pnl_rupees) tuples for REAL (non-paper) trades."""
    out = []
    for t in getattr(result, "trades", []):
        if getattr(t, "is_paper", False):
            continue
        out.append((getattr(t, "date", None), float(getattr(t, "pnl_rupees", 0.0))))
    return out


def overfit_flag(train: Score, test: Score) -> str:
    """Classify a combo by its train→test behaviour."""
    if test.n < 10:
        return "THIN"                       # too few OOS trades to trust
    if test.profit_factor == float("inf"):
        return "OK"
    if test.total <= 0 or test.profit_factor < 1.0:
        return "FAILS_OOS"                  # lost money out-of-sample
    # overfit = strong in-sample, much weaker out-of-sample
    if train.profit_factor > 1.2 and test.profit_factor < train.profit_factor * 0.6:
        return "OVERFIT"
    return "ROBUST"


# --------------------------------------------------------------------------- #
# Optimisation driver (needs Kite history — runs on the operator machine)
# --------------------------------------------------------------------------- #
def _split_windows(frm: date, to: date, train_frac: float):
    total = (to - frm).days
    cut = frm + timedelta(days=int(total * train_frac))
    return (frm, cut), (cut + timedelta(days=1), to)


def run_optimization(global_config: dict, strategy_config: dict, strategy: str,
                     frm: date, to: date, train_frac: float = 0.65,
                     cost_per_trade: float = 150.0, grid: str = "conservative") -> list:
    """Sweep the grid; backtest each combo on train + test windows; return rows
    sorted by out-of-sample (test) total, net of costs."""
    from src.backtest.engine import BacktestEngine

    (tr_s, tr_e), (te_s, te_e) = _split_windows(frm, to, train_frac)
    tr_years = max((tr_e - tr_s).days / 365.0, 1e-6)
    te_years = max((te_e - te_s).days / 365.0, 1e-6)

    def _run(cfg, s, e) -> Score:
        c = copy.deepcopy(cfg)
        c.setdefault("backtest", {})
        c["backtest"]["start_date"] = s.isoformat()
        c["backtest"]["end_date"] = e.isoformat()
        eng = BacktestEngine(global_config, c)
        method = getattr(eng, f"run_{strategy}", None) or eng.run
        result = method()
        yrs = max((e - s).days / 365.0, 1e-6)
        return score_trades(_result_to_trades(result), cost_per_trade, yrs)

    rows = []
    for label, combo in iter_combos(strategy, grid):
        cfg = apply_combo(strategy_config, strategy, combo, grid)
        try:
            tr = _run(cfg, tr_s, tr_e)
            te = _run(cfg, te_s, te_e)
        except Exception as e:  # never let one combo kill the sweep
            rows.append({"label": label, "error": str(e)[:80]})
            continue
        rows.append({"label": label, "train": tr, "test": te,
                     "flag": overfit_flag(tr, te)})
    rows.sort(key=lambda r: (r.get("test").total if r.get("test") else -1e18), reverse=True)
    return rows


def print_ranked(rows: list, train_win, test_win) -> None:
    print("=" * 100)
    print("  STRATEGY OPTIMISATION — ranked by OUT-OF-SAMPLE (test) net P&L")
    print(f"  train {train_win[0]}→{train_win[1]}  |  test (unseen) {test_win[0]}→{test_win[1]}")
    print("=" * 100)
    print(f"  {'combo':<34}{'OOS net':>11}{'OOS PF':>8}{'OOS win%':>9}"
          f"{'OOS DD':>10}{'OOS n':>7}  {'IS PF':>6}  verdict")
    for r in rows:
        if "error" in r:
            print(f"  {r['label']:<34}  ERROR: {r['error']}"); continue
        te, tr = r["test"], r["train"]
        pf = "inf" if te.profit_factor == float("inf") else f"{te.profit_factor:.2f}"
        ispf = "inf" if tr.profit_factor == float("inf") else f"{tr.profit_factor:.2f}"
        print(f"  {r['label']:<34}{te.total:>11,.0f}{pf:>8}{te.win_rate:>9}"
              f"{te.max_drawdown:>10,.0f}{te.n:>7}  {ispf:>6}  {r['flag']}")
    print("=" * 100)
    print("  READ: trust only ROBUST rows — positive OOS net, OOS PF ≥ ~1.3, small")
    print("  train→test gap, enough OOS trades. OVERFIT/FAILS_OOS/THIN = do NOT ship.")
    print("=" * 100)


# --------------------------------------------------------------------------- #
# Offline self-test of the pure logic (no Kite)
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    # combo generation
    combos = list(iter_combos("expiry_scalper", "conservative"))
    assert len(combos) == 3 * 3 * 3, len(combos)
    # apply_combo does not mutate the base
    base = {"expiry_scalper": {"otm_strikes": 2, "windows": [
        {"momentum_threshold_pct": 0.20, "target_multiplier": 2.5, "stop_loss_pct": 35}]}}
    _, combo = combos[0]
    out = apply_combo(base, "expiry_scalper", combo, "conservative")
    assert base["expiry_scalper"]["windows"][0]["target_multiplier"] == 2.5   # unchanged
    assert out["expiry_scalper"]["windows"][0]["momentum_threshold_pct"] == combo["mom_thr"]
    # scorecard + cost sensitivity
    trades = [("d1", 500.0), ("d1", -300.0), ("d2", 800.0), ("d2", -300.0), ("d3", 400.0)]
    s0 = score_trades(trades, cost_per_trade=0.0)
    s1 = score_trades(trades, cost_per_trade=150.0)
    assert s0.n == 5 and s0.total == 1100.0
    assert s1.total < s0.total                       # costs reduce net
    assert s0.max_drawdown >= 0 and s0.max_consec_loss >= 1
    # overfit flags
    good_tr = score_trades([("d%d" % i, 200.0) for i in range(30)], 0.0)
    good_te = score_trades([("d%d" % i, 150.0) for i in range(30)], 0.0)
    assert overfit_flag(good_tr, good_te) in ("ROBUST", "OK")
    bad_te = score_trades([("d%d" % i, -100.0) for i in range(30)], 0.0)
    assert overfit_flag(good_tr, bad_te) == "FAILS_OOS"
    thin = score_trades([("d1", 100.0)], 0.0)
    assert overfit_flag(good_tr, thin) == "THIN"
    # bb_expiry grid: 27 conservative combos, multi-key setters apply correctly
    bb_combos = list(iter_combos("bb_expiry_scalper", "conservative"))
    assert len(bb_combos) == 3 * 3 * 3, len(bb_combos)
    bb_base = {"bb_expiry_scalper": {"mode_a_min_score": 65, "score_entry": 65,
                                     "bb_squeeze_threshold": 0.5,
                                     "mode_a_stop_pct": 35, "mode_b_stop_pct": 40}}
    _, bbc = bb_combos[0]
    bb_out = apply_combo(bb_base, "bb_expiry_scalper", bbc, "conservative")
    assert bb_base["bb_expiry_scalper"]["mode_a_min_score"] == 65          # base unchanged
    assert bb_out["bb_expiry_scalper"]["mode_a_min_score"] == bbc["score"]  # both keys moved
    assert bb_out["bb_expiry_scalper"]["score_entry"] == bbc["score"]
    assert bb_out["bb_expiry_scalper"]["mode_a_stop_pct"] == bbc["stop"]
    assert bb_out["bb_expiry_scalper"]["mode_b_stop_pct"] == bbc["stop"]
    print("  optimize.py self-test: all assertions passed "
          f"({len(combos)} expiry + {len(bb_combos)} bb_expiry conservative combos).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Anti-overfit parameter optimisation for a strategy.")
    ap.add_argument("--selftest", action="store_true", help="run offline logic tests (no Kite)")
    ap.add_argument("--strategy", default="expiry_scalper",
                    help="engine dispatch key + grid key (e.g. expiry_scalper)")
    ap.add_argument("--name", help="strategy directory under strategies/ (e.g. EXPIRY_SCALPER_v1)")
    ap.add_argument("--from", dest="frm", default="2023-01-01")
    ap.add_argument("--to", dest="to", default="2026-06-30")
    ap.add_argument("--train-frac", type=float, default=0.65)
    ap.add_argument("--cost", type=float, default=150.0, help="round-trip cost per trade (Rs)")
    ap.add_argument("--grid", default="conservative", choices=["conservative", "full"])
    ap.add_argument("--source", default="kite", choices=["kite", "yahoo"],
                    help="'kite' = deep intraday history (REQUIRED for a real train/test "
                         "split). 'yahoo' only has ~60 days and collapses the split.")
    ap.add_argument("--allow-shallow", action="store_true",
                    help="override the deep-history guard (produces an INVALID OOS split — "
                         "smoke-testing only)")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if not args.name:
        print("  --name <STRATEGY_DIR> is required for a real run (or use --selftest).")
        print("  e.g. --name EXPIRY_SCALPER_v1"); return 1

    from main import load_configs
    global_config, strategy_config = load_configs(args.name)
    frm = date.fromisoformat(args.frm)
    to = date.fromisoformat(args.to)
    (tr, te) = _split_windows(frm, to, args.train_frac)

    # ── Deep-history guard (anti-fake-OOS) ────────────────────────────────────
    # Without Kite's deep intraday history the engine clamps EVERY window to the
    # last ~60 days, so train and test collapse to the SAME data and the
    # out-of-sample split is meaningless. Enable Kite and refuse to run shallow.
    deep = False
    if args.source == "kite":
        try:
            from src.data import kite_historical
            deep = kite_historical.enable(global_config)
        except Exception as e:
            print(f"  [!] Could not enable Kite historical: {str(e)[:120]}")
    if deep:
        print("  ✓ Kite deep intraday history ENABLED — train/test are genuinely disjoint.")
    else:
        span_days = (to - frm).days
        print("\n  " + "!" * 68)
        print("  DEEP HISTORY NOT AVAILABLE — the train/test split would COLLAPSE")
        print("  to the same ~60 recent days, making the out-of-sample result FAKE.")
        print("  Fix: run `python main.py --mode login` first, then re-run with")
        print("  --source kite. (Kite historical is a paid add-on.)")
        print("  " + "!" * 68)
        if not (args.allow_shallow or span_days <= 60):
            print("\n  Aborting to avoid a misleading result. Use --allow-shallow to force.\n")
            return 1
        print("\n  Proceeding SHALLOW (results are NOT a valid out-of-sample test).\n")
    print(f"\n  Optimising {args.strategy} · grid={args.grid} · cost=Rs{args.cost}/trade")
    rows = run_optimization(global_config, strategy_config, args.strategy,
                            frm, to, args.train_frac, args.cost, args.grid)
    print_ranked(rows, tr, te)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
