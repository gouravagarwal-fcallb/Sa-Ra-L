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
from collections import deque
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from src.brahmastra.logger import get_brahmastra_logger
from src.brahmastra.data.bar_builder import MultiInstrumentBarBuilder, Bar, TIMEFRAMES
from src.brahmastra.api.state import get_state, reset_state, IndicatorSnapshot
from src.brahmastra.live.narrator import MarketNarrator, NarratorInput
from src.brahmastra.live.human_gate import HumanGate, ExecutionMode

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
        self.lot_size = inst_cfg.get("lot_size", 65 if instrument == "NIFTY" else 10)

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

        # Auto mode-downgrade tracking
        self.consecutive_losses: int = 0

        # Rolling volume history for G8 (20-bar average)
        self._vol_history: deque = deque(maxlen=20)

        # Gate context injected by BrahmastraLive before each bar
        self._vix_spike_30min_pct: float = 0.0
        self._next_event_minutes:  int   = 999

        # Higher-TF indicators (1h and 1W)
        from src.brahmastra.indicators.ema import EMAStack
        self.ema_1h  = EMAStack([9, 21, 50])
        self.ema_1w  = EMAStack([9, 21])
        self._1h_bias: Optional[str] = None
        self._1w_bias: Optional[str] = None

        # Options Intelligence engine (Layer 5)
        try:
            from src.brahmastra.options.options_intel import OptionsIntelEngine
            self._options_engine = OptionsIntelEngine()
        except Exception:
            self._options_engine = None
        self._last_options_snap = None

        # Narrator (always active, mode-independent)
        _conf_threshold = config.get("strategy", {}).get("confluence_threshold", 70.0)
        self._narrator  = MarketNarrator(entry_threshold=_conf_threshold)

        # Human gate injected by BrahmastraLive after construction
        self._gate: Optional[HumanGate] = None

        # Track last narrator tier to avoid duplicate WATCH alerts
        self._last_alert_tier: str = "QUIET"

        # ── Phase 6d: Scout watchman (enabled via scout_mode.enabled in settings) ──
        self._scout = None
        if config.get("scout_mode", {}).get("enabled", False):
            try:
                from src.brahmastra.live.scout_watchman import ScoutWatchman
                self._scout = ScoutWatchman(instrument, self.lot_size, config)
                log.system(f"ScoutWatchman active for {instrument}")
            except Exception as e:
                log.system(f"ScoutWatchman init failed for {instrument}: {e}")

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
        if event_type in ("EXIT", "SL_HIT"):
            if trade.net_pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
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

    def _nearest_sr_pct(self, price: float) -> float:
        """Compute % distance from price to nearest known S/R level."""
        levels: list[float] = []

        # VWAP
        vw = self.vwap.value
        if vw:
            levels.append(vw.vwap)
            if vw.upper1: levels.append(vw.upper1)
            if vw.lower1: levels.append(vw.lower1)

        # Pivot points
        pv = self.pivots.standard
        if pv:
            for attr in ("pivot", "r1", "r2", "s1", "s2"):
                v = getattr(pv, attr, None)
                if v and v > 0:
                    levels.append(v)

        # Fibonacci levels
        fib = self.fibonacci.value
        if fib:
            for level_val in fib.levels.values():
                if level_val and level_val > 0:
                    levels.append(level_val)

        # Round numbers (NIFTY 50pt, SENSEX 100pt)
        step = 50 if self.instrument == "NIFTY" else 100
        levels.append(round(price / step) * step)

        if not levels or price <= 0:
            return 100.0

        dists = [abs(price - lvl) / price * 100 for lvl in levels if lvl > 0]
        return min(dists) if dists else 100.0

    def on_bar(self, bar: Bar, capital_free: float = 100000) -> None:
        """
        Feed a completed bar for this instrument.
        Runs all indicators → confluence → scenarios → (trade if CONFIRMED).
        """
        if bar.timeframe not in (PRIMARY_TF, CONFIRM_TF, "1D", "1h", "1W"):
            return

        # ── 1D bar: update pivot points ──────────────────────────────────────
        if bar.timeframe == "1D":
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

        # ── 1h bar: update higher-TF EMA, compute 1h bias ────────────────────
        if bar.timeframe == "1h":
            self.ema_1h.update(bar)
            bias = self.ema_1h.trend_structure()
            if bias:
                self._1h_bias = bias
                self.confluence.set_higher_tf_bias("1h", bias)
            return

        # ── 1W bar: update weekly EMA for macro trend ─────────────────────────
        if bar.timeframe == "1W":
            self.ema_1w.update(bar)
            bias = self.ema_1w.trend_structure()
            if bias:
                self._1w_bias = bias
                self.confluence.set_higher_tf_bias("1W", bias)
            return

        # ── CONFIRM_TF (15m): pass to scenario engine only ───────────────────
        if bar.timeframe == CONFIRM_TF:
            # Scenarios can use 15m bars for confirmation — pass close only
            return

        # ── Volume history for G8 gate ───────────────────────────────────────
        self._vol_history.append(bar.volume)
        volume_20_avg = (sum(self._vol_history) / len(self._vol_history)
                         if self._vol_history else 0.0)

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

        # Compute derived indicator values used in multiple places below
        ema_struct = self.ema_stack.trend_structure()
        vwap_v     = self.vwap.value
        macd_v     = self.macd.value
        adx_v      = self.adx.value
        rsi_v      = self.rsi.value

        # Update confluence scorer with VIX
        self.confluence.set_india_vix(self._india_vix or 15.0)

        # ── ANALYSE log line for each key indicator ───────────────────────────
        self._log_indicator_state(bar, atr_val, obv_val, roc_val, st_result, patterns)

        # ── Run confluence score ──────────────────────────────────────────────
        conf_result = self.confluence.score(bar)
        self._log.score(
            f"{self.instrument} {PRIMARY_TF} | {conf_result.summary_line()}"
        )

        # ── Narrator: forward-looking commentary every bar ────────────────────
        narrator_update = self._narrator.update(NarratorInput(
            timestamp      = bar.ts_close,
            instrument     = self.instrument,
            price          = bar.close,
            score          = conf_result.score,
            threshold      = self._narrator._threshold,
            atr            = atr_val,
            vwap           = vwap_v.vwap     if vwap_v else None,
            vwap_position  = vwap_v.position if vwap_v else None,
            rsi            = rsi_v           if rsi_v else None,
            macd_hist      = macd_v.histogram if macd_v else None,
            adx            = adx_v.adx        if adx_v else None,
            supertrend_dir = st_result.direction if st_result else None,
            ema_structure  = ema_struct,
            india_vix      = self._india_vix,
        ))
        # Always log the headline; log full detail only on ALERT+ tiers
        self._log.analyse(f"NARRATOR: {narrator_update.headline}")
        if narrator_update.alert_tier in ("ALERT", "SIGNAL"):
            self._log.alert(narrator_update.detail)

        # Push to dashboard
        try:
            get_state().update_narrator(self.instrument, narrator_update)
        except Exception:
            pass

        # Fire WATCH alert when tier escalates (not on every bar to avoid spam)
        if (narrator_update.alert_tier in ("WATCH", "ALERT")
                and self._last_alert_tier == "QUIET"
                and self._notifier):
            try:
                self._notifier.send_setup_building(
                    instrument    = self.instrument,
                    hypothesis    = "BULL" if conf_result.score >= 0 else "BEAR",
                    score         = abs(conf_result.score),
                    threshold     = self._narrator._threshold,
                    bars_to_entry = narrator_update.bars_to_entry,
                    headline      = narrator_update.headline,
                )
            except Exception:
                pass
        self._last_alert_tier = narrator_update.alert_tier

        # ── Update scenarios ──────────────────────────────────────────────────
        obv_rising  = obv_val > self._prev_obv if obv_val != 0 else None
        self._prev_obv = obv_val

        strongest_pattern = self.candles.strongest()
        pattern_dir  = strongest_pattern.direction if strongest_pattern else None
        pattern_conf = strongest_pattern.confidence if strongest_pattern else 0.0

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

        # ── Compute gate data for this bar ──────────────────────────────────
        sr_dist_pct = self._nearest_sr_pct(bar.close)

        # ── Options intelligence update (every 5 min cadence via engine) ────
        if self._options_engine and self._last_close > 0:
            try:
                snap = self._options_engine.get_snapshot(
                    symbol=self.instrument,
                    spot=self._last_close,
                )
                if snap and not snap.error:
                    self._last_options_snap = snap
                    self.confluence.set_options_intel(snap)
                    # Push options data to dashboard indicator snapshot
                    try:
                        from src.brahmastra.api.state import get_state
                        get_state().update_options(self.instrument, snap)
                    except Exception:
                        pass
            except Exception:
                pass

        # ── Trigger trade engine for CONFIRMED scenarios ──────────────────────
        for scenario in self.scenarios.confirmed_scenarios():
            if scenario.state.value != "CONFIRMED":
                continue

            # Compute ATM strike
            strike  = self._get_atm_strike(bar.close)
            expiry  = self._get_expiry()
            if not strike or not expiry:
                continue

            # Estimate SL and targets for gate check (trade engine refines these)
            _atr       = atr_val or 50.0
            _hyp       = scenario.hypothesis
            _sl        = (bar.close - _atr * 2 if _hyp == "BULL"
                          else bar.close + _atr * 2)
            _t1        = scenario.target1 or (bar.close + _atr * 4 if _hyp == "BULL"
                                              else bar.close - _atr * 4)
            _t2        = scenario.target2 or (bar.close + _atr * 6 if _hyp == "BULL"
                                              else bar.close - _atr * 6)
            _t3        = scenario.target3 or (bar.close + _atr * 8 if _hyp == "BULL"
                                              else bar.close - _atr * 8)

            # Human gate: AUTO → execute; HUMAN_WATCH → queue + alert
            if self._gate and not self._gate.check_signal(
                instrument  = self.instrument,
                hypothesis  = _hyp,
                entry_price = bar.close,
                sl_price    = _sl,
                target1     = _t1,
                target2     = _t2,
                target3     = _t3,
                confidence  = scenario.confidence,
                lots        = 1,
            ):
                pending = self._gate.pending(self.instrument)
                self._log.alert(
                    f"HUMAN_WATCH | {self.instrument} {_hyp} signal PENDING "
                    f"conf={scenario.confidence:.0f}%  "
                    f"entry≈{bar.close:.0f}  SL≈{_sl:.0f}  T1≈{_t1:.0f}  "
                    f"expires in {pending.minutes_left if pending else '?'}m  "
                    f"→ approve: POST /api/approve/{self.instrument}"
                )
                if self._notifier and pending:
                    try:
                        self._notifier.send_signal_pending(pending.telegram_text())
                    except Exception:
                        pass
                continue   # do NOT execute — human must approve

            self.trades.on_scenario_confirmed(
                scenario            = scenario,
                current_price       = bar.close,
                atr_value           = _atr,
                india_vix           = self._india_vix,
                capital_free        = capital_free,
                strike              = strike,
                expiry              = expiry,
                now                 = bar.ts_close,
                volume_current      = bar.volume,
                volume_20_avg       = volume_20_avg,
                sr_nearest_dist_pct = sr_dist_pct,
                vix_spike_30min_pct = self._vix_spike_30min_pct,
                next_event_minutes  = self._next_event_minutes,
            )

        # ── Phase 6d: Scout watchman ──────────────────────────────────────────
        if self._scout:
            ema_vals     = self.ema_stack.values()
            scout_result = self._scout.on_bar(
                bar_close  = bar.close,
                bar_high   = bar.high,
                bar_low    = bar.low,
                bar_dt     = bar.ts_close,
                adx        = adx_v.adx        if adx_v  else None,
                vwap       = vwap_v.vwap       if vwap_v else None,
                ema9       = ema_vals.get(9),
                ema21      = ema_vals.get(21),
                rsi        = rsi_v,
                macd_hist  = macd_v.histogram  if macd_v else None,
                atr        = atr_val,
                obv        = obv_val,
                roc        = roc_val,
            )
            if scout_result.exit_price is not None:
                self._log.trade(
                    f"SCOUT EXIT | {self.instrument} {scout_result.direction} | "
                    f"pnl=Rs.{scout_result.pnl:+.0f} | reason={scout_result.exit_reason}"
                )
            elif scout_result.entry_price is not None:
                self._log.trade(
                    f"SCOUT ENTRY | {self.instrument} {scout_result.direction} | "
                    f"@ Rs.{scout_result.entry_price:.0f} | "
                    f"momentum={scout_result.momentum_score:+.1f}"
                )
            elif scout_result.alert_reason:
                self._log.alert(
                    f"SCOUT → ALERT | {self.instrument} | {scout_result.alert_reason}"
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

        # Push full indicator snapshot to dashboard state
        try:
            ema_vals    = self.ema_stack.values()
            rsi_v       = self.rsi.value
            macd_v      = self.macd.value
            bb_v        = self.bb.value
            vwap_v      = self.vwap.value
            adx_v       = self.adx.value
            ichi_v      = self.ichimoku.value
            stoch_v     = self.stoch_rsi.value
            conf_r      = self.confluence.last_result
            strongest   = self.candles.strongest()
            obv_rising  = (obv_val > self._prev_obv) if obv_val != 0 else None
            snap = IndicatorSnapshot(
                instrument        = self.instrument,
                timeframe         = PRIMARY_TF,
                timestamp         = bar.ts_close.strftime("%H:%M:%S"),
                # raw values
                ema9              = ema_vals.get(9),
                ema21             = ema_vals.get(21),
                ema50             = ema_vals.get(50),
                ema200            = ema_vals.get(200),
                rsi               = rsi_v,
                macd              = macd_v.macd      if macd_v else None,
                macd_hist         = macd_v.histogram  if macd_v else None,
                bb_upper          = bb_v.upper        if bb_v else None,
                bb_lower          = bb_v.lower        if bb_v else None,
                bb_pct_b          = bb_v.pct_b        if bb_v else None,
                vwap              = vwap_v.vwap        if vwap_v else None,
                atr               = atr_val,
                adx               = adx_v.adx         if adx_v else None,
                supertrend_dir    = st_result.direction if st_result else None,
                ichimoku_bias     = ichi_v.bias()      if ichi_v else None,
                confluence_score  = conf_r.score       if conf_r else None,
                confluence_dir    = conf_r.direction   if conf_r else None,
                # derived signals
                ema_structure     = ema_struct,
                vwap_position     = vwap_v.position    if vwap_v else None,
                macd_cross        = macd_v.crossover   if macd_v else None,
                macd_zero_cross   = macd_v.zero_cross  if macd_v else None,
                adx_trend         = adx_v.trend        if adx_v else None,
                adx_plus_di       = adx_v.plus_di      if adx_v else None,
                adx_minus_di      = adx_v.minus_di     if adx_v else None,
                bb_squeeze        = bb_v.squeeze       if bb_v else None,
                bb_breakout       = bb_v.breakout      if bb_v else None,
                obv_rising        = obv_rising,
                roc               = roc_val,
                pattern_name      = strongest.name      if strongest else None,
                pattern_dir       = strongest.direction  if strongest else None,
                pattern_conf      = strongest.confidence if strongest else None,
                supertrend_flipped = st_result.flipped   if st_result else None,
                supertrend_value  = st_result.value      if st_result else None,
                tk_cross          = ichi_v.tk_cross      if ichi_v else None,
                price_vs_cloud    = ichi_v.price_vs_cloud if ichi_v else None,
                ichimoku_strength = ichi_v.strength      if ichi_v else None,
                stoch_rsi_k       = stoch_v.k            if stoch_v else None,
                stoch_rsi_d       = stoch_v.d            if stoch_v else None,
                stoch_rsi_signal  = stoch_v.signal       if stoch_v else None,
                confluence_strength   = conf_r.strength    if conf_r else None,
                confluence_agreement  = conf_r.agreement   if conf_r else None,
                ema_1h_bias       = getattr(self, "_1h_bias", None),
                ema_1w_bias       = getattr(self, "_1w_bias", None),
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
        if self._scout:
            pnl = self._scout.force_eod_close(price, datetime.now(IST))
            if pnl is not None:
                self._log.trade(
                    f"SCOUT EOD CLOSE | {self.instrument} | pnl=Rs.{pnl:+.0f}"
                )


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
            if state._scout is not None:
                state._scout._notifier = self._notifier

        # Human gate — controls execution mode; analysis always runs
        # Check both "execution" (BRAHMASTRA config.yaml) and "strategy" (settings.yaml)
        _exec_cfg  = strategy_config.get("execution") or strategy_config.get("strategy") or {}
        _exec_mode = _exec_cfg.get("execution_mode", "auto")
        self._human_gate = HumanGate(
            mode = (ExecutionMode.HUMAN_WATCH
                    if _exec_mode == "human_watch"
                    else ExecutionMode.AUTO),
            signal_timeout_minutes = _exec_cfg.get("signal_timeout_minutes", 10),
        )
        # Inject gate into each instrument state
        for state in self._inst_state.values():
            state._gate = self._human_gate

        # Dashboard state wiring.
        # IMPORTANT: use get_state() (the registered singleton), NOT reset_state().
        # The unified runner registers get_state() into MultiStrategyState BEFORE
        # constructing this engine; reset_state() would rebind the module global and
        # orphan that registration, making live trades/P&L invisible to the dashboard.
        self._dash_state = get_state()
        tg_enabled = bool(
            self._notifier and
            getattr(self._notifier, "_telegram", None) and
            getattr(self._notifier._telegram, "enabled", False)
        )
        self._dash_state.update_session(
            mode=mode, date=str(self._session_date), phase="INIT",
            notifications={"telegram": tg_enabled},
            execution_mode=_exec_mode,
        )
        self.log.set_structured_callback(
            lambda cat, msg: self._dash_state.add_log(cat, msg)
        )

        # Track prev prices for change_pct in tick state
        self._prev_prices: dict[str, float] = {}

        # Auto mode-downgrade state
        self._vix_1h_ago:         Optional[float] = None
        self._vix_1h_timestamp:   Optional[float] = None
        self._mode_paused_until:  Optional[float] = None
        self._daily_loss_atr_ref: Optional[float] = None

        # VIX 30-min spike tracking (for G11 entry gate)
        self._vix_30min_ago:        Optional[float] = None
        self._vix_30min_ts:         Optional[float] = None
        self._vix_spike_30min_pct:  float = 0.0

        # Economic calendar (G12 gate)
        self._calendar_events: list = []
        try:
            from src.brahmastra.data.fetchers.economic_calendar import load_calendar
            self._calendar_events = load_calendar()
            self.log.system(
                f"Economic calendar loaded — {len(self._calendar_events)} events"
            )
        except Exception as e:
            self.log.system(f"Economic calendar not loaded: {e}")

        self.log.system("BRAHMASTRA_v1 fully initialised — Phase 0–5 integrated (13-gate entry)")

    # ── Bar complete callback ─────────────────────────────────────────────────

    def _on_bar_complete(self, instrument: str, bar: Bar) -> None:
        """Called for every completed bar on every timeframe for every instrument."""
        self.log.bar(
            f"{bar.timeframe} | {instrument} | "
            f"O={bar.open:.1f} H={bar.high:.1f} L={bar.low:.1f} C={bar.close:.1f} "
            f"V={bar.volume:,.0f} VWAP={bar.vwap:.1f} {'▲' if bar.is_bull else '▼'}"
        )

        state = self._inst_state.get(instrument)
        if not state:
            return

        # Inject current gate context into instrument state before processing
        state._vix_spike_30min_pct = self._vix_spike_30min_pct
        state._next_event_minutes  = self._get_next_event_minutes()

        # Run the full analysis pipeline
        if bar.timeframe in (PRIMARY_TF, CONFIRM_TF, "1D", "1h", "1W"):
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
        # Cooperative stop: once a dashboard Stop is requested, drop ticks so no
        # downstream bar/scenario callback can place a NEW real order during shutdown.
        if getattr(self, "_stop_event", None) is not None and self._stop_event.is_set():
            return
        if not self._running:
            return
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
        tf_map   = {"5m": "5m", "15m": "15m", "1h": "1h", "1D": "1d", "1W": "1wk"}
        days_map = {"5m": 5, "15m": 7, "1h": 30, "1D": 300, "1W": 1000}
        sym_map  = {"NIFTY": "^NSEI", "SENSEX": "^BSESN"}

        try:
            import yfinance as yf
            for inst in INSTRUMENTS:
                sym = sym_map.get(inst, inst)
                for tf in ["5m", "15m", "1h", "1D", "1W"]:
                    interval = tf_map[tf]
                    days     = days_map[tf]
                    try:
                        ticker = yf.Ticker(sym)
                        period = (f"{days}d" if tf not in ("1D", "1W")
                                 else "3y" if tf == "1W" else "1y")
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

        # Step 2: Pre-market — run immediately after backfill (no 8 AM wait)
        self._dash_state.update_session(phase="PRE_MARKET")
        self._run_premarket()

        # Wait for market open if needed
        now = datetime.now(IST)
        if now.hour < 9 or (now.hour == 9 and now.minute < 15):
            print("\n  Waiting for market open (9:15 AM IST)...")
            self._wait_until(9, 15)

        # Propagate full pre-market briefing to dashboard state
        if self._briefing:
            try:
                global_snaps = {
                    k: {
                        "name":       v.name,
                        "change_pct": v.change_pct,
                        "direction":  v.direction,
                        "price":      v.price,
                        "error":      v.error,
                    }
                    for k, v in self._briefing.snapshots.items()
                }
                self._dash_state.update_session(
                    india_vix        = self._briefing.india_vix,
                    bias_score       = self._briefing.bias_score,
                    bias_label       = self._briefing.bias_label,
                    global_snapshots = global_snaps,
                    news             = getattr(self._briefing, "news", []),
                    score_breakdown  = self._briefing.score_breakdown,
                    pcr              = self._briefing.pcr,
                    max_pain         = self._briefing.max_pain,
                    fii_net_cr       = self._briefing.fii_net_cr,
                    vix_trend        = self._briefing.vix_trend,
                    high_risk_events = self._briefing.high_risk_events,
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

        # Market session time constants (in minutes since midnight IST)
        _MARKET_OPEN  = 9 * 60 + 15   # 9:15 AM
        _EOD_FORCE    = 15 * 60 + 20  # 3:20 PM  — force exit
        _EOD_END      = 16 * 60        # 4:00 PM  — EOD window closes

        try:
            while self._running:
                # Cooperative stop — honor a dashboard Stop / STOP ALL immediately.
                # Every other engine checks this; BRAHMASTRA must too, or it keeps
                # placing REAL orders until EOD after the operator hit Stop.
                if getattr(self, "_stop_event", None) is not None and self._stop_event.is_set():
                    self.log.system("Stop requested via dashboard — exiting and squaring off.")
                    if self._tick_stream:          # stop ticks NOW so no callback places a new order
                        try: self._tick_stream.stop()
                        except Exception: pass
                    break

                now   = datetime.now(IST)
                now_m = now.hour * 60 + now.minute

                # EOD forced exit — only during the 3:20–4:00 PM window
                # (avoids firing at 11 PM or midnight when now_m > 3:20 PM)
                if _EOD_FORCE <= now_m <= _EOD_END:
                    self.log.system("3:20 PM — forcing exit all open positions")
                    for inst, state in self._inst_state.items():
                        try:
                            from src.data.market_data import get_spot_price
                            price = get_spot_price(inst) or state._last_close
                        except Exception:
                            price = state._last_close
                        state.force_eod_exit(price)
                    break

                # Outside market hours (before 9:15 AM or after 4:00 PM)
                # Keep API server alive; engine idles
                if now_m < _MARKET_OPEN or now_m > _EOD_END:
                    if time.monotonic() - last_log >= 300:
                        self.log.system(
                            f"Market closed | dashboard alive → http://localhost:8000 | "
                            f"Next session: 9:15 AM IST"
                        )
                        last_log = time.monotonic()
                    time.sleep(30)
                    continue

                mono = time.monotonic()

                # ── Sync execution mode from dashboard ───────────────────────
                self._sync_execution_mode()

                # ── Expire stale pending signals ──────────────────────────────
                expired = self._human_gate.expire_old_signals()
                for inst in expired:
                    self.log.risk(
                        f"HUMAN_WATCH: {inst} signal expired — no human approval received"
                    )

                # ── Push pending signal state to dashboard ────────────────────
                try:
                    self._dash_state.update_pending_signals(
                        self._human_gate.status_dict()["pending_signals"]
                    )
                except Exception:
                    pass

                # ── Gate context updates (VIX spike, econ calendar) ───────────
                self._update_vix_spike(mono)

                # ── Auto mode-downgrade checks (every loop iteration) ─────────
                self._check_auto_mode_downgrade(mono)

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

                # Event-aware wait: wakes within ~1s of a dashboard Stop instead of
                # sleeping a full 10s (the heavy work above is throttled by last_log/
                # last_risk, so a 1s cadence is cheap).
                _ev = getattr(self, "_stop_event", None)
                if _ev is not None:
                    if _ev.wait(1.0):
                        continue          # loop-top guard handles the clean exit
                else:
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

    def _get_next_event_minutes(self) -> int:
        """Return minutes until next high-impact economic event (999 if none soon)."""
        try:
            from src.brahmastra.data.fetchers.economic_calendar import minutes_to_next_event
            return minutes_to_next_event(datetime.now(IST), self._calendar_events)
        except Exception:
            return 999

    def _update_vix_spike(self, mono: float) -> None:
        """Track VIX change over last 30 minutes for G11 entry gate."""
        if self._india_vix is None:
            return
        if self._vix_30min_ts is None or (mono - self._vix_30min_ts) >= 1800:
            self._vix_30min_ago = self._india_vix
            self._vix_30min_ts  = mono
            self._vix_spike_30min_pct = 0.0
        elif self._vix_30min_ago and self._vix_30min_ago > 0:
            self._vix_spike_30min_pct = (
                (self._india_vix - self._vix_30min_ago) / self._vix_30min_ago * 100
            )

    def _check_auto_mode_downgrade(self, mono: float) -> None:
        """
        Evaluate auto mode-downgrade rules each main-loop iteration.

        Rule 1: VIX spike > 10% in last 1 hour while mode == 'full_auto'
                → downgrade to 'armed_confirm'
        Rule 2: consecutive_losses >= 3 while mode == 'full_auto'
                → pause 30 min, then resume as 'armed_confirm'
        Rule 3: daily_loss > 2 × ATR × lot_size while mode == 'full_auto'
                → downgrade to 'armed_confirm' for rest of day
        """
        if self.mode != "full_auto":
            # Un-pause if a 30-min pause has elapsed
            if self._mode_paused_until and mono >= self._mode_paused_until:
                self._mode_paused_until = None
                self.mode = "armed_confirm"
                self.log.risk("AUTO MODE: 30-min pause complete — resuming as armed_confirm")
            return

        # Track VIX history for Rule 1
        if self._india_vix is not None:
            if self._vix_1h_ago is None or (mono - (self._vix_1h_timestamp or 0)) >= 3600:
                self._vix_1h_ago       = self._india_vix
                self._vix_1h_timestamp = mono
            elif self._vix_1h_ago and self._vix_1h_ago > 0:
                vix_change_pct = (self._india_vix - self._vix_1h_ago) / self._vix_1h_ago * 100
                if vix_change_pct > 10:
                    self.mode = "armed_confirm"
                    self.log.risk(
                        f"AUTO MODE-DOWNGRADE: VIX spike {vix_change_pct:+.1f}% in 1h "
                        f"({self._vix_1h_ago:.1f} → {self._india_vix:.1f}) "
                        f"— switching to armed_confirm"
                    )
                    return

        # Rule 2: consecutive losses
        total_consec = sum(s.consecutive_losses for s in self._inst_state.values())
        if total_consec >= 3:
            self.mode = "armed_confirm"
            self._mode_paused_until = mono + 1800
            for state in self._inst_state.values():
                state.consecutive_losses = 0
            self.log.risk(
                f"AUTO MODE-DOWNGRADE: {total_consec} consecutive losses "
                f"— pausing 30 min, then armed_confirm"
            )
            return

        # Rule 3: daily loss > 2 × ATR × lot_size
        daily_pnl = sum(s.trades.session_pnl for s in self._inst_state.values())
        if daily_pnl < 0:
            for inst, state in self._inst_state.items():
                atr_val  = state._last_atr
                lot_size = state.lot_size
                if atr_val and lot_size and abs(daily_pnl) > 2 * atr_val * lot_size:
                    self.mode = "armed_confirm"
                    self.log.risk(
                        f"AUTO MODE-DOWNGRADE: daily_loss=Rs.{daily_pnl:.0f} "
                        f"> 2×ATR({atr_val:.0f})×lot({lot_size}) "
                        f"— switching to armed_confirm for rest of day"
                    )
                    return

    def _sync_execution_mode(self) -> None:
        """Sync execution mode from dashboard (POST /api/mode) → HumanGate."""
        try:
            dash_mode = self._dash_state.session.execution_mode
            gate_mode = self._human_gate.mode.value
            if dash_mode != gate_mode:
                new_mode = (ExecutionMode.HUMAN_WATCH
                            if dash_mode == "human_watch"
                            else ExecutionMode.AUTO)
                self._human_gate.set_mode(new_mode)
                self.log.system(
                    f"Execution mode changed: {gate_mode.upper()} → {dash_mode.upper()}"
                )
                # Keep session stats in sync
                self._dash_state.update_session(execution_mode=dash_mode)
        except Exception:
            pass

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
