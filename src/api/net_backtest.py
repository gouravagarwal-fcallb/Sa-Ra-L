"""
Net Backtest Summary (for the dashboard)
────────────────────────────────────────
Reads the most recent consolidated net-backtest report written by
`main.py --mode backtest_all` (reports/net_backtest_<date>.json) and enriches it
with the *capital* picture the rupee P&L alone doesn't convey:

  • per-trade capital each strategy deploys (from registry budget_range_rs),
  • peak simultaneous deployment (one open position per strategy),
  • average P&L per trade,
  • the honest caveats (modelled premiums, no costs, fixed-bet, perfect fills).

This is what the Backtests page shows at the top so the net result — and what it
really rests on — lives in the UI, not just in chat.
"""
from __future__ import annotations

import os
import re
import glob
import json

REPORTS_DIR = "reports"


def _latest_report() -> str | None:
    paths = glob.glob(os.path.join(REPORTS_DIR, "net_backtest_*.json"))
    if not paths:
        return None
    # Filenames carry an ISO date; newest by name == newest run.
    return sorted(paths)[-1]


def _budget_min(cfg: dict) -> float | None:
    br = cfg.get("budget_range_rs")
    if isinstance(br, (list, tuple)) and br:
        try:
            return float(br[0])
        except Exception:
            return None
    return None


def build_net_backtest(registry: dict) -> dict:
    """Return the latest net-backtest report + a computed capital block. Never
    raises — returns {available: False, ...} when no report exists yet."""
    path = _latest_report()
    if not path:
        return {
            "available": False,
            "note": "No net backtest yet. Run:  python main.py --mode backtest_all "
                    "--source kite --from 2019-01-01  (add --futures-volume for the "
                    "volume-surge strategies).",
        }
    try:
        with open(path, encoding="utf-8") as f:
            rep = json.load(f)
    except Exception as e:
        return {"available": False, "note": f"Could not read {os.path.basename(path)}: {str(e)[:120]}"}

    strategies = rep.get("strategies", [])
    traded = [s for s in strategies if (s.get("trades") or 0) > 0]
    zero = [s.get("name") for s in strategies if (s.get("trades") or 0) == 0]

    # ── Capital picture ───────────────────────────────────────────────────────
    # Each strategy buys a FIXED rupee budget of premium per trade (non-compounded)
    # and holds ~one position at a time, so peak simultaneous deployment is the
    # sum of one-trade budgets across the strategies that actually traded.
    per_trade = []
    peak = 0.0
    for s in traded:
        cfg = registry.get(s.get("name"), {})
        b = _budget_min(cfg)
        per_trade.append({"name": s.get("name"), "per_trade_rs": b,
                          "status": cfg.get("status")})
        if b:
            peak += b

    total_pnl = rep.get("portfolio_net_pnl") or 0.0
    total_trades = rep.get("portfolio_trades") or 0
    avg = round(total_pnl / total_trades, 2) if total_trades else None

    capital = {
        "model": "fixed-budget, non-compounded (each trade buys a fixed premium budget)",
        "per_trade_rs": per_trade,
        "peak_simultaneous_rs": round(peak) if peak else None,
        "avg_pnl_per_trade_rs": avg,
        "caveats": [
            "Option premiums are MODELLED (Black-Scholes) — no real bid/ask spread.",
            "Zero transaction costs — no brokerage, STT, exchange/GST/stamp.",
            "Perfect fills assumed — no slippage or partial fills.",
            "Fixed-bet, not compounded — not a return on a single lump sum.",
        ],
    }

    return {
        "available": True,
        "report_file": os.path.basename(path),
        "generated": rep.get("generated"),
        "source": rep.get("source"),
        "portfolio_net_pnl": total_pnl,
        "portfolio_trades": total_trades,
        "strategies": strategies,
        "zero_trade": zero,
        "capital": capital,
    }
