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
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_pct": t.pnl_pct,
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
