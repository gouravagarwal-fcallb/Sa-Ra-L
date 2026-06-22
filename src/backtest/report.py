"""
Backtest Report Generator
──────────────────────────
Produces terminal summary + CSV export + equity curve plot.
Shows both Real P&L (trades before day stop) and Paper P&L (all trades).
"""

from __future__ import annotations
import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from rich.console import Console
from rich.table import Table
from rich import box

from src.backtest.engine import BacktestResult
from src.utils.helpers import format_inr

console = Console()


def print_summary(result: BacktestResult) -> None:
    all_trades = result.trades
    total_trades = len(all_trades)
    if total_trades == 0:
        console.print("[red]No trades in backtest.[/red]")
        return

    real_trades = [t for t in all_trades if not t.is_paper]
    paper_trades = [t for t in all_trades if t.is_paper]

    wins = [t for t in real_trades if t.pnl_rupees > 0]
    losses = [t for t in real_trades if t.pnl_rupees <= 0]

    avg_win = sum(t.pnl_rupees for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_rupees for t in losses) / len(losses) if losses else 0
    gross_wins = sum(t.pnl_rupees for t in wins)
    gross_losses = sum(t.pnl_rupees for t in losses)
    profit_factor = abs(gross_wins / gross_losses) if gross_losses != 0 else float("inf")

    nifty_trades = [t for t in real_trades if t.instrument == "NIFTY"]
    sensex_trades = [t for t in real_trades if t.instrument == "SENSEX"]
    expiry_trades = [t for t in real_trades if t.is_expiry]

    console.rule("[bold cyan]Sa-Ra-L Backtest Report v2[/bold cyan]")

    # ── Summary Table ────────────────────────────────────────────────────────
    t = Table(box=box.ROUNDED, show_header=False, min_width=55)
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")

    if result.daily_pnl:
        t.add_row("Period", f"{min(result.daily_pnl)} to {max(result.daily_pnl)}")
    t.add_row("Trading Days", str(len(result.daily_pnl)))
    t.add_row("Total Trades (Real)", str(len(real_trades)))
    t.add_row("  - Paper trades", str(len(paper_trades)))
    t.add_row(
        "Real P&L",
        f"[green]{format_inr(result.total_pnl)}[/green]"
        if result.total_pnl >= 0
        else f"[red]{format_inr(result.total_pnl)}[/red]",
    )
    t.add_row(
        "Paper P&L (all signals)",
        f"[green]{format_inr(result.total_pnl_paper)}[/green]"
        if result.total_pnl_paper >= 0
        else f"[red]{format_inr(result.total_pnl_paper)}[/red]",
    )
    t.add_row("Win Rate (real)", f"{result.win_rate:.1f}%")
    t.add_row("Profit Factor", f"{profit_factor:.2f}")
    t.add_row("Avg Win", format_inr(avg_win))
    t.add_row("Avg Loss", format_inr(avg_loss))
    t.add_row("Max Drawdown (real)", f"[red]{format_inr(result.max_drawdown)}[/red]")
    t.add_row("Sharpe Ratio", f"{result.sharpe:.2f}")
    t.add_row("  Nifty trades", str(len(nifty_trades)))
    t.add_row("  Sensex trades", str(len(sensex_trades)))
    t.add_row("  Expiry day trades", str(len(expiry_trades)))
    console.print(t)

    # ── Instrument Breakdown ─────────────────────────────────────────────────
    inst_table = Table(title="Instrument Breakdown (Real Trades)", box=box.SIMPLE)
    inst_table.add_column("Instrument")
    inst_table.add_column("Trades", justify="right")
    inst_table.add_column("Win %", justify="right")
    inst_table.add_column("Total P&L", justify="right")
    inst_table.add_column("Avg Budget", justify="right")
    for inst, trades_list in [("NIFTY", nifty_trades), ("SENSEX", sensex_trades)]:
        if not trades_list:
            continue
        iw = sum(1 for t in trades_list if t.pnl_rupees > 0)
        ip = sum(t.pnl_rupees for t in trades_list)
        avg_budget = sum(t.trade_budget for t in trades_list) / len(trades_list)
        color = "green" if ip >= 0 else "red"
        inst_table.add_row(
            inst,
            str(len(trades_list)),
            f"{iw/len(trades_list)*100:.0f}%",
            f"[{color}]{format_inr(ip)}[/{color}]",
            f"Rs.{avg_budget/1e5:.1f}L",
        )
    console.print(inst_table)

    # ── Exit Reason Breakdown ─────────────────────────────────────────────────
    exits = pd.DataFrame(
        [(t.exit_reason, t.pnl_rupees) for t in real_trades],
        columns=["reason", "pnl"],
    )
    exit_table = Table(title="Exit Reasons (Real Trades)", box=box.SIMPLE)
    exit_table.add_column("Reason")
    exit_table.add_column("Count", justify="right")
    exit_table.add_column("Total P&L", justify="right")
    for reason, grp in exits.groupby("reason"):
        pnl_sum = grp["pnl"].sum()
        color = "green" if pnl_sum >= 0 else "red"
        exit_table.add_row(
            str(reason),
            str(len(grp)),
            f"[{color}]{format_inr(pnl_sum)}[/{color}]",
        )
    console.print(exit_table)

    # ── Window Performance ────────────────────────────────────────────────────
    win_table = Table(title="Trade Window Performance (Real Trades)", box=box.SIMPLE)
    win_table.add_column("Window")
    win_table.add_column("Trades", justify="right")
    win_table.add_column("Win %", justify="right")
    win_table.add_column("Total P&L", justify="right")
    for wid in ["T1", "T2", "T3", "T4"]:
        wt = [t for t in real_trades if t.window_id == wid]
        if not wt:
            continue
        w_wins = sum(1 for t in wt if t.pnl_rupees > 0)
        w_pnl = sum(t.pnl_rupees for t in wt)
        color = "green" if w_pnl >= 0 else "red"
        win_table.add_row(
            wid,
            str(len(wt)),
            f"{w_wins/len(wt)*100:.0f}%",
            f"[{color}]{format_inr(w_pnl)}[/{color}]",
        )
    console.print(win_table)


def print_walk_forward_report(folds: list) -> None:
    """
    Print fold-by-fold metrics table and consistency summary.
    `folds` is the list returned by BacktestEngine.run_walk_forward().
    """
    if not folds:
        console.print("[red]No WFV folds to report.[/red]")
        return

    console.rule("[bold cyan]Walk-Forward Validation Report[/bold cyan]")

    # ── Per-fold metrics table ────────────────────────────────────────────────
    tbl = Table(title="Fold Results", box=box.ROUNDED, show_lines=True)
    tbl.add_column("Fold", justify="center", style="bold")
    tbl.add_column("Period",        justify="center")
    tbl.add_column("Days",          justify="right")
    tbl.add_column("Trades",        justify="right")
    tbl.add_column("Win %",         justify="right")
    tbl.add_column("Real P&L",      justify="right")
    tbl.add_column("Paper P&L",     justify="right")
    tbl.add_column("Max DD",        justify="right")
    tbl.add_column("Sharpe",        justify="right")
    tbl.add_column("Pfactor",       justify="right")

    fold_pnls  = []
    profitable = 0
    active_folds = 0  # folds that actually had trades

    for fold in folds:
        meta   = fold["meta"]
        result = fold["result"]
        real_trades = [t for t in result.trades if not t.is_paper]

        wins   = [t for t in real_trades if t.pnl_rupees > 0]
        losses = [t for t in real_trades if t.pnl_rupees <= 0]
        gw     = sum(t.pnl_rupees for t in wins)
        gl     = sum(t.pnl_rupees for t in losses)
        pf     = abs(gw / gl) if gl != 0 else float("inf")
        wr     = (len(wins) / len(real_trades) * 100) if real_trades else 0.0

        has_data = meta["trading_days"] >= 3  # minimum meaningful sample
        fold_pnls.append(result.total_pnl)
        if has_data:
            active_folds += 1
            if result.total_pnl > 0:
                profitable += 1

        pnl_col = (
            "[dim]Rs.0 (no data)[/dim]" if not has_data else
            f"[green]{format_inr(result.total_pnl)}[/green]"
            if result.total_pnl >= 0
            else f"[red]{format_inr(result.total_pnl)}[/red]"
        )
        paper_col = (
            "[dim]—[/dim]" if not has_data else
            f"[green]{format_inr(result.total_pnl_paper)}[/green]"
            if result.total_pnl_paper >= 0
            else f"[red]{format_inr(result.total_pnl_paper)}[/red]"
        )
        dd_col  = "[dim]—[/dim]" if not has_data else f"[red]{format_inr(result.max_drawdown)}[/red]"
        wr_str  = "[dim]—[/dim]" if not has_data else f"{wr:.0f}%"
        tr_str  = "[dim]—[/dim]" if not has_data else str(len(real_trades))
        pf_col  = "[dim]—[/dim]" if not has_data else (f"{pf:.2f}" if pf != float("inf") else "∞")
        day_str = str(meta["trading_days"]) if has_data else "[dim]0 ⚠[/dim]"

        tbl.add_row(
            str(meta["fold_num"]),
            f"{meta['start'].strftime('%d%b%y')}–{meta['end'].strftime('%d%b%y')}",
            day_str,
            tr_str,
            wr_str,
            pnl_col,
            paper_col,
            dd_col,
            "[dim]—[/dim]" if not has_data else f"{result.sharpe:.2f}",
            pf_col,
        )

    console.print(tbl)
    skipped = len(folds) - active_folds
    if skipped:
        console.print(
            f"[dim]⚠  {skipped} fold(s) marked with ⚠ had < 3 trading days — "
            "yfinance 5-min data only covers ~60 days. "
            "These are excluded from the consistency verdict.[/dim]"
        )

    # ── Consistency summary (active folds only) ───────────────────────────────
    n          = len(folds)
    active_pnls = [fold_pnls[i] for i, f in enumerate(folds) if f["meta"]["trading_days"] >= 3]
    avg_pnl    = sum(active_pnls) / len(active_pnls) if active_pnls else 0
    std_pnl    = pd.Series(active_pnls).std() if len(active_pnls) > 1 else 0.0
    total_pnl  = sum(fold_pnls)
    consistency = (profitable / active_folds * 100) if active_folds else 0.0

    summary = Table(title="Consistency Summary", box=box.SIMPLE, show_header=False)
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Total folds",              str(n))
    summary.add_row("Active folds (≥3 trade days)", str(active_folds))
    summary.add_row("Profitable folds",         f"{profitable}/{active_folds}  ({consistency:.0f}%)")
    summary.add_row("Combined real P&L",
                    f"[green]{format_inr(total_pnl)}[/green]"
                    if total_pnl >= 0 else f"[red]{format_inr(total_pnl)}[/red]")
    summary.add_row("Avg P&L per fold",         format_inr(avg_pnl))
    summary.add_row("Std dev of fold P&L",      format_inr(std_pnl))
    summary.add_row("Consistency score",        f"{consistency:.0f}%")

    # Regime flags
    consecutive_losses = max(
        sum(1 for _ in g) for k, g in
        __import__("itertools").groupby(pnl < 0 for pnl in fold_pnls)
        if k
    ) if fold_pnls else 0
    summary.add_row("Max consecutive losing folds", str(consecutive_losses))

    if consistency >= 75:
        verdict = "[bold green]ROBUST[/bold green] — strategy edge consistent across regimes"
    elif consistency >= 50:
        verdict = "[bold yellow]MODERATE[/bold yellow] — some regime sensitivity, monitor closely"
    else:
        verdict = "[bold red]FRAGILE[/bold red] — edge present in fewer than half of periods"
    summary.add_row("Verdict", verdict)
    console.print(summary)

    # ── Trend check ───────────────────────────────────────────────────────────
    if n >= 4:
        first_half_avg = sum(fold_pnls[:n//2]) / (n//2)
        second_half_avg = sum(fold_pnls[n//2:]) / (n - n//2)
        if second_half_avg < first_half_avg * 0.5:
            console.print(
                "[yellow]⚠  Performance in the second half is significantly lower than "
                "the first half — check for strategy decay or market regime shift.[/yellow]"
            )
        elif second_half_avg > first_half_avg * 1.5 and first_half_avg > 0:
            console.print(
                "[cyan]ℹ  Performance is improving over time — strategy may be "
                "benefiting from evolving market conditions.[/cyan]"
            )


def export_walk_forward_csv(folds: list, path: str = "data/historical/wfv_results.csv") -> None:
    """
    Export two CSVs:
      1. wfv_results.csv   — one row per fold (summary)
      2. wfv_all_trades.csv — every trade across all folds (detailed)
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # ── 1. Fold summary ───────────────────────────────────────────────────────
    summary_rows = []
    for fold in folds:
        meta   = fold["meta"]
        result = fold["result"]
        real_trades = [t for t in result.trades if not t.is_paper]
        wins   = [t for t in real_trades if t.pnl_rupees > 0]
        losses = [t for t in real_trades if t.pnl_rupees <= 0]
        gw = sum(t.pnl_rupees for t in wins)
        gl = sum(t.pnl_rupees for t in losses)
        summary_rows.append({
            "fold":          meta["fold_num"],
            "start":         meta["start"],
            "end":           meta["end"],
            "trading_days":  meta["trading_days"],
            "real_trades":   len(real_trades),
            "win_rate_pct":  round(len(wins) / len(real_trades) * 100, 1) if real_trades else 0,
            "real_pnl":      round(result.total_pnl, 2),
            "paper_pnl":     round(result.total_pnl_paper, 2),
            "max_drawdown":  round(result.max_drawdown, 2),
            "sharpe":        round(result.sharpe, 3),
            "profit_factor": round(abs(gw / gl), 3) if gl != 0 else None,
        })
    if summary_rows:
        pd.DataFrame(summary_rows).to_csv(path, index=False)
        print(f"WFV fold summary   : {path}")

    # ── 2. All trade details ──────────────────────────────────────────────────
    trade_rows = []
    cumulative = 0.0
    for fold in folds:
        meta   = fold["meta"]
        result = fold["result"]
        for t in sorted(result.trades, key=lambda x: (x.date, x.entry_time)):
            if not t.is_paper:
                cumulative += t.pnl_rupees
            trade_rows.append({
                "fold":             meta["fold_num"],
                "fold_period":      f"{meta['start'].strftime('%d%b%y')}–{meta['end'].strftime('%d%b%y')}",
                "date":             t.date,
                "window":           t.window_id,
                "instrument":       t.instrument,
                "direction":        t.direction,
                "option_type":      t.option_type,
                "strike":           t.strike,
                "entry_time":       t.entry_time,
                "exit_time":        t.exit_time,
                "entry_price":      t.entry_price,
                "exit_price":       t.exit_price,
                "pnl_pct":          t.pnl_pct,
                "gross_pnl":        round(t.gross_pnl, 2),
                "transaction_cost": round(t.transaction_cost, 2),
                "pnl_rupees":       round(t.pnl_rupees, 2),
                "cumulative_pnl":   round(cumulative, 2),
                "quantity":         t.quantity,
                "trade_budget":     t.trade_budget,
                "exit_reason":      t.exit_reason,
                "holding_min":      t.holding_minutes,
                "is_expiry":        t.is_expiry,
                "is_paper":         t.is_paper,
            })
    if trade_rows:
        trades_path = path.replace("wfv_results.csv", "wfv_all_trades.csv")
        pd.DataFrame(trade_rows).to_csv(trades_path, index=False)
        print(f"WFV all trades     : {trades_path}")


def export_csv(result: BacktestResult, path: str = "data/historical/backtest_trades.csv") -> None:
    if not result.trades:
        return

    rows = [
        {
            "date": t.date,
            "window": t.window_id,
            "instrument": t.instrument,
            "direction": t.direction,
            "option_type": t.option_type,
            "strike": t.strike,
            "entry_time": getattr(t, "entry_time", ""),
            "exit_time": getattr(t, "exit_time", ""),
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_pct": t.pnl_pct,
            "gross_pnl": getattr(t, "gross_pnl", t.pnl_rupees),
            "transaction_cost": getattr(t, "transaction_cost", 0),
            "pnl_rupees": t.pnl_rupees,
            "trade_budget": t.trade_budget,
            "quantity": t.quantity,
            "exit_reason": t.exit_reason,
            "holding_min": t.holding_minutes,
            "is_expiry": t.is_expiry,
            "is_paper": t.is_paper,
        }
        for t in result.trades
    ]

    df = pd.DataFrame(rows)
    df = df.sort_values(["date", "window"]).reset_index(drop=True)

    # Cumulative P&L and capital track (real trades only; paper trades shown at last real value)
    cumulative = 0.0
    cum_pnl_col = []
    cum_capital_col = []
    for _, row in df.iterrows():
        if not row["is_paper"]:
            cumulative += row["pnl_rupees"]
        cum_pnl_col.append(round(cumulative, 2))
        cum_capital_col.append(round(result.initial_capital + cumulative, 2))

    df.insert(df.columns.get_loc("pnl_rupees") + 1, "cumulative_pnl", cum_pnl_col)
    df.insert(df.columns.get_loc("cumulative_pnl") + 1, "cumulative_capital", cum_capital_col)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    print(f"Trades exported: {path}")


def plot_equity_curve(result: BacktestResult, path: str = "data/historical/equity_curve.png") -> None:
    if not result.daily_pnl:
        return

    real_series = pd.Series(result.daily_pnl).sort_index()
    paper_series = pd.Series(result.daily_pnl_paper).sort_index()

    real_cum = real_series.cumsum() + result.initial_capital
    paper_cum = paper_series.cumsum() + result.initial_capital

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={"height_ratios": [3, 1]})
    fig.suptitle("Sa-Ra-L Strategy v2 — Equity Curve (Nifty Tue + Sensex Thu)", fontsize=13, fontweight="bold")

    # Real equity
    ax1.plot(real_cum.index, real_cum.values / 1e5, color="#2196F3", linewidth=1.8, label="Real Trades", zorder=3)
    # Paper equity (dashed, lighter)
    ax1.plot(paper_cum.index, paper_cum.values / 1e5, color="#FF9800", linewidth=1.0,
             linestyle="--", alpha=0.7, label="All Signals (incl. paper)", zorder=2)
    ax1.axhline(result.initial_capital / 1e5, color="gray", linestyle="--", alpha=0.5, label="Starting capital")
    ax1.fill_between(real_cum.index, real_cum.values / 1e5, result.initial_capital / 1e5,
                     alpha=0.08, color="#2196F3")
    ax1.set_ylabel("Portfolio Value (Rs. Lakhs)")
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"Rs.{x:.0f}L"))
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.3)

    colors = ["#4CAF50" if v >= 0 else "#F44336" for v in real_series.values]
    ax2.bar(real_series.index, real_series.values / 1e5, color=colors, width=0.8)
    ax2.axhline(0, color="gray", linewidth=0.8)
    ax2.set_ylabel("Daily P&L (Rs. L)")
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"Rs.{x:.1f}L"))
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Equity curve saved: {path}")
