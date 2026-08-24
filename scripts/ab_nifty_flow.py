"""
A/B — does REAL near-month futures volume change NIFTY_INTRADAY's backtest?
═══════════════════════════════════════════════════════════════════════════
Why this is the honest experiment (not "add a new filter"):

  NIFTY_INTRADAY's TREND setup ALREADY gates entries on VWAP position + a volume
  surge (`min_volume_ratio`, see engine.run_nifty_intraday). But an index's SPOT
  volume is 0, so on plain spot bars that gate is degenerate — the volume-surge
  condition can't pass, so trend trades effectively never fire. The near-month
  FUTURES carry the real traded volume (same source the dashboard FLOW chip uses).

  So the question "does the volume / VWAP flow signal help this strategy?" is
  answered by running the SAME backtest twice:
     A) flat spot volume        (today's default — flow gate inert)
     B) futures-volume overlay  (real volume — flow gate live)
  ...and comparing. If B is better, the flow signal earns its place; if worse or
  equal, it doesn't — and we do NOT wire it live.

Research only. Changes NO live behaviour. Cross-strategy regime-gating stays HELD.
Needs Kite (run on the operator's machine, logged in):

    python -m scripts.ab_nifty_flow --from 2024-01-01 --to 2026-06-30
"""
from __future__ import annotations

import os
import sys
import copy
import argparse

# Allow both `python -m scripts.ab_nifty_flow` and `python scripts/ab_nifty_flow.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _metrics(result, export_summary_json, out_path, name):
    s = export_summary_json(result, out_path, strategy_name=name, run_kind="backtest")
    return {
        "trades": s.get("total_trades", 0),
        "pnl": s.get("total_pnl", 0.0),
        "win_rate": s.get("win_rate", 0.0),
        "sharpe": s.get("sharpe", 0.0),
        "max_dd": s.get("max_drawdown", 0.0),
        "profit_factor": s.get("profit_factor"),
        "period": s.get("period"),
    }


def _run_leg(strategy_config, name, futures_on, kite_historical,
             dispatch, get_results_dir, export_summary_json, tag):
    from src.backtest.engine import BacktestEngine
    kite_historical.set_futures_volume(futures_on)          # cache is keyed by this → re-fetches
    cfg = copy.deepcopy(strategy_config)
    engine = BacktestEngine({}, cfg)
    stype = cfg.get("strategy_type", "nifty_intraday")
    result = dispatch(engine, stype)
    out = get_results_dir(name)
    path = f"{out}/summary_Run{tag}.json"
    return _metrics(result, export_summary_json, path, name), path


def _pf_num(m):
    v = m.get("profit_factor")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pf_str(m):
    v = _pf_num(m)
    return "n/a" if v is None else f"{v:.2f}"


def _verdict(a, b):
    da = (b["pnl"] or 0) - (a["pnl"] or 0)
    pf_a, pf_b = _pf_num(a), _pf_num(b)
    more_trades = b["trades"] > a["trades"]
    # "material" = at least 5% of A's |P&L| or Rs.5,000, whichever larger
    thresh = max(5000.0, 0.05 * abs(a["pnl"] or 0))
    if abs(da) < thresh:
        return ("NO MATERIAL DIFFERENCE — real futures volume barely changes "
                "NIFTY_INTRADAY. The flow signal doesn't move this strategy on its "
                "own; keep it as a dashboard readout, don't wire it in.")
    better_pf = (pf_a is None or pf_b is None) or (pf_b >= pf_a)
    if da > 0 and better_pf:
        extra = " and enables trend trades that were previously blocked" if more_trades else ""
        return (f"HELPS — real volume adds Rs.{int(da):,} net"
                f"{f' (PF {pf_a:.2f} -> {pf_b:.2f})' if (pf_a and pf_b) else ''}{extra}. "
                "Promising, but confirm with a walk-forward run before ANY live use.")
    return (f"HURTS — real volume costs Rs.{int(-da):,} net"
            f"{f' (PF {pf_a:.2f} -> {pf_b:.2f})' if (pf_a and pf_b) else ''}. "
            "Do NOT wire the flow gate into NIFTY_INTRADAY live.")


def main():
    ap = argparse.ArgumentParser(
        description="A/B NIFTY_INTRADAY: flat spot volume vs near-month futures volume")
    ap.add_argument("--strategy", default="NIFTY_INTRADAY_v1")
    ap.add_argument("--from", dest="date_from", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default=None, metavar="YYYY-MM-DD")
    args = ap.parse_args()

    # Imported here (after arg-parse) so `--help` needs no Kite / heavy imports.
    from main import load_configs, _apply_date_override, get_results_dir, _dispatch_backtest
    from src.backtest.report import export_summary_json
    from src.data import kite_historical

    settings, strategy_config = load_configs(args.strategy)
    _apply_date_override(strategy_config, args)

    if not kite_historical.enable(settings):
        print("\n  [!] Could not enable Kite historical data — this A/B needs it.")
        print("      Log in first:  python main.py --mode autologin\n")
        sys.exit(1)

    print("\n  ══════════════════════════════════════════════════════════════════")
    print(f"   A/B · {args.strategy}")
    print("   flat spot volume (A)   vs   near-month futures volume (B)")
    print("  ══════════════════════════════════════════════════════════════════")

    print("\n  Leg A — flat spot volume (flow/volume gate inert)…")
    a, a_path = _run_leg(strategy_config, args.strategy, False, kite_historical,
                         _dispatch_backtest, get_results_dir, export_summary_json, "A")

    print("  Leg B — near-month futures-volume overlay (real volume)…")
    b, b_path = _run_leg(strategy_config, args.strategy, True, kite_historical,
                         _dispatch_backtest, get_results_dir, export_summary_json, "B")

    w = 18
    print("\n  ──────────────────────────────────────────────────────────────────")
    print(f"  {'METRIC':<16}{'A · flat vol':>{w}}{'B · futures vol':>{w}}")
    print("  ──────────────────────────────────────────────────────────────────")
    print(f"  {'Trades':<16}{a['trades']:>{w}}{b['trades']:>{w}}")
    print(f"  {'Net P&L (Rs)':<16}{int(a['pnl']):>{w},}{int(b['pnl']):>{w},}")
    print(f"  {'Win rate %':<16}{a['win_rate']:>{w}.1f}{b['win_rate']:>{w}.1f}")
    print(f"  {'Profit factor':<16}{_pf_str(a):>{w}}{_pf_str(b):>{w}}")
    print(f"  {'Sharpe':<16}{a['sharpe']:>{w}.2f}{b['sharpe']:>{w}.2f}")
    print(f"  {'Max DD (Rs)':<16}{int(a['max_dd']):>{w},}{int(b['max_dd']):>{w},}")
    print("  ──────────────────────────────────────────────────────────────────")
    print(f"\n  VERDICT: {_verdict(a, b)}")
    print(f"\n  Saved:  {a_path}\n          {b_path}")
    print("\n  Reminder: one backtest is evidence, not proof. Modelled option premiums,")
    print("  optimistic fills. Confirm anything promising with a walk-forward run.\n")


if __name__ == "__main__":
    main()
