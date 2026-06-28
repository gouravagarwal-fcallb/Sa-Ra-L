"""
API-Driven Portfolio Runner
───────────────────────────
Wraps the existing `PortfolioRunner` (src/live/portfolio_runner.py) so strategies
can be started/stopped individually from the web API instead of all-at-once from
the CLI. Reuses the base class for registry/config loading, the strategy_type →
engine ladder, and the CSV trade log. Adds:

  • per-strategy start/stop on demand (start_strategy / stop_strategy)
  • a status callback that ALSO writes into the per-strategy BrahmastraState
    (the bridge that powers the rich per-strategy detail view)
  • no Rich terminal UI and no blocking input("YES") prompt — the live arming
    guard lives in the API layer (see server.py / Phase 4)
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta

from src.live.portfolio_runner import PortfolioRunner, StrategyStatus
from src.api.state_registry import MultiStrategyState, get_multi_state

IST = timezone(timedelta(hours=5, minutes=30))


class ApiPortfolioRunner(PortfolioRunner):
    """PortfolioRunner controllable per-strategy via the API."""

    def __init__(self, registry_path: str = "strategies/registry.yaml",
                 settings: dict = None, multi: MultiStrategyState = None):
        super().__init__(registry_path=registry_path, settings=settings)
        self.multi = multi or get_multi_state()
        self._init_csv()

    # ── Bridge callback: base status update + per-strategy state writes ────────

    def _bridge_callback(self, name: str):
        base_cb = self._update_status(name)          # reuse base behaviour
        st      = self.multi.get(name)

        def _cb(**kwargs):
            base_cb(**kwargs)                         # update StrategyStatus + CSV
            try:
                self._mirror_to_state(st, name, kwargs)
            except Exception:
                pass                                  # never let mirroring break a trade
        return _cb

    def _mirror_to_state(self, st, name: str, kw: dict) -> None:
        """Translate StrategyStatus/trade_event kwargs into BrahmastraState writes."""
        # Session-level fields for the detail view
        sess = {}
        if "real_pnl" in kw or "paper_pnl" in kw:
            sess["session_pnl"] = (kw.get("real_pnl", 0.0) or 0.0) + (kw.get("paper_pnl", 0.0) or 0.0)
        if "trades_today" in kw:
            sess["total_trades"] = kw["trades_today"]
        if "wins_today" in kw:
            sess["wins"] = kw["wins_today"]
        with self._lock:
            stt = self._statuses.get(name)
        if stt:
            sess.setdefault("mode", stt.mode)
            sess.setdefault("phase", stt.state)
        if sess:
            st.update_session(**sess)

        signal = kw.get("signal")
        if signal and kw.get("notable"):
            st.add_log("ANALYSIS", signal)

        ev = kw.get("trade_event")
        if ev:
            etype = ev.get("event", "TRADE")
            st.add_log("TRADE",
                       f"{etype} {ev.get('instrument','')} {ev.get('option_type','')}"
                       f"{ev.get('strike','')} qty={ev.get('quantity','')} "
                       f"@{ev.get('price','')} pnl={ev.get('pnl','')}")
            # Feed the TradePanel's closed-trades list directly.
            if etype not in ("ENTRY",):
                with st._lock:
                    st.closed_trades.append({
                        "trade_id":   f"{name}-{ev.get('time','')}",
                        "instrument": ev.get("instrument", ""),
                        "hypothesis": ev.get("direction", ""),
                        "strike":     ev.get("strike", ""),
                        "option_type": ev.get("option_type", ""),
                        "entry_price": ev.get("price", ""),
                        "current_price": ev.get("price", ""),
                        "realised_pnl": ev.get("pnl", 0),
                        "state":      etype,
                        "opened_at":  ev.get("time", ""),
                    })

    # ── Engine construction (reuses the base ladder, extended) ────────────────

    def _create_engine(self, name: str, strategy_config: dict, broker, mode: str,
                        cb, stop_event: threading.Event):
        stype = strategy_config.get("strategy_type", "5min_fixed_quantity")

        if stype == "expiry_scalper":
            from src.live.expiry_scalper_live import ExpiryScalperLive
            engine = ExpiryScalperLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "range_scalper":
            from src.live.range_scalper_live import RangeScalperLive
            engine = RangeScalperLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "black_swan":
            from src.live.black_swan_live import BlackSwanLive
            engine = BlackSwanLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "nifty_intraday":
            from src.live.nifty_intraday_live import NiftyIntradayLive
            engine = NiftyIntradayLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "atm_pulse_burst":
            from src.live.atm_pulse_burst_live import ATMPulseBurstLive
            engine = ATMPulseBurstLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "bb_expiry_scalper":
            from src.live.bb_expiry_scalper_live import BBExpiryScalperLive
            engine = BBExpiryScalperLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "brahmastra":
            from src.brahmastra.brahmastra_live import BrahmastraLive
            from src.brahmastra.api.state import get_state
            # Register brahmastra's global state as this strategy's coordinator slot
            self.multi.register(name, get_state())
            engine = BrahmastraLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "inrusd_futures":
            from src.live.inrusd_live import INRUSDLive
            engine = INRUSDLive(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype == "pashupatastra":
            from src.live.pashupatastra_runner import PashupatastraRunner
            engine = PashupatastraRunner(strategy_config, broker, mode=mode, status_callback=cb)
        elif stype in ("gap_fade", "trend_following", "volatility_mean_reversion"):
            # Testing-stage strategies: backtest-validated, live engine pending.
            raise NotImplementedError(
                f"{name} is in testing stage — backtest works "
                f"(python main.py --mode backtest --strategy {name}); "
                f"live engine pending validation.")
        else:
            from src.live.live_engine import LiveEngine
            engine = LiveEngine(strategy_config, broker, mode=mode)
            engine._status_callback = cb

        # Cooperative stop hook — engines check getattr(self,'_stop_event',None)
        try:
            engine._stop_event = stop_event
        except Exception:
            pass
        return engine

    # ── Per-strategy thread ───────────────────────────────────────────────────

    def _run_one(self, name: str, mode: str, stop_event: threading.Event) -> None:
        from src.utils.logger import setup_logger
        _log = setup_logger("api_runner")
        st_slot = self.multi.get(name)
        try:
            with self._lock:
                self._statuses[name].state = "STARTING"
            st_slot.update_session(phase="STARTING", mode=mode)

            strategy_config = self._load_strategy_config(name)

            if mode == "live":
                from src.broker.kite_broker import create_kite_broker
                broker = create_kite_broker(self.settings)
            else:
                from src.broker.paper_broker import PaperBroker
                broker = PaperBroker(
                    slippage_pct=strategy_config.get("backtest", {}).get("slippage_pct", 0.1))

            cb     = self._bridge_callback(name)
            engine = self._create_engine(name, strategy_config, broker, mode, cb, stop_event)

            with self._lock:
                self._statuses[name].state = "RUNNING"
            st_slot.update_session(phase="ACTIVE")
            _log.info(f"[{name}] engine running (mode={mode})")
            engine.run()

            with self._lock:
                self._statuses[name].state = "STOPPED"
            st_slot.update_session(phase="STOPPED")

        except NotImplementedError as e:
            with self._lock:
                self._statuses[name].state = "IDLE"
                self._statuses[name].error = str(e)[:120]
            st_slot.add_log("CTRL", str(e)[:160])
        except Exception as e:
            import traceback
            with self._lock:
                self._statuses[name].state = "ERROR"
                self._statuses[name].error = str(e)[:120]
            st_slot.add_log("ERROR", f"CRASHED: {str(e)[:160]}")
            _log.error(f"[{name}] CRASHED: {e}\n{traceback.format_exc()}")

    # ── Public control API ────────────────────────────────────────────────────

    def is_running(self, name: str) -> bool:
        t = self._threads.get(name)
        return bool(t and t.is_alive())

    def start_strategy(self, name: str, mode: str = "paper") -> dict:
        if self.is_running(name):
            return {"name": name, "started": False, "reason": "already running"}
        registry = self._load_registry().get("strategies", {})
        if name not in registry:
            return {"name": name, "started": False, "reason": "unknown strategy"}

        self._statuses[name]    = StrategyStatus(name=name, mode=mode)
        self._stop_events[name] = threading.Event()
        t = threading.Thread(target=self._run_one,
                             args=(name, mode, self._stop_events[name]),
                             name=f"strategy-{name}", daemon=True)
        self._threads[name] = t
        t.start()
        return {"name": name, "started": True, "mode": mode}

    def stop_strategy(self, name: str) -> dict:
        ev = self._stop_events.get(name)
        if ev:
            ev.set()
        with self._lock:
            if name in self._statuses:
                self._statuses[name].state = "STOPPING"
        if self.multi.has(name):
            self.multi.get(name).update_session(phase="STOPPED")
        return {"name": name, "stopping": True}

    def stop_all(self) -> dict:
        names = list(self._stop_events.keys())
        for n in names:
            self.stop_strategy(n)
        return {"stopping": names}

    def autostart_from_registry(self) -> list[dict]:
        """Start every strategy whose registry status is live/paper."""
        registry = self._load_registry().get("strategies", {})
        out = []
        for name, cfg in registry.items():
            status = cfg.get("status")
            if status in ("live", "paper"):
                out.append(self.start_strategy(name, mode=status))
        return out

    def runtime_status(self, name: str) -> dict:
        with self._lock:
            st = self._statuses.get(name)
            running = self.is_running(name)
            if not st:
                return {"running": running, "mode": None, "state": "IDLE",
                        "real_pnl": 0, "paper_pnl": 0, "open_positions": 0,
                        "trades_today": 0, "wins_today": 0, "last_tick": "—",
                        "last_signal": "—", "error": ""}
            return {
                "running": running, "mode": st.mode, "state": st.state,
                "direction": st.direction, "score": st.score, "budget": st.budget,
                "real_pnl": st.real_pnl, "paper_pnl": st.paper_pnl,
                "open_positions": st.open_positions, "trades_today": st.trades_today,
                "wins_today": st.wins_today, "last_tick": st.last_tick,
                "last_signal": st.last_signal, "error": st.error,
            }
