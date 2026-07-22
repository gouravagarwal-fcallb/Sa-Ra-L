#!/usr/bin/env python3
"""
Sa-Ra-L  |  Nifty & Sensex Weekly Options Strategy
════════════════════════════════════════════════════

Usage:
  python main.py --mode backtest          # Run historical backtest (5-min, 3 years)
  python main.py --mode backtest1m        # 1-min strategy backtest (last 7 days)
  python main.py --mode paper             # Live paper trading (no real orders)
  python main.py --mode live              # Live trading via Kite Connect
  python main.py --mode premarket         # Pre-market direction report only
  python main.py --mode login             # Generate daily Kite access token
  python main.py --mode test              # Run unit tests

  python main.py --mode brahmastra        --strategy BRAHMASTRA_v1  # BRAHMASTRA live
  python main.py --mode brahmastra_paper  --strategy BRAHMASTRA_v1  # BRAHMASTRA paper
  python main.py --mode connectivity_test --strategy BRAHMASTRA_v1  # Verify Kite plumbing

  python main.py --mode inrusd_bt         --strategy INRUSD_v1      # INRUSD 14-year backtest
  python main.py --mode inrusd_paper      --strategy INRUSD_v1      # INRUSD paper trading

Modes:
  backtest   — Replay 5-min intraday strategy on historical data (2023 → present).
  backtest1m — Replay 1-min three-layer confluence strategy. Uses yfinance 1-min
               data which covers only the last ~7 calendar days (~5 trading days).
  wfv        — Walk-Forward Validation: splits the full history into 8 equal
               folds and tests the strategy independently on each. Reports
               consistency across market regimes (fold win rate, Sharpe, drawdown).
  paper      — 1-min tick loop with Black-Scholes pricing. No Kite credentials needed.
  live       — Same loop but places real orders via Kite Connect.
               Requires KITE_API_KEY and KITE_ACCESS_TOKEN env vars.
  login      — Guides you through Kite OAuth to generate today's access token.
  premarket  — Quick morning briefing: direction score + today's bias.
  backfill   — Download and cache historical 1-min/5-min bars for indicator warmup.
               Use before starting live mode to avoid cold-start blind spots.
               Example: python main.py --mode backfill --strategy ATM_PULSE_BURST_v1

  brahmastra        — BRAHMASTRA_v1 fully autonomous live trading (real Kite orders).
  brahmastra_paper  — BRAHMASTRA_v1 paper trading (no real orders, uses MockTickStream).
  connectivity_test — Simple buy-hold-sell test to verify Kite API plumbing end-to-end.
                      Expected cost: Rs.50-200. NOT an intelligent trade.
  brahmastra_bt        — BRAHMASTRA 16-year structural backtest (2008-2024) via yfinance.
  brahmastra_dashboard — Paper engine + FastAPI + React dashboard on http://localhost:8000.
                         Build frontend first: cd frontend/brahmastra && npm run build
"""

import argparse
import sys
import yaml
import os

CONFIG_PATH   = "config/settings.yaml"
STRATEGY_PATH = "config/strategy_config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (override wins on conflicts)."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_configs(strategy_name: str = None):
    with open(CONFIG_PATH, encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    # Merge local overrides (gitignored — contains credentials)
    local_path = "config/settings.local.yaml"
    if os.path.exists(local_path):
        with open(local_path, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        settings = _deep_merge(settings, local)

    if strategy_name:
        strat_path = f"strategies/{strategy_name}/config.yaml"
        if not os.path.exists(strat_path):
            print(f"ERROR: Strategy '{strategy_name}' not found at {strat_path}")
            print("Available strategies:")
            for d in sorted(os.listdir("strategies")):
                if os.path.isdir(f"strategies/{d}"):
                    print(f"  --strategy {d}")
            sys.exit(1)
    else:
        strat_path = STRATEGY_PATH

    with open(strat_path, encoding="utf-8") as f:
        strategy = yaml.safe_load(f)

    return settings, strategy


def get_results_dir(strategy_name: str = None) -> str:
    """Return the output directory for backtest results."""
    if strategy_name:
        path = f"strategies/{strategy_name}/results"
        os.makedirs(path, exist_ok=True)
        return path
    os.makedirs("data/historical", exist_ok=True)
    return "data/historical"


# ─────────────────────────────────────────────────────
#  Mode: login  (generate daily Kite access token)
# ─────────────────────────────────────────────────────

def run_login(settings: dict) -> None:
    from src.broker.kite_broker import kite_login_flow
    kite_login_flow(settings)


# ─────────────────────────────────────────────────────
#  Mode: autologin  (headless — no browser required)
# ─────────────────────────────────────────────────────

def run_autologin(scheduled: bool = False) -> None:
    from src.broker.kite_auto_login import auto_login, wait_and_login
    if scheduled:
        # Block until 8:00 AM IST, then login (use when starting the night before)
        wait_and_login(target_hour=8, target_minute=0)
    else:
        auto_login(verbose=True)


# ─────────────────────────────────────────────────────
#  Mode: pre-market
# ─────────────────────────────────────────────────────

def run_premarket(strategy_config: dict) -> None:
    from src.strategy.direction_engine import DirectionEngine
    from src.data.market_data import get_india_vix
    from src.dashboard.signal_display import print_premarket_report

    engine = DirectionEngine(strategy_config)
    vix = get_india_vix()

    print("\nFetching pre-market data...")
    try:
        result = engine.evaluate_from_live_data()
    except Exception as e:
        print(f"Live data fetch failed ({e}). Running with sample data.")
        from src.strategy.direction_engine import DirectionInputs
        result = engine.evaluate(DirectionInputs(india_vix=vix or 15.0))

    print_premarket_report(result, vix or 15.0)


# ─────────────────────────────────────────────────────
#  Mode: backtest
# ─────────────────────────────────────────────────────

def _dispatch_backtest(engine, stype: str):
    """Run the right backtest method for a strategy_type and return the result."""
    table = {
        # Pass the configured range so RAMS gets a DEEP 1-min backtest when Kite is
        # on (else run_1min auto-falls back to the last 7 days for yfinance).
        "1min_confluence":          lambda: engine.run_1min(
                                        days_back=7,
                                        start=getattr(engine, "start_date", None),
                                        end=getattr(engine, "end_date", None)),
        "expiry_scalper":           engine.run_expiry_scalper,
        "range_scalper":            engine.run_range_scalper,
        "nifty_intraday":           engine.run_nifty_intraday,
        "atm_pulse_burst":          engine.run_atm_pulse_burst,
        "bb_expiry_scalper":        engine.run_bb_expiry_scalper,
        "black_swan":               engine.run_black_swan,
        "gap_fade":                 engine.run_gap_fade,
        "trend_following":          engine.run_trend_rider,
        "volatility_mean_reversion": engine.run_vix_seller,
    }
    return table.get(stype, engine.run)()


def _apply_date_override(strategy_config: dict, args) -> None:
    """Apply --from / --to to a strategy's backtest range (for deep runs)."""
    bt = strategy_config.setdefault("backtest", {})
    if getattr(args, "date_from", None):
        bt["start_date"] = args.date_from
    if getattr(args, "date_to", None):
        bt["end_date"] = args.date_to


def run_backtest(strategy_config: dict, strategy_name: str = None) -> None:
    from src.backtest.engine import BacktestEngine
    from src.backtest.report import print_summary, export_csv, plot_equity_curve, export_summary_json

    stype    = strategy_config.get("strategy_type", "5min_fixed_quantity")
    label    = strategy_name or "default"

    # BRAHMASTRA / PASHUPATASTRA have their own dedicated engines — route to them
    # instead of silently falling back to the generic 5-min engine.
    from src.backtest.special_backtests import is_special, run_special
    if is_special(stype):
        print(f"\nRunning [{stype}] via its dedicated engine for {label}...")
        s = run_special(stype, label, strategy_config)
        print(f"  {label}: {s['total_trades']} trades · "
              f"P&L Rs.{format(int(s['total_pnl']), ',')} · win {s['win_rate']}%")
        print(f"  basis: {s.get('data_basis')}")
        print(f"  ⚠ {s.get('caveat')}")
        print(f"\nBacktest complete. Results saved to {get_results_dir(strategy_name)}/")
        return

    engine   = BacktestEngine({}, strategy_config)
    out      = get_results_dir(strategy_name)

    print(f"\nRunning backtest [{stype}]: {engine.start_date} → {engine.end_date}")
    print(f"Strategy: {label}  |  Output: {out}/")
    print("Downloading historical data (first run may take 1–2 minutes)...\n")

    result = _dispatch_backtest(engine, stype)

    print_summary(result)
    export_csv(result, path=f"{out}/backtest_trades.csv")
    plot_equity_curve(result, path=f"{out}/equity_curve.png")
    export_summary_json(result, f"{out}/summary.json", strategy_name=label, run_kind="backtest")

    print(f"\nBacktest complete. Results saved to {out}/")


def run_backtest_all(args) -> None:
    """
    Net backtest across EVERY strategy in one command — the pre-open decision view.
    Runs each strategy's backtest over the configured (or --from/--to) range,
    writes each summary.json, and prints + saves a consolidated table.

    Deep history:  python main.py --mode backtest_all --source kite --from 2018-01-01
    """
    import os, json, yaml
    from datetime import date
    from src.backtest.engine import BacktestEngine
    from src.backtest.report import export_csv, plot_equity_curve, export_summary_json

    reg = yaml.safe_load(open("strategies/registry.yaml", encoding="utf-8"))["strategies"]
    rows = []
    print("\n  ════════════════════════════════════════════════════════════════")
    print("   Sa-Ra-L  ·  NET BACKTEST  (all strategies)")
    src = "KITE (deep history)" if getattr(args, "source", "yahoo") == "kite" else "yfinance (~60 days)"
    print(f"   Data source: {src}")
    print("  ════════════════════════════════════════════════════════════════\n")
    print(f"  {'STRATEGY':<24}{'TRADES':>8}{'NET P&L':>15}{'WIN%':>8}{'SHARPE':>8}{'MAX DD':>14}")
    print("  " + "─" * 77)

    for name, cfg in reg.items():
        path = f"strategies/{name}/config.yaml"
        if not os.path.exists(path):
            continue
        stype = None
        try:
            _, scfg = load_configs(name)
            _apply_date_override(scfg, args)
            stype = scfg.get("strategy_type", "")
            _special = {
                "brahmastra":     "dedicated engine — run: --mode backtest --strategy BRAHMASTRA_v1",
                "inrusd_futures": "use --mode inrusd_bt (USD/INR 14-yr)",
                "pashupatastra":  "dedicated engine — run: --mode backtest --strategy PASHUPATASTRA_v1",
            }
            if stype in _special:
                print(f"  {name:<24}{'—':>8}  ({_special[stype]})")
                rows.append({"name": name, "trades": 0, "pnl": 0.0, "win_rate": 0.0,
                             "sharpe": 0.0, "max_drawdown": 0.0,
                             "period": {"start": None, "end": None},
                             "status": "separate_engine", "note": _special[stype]})
                continue
            engine = BacktestEngine({}, scfg)
            result = _dispatch_backtest(engine, stype)
            out = get_results_dir(name)
            export_csv(result, path=f"{out}/backtest_trades.csv")
            try: plot_equity_curve(result, path=f"{out}/equity_curve.png")
            except Exception: pass
            s = export_summary_json(result, f"{out}/summary.json", strategy_name=name, run_kind="backtest")
            rows.append({"name": name, "trades": s["total_trades"], "pnl": s["total_pnl"],
                         "win_rate": s["win_rate"], "sharpe": s["sharpe"],
                         "max_drawdown": s["max_drawdown"], "period": s["period"]})
            print(f"  {name:<24}{s['total_trades']:>8}{('Rs.%s' % format(int(s['total_pnl']),',')):>15}"
                  f"{s['win_rate']:>7.1f}%{s['sharpe']:>8.2f}{('Rs.%s' % format(int(s['max_drawdown']),',')):>14}")
        except Exception as e:
            print(f"  {name:<24}  ERROR: {str(e)[:46]}")
            # Record the failure so the strategy stays VISIBLE in the net report /
            # dashboard instead of silently vanishing (this is why RAMS disappeared).
            rows.append({"name": name, "trades": 0, "pnl": 0.0, "win_rate": 0.0,
                         "sharpe": 0.0, "max_drawdown": 0.0,
                         "period": {"start": None, "end": None},
                         "status": "error", "error": str(e)[:160],
                         "strategy_type": stype})

    # Portfolio totals exclude error/separate-engine placeholder rows.
    real_rows = [r for r in rows if not r.get("status")]
    total_pnl = sum(r["pnl"] for r in real_rows)
    total_trades = sum(r["trades"] for r in real_rows)
    print("  " + "─" * 77)
    print(f"  {'PORTFOLIO (net)':<24}{total_trades:>8}{('Rs.%s' % format(int(total_pnl),',')):>15}")

    os.makedirs("reports", exist_ok=True)
    # Period ACTUALLY covered (min start / max end across strategies that traded), so
    # the dashboard can shout the window — a short test must never look like a full run.
    # Coerce to ISO strings first — some engines report period dates as datetime.date
    # objects and others as strings, and min()/max() can't compare across the two types
    # (TypeError: '<' not supported between 'str' and 'datetime.date'). ISO strings sort
    # chronologically, so string min/max gives the right earliest/latest.
    _starts = [str(r["period"]["start"]) for r in rows if (r.get("period") or {}).get("start")]
    _ends   = [str(r["period"]["end"])   for r in rows if (r.get("period") or {}).get("end")]
    period_covered = None
    if _starts and _ends:
        _s, _e = min(_starts), max(_ends)
        try:
            _days = (date.fromisoformat(str(_e)) - date.fromisoformat(str(_s))).days
        except Exception:
            _days = None
        period_covered = {"start": _s, "end": _e, "days": _days}
    req_from = getattr(args, "date_from", None)
    req_to   = getattr(args, "date_to", None)
    # Window tag in the filename so a short test never OVERWRITES the full-history run.
    _tag = lambda x: str(x).replace("-", "") if x else "auto"
    out_path = f"reports/net_backtest_{date.today().isoformat()}_{_tag(req_from)}_{_tag(req_to)}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated": date.today().isoformat(), "source": src,
                   "window_requested": {"from": req_from, "to": req_to},
                   "period_covered": period_covered,
                   "portfolio_net_pnl": round(total_pnl, 2),
                   "portfolio_trades": total_trades, "strategies": rows}, f, indent=2, default=str)
    print(f"\n  Net backtest saved to {out_path}")
    if period_covered:
        print(f"  Period covered: {period_covered['start']} → {period_covered['end']} "
              f"({period_covered['days']} days)")
    print("  Open the dashboard (Backtests tab) to drill into any strategy's report.\n")


# ─────────────────────────────────────────────────────
#  Mode: wfv  (Walk-Forward Validation)
# ─────────────────────────────────────────────────────

def run_wfv(strategy_config: dict, strategy_name: str = None) -> None:
    from src.backtest.engine import BacktestEngine
    from src.backtest.report import print_walk_forward_report, export_walk_forward_csv

    engine = BacktestEngine({}, strategy_config)
    out    = get_results_dir(strategy_name)
    print(f"\nWalk-Forward Validation: {engine.start_date} → {engine.end_date}")
    print(f"Strategy: {strategy_name or 'default'}  |  Output: {out}/")
    print("Splitting into 8 equal folds (~5 months each)...")
    print("Downloading historical data (first run may take 1–2 minutes)...\n")

    folds = engine.run_walk_forward(n_folds=8)
    print_walk_forward_report(folds)
    export_walk_forward_csv(folds, output_dir=out)
    print(f"\nWFV complete. Results saved to {out}/")


# ─────────────────────────────────────────────────────
#  Mode: backtest1m  (1-min strategy, last ~7 days)
# ─────────────────────────────────────────────────────

def run_backtest_1min(strategy_config: dict, strategy_name: str = None) -> None:
    from src.backtest.engine import BacktestEngine
    from src.backtest.report import print_summary, export_csv, plot_equity_curve, export_summary_json

    engine = BacktestEngine({}, strategy_config)
    out    = get_results_dir(strategy_name)
    print("\nRunning 1-min backtest — last 7 calendar days")
    print(f"Strategy: {strategy_name or 'default'}  |  Output: {out}/")
    print("(yfinance 1-min data limit: ~5 trading days)\n")

    result = engine.run_1min(days_back=7)

    print_summary(result)
    export_csv(result, path=f"{out}/backtest_1min_trades.csv")
    plot_equity_curve(result, path=f"{out}/equity_curve_1min.png")
    export_summary_json(result, f"{out}/summary.json",
                        strategy_name=strategy_name or "default", run_kind="backtest1m")

    print(f"\n1-min backtest complete. Results saved to {out}/")


# ─────────────────────────────────────────────────────
#  Mode: paper trading (no real orders)
# ─────────────────────────────────────────────────────

def run_paper(settings: dict, strategy_config: dict) -> None:
    from src.broker.paper_broker import PaperBroker
    from src.live.live_engine import LiveEngine

    broker = PaperBroker(slippage_pct=strategy_config.get("backtest", {}).get("slippage_pct", 0.1))
    engine = LiveEngine(strategy_config, broker, mode="paper")
    engine.run()


# ─────────────────────────────────────────────────────
#  Mode: live trading (real Kite Connect orders)
# ─────────────────────────────────────────────────────

def run_portfolio(settings: dict) -> None:
    from src.live.portfolio_runner import PortfolioRunner
    runner = PortfolioRunner(registry_path="strategies/registry.yaml", settings=settings)
    runner.run()


def run_live(settings: dict, strategy_config: dict) -> None:
    from src.broker.kite_broker import create_kite_broker
    from src.live.live_engine import LiveEngine

    print("\n⚠  LIVE MODE — Real orders will be placed on Kite Connect.")
    confirm = input("Type 'YES' or 'y' to confirm: ").strip().upper()
    if confirm not in ("YES", "Y"):
        print("Aborted.")
        return

    broker = create_kite_broker(settings)
    engine = LiveEngine(strategy_config, broker, mode="live")
    engine.run()


# ─────────────────────────────────────────────────────
#  Mode: test
# ─────────────────────────────────────────────────────

def run_backfill(strategy_config: dict = None, strategy_name: str = None) -> None:
    from src.data.backfill import run_backfill as _backfill
    _backfill(strategy_config=strategy_config, strategy_name=strategy_name)


# ─────────────────────────────────────────────────────
#  Mode: BRAHMASTRA live  (real Kite orders)
# ─────────────────────────────────────────────────────

def run_brahmastra(settings: dict, strategy_config: dict) -> None:
    from src.broker.kite_broker import create_kite_broker
    from src.brahmastra.brahmastra_live import BrahmastraLive

    print("\n")
    print("┌─────────────────────────────────────────────────────────────────┐")
    print("│  ⚡  BRAHMASTRA v1  —  LIVE MODE                               │")
    print("│  Real orders will be placed via Kite Connect.                  │")
    print("│  MIS product — all positions auto-squared at 3:20 PM.         │")
    print("└─────────────────────────────────────────────────────────────────┘")
    confirm = input("\n  Type YES to confirm or NO to abort: ").strip().upper()
    if confirm not in ("YES", "Y"):
        print("  Aborted.")
        return

    broker = create_kite_broker(settings)
    engine = BrahmastraLive(strategy_config, broker, mode="live")
    engine.run()


# ─────────────────────────────────────────────────────
#  Mode: BRAHMASTRA paper  (no real orders)
# ─────────────────────────────────────────────────────

def run_brahmastra_paper(settings: dict, strategy_config: dict) -> None:
    from src.broker.paper_broker import PaperBroker
    from src.brahmastra.brahmastra_live import BrahmastraLive

    broker = PaperBroker(
        slippage_pct=strategy_config.get("backtest", {}).get("slippage_pct", 0.1)
    )
    engine = BrahmastraLive(strategy_config, broker, mode="paper")
    engine.run()


# ─────────────────────────────────────────────────────
#  Mode: connectivity_test  (verify Kite plumbing)
# ─────────────────────────────────────────────────────

def run_brahmastra_backtest(strategy_config: dict) -> None:
    from src.brahmastra.backtest.backtest_engine import BrahmastraBacktest
    cfg        = strategy_config.get("backtest", {})
    instrument = cfg.get("instrument", "NIFTY")
    start_year = cfg.get("start_year", 2008)
    end_year   = cfg.get("end_year", 2024)
    capital    = strategy_config.get("capital", {}).get("starting_capital", 10000)
    lot_size   = (strategy_config.get("instruments", {})
                  .get(instrument.lower(), {}).get("lot_size", 65))
    out_dir    = f"strategies/BRAHMASTRA_v1/results"
    import os; os.makedirs(out_dir, exist_ok=True)

    engine = BrahmastraBacktest(
        instrument       = instrument,
        start_year       = start_year,
        end_year         = end_year,
        starting_capital = capital,
        lot_size         = lot_size,
    )
    result = engine.run(verbose=True)
    if result.total_trades > 0:
        engine.export_csv(result, f"{out_dir}/backtest_16yr_{instrument}.csv")
        engine.plot_equity_curve(result, f"{out_dir}/equity_curve_16yr_{instrument}.png")
    print(f"\n  Results saved to {out_dir}/")


# ─────────────────────────────────────────────────────
#  Mode: brahmastra_dashboard  (paper + FastAPI + React)
# ─────────────────────────────────────────────────────

def run_brahmastra_dashboard(settings: dict, strategy_config: dict) -> None:
    """
    DEPRECATED standalone BRAHMASTRA dashboard. BRAHMASTRA now runs INSIDE the
    unified dashboard, which serves the same React app on port 8000 but with the
    full /api/strategies… API. Running this old server on 8000 made every page show
    'Backend route /api/strategies returned the web page' (its API lacks those
    routes). So this mode now transparently launches the unified dashboard instead.
    """
    print("\n  ┌──────────────────────────────────────────────────────────────┐")
    print("  │  The standalone BRAHMASTRA dashboard is retired.             │")
    print("  │  BRAHMASTRA now runs INSIDE the unified dashboard — launching │")
    print("  │  that instead. Start it from the Strategies tab (Paper/Live). │")
    print("  └──────────────────────────────────────────────────────────────┘")
    run_unified(settings)


def _run_brahmastra_dashboard_standalone(settings: dict, strategy_config: dict) -> None:
    """(kept for reference — the original standalone server, no longer wired)"""
    import threading
    from src.brahmastra.brahmastra_live import BrahmastraLive

    broker_mode = settings.get("broker", {}).get("mode", "paper")

    if broker_mode == "live":
        print("\n")
        print("  ╔══════════════════════════════════════════════════╗")
        print("  ║  ⚡  BRAHMASTRA v1  —  LIVE MODE                ║")
        print("  ║  Real orders will be placed via Kite Connect.   ║")
        print("  ║  All positions auto-squared at 3:20 PM IST.     ║")
        print("  ╚══════════════════════════════════════════════════╝")
        confirm = input("\n  Type YES to confirm: ").strip().upper()
        if confirm not in ("YES", "Y"):
            print("  Aborted.")
            return
        from src.broker.kite_broker import create_kite_broker
        broker = create_kite_broker(settings)
    else:
        from src.broker.paper_broker import PaperBroker
        broker = PaperBroker(
            slippage_pct=strategy_config.get("backtest", {}).get("slippage_pct", 0.1)
        )

    engine = BrahmastraLive(strategy_config, broker, mode=broker_mode)

    t = threading.Thread(target=engine.run, daemon=True, name="brahmastra-engine")
    t.start()

    print(f"\n  BRAHMASTRA dashboard | mode={broker_mode.upper()}")
    print("  Dashboard: http://localhost:8000")
    print("  Press Ctrl+C to stop.\n")

    import uvicorn
    from src.brahmastra.api.server import app
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


def run_connectivity_test_mode(settings: dict, strategy_config: dict) -> None:
    from src.broker.kite_broker import create_kite_broker
    from src.brahmastra.connectivity_test import run_connectivity_test

    broker      = create_kite_broker(settings)
    cfg         = strategy_config.get("connectivity_test", {})
    hold_secs   = cfg.get("hold_seconds", 30)
    result      = run_connectivity_test(
        broker,
        strategy_config,
        hold_seconds=hold_secs,
        require_confirmation=True,
    )
    sys.exit(0 if result.get("success") else 1)


def run_inrusd_backtest(strategy_config: dict) -> None:
    from src.inrusd.inrusd_backtest import INRUSDBacktest
    import os

    bt_cfg  = strategy_config.get("backtest", {})
    start   = bt_cfg.get("start_date", "2012-01-01")
    end     = bt_cfg.get("end_date",   "2025-12-31")
    out_dir = "strategies/INRUSD_v1/results"
    os.makedirs(out_dir, exist_ok=True)

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  INRUSD_v1 — Currency Futures Trend Scalper Backtest        ║")
    print(f"║  Period: {start} → {end}                    ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    bt     = INRUSDBacktest(config=strategy_config, start_date=start, end_date=end, verbose=True)
    result = bt.run()

    if result.total_trades == 0:
        print("  No trades generated. Check data / signal conditions.")
        return

    print(result.summary())
    bt.print_regime_table(result)
    bt.print_yearly_table(result)
    bt.export_csv(result,        f"{out_dir}/backtest_trades.csv")
    bt.export_equity_csv(result, f"{out_dir}/equity_curve.csv")
    bt.plot_equity_curve(result, f"{out_dir}/equity_curve.png")
    print(f"\n  Results saved to {out_dir}/")


def run_inrusd_paper(strategy_config: dict) -> None:
    """
    INRUSD paper trading: computes pre-session bias, then runs signal engine
    on live USDINR quotes (yfinance) and logs signals without placing real orders.
    """
    from src.inrusd.data_fetcher import fetch_pre_session_snapshot
    from src.inrusd.premarket_bias import CurrencyBiasEngine
    from src.inrusd.inrusd_engine import INRUSDEngine

    print("\n  INRUSD_v1 — Paper Mode (no real orders)")
    print("  Fetching pre-session data...")

    snap   = fetch_pre_session_snapshot(verbose=True)
    engine = CurrencyBiasEngine(strategy_config)
    bias   = engine.compute(snap)

    print()
    print(bias.format_message())

    if bias.skip_trading:
        print("\n  Session bias is NEUTRAL — no trades today.")
        return

    print(f"\n  Session bias: {bias.direction} (score={bias.score:+d})")
    print("  Signal engine ready. Feed live USDINR bars to evaluate entry conditions.")
    print("  (Full tick-loop integration with Kite Connect stream pending.)")


def run_tests() -> None:
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        check=False,
    )
    sys.exit(result.returncode)


# ─────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sa-Ra-L — Nifty & Sensex Weekly Options Strategy"
    )
    parser.add_argument(
        "--mode",
        choices=[
            "backtest", "backtest1m", "wfv", "paper", "live", "portfolio",
            "premarket", "login", "autologin", "backfill", "test",
            "brahmastra", "brahmastra_paper", "connectivity_test", "brahmastra_bt",
            "brahmastra_dashboard",
            "inrusd_bt", "inrusd_paper",
            "unified", "readiness_check", "premarket_alert", "preflight",
            "backtest_all",
        ],
        default="premarket",
        help="Execution mode (default: premarket)",
    )
    parser.add_argument(
        "--source",
        choices=["yahoo", "kite"],
        default="yahoo",
        help="Backtest intraday data source. 'yahoo' (free, last ~60 days) or "
             "'kite' (deep intraday history ~2015+, needs a valid Kite login).",
    )
    parser.add_argument(
        "--futures-volume", dest="futures_volume", action="store_true",
        help="Overlay real NIFTY/SENSEX futures volume onto spot bars (--source kite "
             "only). Index spot candles carry volume=0, so volume-surge strategies "
             "(RAMS, TREND_RIDER, ATM_PULSE_BURST) fire 0 trades without this.",
    )
    parser.add_argument("--from", dest="date_from", default=None, metavar="YYYY-MM-DD",
                        help="Override backtest START date (deep history needs --source kite).")
    parser.add_argument("--to", dest="date_to", default=None, metavar="YYYY-MM-DD",
                        help="Override backtest END date.")
    parser.add_argument(
        "--strategy",
        default=None,
        metavar="NAME",
        help=(
            "Strategy name from strategies/ registry "
            "(e.g. --strategy RAMS_v1). "
            "Omit to use config/strategy_config.yaml with output to data/historical/."
        ),
    )
    args = parser.parse_args()

    # Prefer IPv4 for outbound (stable Kite IP-whitelist match across IPv6
    # rotations). Disable with PREFER_IPV4=0.
    try:
        from src.utils.net import prefer_ipv4_from_env
        prefer_ipv4_from_env(default=True)
    except Exception:
        pass

    if args.mode == "test":
        run_tests()
        return

    if args.mode == "autologin":
        # No strategy config needed — reads credentials from settings.local.yaml
        run_autologin(scheduled=False)
        return

    if args.mode == "preflight":
        from src.api.preflight import run_preflight
        sys.exit(run_preflight())

    settings, strategy_config = load_configs(args.strategy)

    # Route intraday backtest data through Kite when requested.
    if getattr(args, "source", "yahoo") == "kite":
        from src.data import kite_historical
        if kite_historical.enable(settings):
            print("  Backtest data source: KITE (deep intraday history ~2015+).")
            if getattr(args, "futures_volume", False):
                kite_historical.set_futures_volume(True)
                print("  Futures-volume overlay: ON (real NIFTY/SENSEX futures "
                      "volume on spot bars — volume-surge strategies can now fire).")
        else:
            print("  [!] Could not enable Kite source — falling back to yfinance. "
                  "Run 'python main.py --mode login' first.")

    # Apply --from / --to overrides to the single-strategy backtest range.
    _apply_date_override(strategy_config, args)

    if args.mode == "login":
        run_login(settings)
    elif args.mode == "premarket":
        run_premarket(strategy_config)
    elif args.mode == "backtest":
        run_backtest(strategy_config, args.strategy)
    elif args.mode == "backtest_all":
        run_backtest_all(args)
    elif args.mode == "backtest1m":
        run_backtest_1min(strategy_config, args.strategy)
    elif args.mode == "wfv":
        run_wfv(strategy_config, args.strategy)
    elif args.mode == "paper":
        run_paper(settings, strategy_config)
    elif args.mode == "live":
        run_live(settings, strategy_config)
    elif args.mode == "portfolio":
        run_portfolio(settings)
    elif args.mode == "backfill":
        run_backfill(strategy_config, args.strategy)
    elif args.mode == "brahmastra":
        run_brahmastra(settings, strategy_config)
    elif args.mode == "brahmastra_paper":
        run_brahmastra_paper(settings, strategy_config)
    elif args.mode == "connectivity_test":
        run_connectivity_test_mode(settings, strategy_config)
    elif args.mode == "brahmastra_bt":
        run_brahmastra_backtest(strategy_config)
    elif args.mode == "brahmastra_dashboard":
        run_brahmastra_dashboard(settings, strategy_config)
    elif args.mode == "inrusd_bt":
        run_inrusd_backtest(strategy_config)
    elif args.mode == "inrusd_paper":
        run_inrusd_paper(strategy_config)
    elif args.mode == "unified":
        run_unified(settings)
    elif args.mode == "readiness_check":
        run_readiness_check()
    elif args.mode == "premarket_alert":
        run_premarket_alert(settings)


def run_unified(settings: dict) -> None:
    """Launch the unified control dashboard (FastAPI + React) for all strategies."""
    from src.api.server import run_server
    import os
    autostart = os.environ.get("SARAL_AUTOSTART", "").lower() in ("1", "true", "yes")
    port = int(os.environ.get("SARAL_PORT", "8000"))
    # Bind to localhost by default — the dashboard places REAL orders and must not be
    # reachable from other machines on the network. Set SARAL_BIND_HOST=0.0.0.0 only
    # if you deliberately need LAN access (and put it behind auth/VPN).
    host = os.environ.get("SARAL_BIND_HOST", "127.0.0.1")
    run_server(host=host, port=port, autostart=autostart)


def run_premarket_alert(settings: dict) -> None:
    """Build the pre-market analysis and push the bias + conclusion to Telegram/email.
    Schedule this ~08:10 IST each trading morning (cron / Windows Task Scheduler)."""
    from src.api.premarket import build_premarket, format_alert
    print("\n  Building pre-market analysis (global basis)...")
    out = build_premarket(force=True)
    msg = format_alert(out)
    print("\n" + msg + "\n")
    try:
        from src.brahmastra.notifications.notifier import BrahmastraNotifier
        notifier = BrahmastraNotifier(settings)
        if getattr(notifier, "any_enabled", False):
            notifier.send_premarket_briefing(msg)
            print("  ✓ Pre-market briefing sent to enabled channels.")
        else:
            print("  (No notification channels enabled in settings — printed only.)")
    except Exception as e:
        print(f"  Notification skipped: {e}")


def run_readiness_check() -> None:
    """Print the backtest/backfill/ticks/config readiness matrix for all strategies."""
    import yaml
    from src.api.readiness import check_readiness
    from src.api.state_registry import get_multi_state
    reg = yaml.safe_load(open("strategies/registry.yaml", encoding="utf-8"))["strategies"]
    multi = get_multi_state()
    def mark(v):
        return "OK " if v is True else ("--" if v is None else "XX")
    print(f"\n  Sa-Ra-L Readiness Check — {len(reg)} strategies\n")
    print(f"  {'STRATEGY':<22} {'STATUS':<9} {'BTEST':<6} {'BFILL':<6} {'TICKS':<6} {'CFG':<5} OVERALL")
    print("  " + "─" * 68)
    for name, cfg in reg.items():
        runtime = {"running": False}
        try:
            r = check_readiness(name, cfg, runtime, multi)
        except Exception as e:
            print(f"  {name:<22} ERROR {str(e)[:40]}")
            continue
        print(f"  {name:<22} {str(cfg.get('status')):<9} "
              f"{mark(r['backtest_ok']):<6} {mark(r['backfill_ok']):<6} "
              f"{mark(r['ticks_ok']):<6} {mark(r['config_audit_ok']):<5} {r['overall']}")
    print()


if __name__ == "__main__":
    main()

