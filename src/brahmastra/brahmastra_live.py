"""
BRAHMASTRA_v1 — Main Live Runner (Phase 4: Fully Integrated)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Orchestrates all layers:
  Phase 0 — Foundation: logger, bar builder, pre-market, tick stream
  Phase 1 — 25 indicators (ATR, EMA, RSI, MACD, BB, VWAP, ADX, Supertrend,
             StochRSI, Ichimoku, Pivots, Fibonacci, 20 candlestick patterns,
             Volume Profile, OBV, ROC, Aroon)
  Phase 2 — Scenario Engine (3 parallel: Bull/Bear 5m + best 15m)
  Phase 3 — Trade Engine (7-gate entry, half-Kelly sizing, trailing SL)
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from src.brahmastra.logger import get_brahmastra_logger
from src.brahmastra.data.bar_builder import MultiInstrumentBarBuilder, Bar, TIMEFRAMES
from src.brahmastra.api.state import get_state, reset_state, IndicatorSnapshot

IST = timezone(timedelta(hours=5, minutes=30))

INSTRUMENTS = ["NIFTY", "SENSEX"]

# Lazy-loaded notifier (None if all channels disabled)
_notifier = None


def _get_notifier(settings: dict):
    global _notifier
    if _notifier is None:
        from src.brahmastra.notifications.notifier import BrahmastraNotifier
        _notifier = BrahmastraNotifier(settings)
    return _notifier

# Primary timeframe for indicator computation and scenario engine
PRIMARY_TF  = "5m"
CONFIRM_TF  = "15m"


class _InstrumentState:
    """
    Holds all indicators, scenario engine, and trade engine for one instrument.
    One instance per instrument — everything runs in isolation.
    """

    def __init__(self, instrument: str, config: dict, broker, mode: str, log):
        self.instrument = instrument
        self._log       = log
        self._mode      = mode

        inst_cfg   = config.get("instruments", {}).get(instrument.lower(), {})
        self.lot_size = inst_cfg.get("lot_size", 75 if instrument == "NIFTY" else 10)

        # ── Phase 1: Indicators ───────────────────────────────────────────────
        from src.brahmastra.indicators.atr            import ATR
        from src.brahmastra.indicators.ema            import EMAStack
        from src.brahmastra.indicators.rsi            import RSI
        from src.brahmastra.indicators.macd           import MACD
        from src.brahmastra.indicators.bollinger      import BollingerBands
        from src.brahmastra.indicators.vwap           import VWAP
        from src.brahmastra.indicators.adx            import ADX
        from src.brahmastra.indicators.supertrend     import Supertrend
        from src.brahmastra.indicators.stochastic_rsi import StochasticRSI
        from src.brahmastra.indicators.ichimoku       import Ichimoku
        from src.brahmastra.indicators.pivot_points   import PivotTracker
        from src.brahmastra.indicators.fibonacci      import FibonacciTracker
        from src.brahmastra.indicators.candlestick_patterns import CandlestickScanner
        from src.brahmastra.indicators.volume_profile import VolumeProfile
        from src.brahmastra.indicators.momentum       import OBV, ROC, Aroon
        from src.brahmastra.indicators.confluence_scorer import ConfluenceScorer

        # Per primary timeframe indicators
        self.atr         = ATR(14)
        self.ema_stack   = EMAStack([9, 21, 50, 200])
        self.rsi         = RSI(14)
        self.macd        = MACD(12, 26, 9)
        self.bb          = BollingerBands(20, 2.0)
        self.vwap        = VWAP()
        self.adx         = ADX(14)
        self.supertrend  = Supertrend(10, 3.0)
        self.stoch_rsi   = StochasticRSI(14, 14, 3, 3)
        self.ichimoku    = Ichimoku()
        self.pivots      = PivotTracker()
        self.fibonacci   = FibonacciTracker(10)
        self.candles     = CandlestickScanner()
        self.vol_profile = VolumeProfile(50.0)
        self.obv         = OBV()
        self.roc         = ROC(10)
        self.aroon       = Aroon(25)

        self.confluence  = ConfluenceScorer()
        self.confluence.set_ema(self.ema_stack)
        self.confluence.set_rsi(self.rsi)
        self.confluence.set_macd(self.macd)
        self.confluence.set_bollinger(self.bb)
        self.confluence.set_vwap(self.vwap)
        self.confluence.set_adx(self.adx)
        self.confluence.set_supertrend(self.supertrend)
        self.confluence.set_stoch_rsi(self.stoch_rsi)
        self.confluence.set_ichimoku(self.ichimoku)
        self.confluence.set_obv(self.obv)
        self.confluence.set_roc(self.roc)
        self.confluence.set_aroon(self.aroon)
        self.confluence.set_volume_profile(self.vol_profile)
        self.confluence.set_candlestick_scanner(self.candles)
        self.confluence.set_atr(self.atr)

        self._india_vix: Optional[float] = None

        # ── Phase 2: Scenario Engine ─────────────────────────────────────────
        from src.brahmastra.scenarios.scenario_engine import ScenarioEngine
        self.scenarios = ScenarioEngine(
            instrument  = instrument,
            primary_tf  = PRIMARY_TF,
            confirm_tf  = CONFIRM_TF,
            on_signal   = self._on_scenario_signal,
        )

        # ── Phase 3: Trade Engine ─────────────────────────────────────────────
        from src.brahmastra.trading.trade_engine import TradeEngine
        self.trades = TradeEngine(
            instrument      = instrument,
            lot_size        = self.lot_size,
            config          = config,
            broker          = broker,
            mode            = mode,
            on_trade_event  = self._on_trade_event,
            log             = log,
        )

        # Track previous OBV for direction
        self._prev_obv: float = 0.0

        # Last computed strike (updated each bar)
        self._last_strike: Optional[int] = None
        self._last_expiry:  Optional[str] = None
        self._last_atr:     Optional[float] = None
        self._last_close:   float = 0.0

        # Prev session high/low for pivot calculation
        self._prev_high:  float = 0.0
        self._prev_low:   float = 0.0
        self._prev_close: float = 0.0
        self._session_date = None

    def _on_scenario_signal(self, signal) -> None:
        from src.brahmastra.scenarios.scenario_engine import ScenarioState
        self._log.scenario(
            f"SCN{signal.scenario_id} {signal.hypothesis} → {signal.state.value} "
            f"conf={signal.confidence:.1f}% | {signal.reason}"
        )
        if signal.sl_price:
            self._log.scenario(
                f"  SL={signal.sl_price:.0f}  T1={signal.target1 or '?'}  "
                f"T2={signal.target2 or '?'}  T3={signal.target3 or '?'}"
            )

        if signal.state == ScenarioState.CONFIRMED:
            self._log.alert(
                f"ENTRY SIGNAL | {self.instrument} {signal.hypothesis} "
                f"conf={signal.confidence:.1f}% | "
                f"SL={signal.sl_price or '?'}  T1={signal.target1 or '?'}"
            )
            # Notification sent from BrahmastraLive after trade engine confirms fill
            # (notifier reference is injected by BrahmastraLive after construction)

    def _on_trade_event(self, event_type: str, trade) -> None:
        self._log.trade(
            f"{event_type} | {trade.trade_id} | {trade.instrument} "
            f"{trade.strike}{trade.option_type} | qty={trade.quantity} | "
            f"pnl=Rs.{trade.net_pnl:+.0f}"
        )
        try:
            get_state().update_trade(trade)
        except Exception:
            pass
        # Push notification
        if self._notifier:
            try:
                if event_type == "ENTRY" and trade.entry_price:
                    self._notifier.send_entry_signal(
                        instrument  = trade.instrument,
                        hypothesis  = trade.hypothesis,
                        strike      = trade.strike,
                        option_type = trade.option_type,
                        confidence  = trade.confidence_at_entry,
                        entry_price = trade.entry_price,
                        sl_price    = trade.sl_price or 0,
                        target1     = trade.target1 or 0,
                        target2     = trade.target2 or 0,
                        target3     = trade.target3 or 0,
                        lot_size    = trade.lot_size,
                        lots        = trade.lots,
                    )
                elif event_type in ("EXIT", "SL_HIT") and trade.exit_price:
                    self._notifier.send_trade_exit(
                        instrument  = trade.instrument,
                        hypothesis  = trade.hypothesis,
                        strike      = trade.strike,
                        option_type = trade.option_type,
                        entry_price = trade.entry_price or 0,
                        exit_price  = trade.exit_price,
                        quantity    = trade.quantity,
                        reason      = trade.exit_reason,
                        net_pnl     = trade.net_pnl,
                    )
            except Exception:
                pass

    # Notifier injected by BrahmastraLive after __init__
    _notifier = None

    def on_bar(self, bar: Bar, capital_free: float = 100000) -> None:
        """
        Feed a completed bar for this instrument.
        Runs all indicators → confluence → scenarios → (trade if CONFIRMED).
        """
        if bar.timeframe != PRIMARY_TF:
            # Still run some indicators on other timeframes if needed
            if bar.timeframe == "1D":
                # Update pivot points with previous day's data
                self.pivots.update_session(
                    bar.ts_open.date(),
                    self._prev_high or bar.high,
                    self._prev_low  or bar.low,
                    self._prev_close or bar.close,
                )
                self._prev_high  = bar.high
                self._prev_low   = bar.low
                self._prev_close = bar.close
            return

        # ── Run all 15+ indicator updates ────────────────────────────────────
        atr_val = self.atr.update(bar)
        self.ema_stack.update(bar)
        self.rsi.update(bar)
        self.macd.update(bar)
        self.bb.update(bar)
        self.vwap.update(bar)
        self.adx.update(bar)
        st_result = self.supertrend.update(bar)
        self.stoch_rsi.update(bar)
        self.ichimoku.update(bar)
        self.fibonacci.update(bar)
        patterns = self.candles.update(bar)
        self.vol_profile.update(bar)
        obv_val = self.obv.update(bar)
        roc_val = self.roc.update(bar)

        if atr_val:
            self._last_atr = atr_val
        self._last_close = bar.close

        # Update confluence scorer with VIX
        self.confluence.set_india_vix(self._india_vix or 15.0)

        # ── ANALYSE log line for each key indicator ───────────────────────────
        self._log_indicator_state(bar, atr_val, obv_val, roc_val, st_result, patterns)

        # ── Run confluence score ──────────────────────────────────────────────
        conf_result = self.confluence.score(bar)
        self._log.score(
            f"{self.instrument} {PRIMARY_TF} | {conf_result.summary_line()}"
        )

        # ── Update scenarios ──────────────────────────────────────────────────
        obv_rising  = obv_val > self._prev_obv if obv_val != 0 else None
        self._prev_obv = obv_val

        strongest_pattern = self.candles.strongest()
        pattern_dir  = strongest_pattern.direction if strongest_pattern else None
        pattern_conf = strongest_pattern.confidence if strongest_pattern else 0.0

        ema_struct   = self.ema_stack.trend_structure()
        ichi_val     = self.ichimoku.value
        ichi_bias    = ichi_val.bias() if ichi_val else None

        st_flipped   = st_result.flipped if st_result else False
        st_dir       = st_result.direction if st_result else None

        signals = self.scenarios.update(
            confluence_score = conf_result.score,
            bar_close        = bar.close,
            atr_value        = atr_val,
            india_vix        = self._india_vix,
            pattern_signal   = pattern_dir,
            pattern_conf     = pattern_conf,
            st_flipped       = st_flipped,
            st_direction     = st_dir,
            obv_rising       = obv_rising,
            ema_structure    = ema_struct,
            ichi_bias        = ichi_bias,
            now              = bar.ts_close,
        )

        # Log scenario display block every bar
        self._log.scenario(self.scenarios.display_block())

        # ── Trigger trade engine for CONFIRMED scenarios ──────────────────────
        for scenario in self.scenarios.confirmed_scenarios():
            if scenario.state.value != "CONFIRMED":
                continue

            # Compute ATM strike
            strike  = self._get_atm_strike(bar.close)
            expiry  = self._get_expiry()
            if not strike or not expiry:
                continue

            self.trades.on_scenario_confirmed(
                scenario     = scenario,
                current_price = bar.close,
                atr_value    = atr_val or 50.0,
                india_vix    = self._india_vix,
                capital_free = capital_free,
                strike       = strike,
                expiry       = expiry,
                now          = bar.ts_close,
            )

    def _log_indicator_state(self, bar, atr_val, obv_val, roc_val,
                              st_result, patterns) -> None:
        """Emit ANALYSE log lines — system is always explaining itself."""
        now_str = bar.ts_close.strftime("%H:%M")

        # Core price structure
        self._log.analyse(
            f"[{now_str}] {self.instrument} {PRIMARY_TF} "
            f"O={bar.open:.0f} H={bar.high:.0f} L={bar.low:.0f} C={bar.close:.0f} "
            f"{'BULL' if bar.is_bull else 'BEAR'} body={bar.body_pct:.1f}%"
        )

        # EMA
        ema_vals = self.ema_stack.values()
        ema_str  = self.ema_stack.trend_structure() or "WARMING"
        self._log.analyse(
            f"  EMA: 9={ema_vals.get(9) or '?':.0f}  "
            f"21={ema_vals.get(21) or '?':.0f}  "
            f"50={ema_vals.get(50) or '?':.0f}  "
            f"200={ema_vals.get(200) or '?':.0f}  [{ema_str}]"
            if all(ema_vals.get(p) for p in [9, 21, 50]) else
            f"  EMA: warming up"
        )

        # RSI + MACD
        rsi_v = self.rsi.value
        macd_v = self.macd.value
        if rsi_v:
            div = self.rsi.divergence()
            self._log.analyse(
                f"  RSI={rsi_v:.1f} {'OB' if rsi_v>70 else 'OS' if rsi_v<30 else 'MID'}"
                f"{' DIV:'+div if div else ''}"
            )
        if macd_v:
            self._log.analyse(
                f"  MACD={macd_v.macd:+.2f}  Signal={macd_v.signal:+.2f}  "
                f"Hist={macd_v.histogram:+.2f}"
                f"{' CROSS:'+macd_v.crossover if macd_v.crossover else ''}"
            )

        # Supertrend + ATR
        if st_result:
            self._log.analyse(
                f"  SuperTrend={st_result.direction} val={st_result.value:.0f} "
                f"dist={st_result.distance:.0f}{'  *** FLIP ***' if st_result.flipped else ''}"
            )
        if atr_val:
            self._log.analyse(f"  ATR(14)={atr_val:.2f}")

        # Ichimoku
        ichi = self.ichimoku.value
        if ichi and ichi.senkou_b:
            self._log.analyse(
                f"  Ichimoku: bias={ichi.bias()} "
                f"cloud={'BULL' if ichi.cloud_bullish else 'BEAR'} "
                f"price={ichi.price_vs_cloud} "
                f"strength={ichi.strength}/6"
                f"{' TK:'+ichi.tk_cross if ichi.tk_cross else ''}"
            )

        # Volume Profile + VWAP
        vp = self.vol_profile.value
        vw = self.vwap.value
        if vp:
            self._log.analyse(
                f"  VolProfile: POC={vp.poc:.0f}  VA={vp.va_low:.0f}-{vp.va_high:.0f}  "
                f"zone={vp.zone(bar.close)}"
            )
        if vw:
            self._log.analyse(
                f"  VWAP={vw.vwap:.0f}  {vw.position}  "
                f"σ1={vw.lower1:.0f}–{vw.upper1:.0f}"
            )

        # Candlestick patterns
        if patterns:
            for p in patterns:
                self._log.analyse(
                    f"  CANDLE: {p.name} ({p.direction}) conf={p.confidence:.0%}"
                )

        # Push indicator snapshot to dashboard state
        try:
            ema_vals   = self.ema_stack.values()
            rsi_v      = self.rsi.value
            macd_v     = self.macd.value
            bb_v       = self.bb.value
            vwap_v     = self.vwap.value
            adx_v      = self.adx.value
            conf_r     = self.confluence.last_result
            strongest  = self.candles.strongest()
            snap = IndicatorSnapshot(
                instrument        = self.instrument,
                timeframe         = PRIMARY_TF,
                timestamp         = bar.ts_close.strftime("%H:%M:%S"),
                ema9              = ema_vals.get(9),
                ema21             = ema_vals.get(21),
                ema50             = ema_vals.get(50),
                ema200            = ema_vals.get(200),
                rsi               = rsi_v,
                macd              = macd_v.macd if macd_v else None,
                macd_hist         = macd_v.histogram if macd_v else None,
                bb_upper          = bb_v.upper if bb_v else None,
                bb_lower          = bb_v.lower if bb_v else None,
                bb_pct_b          = bb_v.pct_b if bb_v else None,
                vwap              = vwap_v.vwap if vwap_v else None,
                atr               = atr_val,
                adx               = adx_v.adx if adx_v else None,
                supertrend_dir    = st_result.direction if st_result else None,
                ichimoku_bias     = self.ichimoku.value.bias() if self.ichimoku.value else None,
                confluence_score  = conf_r.score if conf_r else None,
                confluence_dir    = conf_r.direction if conf_r else None,
            )
            get_state().update_indicators(self.instrument, snap)
        except Exception:
            pass

    def _get_atm_strike(self, spot: float) -> Optional[int]:
        from src.utils.helpers import round_to_strike
        step = 50 if self.instrument == "NIFTY" else 100
        return round_to_strike(spot, step)

    def _get_expiry(self) -> Optional[str]:
        try:
            from src.utils.market_calendar import get_nifty_weekly_expiry
            from datetime import date as _date
            expiry = get_nifty_weekly_expiry(_date.today())
            return expiry.strftime("%Y%m%d")
        except Exception:
            return None

    def on_tick(self, price: float, atr: Optional[float] = None,
                india_vix: Optional[float] = None,
                now: Optional[datetime] = None) -> None:
        """Update open trades on each price tick."""
        if india_vix:
            self._india_vix = india_vix
            self.confluence.set_india_vix(india_vix)
        self.trades.on_tick(price, atr or self._last_atr, india_vix, now)

    def force_eod_exit(self, price: float) -> None:
        self.trades.force_exit_all(price)
        self.scenarios.expire_all()


class BrahmastraLive:
    """
    Main orchestrator for BRAHMASTRA_v1 live session.

    Lifecycle per session:
      1. Startup: init logger, init bar builders, init per-instrument state
      2. Backfill: load historical bars for indicator warmup
      3. Pre-market (8:00–9:14 AM): fetch global data, compute BIAS
      4. Market open (9:15 AM → 3:20 PM):
            tick stream → bar builder → indicators → confluence → scenarios → trades
      5. EOD 3:20 PM: force-exit all positions, print session report
    """

    def __init__(
        self,
        strategy_config: dict,
        broker,
        mode: str = "paper",
        status_callback=None,
    ):
        self.sc     = strategy_config
        self.broker = broker
        self.mode   = mode
        self._cb    = status_callback

        self.log = get_brahmastra_logger(config=strategy_config)
        self.log.system(
            f"BRAHMASTRA_v1 initialising | mode={mode} | instruments={INSTRUMENTS}"
        )

        # Multi-instrument bar builder
        self.bar_builders = MultiInstrumentBarBuilder(
            instruments    = INSTRUMENTS,
            timeframes     = TIMEFRAMES,
            on_bar_complete= self._on_bar_complete,
        )

        # Per-instrument state (indicators + scenarios + trades)
        cap = strategy_config.get("capital", {}).get("starting_capital", 100000)
        self._inst_state: dict[str, _InstrumentState] = {
            inst: _InstrumentState(inst, strategy_config, broker, mode, self.log)
            for inst in INSTRUMENTS
        }
        self._capital_free: float = cap

        self._tick_stream  = None
        self._briefing     = None
        self._session_date = date.today()
        self._running      = False
        self._tick_count   = 0
        self._india_vix:   Optional[float] = None

        # Notifications
        self._notifier = _get_notifier(strategy_config)
        # Inject notifier reference into each instrument state
        for state in self._inst_state.values():
            state._notifier = self._notifier

        # Dashboard state wiring
        self._dash_state = reset_state()
        self._dash_state.update_session(
            mode=mode, date=str(self._session_date), phase="INIT"
        )
        self.log.set_structured_callback(
            lambda cat, msg: self._dash_state.add_log(cat, msg)
        )

        # Track prev prices for change_pct in tick state
        self._prev_prices: dict[str, float] = {}

        self.log.system("BRAHMASTRA_v1 fully initialised — Phase 0-4 integrated")

    # ── Bar complete callback ─────────────────────────────────────────────────

    def _on_bar_complete(self, instrument: str, bar: Bar) -> None:
        """Called for every completed bar on every timeframe for every instrument."""
        self.log.bar(
            f"{bar.timeframe} | {instrument} | "
            f"O={bar.open:.1f} H={bar.high:.1f} L={bar.low:.1f} C={bar.close:.1f} "
            f"V={bar.volume:,.0f} VWAP={bar.vwap:.1f} {'▲' if bar.is_bull else '▼'}"
        )

        # Run the full analysis pipeline for primary and confirmation timeframes
        if bar.timeframe in (PRIMARY_TF, CONFIRM_TF, "1D"):
            state = self._inst_state.get(instrument)
            if state:
                state.on_bar(bar, self._capital_free)
                # Push scenario states to dashboard
                try:
                    if bar.timeframe == PRIMARY_TF:
                        self._dash_state.update_scenarios(
                            instrument,
                            state.scenarios.all_statuses(),
                        )
                except Exception:
                    pass

    # ── Tick handler ──────────────────────────────────────────────────────────

    def _on_tick(self, tick) -> None:
        self._tick_count += 1
        price = tick.last_price
        inst  = tick.instrument

        # Route to bar builder
        self.bar_builders.on_tick(
            instrument = inst,
            price      = price,
            volume     = getattr(tick, "volume", 0),
            ts         = tick.timestamp,
        )

        self.log.tick(f"{inst}={price:.2f}  #{self._tick_count}")

        # Dashboard tick state
        try:
            prev = self._prev_prices.get(inst, price)
            self._dash_state.update_tick(inst, price, prev)
            self._prev_prices[inst] = price
        except Exception:
            pass

        # Update open trade SL on every tick
        state = self._inst_state.get(inst)
        if state:
            state.on_tick(price, india_vix=self._india_vix, now=tick.timestamp)

    # ── Pre-market ───────────────────────────────────────────────────────────

    def _run_premarket(self) -> None:
        self.log.system("Pre-market intelligence fetch starting...")
        try:
            from src.brahmastra.data.fetchers.premarket_fetch import fetch_premarket_briefing
            self._briefing = fetch_premarket_briefing(self.sc)
            self._india_vix = self._briefing.india_vix or 15.0

            for state in self._inst_state.values():
                state._india_vix = self._india_vix
                state.confluence.set_india_vix(self._india_vix)

            sign = "+" if self._briefing.bias_score >= 0 else ""
            self.log.system(
                f"Pre-market | BIAS={sign}{self._briefing.bias_score} "
                f"({self._briefing.bias_label}) | VIX={self._india_vix:.1f} "
                f"| PCR={self._briefing.pcr}"
            )
            print("\n" + self._briefing.format_message())
            if self._notifier and self._notifier.any_enabled:
                self._notifier.send_premarket_briefing(self._briefing.format_message())
        except Exception as e:
            self.log.error(f"Pre-market fetch failed: {e}")
            self.log.system("Proceeding with NEUTRAL bias")

    # ── Backfill ─────────────────────────────────────────────────────────────

    def _run_backfill(self) -> None:
        self.log.system("Backfill: loading historical bars for indicator warmup...")
        tf_map   = {"5m": "5m", "15m": "15m", "1h": "1h", "1D": "1d"}
        days_map = {"5m": 5, "15m": 7, "1h": 30, "1D": 300}
        sym_map  = {"NIFTY": "^NSEI", "SENSEX": "^BSESN"}

        try:
            import yfinance as yf
            for inst in INSTRUMENTS:
                sym = sym_map.get(inst, inst)
                for tf in ["5m", "15m", "1h", "1D"]:
                    interval = tf_map[tf]
                    days     = days_map[tf]
                    try:
                        ticker = yf.Ticker(sym)
                        period = f"{days}d" if tf != "1D" else "1y"
                        hist   = ticker.history(period=period, interval=interval)
                        if hist.empty:
                            continue

                        from src.brahmastra.data.bar_builder import Bar as BBar
                        loaded: list[BBar] = []
                        for ts, row in hist.iterrows():
                            try:
                                if hasattr(ts, 'to_pydatetime'):
                                    ts_dt = ts.to_pydatetime()
                                else:
                                    ts_dt = datetime.fromisoformat(str(ts))
                                if ts_dt.tzinfo is None:
                                    ts_dt = ts_dt.replace(tzinfo=IST)
                                b = BBar(
                                    instrument = inst,
                                    timeframe  = tf,
                                    ts_open    = ts_dt,
                                    ts_close   = ts_dt,
                                    open       = float(row.get("Open",  row.get("Close", 0))),
                                    high       = float(row.get("High",  row.get("Close", 0))),
                                    low        = float(row.get("Low",   row.get("Close", 0))),
                                    close      = float(row.get("Close", 0)),
                                    volume     = float(row.get("Volume", 0)),
                                    vwap       = float(row.get("Close", 0)),
                                    tick_count = 1,
                                    complete   = True,
                                )
                                loaded.append(b)
                            except Exception:
                                pass

                        builder = self.bar_builders.get_builder(inst)
                        if builder and loaded:
                            builder.load_history(tf, loaded)
                            # Also pre-feed indicators with historical bars
                            state = self._inst_state.get(inst)
                            if state:
                                for b in loaded[-200:]:
                                    state.on_bar(b, self._capital_free)
                            self.log.data(
                                f"Backfill OK | {inst} {tf} | {len(loaded)} bars loaded"
                            )
                    except Exception as e:
                        self.log.data(f"Backfill skip | {inst} {tf}: {e}")
        except ImportError:
            self.log.data("yfinance not available — skipping backfill")
        except Exception as e:
            self.log.error(f"Backfill error: {e}")

    # ── Tick stream ───────────────────────────────────────────────────────────

    def _connect_stream(self) -> None:
        try:
            if self.mode == "live" and hasattr(self.broker, "kite"):
                from src.brahmastra.data.fetchers.kite_stream import KiteTickStream
                self._tick_stream = KiteTickStream(
                    kite_api    = self.broker.kite,
                    instruments = INSTRUMENTS,
                    on_tick     = self._on_tick,
                    on_connect  = lambda: self.log.system("Kite WebSocket connected"),
                    on_disconnect = lambda e: self.log.error(f"WebSocket closed: {e}"),
                )
                self._tick_stream.start()
                self.log.system("Kite WebSocket stream started")
                time.sleep(2)
            else:
                from src.brahmastra.data.fetchers.kite_stream import MockTickStream
                self._tick_stream = MockTickStream(
                    instruments  = INSTRUMENTS,
                    on_tick      = self._on_tick,
                    replay_speed = 1.0,
                )
                self._tick_stream.start()
                self.log.system("Mock tick stream started (paper/dev mode)")
        except Exception as e:
            self.log.error(f"Stream connection failed: {e}")

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._running = True
        today = date.today()

        self.log.system("═" * 62)
        self.log.system(
            f"BRAHMASTRA_v1 SESSION START | {today} | mode={self.mode.upper()}"
        )
        self.log.system("═" * 62)
        self.log.system(
            "Jai Shree Ganesh | Jai Shree Shyam | Om Namah Shivay | "
            "Jai Maa Shakambari | Jai Balaji Maharaj | Jai Balaji | "
            "Khama Baba Ramdev | Jai Shree Ram"
        )

        print(f"\n{'═'*62}")
        print(f"  BRAHMASTRA_v1  |  {today}  |  {self.mode.upper()}")
        print(f"  Phases 0–3 integrated  |  25 indicators active")
        print(f"{'═'*62}")

        # Step 1: Backfill
        print("\n  [1/4] Loading historical bars...")
        self._dash_state.update_session(phase="BACKFILL")
        self._run_backfill()
        self._log_warmup()

        # Step 2: Pre-market
        self._dash_state.update_session(phase="PRE_MARKET")
        now = datetime.now(IST)
        if now.hour < 9 or (now.hour == 9 and now.minute < 15):
            self._wait_until(8, 0)
            self._run_premarket()
            print("\n  Waiting for market open (9:15 AM IST)...")
            self._wait_until(9, 15)
        else:
            self._run_premarket()

        # Propagate pre-market BIAS to session stats
        if self._briefing:
            try:
                self._dash_state.update_session(
                    india_vix  = self._briefing.india_vix,
                    bias_score = self._briefing.bias_score,
                    bias_label = self._briefing.bias_label,
                )
            except Exception:
                pass

        # Step 3: Connect tick stream
        print("\n  [3/4] Connecting tick stream...")
        self._connect_stream()
        self._dash_state.update_session(phase="SCANNING")

        # Step 4: Main loop
        print("\n  [4/4] BRAHMASTRA scanning — watching all instruments...")
        print("  Log file shows full bar-by-bar analysis.")
        print("  Press Ctrl+C to stop.\n")
        self.log.system("Main scanning loop started")

        last_log   = 0
        last_risk  = 0

        # Track whether this run entered a market session (9:15–15:20 IST).
        # Prevents EOD from firing immediately when started outside market hours.
        _session_entered = False

        try:
            while self._running:
                now   = datetime.now(IST)
                now_m = now.hour * 60 + now.minute

                # Mark session entered once we're past 9:15 AM
                if now_m >= 9 * 60 + 15:
                    _session_entered = True

                # EOD forced exit — only fires if we actually entered the session
                if _session_entered and now_m >= 15 * 60 + 20:
                    self.log.system("3:20 PM — forcing exit all open positions")
                    for inst, state in self._inst_state.items():
                        try:
                            from src.data.market_data import get_spot_price
                            price = get_spot_price(inst) or state._last_close
                        except Exception:
                            price = state._last_close
                        state.force_eod_exit(price)
                    break

                # Outside market hours — keep API alive, log status every 5 min
                if now_m < 9 * 60 + 15 or now_m >= 15 * 60 + 30:
                    if time.monotonic() - last_log >= 300:
                        self.log.system(
                            f"Market closed | API alive at http://localhost:8000 | "
                            f"Next session: tomorrow 9:15 AM IST"
                        )
                        last_log = time.monotonic()
                    time.sleep(30)
                    continue

                mono = time.monotonic()

                # Scan state log every 60s
                if mono - last_log >= 60:
                    for inst, state in self._inst_state.items():
                        self._log_scan_state(inst, state)
                    last_log = mono

                # Risk heartbeat every 5 minutes
                if mono - last_risk >= 300:
                    self._log_risk_heartbeat()
                    last_risk = mono

                # Dashboard session stats update
                try:
                    pnl_now = sum(
                        s.trades.session_pnl
                        for s in self._inst_state.values()
                    )
                    total_t = sum(
                        s.trades.session_summary()["total_trades"]
                        for s in self._inst_state.values()
                    )
                    self._dash_state.update_session(
                        tick_count   = self._tick_count,
                        session_pnl  = pnl_now,
                        total_trades = total_t,
                        india_vix    = self._india_vix,
                    )
                except Exception:
                    pass

                time.sleep(10)

        except KeyboardInterrupt:
            self.log.system("Stopped by user (Ctrl+C)")
            print("\n  BRAHMASTRA stopped.")

        finally:
            self._running = False
            self._dash_state.update_session(phase="EOD")
            if self._tick_stream:
                self._tick_stream.stop()
            self._eod_report()

    def _log_warmup(self) -> None:
        status = self.bar_builders.warmup_status()
        for inst, tf_status in status.items():
            for tf, s in tf_status.items():
                if tf in (PRIMARY_TF, CONFIRM_TF, "1D"):
                    warm = "WARM" if s["warm"] else f"WARMING {s['pct']:.0f}%"
                    self.log.data(
                        f"Warmup | {inst} {tf} | {s['have']}/{s['need']} | {warm}"
                    )

    def _log_scan_state(self, instrument: str, state: _InstrumentState) -> None:
        now = datetime.now(IST)
        cf  = state.confluence.last_result
        conf_line = cf.summary_line() if cf else "confluence warming up"
        open_trades = state.trades.open_trades

        self.log.decision(
            f"[{now.strftime('%H:%M:%S')}] SCANNING | {instrument} | "
            f"{conf_line}"
        )
        for trade in open_trades:
            self.log.trade(trade.summary_line())
        self.log.decision(state.scenarios.display_block())

    def _log_risk_heartbeat(self) -> None:
        pnl_total = sum(s.trades.session_pnl for s in self._inst_state.values())
        self.log.risk(
            f"RISK HEARTBEAT | session_pnl=Rs.{pnl_total:+.0f} "
            f"| ticks={self._tick_count} "
            f"| VIX={self._india_vix or '?':.1f}"
            f"| mode={self.mode}"
        )

    def _wait_until(self, hour: int, minute: int) -> None:
        while self._running:
            now = datetime.now(IST)
            if now.hour > hour or (now.hour == hour and now.minute >= minute):
                return
            remaining = (hour * 60 + minute) - (now.hour * 60 + now.minute)
            self.log.system(
                f"Waiting for {hour:02d}:{minute:02d} IST — {remaining} min remaining"
            )
            time.sleep(60)

    def _eod_report(self) -> None:
        self.log.system("═" * 62)
        self.log.system(f"BRAHMASTRA_v1 SESSION END | {self._session_date}")

        all_summaries = {}
        for inst, state in self._inst_state.items():
            summary = state.trades.session_summary()
            all_summaries[inst] = summary
            self.log.trade(
                f"{inst} | trades={summary['total_trades']} "
                f"wins={summary['wins']} losses={summary['losses']} "
                f"win_rate={summary['win_rate']:.0f}% "
                f"session_pnl=Rs.{summary['session_pnl']:+.0f}"
            )
        self.log.system("═" * 62)

        pnl_total    = sum(s["session_pnl"]    for s in all_summaries.values())
        total_trades = sum(s["total_trades"]    for s in all_summaries.values())
        total_wins   = sum(s["wins"]            for s in all_summaries.values())
        total_losses = sum(s["losses"]          for s in all_summaries.values())
        win_rate     = round(total_wins / total_trades * 100, 1) if total_trades else 0
        best         = max((s["best_trade"]  for s in all_summaries.values()), default=0)
        worst        = min((s["worst_trade"] for s in all_summaries.values()), default=0)

        print(f"\n  {'═'*62}")
        print(f"  BRAHMASTRA_v1 EOD | {self._session_date}")
        print(f"  Trades: {total_trades}  W:{total_wins} L:{total_losses}  "
              f"Win Rate: {win_rate:.0f}%")
        print(f"  Session P&L: Rs.{pnl_total:+.0f}")
        print(f"  Total ticks: {self._tick_count}")
        print(f"  {'═'*62}\n")

        # EOD notification
        if self._notifier and self._notifier.any_enabled:
            try:
                self._notifier.send_eod_report(
                    date_str     = str(self._session_date),
                    total_trades = total_trades,
                    wins         = total_wins,
                    losses       = total_losses,
                    win_rate     = win_rate,
                    session_pnl  = pnl_total,
                    best_trade   = best,
                    worst_trade  = worst,
                    total_ticks  = self._tick_count,
                )
            except Exception:
                pass

        if self._notifier:
            self._notifier.stop()
