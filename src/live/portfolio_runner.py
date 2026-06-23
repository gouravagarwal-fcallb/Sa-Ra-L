"""
Portfolio Runner
────────────────
Runs multiple strategies in parallel threads from a single command.

Usage:
    python main.py --mode portfolio

Features:
  - Rich live dashboard refreshing every 5 s
  - All trades (live + paper) logged to logs/trades_YYYY-MM-DD.csv
  - Recent trade activity shown in dashboard in real time
  - Ctrl+C gracefully stops all threads and prints EOD summary
"""

from __future__ import annotations
import os
import csv
import time
import yaml
import threading
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.live    import Live
from rich.table   import Table
from rich.panel   import Panel
from rich.text    import Text
from rich.console import Console, Group
from rich         import box

IST = timezone(timedelta(hours=5, minutes=30))
REFRESH_SECONDS = 5
MAX_RECENT_EVENTS = 12   # rows shown in "Recent Activity"


# ── Shared status slot ─────────────────────────────────────────────────────

@dataclass
class StrategyStatus:
    name:           str
    mode:           str
    direction:      str  = "—"
    score:          int  = 0
    budget:         float = 0.0
    real_pnl:       float = 0.0
    paper_pnl:      float = 0.0
    open_positions: int  = 0
    state:          str  = "STARTING"
    error:          str  = ""
    last_tick:      str  = "—"
    trades_today:   int  = 0
    wins_today:     int  = 0


# ── Portfolio Runner ──────────────────────────────────────────────────────────

class PortfolioRunner:

    def __init__(self, registry_path: str = "strategies/registry.yaml",
                 settings: dict = None):
        self.registry_path  = registry_path
        self.settings       = settings or {}
        self._lock          = threading.Lock()
        self._statuses:     dict[str, StrategyStatus]   = {}
        self._threads:      dict[str, threading.Thread] = {}
        self._stop_events:  dict[str, threading.Event]  = {}
        self._start_time    = datetime.now(IST)
        self._recent_events: list[dict] = []   # trade activity feed
        self._csv_path      = Path(f"logs/trades_{date.today().isoformat()}.csv")

    # ── CSV trade log ─────────────────────────────────────────────────────────

    def _init_csv(self) -> None:
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._csv_path.exists():
            with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([
                    "time", "strategy", "mode", "event",
                    "instrument", "direction", "option_type", "strike",
                    "price", "quantity", "pnl", "exit_reason", "window",
                ])

    def _log_trade_csv(self, event: dict) -> None:
        try:
            with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([
                    event.get("time", ""),
                    event.get("strategy", ""),
                    event.get("mode", ""),
                    event.get("event", ""),
                    event.get("instrument", ""),
                    event.get("direction", ""),
                    event.get("option_type", ""),
                    event.get("strike", ""),
                    event.get("price", ""),
                    event.get("quantity", ""),
                    event.get("pnl", ""),
                    event.get("exit_reason", ""),
                    event.get("window", ""),
                ])
        except Exception:
            pass

    # ── Registry / config loading ─────────────────────────────────────────────

    def _load_registry(self) -> dict:
        with open(self.registry_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _load_strategy_config(self, strategy_name: str) -> dict:
        path = f"strategies/{strategy_name}/config.yaml"
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config not found: {path}")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    # ── Strategy thread ───────────────────────────────────────────────────────

    def _run_strategy_thread(self, name: str, mode: str,
                             stop_event: threading.Event) -> None:
        try:
            with self._lock:
                self._statuses[name].state = "STARTING"

            strategy_config = self._load_strategy_config(name)
            stype = strategy_config.get("strategy_type", "5min_fixed_quantity")

            if mode == "live":
                from src.broker.kite_broker import create_kite_broker
                broker = create_kite_broker(self.settings)
            else:
                from src.broker.paper_broker import PaperBroker
                broker = PaperBroker(
                    slippage_pct=strategy_config.get("backtest", {}).get("slippage_pct", 0.1)
                )

            cb = self._update_status(name)

            if stype == "expiry_scalper":
                from src.live.expiry_scalper_live import ExpiryScalperLive
                engine = ExpiryScalperLive(strategy_config, broker, mode=mode,
                                           status_callback=cb)
            else:
                from src.live.live_engine import LiveEngine
                engine = LiveEngine(strategy_config, broker, mode=mode)
                engine._status_callback = cb

            with self._lock:
                self._statuses[name].state = "RUNNING"

            engine.run()

            with self._lock:
                self._statuses[name].state = "STOPPED"

        except Exception as e:
            with self._lock:
                self._statuses[name].state = "ERROR"
                self._statuses[name].error = str(e)[:120]

    # ── Status callback ───────────────────────────────────────────────────────

    def _update_status(self, name: str):
        """
        Returns a callback for strategies to report status + trade events.

        For trade events pass:  callback(trade_event={...})
        Standard fields:        callback(direction=..., real_pnl=..., etc.)
        """
        def _cb(**kwargs):
            trade_event = kwargs.pop("trade_event", None)
            with self._lock:
                st = self._statuses.get(name)
                if st:
                    for k, v in kwargs.items():
                        if hasattr(st, k):
                            setattr(st, k, v)
                    st.last_tick = datetime.now(IST).strftime("%H:%M:%S")

                if trade_event:
                    trade_event["strategy"] = name
                    trade_event["mode"]     = st.mode if st else "?"
                    trade_event.setdefault("time", datetime.now(IST).strftime("%H:%M:%S"))
                    self._recent_events.append(trade_event)
                    if len(self._recent_events) > MAX_RECENT_EVENTS:
                        self._recent_events.pop(0)
                    self._log_trade_csv(trade_event)
        return _cb

    # ── Rich dashboard ────────────────────────────────────────────────────────

    def _build_renderable(self):
        now   = datetime.now(IST)
        today = date.today()

        from src.utils.market_calendar import is_nifty_expiry_day, is_sensex_expiry_day
        if is_nifty_expiry_day(today):
            expiry_part = ("  ★ NIFTY EXPIRY DAY", "bold yellow")
        elif is_sensex_expiry_day(today):
            expiry_part = ("  ★ SENSEX EXPIRY DAY", "bold yellow")
        else:
            expiry_part = ("", "")

        elapsed     = int((now - self._start_time).total_seconds())
        hh, rem     = divmod(elapsed, 3600)
        mm, ss      = divmod(rem, 60)

        header = Text.assemble(
            ("  Sa-Ra-L Portfolio  ", "bold white"),
            (f"|  {today.isoformat()}  {today.strftime('%A')}", "dim white"),
            expiry_part,
            (f"  |  {now.strftime('%H:%M:%S')} IST", "dim white"),
            (f"  |  up {hh:02d}:{mm:02d}:{ss:02d}", "dim cyan"),
        )

        # ── Strategy status table ─────────────────────────────────────────
        tbl = Table(
            box=box.SIMPLE_HEAVY, show_header=True,
            header_style="bold cyan", border_style="dim blue",
            pad_edge=True, expand=True,
        )
        tbl.add_column("Strategy",    style="bold white", min_width=24)
        tbl.add_column("Mode",        min_width=6,  justify="center")
        tbl.add_column("State",       min_width=9,  justify="center")
        tbl.add_column("Dir / Score", min_width=12, justify="center")
        tbl.add_column("Budget",      min_width=10, justify="right")
        tbl.add_column("Real P&L",    min_width=13, justify="right")
        tbl.add_column("Paper P&L",   min_width=12, justify="right")
        tbl.add_column("Pos",         min_width=4,  justify="center")
        tbl.add_column("W / T",       min_width=7,  justify="center")
        tbl.add_column("Last tick",   min_width=10, justify="center")

        total_real = total_paper = 0.0

        with self._lock:
            for name, st in self._statuses.items():
                if st.state == "RUNNING":
                    state_s = "[bold green]RUNNING[/bold green]"
                elif st.state == "ERROR":
                    state_s = "[bold red]ERROR[/bold red]"
                elif st.state == "STOPPED":
                    state_s = "[red]STOPPED[/red]"
                else:
                    state_s = f"[yellow]{st.state}[/yellow]"

                mode_s = ("[bold red]LIVE[/bold red]" if st.mode == "live"
                          else "[cyan]PAPER[/cyan]")

                d = st.direction.upper()
                if d in ("BULLISH", "BUL"):
                    dir_s = f"[bold green]▲  {st.score:+d}[/bold green]"
                elif d in ("BEARISH", "BEA"):
                    dir_s = f"[bold red]▼  {st.score:+d}[/bold red]"
                elif d == "EXPIRY":
                    dir_s = "[bold yellow]EXPIRY[/bold yellow]"
                else:
                    dir_s = f"[dim]—  {st.score:+d}[/dim]"

                def _pnl(v):
                    if v > 0: return f"[green]+Rs.{v:,.0f}[/green]"
                    if v < 0: return f"[red]Rs.{v:,.0f}[/red]"
                    return "[dim]—[/dim]"

                bud_s = f"Rs.{st.budget:,.0f}" if st.budget > 0 else "[dim]—[/dim]"
                pos_s = (f"[bold yellow]{st.open_positions}[/bold yellow]"
                         if st.open_positions > 0 else "[dim]0[/dim]")
                wt_s  = (f"[green]{st.wins_today}[/green]/[white]{st.trades_today}[/white]"
                         if st.trades_today > 0 else "[dim]—[/dim]")

                tbl.add_row(name, mode_s, state_s, dir_s, bud_s,
                            _pnl(st.real_pnl), _pnl(st.paper_pnl),
                            pos_s, wt_s, st.last_tick)

                if st.error:
                    tbl.add_row(
                        f"  [dim red]└ {st.error[:88]}[/dim red]",
                        "", "", "", "", "", "", "", "", "",
                    )

                total_real  += st.real_pnl
                total_paper += st.paper_pnl

        def _tot(v):
            if v > 0: return f"[bold green]+Rs.{v:,.0f}[/bold green]"
            if v < 0: return f"[bold red]Rs.{v:,.0f}[/bold red]"
            return "[dim]Rs.0[/dim]"

        tbl.add_section()
        tbl.add_row("[bold white]COMBINED[/bold white]", "", "", "", "",
                    _tot(total_real), _tot(total_paper), "", "", "")

        # ── Recent trade activity feed ────────────────────────────────────
        act = Table(
            box=box.SIMPLE, show_header=True,
            header_style="bold magenta", border_style="dim",
            pad_edge=True, expand=True,
        )
        act.add_column("Time",       min_width=10, justify="center")
        act.add_column("Strategy",   min_width=20)
        act.add_column("Mode",       min_width=6,  justify="center")
        act.add_column("Event",      min_width=8,  justify="center")
        act.add_column("Trade",      min_width=28)
        act.add_column("Price",      min_width=10, justify="right")
        act.add_column("P&L",        min_width=12, justify="right")

        with self._lock:
            events = list(self._recent_events)   # snapshot

        if not events:
            act.add_row("[dim]—[/dim]", "[dim]Waiting for first trade…[/dim]",
                        "", "", "", "", "")
        else:
            for ev in reversed(events):   # newest first
                evt = ev.get("event", "")
                if evt == "ENTRY":
                    evt_s = "[bold cyan]ENTRY[/bold cyan]"
                elif evt in ("TARGET_HIT",):
                    evt_s = "[bold green]TARGET[/bold green]"
                elif evt == "STOP_LOSS":
                    evt_s = "[bold red]STOP[/bold red]"
                elif evt == "FORCE_CLOSE":
                    evt_s = "[yellow]CLOSE[/yellow]"
                else:
                    evt_s = f"[dim]{evt}[/dim]"

                mode_s = ("[bold red]LIVE[/bold red]" if ev.get("mode") == "live"
                          else "[cyan]PAPER[/cyan]")

                direction = ev.get("direction", "")
                dir_arrow = "▲" if direction == "BULLISH" else "▼" if direction == "BEARISH" else ""
                trade_s = (
                    f"{dir_arrow} {ev.get('instrument','')} "
                    f"{ev.get('option_type','')}{ev.get('strike','')}  "
                    f"qty={ev.get('quantity','')}"
                )

                pnl = ev.get("pnl", "")
                if pnl != "" and pnl is not None:
                    try:
                        pnl_f = float(pnl)
                        pnl_s = (f"[bold green]+Rs.{pnl_f:,.0f}[/bold green]" if pnl_f >= 0
                                 else f"[bold red]Rs.{pnl_f:,.0f}[/bold red]")
                    except Exception:
                        pnl_s = str(pnl)
                else:
                    pnl_s = "[dim]—[/dim]"

                price = ev.get("price", "")
                price_s = f"Rs.{float(price):,.1f}" if price != "" else "—"

                window = ev.get("window", "")
                strat_s = f"{ev.get('strategy','')}{'  '+window if window else ''}"

                act.add_row(
                    ev.get("time", "—"),
                    strat_s, mode_s, evt_s, trade_s, price_s, pnl_s,
                )

        hint = Text(
            f"  Ctrl+C to stop  |  Refresh {REFRESH_SECONDS}s  |  "
            f"Log: {self._csv_path}",
            style="dim",
        )

        return Panel(
            Group(header, tbl, act, hint),
            title="[bold blue]Sa-Ra-L  |  Live Portfolio Dashboard[/bold blue]",
            border_style="blue",
            padding=(0, 1),
        )

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        registry       = self._load_registry()
        strategies_cfg = registry.get("strategies", {})

        runnable = {
            name: cfg for name, cfg in strategies_cfg.items()
            if cfg.get("status") in ("live", "paper")
        }

        if not runnable:
            print("\n  No strategies with status 'live' or 'paper' found in registry.")
            print("  Set at least one strategy's status in strategies/registry.yaml\n")
            return

        self._init_csv()

        print(f"\n  Sa-Ra-L Portfolio Runner  —  {len(runnable)} strategy/ies\n")
        for name, cfg in runnable.items():
            mode = "live" if cfg.get("status") == "live" else "paper"
            print(f"  {name:<30}  {mode.upper()}")

        live_names = [n for n, c in runnable.items() if c.get("status") == "live"]
        if live_names:
            print(f"\n  [!] LIVE strategies (REAL ORDERS): {', '.join(live_names)}")
            confirm = input("  Type 'YES' to place real orders: ").strip().upper()
            if confirm != "YES":
                print("  Aborted.")
                return

        for name, cfg in runnable.items():
            mode = "live" if cfg.get("status") == "live" else "paper"
            self._statuses[name]    = StrategyStatus(name=name, mode=mode)
            self._stop_events[name] = threading.Event()

        for name, cfg in runnable.items():
            mode = "live" if cfg.get("status") == "live" else "paper"
            t = threading.Thread(
                target=self._run_strategy_thread,
                args=(name, mode, self._stop_events[name]),
                name=f"strategy-{name}",
                daemon=True,
            )
            self._threads[name] = t
            t.start()

        try:
            with Live(
                self._build_renderable(),
                refresh_per_second=1,
                screen=True,
                transient=False,
            ) as live:
                while any(t.is_alive() for t in self._threads.values()):
                    live.update(self._build_renderable())
                    time.sleep(REFRESH_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            print("\n  Stopping all strategies...")
            for ev in self._stop_events.values():
                ev.set()
            for t in self._threads.values():
                t.join(timeout=10)

        self._print_eod_summary()

    # ── EOD summary ───────────────────────────────────────────────────────────

    def _print_eod_summary(self) -> None:
        console = Console()
        tbl = Table(
            title=f"Portfolio EOD Summary  —  {date.today()}",
            box=box.DOUBLE_EDGE, show_header=True, header_style="bold cyan",
        )
        tbl.add_column("Strategy",  style="bold white", min_width=28)
        tbl.add_column("Mode",      justify="center")
        tbl.add_column("Real P&L",  justify="right", min_width=14)
        tbl.add_column("Paper P&L", justify="right", min_width=14)
        tbl.add_column("Trades",    justify="center")
        tbl.add_column("Wins",      justify="center")

        total_real = total_paper = 0.0
        with self._lock:
            for name, st in self._statuses.items():
                def _p(v):
                    if v >= 0: return f"[green]+Rs.{v:,.0f}[/green]"
                    return f"[red]Rs.{v:,.0f}[/red]"
                tbl.add_row(
                    name,
                    "LIVE" if st.mode == "live" else "PAPER",
                    _p(st.real_pnl), _p(st.paper_pnl),
                    str(st.trades_today), str(st.wins_today),
                )
                total_real  += st.real_pnl
                total_paper += st.paper_pnl

        tbl.add_section()
        tbl.add_row(
            "[bold]TOTAL[/bold]", "",
            (f"[bold green]+Rs.{total_real:,.0f}[/bold green]" if total_real >= 0
             else f"[bold red]Rs.{total_real:,.0f}[/bold red]"),
            (f"[bold green]+Rs.{total_paper:,.0f}[/bold green]" if total_paper >= 0
             else f"[bold red]Rs.{total_paper:,.0f}[/bold red]"),
            "", "",
        )
        console.print(tbl)
        console.print(
            f"\n  [dim]Full trade log: {self._csv_path}[/dim]\n"
        )
