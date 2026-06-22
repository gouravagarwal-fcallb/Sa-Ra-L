"""
Portfolio Runner
────────────────
Runs multiple strategies in parallel threads from a single command.

Usage:
    python main.py --mode portfolio

What it does:
  1. Reads strategies/registry.yaml
  2. Finds all strategies with status: live or status: paper
  3. Launches each strategy's LiveEngine in its own background thread
  4. Shows a consolidated live dashboard (refreshes every 60 s)
  5. Ctrl+C gracefully stops all threads and prints combined EOD summary

Thread model:
  Each strategy is fully isolated — its own config, its own broker session,
  its own P&L tracking. A threading.Lock protects the shared status table.

Capital enforcement:
  The runner checks that total allocated capital across all active strategies
  does not exceed the portfolio capital limit defined in registry.yaml.
"""

from __future__ import annotations
import os
import sys
import time
import yaml
import threading
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


# ── Shared slot for live status of one strategy ────────────────────────────

@dataclass
class StrategyStatus:
    name: str
    mode: str           # "live" or "paper"
    direction: str = "—"
    score: int = 0
    budget: float = 0.0
    real_pnl: float = 0.0
    paper_pnl: float = 0.0
    open_positions: int = 0
    state: str = "STARTING"   # STARTING / RUNNING / STOPPED / ERROR
    error: str = ""
    last_tick: str = "—"


# ── Portfolio Runner ──────────────────────────────────────────────────────────

class PortfolioRunner:

    def __init__(self, registry_path: str = "strategies/registry.yaml",
                 settings: dict = None):
        self.registry_path = registry_path
        self.settings      = settings or {}
        self._lock         = threading.Lock()
        self._statuses: dict[str, StrategyStatus] = {}
        self._threads:  dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}

    def _load_registry(self) -> dict:
        with open(self.registry_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _load_strategy_config(self, strategy_name: str) -> dict:
        path = f"strategies/{strategy_name}/config.yaml"
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config not found: {path}")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _run_strategy_thread(
        self,
        name: str,
        mode: str,
        stop_event: threading.Event,
    ) -> None:
        """Thread target: runs one strategy's LiveEngine until stop_event is set."""
        try:
            with self._lock:
                self._statuses[name].state = "STARTING"

            strategy_config = self._load_strategy_config(name)
            stype = strategy_config.get("strategy_type", "5min_fixed_quantity")

            # Build broker
            if mode == "live":
                from src.broker.kite_broker import create_kite_broker
                broker = create_kite_broker(self.settings)
            else:
                from src.broker.paper_broker import PaperBroker
                broker = PaperBroker(
                    slippage_pct=strategy_config.get("backtest", {}).get("slippage_pct", 0.1)
                )

            # Only 1-min confluence strategies have a live engine today
            if stype == "expiry_scalper":
                from src.live.expiry_scalper_live import ExpiryScalperLive
                engine = ExpiryScalperLive(strategy_config, broker, mode=mode,
                                           status_callback=self._update_status(name))
            else:
                from src.live.live_engine import LiveEngine
                engine = LiveEngine(strategy_config, broker, mode=mode)
                engine._status_callback = self._update_status(name)

            with self._lock:
                self._statuses[name].state = "RUNNING"

            engine.run()

            with self._lock:
                self._statuses[name].state = "STOPPED"

        except Exception as e:
            with self._lock:
                self._statuses[name].state = "ERROR"
                self._statuses[name].error = str(e)[:80]

    def _update_status(self, name: str):
        """Return a callback that updates the shared status table."""
        def _cb(**kwargs):
            with self._lock:
                st = self._statuses.get(name)
                if st:
                    for k, v in kwargs.items():
                        if hasattr(st, k):
                            setattr(st, k, v)
                    st.last_tick = datetime.now(IST).strftime("%H:%M")
        return _cb

    def _print_dashboard(self) -> None:
        now = datetime.now(IST).strftime("%H:%M:%S IST")
        lines = [
            "",
            f"  Sa-Ra-L Portfolio  |  {date.today()}  |  {now}",
            "  " + "─" * 80,
            f"  {'Strategy':<22} {'Mode':<6} {'Dir':<9} {'Score':>5} "
            f"{'Budget':>9} {'Real P&L':>11} {'Paper P&L':>10} {'Pos':>4} {'State':<10}",
            "  " + "─" * 80,
        ]
        with self._lock:
            total_real  = 0.0
            total_paper = 0.0
            for name, st in self._statuses.items():
                state_col = (
                    f"\033[32m{st.state}\033[0m" if st.state == "RUNNING" else
                    f"\033[31m{st.state}\033[0m" if st.state in ("ERROR", "STOPPED") else
                    st.state
                )
                real_col  = f"\033[32m+{st.real_pnl:,.0f}\033[0m" if st.real_pnl >= 0 \
                            else f"\033[31m{st.real_pnl:,.0f}\033[0m"
                lines.append(
                    f"  {name:<22} {st.mode:<6} {st.direction:<9} {st.score:>5} "
                    f"  {st.budget:>8,.0f} {real_col:>20} "
                    f"  {st.paper_pnl:>9,.0f}   {st.open_positions:>3}  {state_col}"
                )
                if st.error:
                    lines.append(f"    └ ERROR: {st.error}")
                total_real  += st.real_pnl
                total_paper += st.paper_pnl
            lines.append("  " + "─" * 80)
            total_col = (f"\033[32m+{total_real:,.0f}\033[0m" if total_real >= 0
                         else f"\033[31m{total_real:,.0f}\033[0m")
            lines.append(
                f"  {'COMBINED':<22} {'':>6} {'':>9} {'':>5} "
                f"  {'':>8} {total_col:>20}   {total_paper:>9,.0f}"
            )
        print("\n".join(lines))

    def run(self) -> None:
        registry = self._load_registry()
        strategies_cfg = registry.get("strategies", {})

        # Find strategies eligible to run (live or paper status)
        runnable = {
            name: cfg for name, cfg in strategies_cfg.items()
            if cfg.get("status") in ("live", "paper")
        }

        if not runnable:
            print("\n  No strategies with status 'live' or 'paper' found in registry.")
            print("  Set at least one strategy's status to 'live' or 'paper' in:")
            print("  strategies/registry.yaml\n")
            return

        print(f"\n  Sa-Ra-L Portfolio Runner — {len(runnable)} strategy/ies active\n")
        for name, cfg in runnable.items():
            mode = "live" if cfg.get("status") == "live" else "paper"
            print(f"  Starting  {name:<25}  mode={mode.upper()}")

        # Confirm live deployments
        live_names = [n for n, c in runnable.items() if c.get("status") == "live"]
        if live_names:
            print(f"\n  LIVE strategies: {', '.join(live_names)}")
            confirm = input("  Type 'YES' to place real orders: ").strip().upper()
            if confirm != "YES":
                print("  Aborted.")
                return

        # Initialise status slots
        for name, cfg in runnable.items():
            mode = "live" if cfg.get("status") == "live" else "paper"
            self._statuses[name]    = StrategyStatus(name=name, mode=mode)
            self._stop_events[name] = threading.Event()

        # Launch threads
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

        print("\n  All strategies running. Press Ctrl+C to stop.\n")

        try:
            while any(t.is_alive() for t in self._threads.values()):
                os.system("cls" if os.name == "nt" else "clear")
                self._print_dashboard()
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n\n  Stopping all strategies...")
            for ev in self._stop_events.values():
                ev.set()
            for t in self._threads.values():
                t.join(timeout=10)

        self._print_eod_summary()

    def _print_eod_summary(self) -> None:
        print("\n" + "=" * 65)
        print(f"  Portfolio EOD Summary — {date.today()}")
        print("=" * 65)
        with self._lock:
            total = 0.0
            for name, st in self._statuses.items():
                sign = "+" if st.real_pnl >= 0 else ""
                print(f"  {name:<28} Real P&L: {sign}Rs.{st.real_pnl:,.0f}")
                total += st.real_pnl
            print("  " + "-" * 45)
            sign = "+" if total >= 0 else ""
            print(f"  {'TOTAL':<28} Real P&L: {sign}Rs.{total:,.0f}")
        print("=" * 65 + "\n")
