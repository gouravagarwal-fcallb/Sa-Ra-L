"""
Live Terminal Dashboard
────────────────────────
Displays pre-market signals, trade alerts, and P&L in real time.
Uses the `rich` library for a clean terminal UI.
"""

from __future__ import annotations
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich import box

from src.strategy.direction_engine import DirectionResult, Direction
from src.strategy.position_manager import PositionManager
from src.utils.helpers import format_inr, current_time_ist
from src.utils.market_calendar import is_expiry_day, get_weekly_expiry
from datetime import date

console = Console()


def _direction_badge(direction: Direction) -> str:
    badges = {
        Direction.BULLISH: "[bold green]▲ BULLISH — BUY CALL[/bold green]",
        Direction.BEARISH: "[bold red]▼ BEARISH — BUY PUT[/bold red]",
        Direction.NEUTRAL: "[bold yellow]◆ NEUTRAL — NO TRADE[/bold yellow]",
    }
    return badges[direction]


def print_premarket_report(dir_result: DirectionResult, vix: float) -> None:
    """Print pre-market direction assessment to terminal."""
    console.rule(f"[bold cyan]Sa-Ra-L Pre-Market Report — {datetime.now().strftime('%d %b %Y')}[/bold cyan]")

    today = date.today()
    expiry = is_expiry_day(today)
    expiry_date = get_weekly_expiry(today)

    info = Table(box=box.SIMPLE, show_header=False, min_width=55)
    info.add_column("", style="dim")
    info.add_column("")
    info.add_row("Today", datetime.now().strftime("%A, %d %b %Y"))
    info.add_row("Expiry Day", "[bold magenta]YES — EXPIRY DAY[/bold magenta]" if expiry else "No")
    info.add_row("Current Expiry", str(expiry_date))
    info.add_row("India VIX", f"[yellow]{vix:.2f}[/yellow]")
    console.print(info)

    # Score breakdown
    bd = dir_result.breakdown
    score_table = Table(title="Direction Score Breakdown", box=box.ROUNDED)
    score_table.add_column("Signal", style="bold")
    score_table.add_column("Value", justify="right")
    score_table.add_column("Score", justify="center")

    score_table.add_row(
        "Dow Jones",
        f"{bd['dow_jones']['change_pct']:+.2f}%",
        _score_cell(bd['dow_jones']['score']),
    )
    score_table.add_row(
        "Gift Nifty",
        f"{bd['gift_nifty']['premium_pts']:+.1f} pts",
        _score_cell(bd['gift_nifty']['score']),
    )
    score_table.add_row(
        "India VIX",
        f"{bd['india_vix']['level']:.2f}",
        _score_cell(bd['india_vix']['score']),
    )
    score_table.add_row(
        "Sensex",
        f"{bd['sensex']['change_pct']:+.2f}%",
        _score_cell(bd['sensex']['score']),
    )
    score_table.add_section()
    score_table.add_row(
        "[bold]TOTAL SCORE[/bold]",
        "",
        f"[bold]{bd['total']:+d}[/bold]",
    )
    console.print(score_table)

    console.print(Panel(
        _direction_badge(dir_result.direction) + f"\n\n[dim]{dir_result.reason}[/dim]",
        title="[bold]Today's Bias",
        border_style="green" if dir_result.direction == Direction.BULLISH
        else "red" if dir_result.direction == Direction.BEARISH else "yellow",
    ))


def _score_cell(score: int) -> str:
    if score > 0:
        return f"[green]{score:+d}[/green]"
    if score < 0:
        return f"[red]{score:+d}[/red]"
    return f"[dim]{score:+d}[/dim]"


def print_trade_alert(
    action: str,
    window_id: str,
    symbol: str,
    strike: int,
    opt_type: str,
    price: float,
    quantity: int,
    target_price: float,
    reason: str,
) -> None:
    """Print a prominent entry/exit trade alert."""
    color = "green" if action == "ENTRY" else "blue"
    console.print(Panel(
        f"[bold {color}]{action} SIGNAL — {window_id}[/bold {color}]\n\n"
        f"  Instrument : [bold]{symbol} {strike} {opt_type}[/bold]\n"
        f"  Action     : [bold]BUY {opt_type}[/bold]\n"
        f"  LTP        : ₹{price:.2f}\n"
        f"  Target     : ₹{target_price:.2f}  (+{(target_price/price-1)*100:.1f}%)\n"
        f"  Quantity   : {quantity:,}\n"
        f"  Time       : {datetime.now().strftime('%H:%M:%S')}\n\n"
        f"  [dim]{reason}[/dim]",
        title=f"[bold {color}]PAPER TRADE[/bold {color}]",
        border_style=color,
    ))


def print_pnl_dashboard(pm: PositionManager) -> None:
    """Print current day P&L summary."""
    console.print(Panel(
        pm.summary(),
        title=f"[bold]P&L Dashboard — {datetime.now().strftime('%H:%M:%S')}[/bold]",
        border_style="cyan",
    ))

    if pm.trades:
        t = Table(title="Today's Trades", box=box.SIMPLE_HEAVY)
        t.add_column("ID", style="dim")
        t.add_column("Window")
        t.add_column("Strike/Type")
        t.add_column("Entry ₹", justify="right")
        t.add_column("Exit ₹", justify="right")
        t.add_column("P&L", justify="right")
        t.add_column("Status")

        for trade in pm.trades:
            pnl_str = format_inr(trade.pnl) if trade.status.value == "CLOSED" else "—"
            pnl_color = "green" if trade.pnl >= 0 else "red"
            exit_p = f"₹{trade.exit_price:.2f}" if trade.status.value == "CLOSED" else "—"
            t.add_row(
                trade.trade_id,
                trade.window_id,
                f"{trade.strike}{trade.option_type}",
                f"₹{trade.entry_price:.2f}",
                exit_p,
                f"[{pnl_color}]{pnl_str}[/{pnl_color}]",
                trade.status.value,
            )
        console.print(t)
