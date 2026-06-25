"""
BRAHMASTRA_v1 — Main Live Runner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Entry point for all live and paper trading sessions.
Orchestrates all 9 layers of the market intelligence platform.

Phases implemented:
  Phase 0 ✓ — Foundation: logging, bar builder, pre-market fetcher, tick stream
  Phase 1   — Indicator engine (in development)
  Phase 2   — Scenario engine (planned)
  Phase 3   — Trade execution (planned)
  Phase 4   — Backtest (planned)
  Phase 5   — UI/Frontend (planned)
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from src.brahmastra.logger import get_brahmastra_logger
from src.brahmastra.data.bar_builder import MultiInstrumentBarBuilder, Bar, TIMEFRAMES
from src.brahmastra.data.fetchers.premarket_fetch import fetch_premarket_briefing

IST = timezone(timedelta(hours=5, minutes=30))

# Instruments BRAHMASTRA watches
INSTRUMENTS = ["NIFTY", "SENSEX"]


class BrahmastraLive:
    """
    Main orchestrator for BRAHMASTRA_v1 live session.

    Lifecycle per session:
      1. Startup: load config, init logger, init bar builders
      2. Backfill: load historical bars for indicator warmup
      3. Pre-market (8:00–9:14 AM): fetch global data, compute BIAS
      4. Market open (9:15 AM onwards): stream ticks, build bars,
                                         run indicators, track scenarios, trade
      5. Market close (3:20 PM): exit all positions, EOD report
    """

    def __init__(
        self,
        strategy_config: dict,
        broker,
        mode: str = "paper",
        status_callback=None,
    ):
        self.sc      = strategy_config
        self.broker  = broker
        self.mode    = mode
        self._cb     = status_callback

        self.log = get_brahmastra_logger(config=strategy_config)
        self.log.system(f"BRAHMASTRA_v1 initialising | mode={mode} | phase=0_foundation")

        # Bar builders for all instruments
        self.bar_builders = MultiInstrumentBarBuilder(
            instruments    = INSTRUMENTS,
            timeframes     = TIMEFRAMES,
            on_bar_complete= self._on_bar_complete,
        )

        # Tick stream (set up in run())
        self._tick_stream = None

        # State
        self._briefing     = None
        self._session_date = date.today()
        self._running      = False
        self._market_open  = False
        self._tick_count   = 0

        # Phase tracking — what is currently active
        self._phase: str = "INIT"

        self.log.system("BRAHMASTRA_v1 initialised — all systems nominal")

    # ── Bar completion callback ────────────────────────────────────

    def _on_bar_complete(self, instrument: str, bar: Bar) -> None:
        """Called every time a bar completes on ANY timeframe for ANY instrument."""
        self.log.bar(
            f"{bar.timeframe} bar closed | {instrument}"
            f"  O={bar.open:.1f}  H={bar.high:.1f}"
            f"  L={bar.low:.1f}  C={bar.close:.1f}"
            f"  V={bar.volume:,.0f}"
            f"  VWAP={bar.vwap:.1f}"
            f"  {'▲' if bar.is_bull else '▼'}"
        )

        # Phase 2+: run indicator updates on the completed bar
        # (indicators not yet implemented — Phase 2)

    # ── Tick handler ────────────────────────────────────────────────

    def _on_tick(self, tick) -> None:
        """Called for every raw tick from Kite WebSocket or mock stream."""
        self._tick_count += 1

        # Route tick to bar builder
        self.bar_builders.on_tick(
            instrument = tick.instrument,
            price      = tick.last_price,
            volume     = getattr(tick, "volume", 0),
            ts         = tick.timestamp,
        )

        # Log tick (only at TICK level — filtered from console by default)
        self.log.tick(
            f"{tick.instrument}={tick.last_price:.2f}"
            f"  vol={getattr(tick, 'volume', 0):,.0f}"
            f"  #{self._tick_count}"
        )

        # Phase 2+: update indicators, scenarios, check entry conditions
        # (not yet implemented — Phase 2)

    # ── Pre-market phase ────────────────────────────────────────────

    def _run_premarket(self) -> None:
        """Fetch global data and compute BIAS score."""
        self._phase = "PREMARKET"
        now = datetime.now(IST)
        self.log.system(f"Pre-market intelligence fetch starting at {now.strftime('%H:%M')}")
        self.log.data("Fetching: SGX Nifty, US markets, Asian markets, VIX, PCR, FII...")

        try:
            self._briefing = fetch_premarket_briefing(self.sc)
            sign  = "+" if self._briefing.bias_score >= 0 else ""
            self.log.system(
                f"Pre-market complete | BIAS={sign}{self._briefing.bias_score}"
                f" ({self._briefing.bias_label})"
                f" | VIX={self._briefing.india_vix}"
                f" | PCR={self._briefing.pcr}"
                f" | FII=Rs.{self._briefing.fii_net_cr} Cr"
            )
            # Print briefing to console
            print("\n" + self._briefing.format_message() + "\n")

            # Phase 7+: send notification via email / Telegram
            # (notification system not yet implemented — Phase 7)

        except Exception as e:
            self.log.error(f"Pre-market fetch failed: {e}")
            self.log.system("Proceeding with NEUTRAL bias due to pre-market fetch failure")

    # ── Warmup status log ───────────────────────────────────────────

    def _log_warmup_status(self) -> None:
        status = self.bar_builders.warmup_status()
        for inst, tf_status in status.items():
            for tf, s in tf_status.items():
                warm = "WARM" if s["warm"] else f"WARMING {s['pct']:.0f}%"
                self.log.data(
                    f"Warmup | {inst} {tf:<4} | {s['have']}/{s['need']} bars | {warm}"
                )

    # ── Market scanning log (every bar, before trade engine) ────────

    def _log_scan_state(self, instrument: str) -> None:
        """Log what BRAHMASTRA is currently analysing — even with no trade active."""
        now = datetime.now(IST)

        # Current bar state across timeframes
        bar_summary = []
        for tf in ["1m", "5m", "15m", "1h"]:
            builder = self.bar_builders.get_builder(instrument)
            if builder:
                bar = builder.get_current_bar(tf)
                if bar:
                    bar_summary.append(
                        f"{tf}:{'▲' if bar.is_bull else '▼'}{bar.close:.0f}"
                    )

        self.log.decision(
            f"[{now.strftime('%H:%M:%S')}] SCANNING | {instrument} | "
            + "  ".join(bar_summary)
            + " | Phase 1 indicators not yet built — watching price structure"
        )

        # Phase 2+: this is where full indicator analysis and scenario
        # confidence scoring will be logged. Each indicator will emit
        # an ANALYSE log line with its value and confidence impact.

    # ── Main run ─────────────────────────────────────────────────────

    def run(self) -> None:
        self._running = True
        today = date.today()
        self.log.system(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        self.log.system(
            f"BRAHMASTRA_v1 SESSION START | {today} | mode={self.mode.upper()}"
        )
        self.log.system(
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        self.log.system(
            "Jai Shree Ganesh | Jai Shree Shyam | Om Namah Shivay | "
            "Jai Maa Shakambari | Jai Balaji Maharaj"
        )

        print(f"\n{'═'*62}")
        print(f"  BRAHMASTRA_v1  |  {today}  |  {self.mode.upper()}")
        print(f"  Market Intelligence Platform — Phase 0 Foundation")
        print(f"{'═'*62}")

        # ── Step 1: Backfill ──────────────────────────────────────
        self._phase = "BACKFILL"
        self.log.system("Step 1: Loading historical bars (backfill)...")
        print("\n  Loading historical bars for indicator warmup...")
        self._run_backfill()
        self._log_warmup_status()

        # ── Step 2: Pre-market ────────────────────────────────────
        now = datetime.now(IST)
        if now.hour < 9 or (now.hour == 9 and now.minute < 15):
            self.log.system("Step 2: Waiting for pre-market window (8:00 AM)...")
            self._wait_until(8, 0)
            self._run_premarket()
            # Wait for market open
            self.log.system("Pre-market complete. Waiting for market open at 9:15 AM...")
            print("\n  Waiting for market open (9:15 AM IST)...")
            self._wait_until(9, 15)
        else:
            self.log.system("Step 2: Market already open or pre-market window passed")
            # Try a quick briefing with whatever data is available
            self._run_premarket()

        # ── Step 3: Connect tick stream ───────────────────────────
        self._phase = "CONNECTING"
        self.log.system("Step 3: Connecting to tick stream...")
        self._connect_stream()

        # ── Step 4: Main scanning loop ────────────────────────────
        self._phase = "SCANNING"
        self._market_open = True
        self.log.system("Step 4: Market open — BRAHMASTRA scanning started")
        print("\n  Market open. BRAHMASTRA scanning...")
        print("  (Indicators and scenario engine: Phase 2 — in development)")
        print("  (Log file shows full bar-by-bar analysis)")
        print("  Press Ctrl+C to stop.\n")

        scan_interval = 60   # seconds between scan log entries
        last_scan_log = 0

        try:
            while self._running:
                now    = datetime.now(IST)
                now_hm = (now.hour, now.minute)

                # Hard close
                if now_hm >= (15, 20):
                    self.log.system("Hard close time reached (3:20 PM) — ending session")
                    break

                # Periodic scan state log
                if time.monotonic() - last_scan_log >= scan_interval:
                    for inst in INSTRUMENTS:
                        self._log_scan_state(inst)
                    last_scan_log = time.monotonic()

                # Risk checks (Phase 4 will add full gate checks here)
                self.log.risk(
                    f"Daily risk check | "
                    f"mode={self.mode} | "
                    f"ticks_received={self._tick_count} | "
                    f"bars_built_1m={self.bar_builders.get_builder('NIFTY').bar_count('1m') if self.bar_builders.get_builder('NIFTY') else 0}"
                )

                time.sleep(10)   # 10-second heartbeat

        except KeyboardInterrupt:
            self.log.system("Stopped by user (Ctrl+C)")
            print("\n  BRAHMASTRA stopped by user.")

        finally:
            self._running = False
            if self._tick_stream:
                self._tick_stream.stop()
            self._eod_report()

    # ── Backfill ──────────────────────────────────────────────────

    def _run_backfill(self) -> None:
        """Load historical bars into bar builders for indicator warmup."""
        try:
            from src.data.backfill import BackfillManager
            bf = BackfillManager()
            for inst in INSTRUMENTS:
                yf_sym = {"NIFTY": "^NSEI", "SENSEX": "^BSESN"}.get(inst, inst)
                for tf in ["5m", "15m", "1h", "1D"]:
                    # Map timeframe to yfinance intervals
                    tf_map = {"5m": "5m", "15m": "15m", "1h": "1h", "1D": "1d"}
                    interval = tf_map.get(tf)
                    days_back = {"5m": 5, "15m": 7, "1h": 30, "1D": 300}.get(tf, 5)
                    try:
                        raw_bars = bf.get_bars(yf_sym, interval=interval, days_back=days_back)
                        if raw_bars:
                            from src.brahmastra.data.bar_builder import Bar as BBar
                            from datetime import datetime as _dt_cls
                            loaded_bars = []
                            for rb in raw_bars:
                                try:
                                    ts = rb.get("datetime") or rb.get("ts")
                                    if isinstance(ts, str):
                                        ts = _dt_cls.fromisoformat(ts)
                                    if ts and ts.tzinfo is None:
                                        ts = ts.replace(tzinfo=IST)
                                    b = BBar(
                                        instrument = inst,
                                        timeframe  = tf,
                                        ts_open    = ts or datetime.now(IST),
                                        ts_close   = ts or datetime.now(IST),
                                        open       = float(rb.get("open",  rb.get("Close", 0))),
                                        high       = float(rb.get("high",  rb.get("High",  0))),
                                        low        = float(rb.get("low",   rb.get("Low",   0))),
                                        close      = float(rb.get("close", rb.get("Close", 0))),
                                        volume     = float(rb.get("volume",rb.get("Volume",0))),
                                        vwap       = float(rb.get("close", rb.get("Close", 0))),
                                        tick_count = 1,
                                        complete   = True,
                                    )
                                    loaded_bars.append(b)
                                except Exception:
                                    pass
                            builder = self.bar_builders.get_builder(inst)
                            if builder and loaded_bars:
                                builder.load_history(tf, loaded_bars)
                                self.log.data(
                                    f"Backfill loaded | {inst} {tf} | "
                                    f"{len(loaded_bars)} bars"
                                )
                    except Exception as e:
                        self.log.data(f"Backfill {inst} {tf}: {e}")
        except Exception as e:
            self.log.error(f"Backfill error: {e}")
            self.log.system("Proceeding without backfill — indicators will warm up from live data")

    # ── Tick stream connection ─────────────────────────────────────

    def _connect_stream(self) -> None:
        """Connect to live tick stream (Kite WebSocket or mock)."""
        try:
            if self.mode == "live" and hasattr(self.broker, "kite"):
                from src.brahmastra.data.fetchers.kite_stream import KiteTickStream
                self._tick_stream = KiteTickStream(
                    kite_api    = self.broker.kite,
                    instruments = INSTRUMENTS,
                    on_tick     = self._on_tick,
                    on_connect  = lambda: self.log.system("Kite WebSocket connected"),
                    on_disconnect = lambda e: self.log.error(f"WebSocket disconnected: {e}"),
                )
                self._tick_stream.start()
                self.log.system("Kite WebSocket stream started — live ticks flowing")
                time.sleep(2)   # let connection establish
            else:
                # Paper / no Kite: use mock stream with yfinance
                from src.brahmastra.data.fetchers.kite_stream import MockTickStream
                self._tick_stream = MockTickStream(
                    instruments  = INSTRUMENTS,
                    on_tick      = self._on_tick,
                    replay_speed = 1.0,
                )
                self._tick_stream.start()
                self.log.system("Mock tick stream started (paper mode — yfinance data)")
        except Exception as e:
            self.log.error(f"Stream connection failed: {e}")
            self.log.system("Running without tick stream — manual spot polling")

    # ── Helpers ───────────────────────────────────────────────────

    def _wait_until(self, hour: int, minute: int) -> None:
        """Sleep until the given IST time, logging every 5 minutes."""
        while self._running:
            now = datetime.now(IST)
            if now.hour > hour or (now.hour == hour and now.minute >= minute):
                return
            remaining = (hour * 60 + minute) - (now.hour * 60 + now.minute)
            self.log.system(f"Waiting for {hour:02d}:{minute:02d} IST — {remaining} min remaining")
            time.sleep(300)   # check every 5 minutes

    def _eod_report(self) -> None:
        """Log end-of-day summary."""
        self.log.system("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        self.log.system(f"BRAHMASTRA_v1 SESSION END | {self._session_date}")
        self.log.system(f"Total ticks received: {self._tick_count}")
        for inst in INSTRUMENTS:
            builder = self.bar_builders.get_builder(inst)
            if builder:
                for tf in ["1m", "5m", "15m", "1h"]:
                    self.log.system(f"  {inst} {tf}: {builder.bar_count(tf)} completed bars")
        self.log.system("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"\n  {'═'*62}")
        print(f"  BRAHMASTRA_v1 EOD | {self._session_date}")
        print(f"  Total ticks received: {self._tick_count}")
        print(f"  Phase 0 complete. Phase 1 (Indicators) — next.")
        print(f"  {'═'*62}\n")
