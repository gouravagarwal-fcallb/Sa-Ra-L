"""
Backtest Report Generator
──────────────────────────
Produces terminal summary + CSV export + equity curve plot.
"""

from __future__ import annotations
import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
from rich.console import Console
from rich.table import Table
from rich import box

from src.backtest.engine import BacktestResult
from src.utils.helpers import format_inr

console = Console()


def print_summary(result: BacktestResult) -> None:
    total_trades = len(result.trades)
    if total_trades == 0:
        console.print("[red]No trades in backtest.[/red]")
        return

    wins = [t for t in result.trades if t.pnl_rupees > 0]
    losses = [t for t in result.trades if t.pnl_rupees <= 0]
    expiry_trades = [t for t in result.trades if t.is_expiry]
    non_expiry_trades = [t for t in result.trades if not t.is_expiry]

    avg_win = sum(t.pnl_rupees for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_rupees for t in losses) / len(losses) if losses else 0
    profit_factor = (
        abs(sum(t.pnl_rupees for t in wins) / sum(t.pnl_rupees for t in losses))
        if losses and sum(t.pnl_rupees for t in losses) != 0
        else float("inf")
    )

    console.rule("[bold cyan]Sa-Ra-L Backtest Report[/bold cyan]")

    # ── Summary Table ────────────────────────────────────
    t = Table(box=box.ROUNDED, show_header=False, min_width=50)
    t.add_column("Metric", style="bold")
    t.add_column("Value", justify="right")

    t.add_row("Period", f"{min(result.daily_pnl)} to {max(result.daily_pnl)}")
    t.add_row("Total Trades", str(total_trades))
    t.add_row("Total P&L", f"[green]{format_inr(result.total_pnl)}[/green]" if result.total_pnl >= 0 else f"[red]{format_inr(result.total_pnl)}[/red]")
    t.add_row("Win Rate", f"{result.win_rate:.1f}%")
    t.add_row("Profit Factor", f"{profit_factor:.2f}")
    t.add_row("Avg Win", format_inr(avg_win))
    t.add_row("Avg Loss", format_inr(avg_loss))
    t.add_row("Max Drawdown", f"[red]{format_inr(result.max_drawdown)}[/red]")
    t.add_row("Sharpe Ratio", f"{result.sharpe:.2f}")
    t.add_row("Trading Days", str(len(result.daily_pnl)))
    t.add_row("─ Expiry day trades", str(len(expiry_trades)))
    t.add_row("─ Non-expiry trades", str(len(non_expiry_trades)))
    console.print(t)

    # ── Exit Reason Breakdown ────────────────────────────
    exits = pd.DataFrame([(t.exit_reason, t.pnl_rupees) for t in result.trades],
                         columns=["reason", "pnl"])
    exit_table = Table(title="Exit Reasons", box=box.SIMPLE)
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

    # ── Window Performance ───────────────────────────────
    win_table = Table(title="Trade Window Performance", box=box.SIMPLE)
    win_table.add_column("Window")
    win_table.add_column("Trades", justify="right")
    win_table.add_column("Win %", justify="right")
    win_table.add_column("Total P&L", justify="right")
    for wid in ["T1", "T2", "T3", "T4"]:
        wt = [t for t in result.trades if t.window_id == wid]
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
            "direction": t.direction,
            "option_type": t.option_type,
            "strike": t.strike,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_pct": t.pnl_pct,
            "pnl_rupees": t.pnl_rupees,
            "exit_reason": t.exit_reason,
            "holding_min": t.holding_minutes,
            "is_expiry": t.is_expiry,
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

    pnl_series = pd.Series(result.daily_pnl).sort_index()
    cumulative = pnl_series.cumsum() + result.initial_capital

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})
    fig.suptitle("Sa-Ra-L Strategy — Equity Curve", fontsize=14, fontweight="bold")

    ax1.plot(cumulative.index, cumulative.values / 1e5, color="#2196F3", linewidth=1.5)
    ax1.fill_between(cumulative.index, cumulative.values / 1e5,
                     result.initial_capital / 1e5, alpha=0.1, color="#2196F3")
    ax1.axhline(result.initial_capital / 1e5, color="gray", linestyle="--", alpha=0.5)
    ax1.set_ylabel("Portfolio Value (₹ Lakhs)")
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"₹{x:.0f}L"))
    ax1.grid(True, alpha=0.3)

    colors = ["#4CAF50" if v >= 0 else "#F44336" for v in pnl_series.values]
    ax2.bar(pnl_series.index, pnl_series.values / 1e5, color=colors, width=0.8)
    ax2.axhline(0, color="gray", linewidth=0.8)
    ax2.set_ylabel("Daily P&L (₹L)")
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"₹{x:.1f}L"))
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Equity curve saved: {path}")
