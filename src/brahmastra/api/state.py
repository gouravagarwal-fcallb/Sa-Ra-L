"""
Shared in-memory state between the live engine and the API layer.
The live engine writes here on every tick/bar/trade.
The API reads from here for HTTP + WebSocket responses.
Thread-safe: all writes use a lock.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class TickState:
    instrument:  str
    price:       float
    change_pct:  float
    timestamp:   str


@dataclass
class ScenarioState:
    id:          int
    hypothesis:  str
    state:       str
    confidence:  float
    peak_conf:   float
    sl:          Optional[float]
    target1:     Optional[float]
    target2:     Optional[float]
    target3:     Optional[float]
    reason:      str
    eta:         Optional[str]
    bars_above_85: int


@dataclass
class TradeState:
    trade_id:    str
    instrument:  str
    hypothesis:  str
    strike:      int
    option_type: str
    entry_price: float
    current_price: float
    sl:          Optional[float]
    target1:     Optional[float]
    unrealised_pnl: float
    realised_pnl:   float
    state:       str
    lots:        int
    quantity:    int
    opened_at:   str


@dataclass
class IndicatorSnapshot:
    instrument:  str
    timeframe:   str
    timestamp:   str
    # ── Raw values ───────────────────────────────────────────────────────────
    ema9:        Optional[float]
    ema21:       Optional[float]
    ema50:       Optional[float]
    ema200:      Optional[float]
    rsi:         Optional[float]
    macd:        Optional[float]
    macd_hist:   Optional[float]
    bb_upper:    Optional[float]
    bb_lower:    Optional[float]
    bb_pct_b:    Optional[float]
    vwap:        Optional[float]
    atr:         Optional[float]
    adx:         Optional[float]
    supertrend_dir: Optional[str]
    ichimoku_bias:  Optional[str]
    confluence_score: Optional[float]
    confluence_dir:   Optional[str]
    # ── Derived signals (needed by dashboard panels) ─────────────────────────
    ema_structure:     Optional[str]   = None  # BULL_ALIGNED | BEAR_ALIGNED | MIXED
    vwap_position:     Optional[str]   = None  # ABOVE | BELOW | AT
    macd_cross:        Optional[str]   = None  # BULLISH | BEARISH
    macd_zero_cross:   Optional[str]   = None  # UP | DOWN
    adx_trend:         Optional[str]   = None  # STRONG_BULL | BULL | STRONG_BEAR | BEAR | SIDEWAYS
    adx_plus_di:       Optional[float] = None
    adx_minus_di:      Optional[float] = None
    bb_squeeze:        Optional[bool]  = None
    bb_breakout:       Optional[str]   = None  # UP | DOWN
    obv_rising:        Optional[bool]  = None
    roc:               Optional[float] = None
    pattern_name:      Optional[str]   = None
    pattern_dir:       Optional[str]   = None
    pattern_conf:      Optional[float] = None
    supertrend_flipped: Optional[bool] = None
    supertrend_value:  Optional[float] = None
    tk_cross:          Optional[str]   = None  # BULLISH | BEARISH
    price_vs_cloud:    Optional[str]   = None  # ABOVE | BELOW | INSIDE
    ichimoku_strength: Optional[int]   = None  # 0-6
    stoch_rsi_k:       Optional[float] = None
    stoch_rsi_d:       Optional[float] = None
    stoch_rsi_signal:  Optional[str]   = None  # BULLISH_CROSS | BEARISH_CROSS
    confluence_strength:   Optional[str]   = None  # STRONG | MODERATE | WEAK
    confluence_agreement:  Optional[float] = None  # 0.0–1.0
    ema_1h_bias:       Optional[str]   = None  # higher-TF bias
    ema_1w_bias:       Optional[str]   = None


@dataclass
class NarratorEntry:
    timestamp:     str
    instrument:    str
    score:         float
    score_dir:     str
    bars_to_entry: Optional[int]
    alert_tier:    str
    headline:      str


@dataclass
class SessionStats:
    date:             str
    mode:             str
    execution_mode:   str           # "auto" or "human_watch"
    total_trades:     int
    wins:             int
    losses:           int
    win_rate:         float
    session_pnl:      float
    tick_count:       int
    india_vix:        Optional[float]
    bias_score:       Optional[float]
    bias_label:       Optional[str]
    started_at:       str
    phase:            str
    global_snapshots: dict          = field(default_factory=dict)
    news:             list          = field(default_factory=list)
    notifications:    dict          = field(default_factory=dict)
    score_breakdown:  dict          = field(default_factory=dict)
    pcr:              Optional[float] = None
    max_pain:         Optional[int]   = None
    fii_net_cr:       Optional[float] = None
    vix_trend:        Optional[str]   = None
    high_risk_events: list          = field(default_factory=list)


class BrahmastraState:
    """
    Central state store for the dashboard.
    Written by the live engine; read by the API.
    """

    def __init__(self, max_log_lines: int = 500):
        self._lock    = threading.RLock()
        self._max_log = max_log_lines

        # Live ticks
        self.ticks:   dict[str, TickState] = {}

        # Scenarios per instrument
        self.scenarios: dict[str, list[ScenarioState]] = {}

        # Open trades
        self.open_trades: dict[str, TradeState] = {}

        # Closed trades (last 50)
        self.closed_trades: deque[dict] = deque(maxlen=50)

        # Latest indicator snapshot per instrument
        self.indicators: dict[str, IndicatorSnapshot] = {}

        # Options intelligence data per instrument (Layer 5)
        self.options_data: dict[str, dict] = {}

        # Narrator feed — last 20 entries per instrument
        self.narrator: dict[str, deque] = {}

        # Pending signals (HUMAN_WATCH mode)
        self.pending_signals: dict[str, dict] = {}

        # Session stats
        self.session: SessionStats = SessionStats(
            date="", mode="", execution_mode="auto",
            total_trades=0, wins=0, losses=0,
            win_rate=0.0, session_pnl=0.0, tick_count=0,
            india_vix=None, bias_score=None, bias_label=None,
            started_at=datetime.now(IST).isoformat(),
            phase="INIT",
            global_snapshots={}, news=[], notifications={},
            score_breakdown={}, pcr=None, max_pain=None,
            fii_net_cr=None, vix_trend=None, high_risk_events=[],
        )

        # Log lines (last N)
        self.log_lines:  deque[dict] = deque(maxlen=max_log_lines)

        # Pending WebSocket broadcast queue
        self._ws_queue: deque[dict] = deque(maxlen=1000)

    # ── Write methods (called by live engine) ────────────────────────────────

    def update_tick(self, instrument: str, price: float,
                    prev_price: float = 0) -> None:
        with self._lock:
            change_pct = ((price - prev_price) / prev_price * 100
                          if prev_price else 0.0)
            self.ticks[instrument] = TickState(
                instrument = instrument,
                price      = price,
                change_pct = round(change_pct, 3),
                timestamp  = datetime.now(IST).strftime("%H:%M:%S"),
            )
            self._push_ws({"type": "tick",
                           "instrument": instrument,
                           "price": price,
                           "change_pct": round(change_pct, 3)})

    def update_scenarios(self, instrument: str, statuses: list) -> None:
        with self._lock:
            snap = []
            for s in statuses:
                snap.append(ScenarioState(
                    id           = s.scenario_id,
                    hypothesis   = s.hypothesis,
                    state        = s.state.value if hasattr(s.state, "value") else str(s.state),
                    confidence   = s.confidence,
                    peak_conf    = s.peak_conf,
                    sl           = s.sl_price,
                    target1      = s.target1,
                    target2      = s.target2,
                    target3      = s.target3,
                    reason       = s.reason,
                    eta          = s.eta_to_entry(),
                    bars_above_85 = s.bars_above_85,
                ))
            self.scenarios[instrument] = snap
            self._push_ws({"type": "scenarios",
                           "instrument": instrument,
                           "data": [self._scenario_dict(s) for s in snap]})

    def update_trade(self, trade) -> None:
        with self._lock:
            ts = TradeState(
                trade_id      = trade.trade_id,
                instrument    = trade.instrument,
                hypothesis    = trade.hypothesis,
                strike        = trade.strike,
                option_type   = trade.option_type,
                entry_price   = trade.entry_price or 0,
                current_price = trade.current_price or trade.entry_price or 0,
                sl            = trade.trailing_sl or trade.sl_price,
                target1       = trade.target1,
                unrealised_pnl = trade.unrealised_pnl,
                realised_pnl   = trade.realised_pnl,
                state         = trade.state.value if hasattr(trade.state, "value") else str(trade.state),
                lots          = trade.lots,
                quantity      = trade.quantity,
                opened_at     = trade.opened_at.strftime("%H:%M:%S") if trade.opened_at else "",
            )
            if trade.is_open:
                self.open_trades[trade.trade_id] = ts
            else:
                self.open_trades.pop(trade.trade_id, None)
                self.closed_trades.append(self._trade_dict(ts))
            self._push_ws({"type": "trade",
                           "data": self._trade_dict(ts)})

    def update_narrator(self, instrument: str, update) -> None:
        """Push a NarratorUpdate (from live.narrator) to the feed."""
        with self._lock:
            inst = instrument.upper()
            if inst not in self.narrator:
                self.narrator[inst] = deque(maxlen=20)
            entry = NarratorEntry(
                timestamp     = update.timestamp,
                instrument    = inst,
                score         = update.score,
                score_dir     = update.score_dir,
                bars_to_entry = update.bars_to_entry,
                alert_tier    = update.alert_tier,
                headline      = update.headline,
            )
            self.narrator[inst].append(entry)
            self._push_ws({
                "type":       "narrator",
                "instrument": inst,
                "data": {
                    "timestamp":     entry.timestamp,
                    "score":         entry.score,
                    "score_dir":     entry.score_dir,
                    "bars_to_entry": entry.bars_to_entry,
                    "alert_tier":    entry.alert_tier,
                    "headline":      entry.headline,
                    "detail":        getattr(update, "detail", ""),
                },
            })

    def update_pending_signals(self, pending: dict) -> None:
        """Sync pending signal state from HumanGate."""
        with self._lock:
            self.pending_signals = dict(pending)
            self._push_ws({"type": "pending_signals", "data": self.pending_signals})

    def update_indicators(self, instrument: str, snapshot: IndicatorSnapshot) -> None:
        with self._lock:
            self.indicators[instrument] = snapshot
            data = {**vars(snapshot), **self.options_data.get(instrument, {})}
            self._push_ws({"type": "indicators",
                           "instrument": instrument,
                           "data": data})

    def update_options(self, instrument: str, snap) -> None:
        """Store options intelligence snapshot and broadcast to dashboard."""
        with self._lock:
            opts = {
                "options_pcr":          getattr(snap, "pcr",          None),
                "options_pcr_label":    getattr(snap, "pcr_label",    None),
                "options_max_pain":     getattr(snap, "max_pain",     None),
                "options_iv_current":   getattr(snap, "iv_current",   None),
                "options_iv_pct":       getattr(snap, "iv_percentile",None),
                "options_iv_label":     getattr(snap, "iv_label",     None),
                "options_oi_buildup":   getattr(snap, "oi_buildup",   None),
                "options_spot":         getattr(snap, "spot_price",   None),
                "options_call_strikes": getattr(snap, "top_call_oi_strikes", []),
                "options_put_strikes":  getattr(snap, "top_put_oi_strikes",  []),
            }
            self.options_data[instrument] = opts
            # Merge into existing indicator data for the WS broadcast
            ind  = self.indicators.get(instrument)
            data = {**(vars(ind) if ind else {}), **opts}
            self._push_ws({"type": "indicators",
                           "instrument": instrument,
                           "data": data})

    def add_log(self, category: str, message: str) -> None:
        with self._lock:
            entry = {
                "ts":       datetime.now(IST).strftime("%H:%M:%S"),
                "category": category,
                "message":  message,
            }
            self.log_lines.append(entry)
            self._push_ws({"type": "log", "data": entry})

    def update_session(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self.session, k):
                    setattr(self.session, k, v)
            self._push_ws({"type": "session", "data": vars(self.session)})

    # ── Read methods (called by API) ─────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "session":      vars(self.session),
                "ticks":        {k: vars(v) for k, v in self.ticks.items()},
                "scenarios":    {
                    inst: [self._scenario_dict(s) for s in scns]
                    for inst, scns in self.scenarios.items()
                },
                "open_trades":  [self._trade_dict(t) for t in self.open_trades.values()],
                "closed_trades": list(self.closed_trades),
                "indicators":   {
                    k: {**vars(v), **self.options_data.get(k, {})}
                    for k, v in self.indicators.items()
                },
                "narrator":     {
                    inst: [vars(e) for e in list(feed)]
                    for inst, feed in self.narrator.items()
                },
                "pending_signals": self.pending_signals,
                "log_lines":    list(self.log_lines)[-50:],
            }

    def pop_ws_events(self, max_events: int = 100) -> list[dict]:
        with self._lock:
            out = []
            for _ in range(min(max_events, len(self._ws_queue))):
                out.append(self._ws_queue.popleft())
            return out

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _push_ws(self, event: dict) -> None:
        self._ws_queue.append(event)

    @staticmethod
    def _scenario_dict(s: ScenarioState) -> dict:
        return {
            "id": s.id, "hypothesis": s.hypothesis, "state": s.state,
            "confidence": s.confidence, "peak_conf": s.peak_conf,
            "sl": s.sl, "t1": s.target1, "t2": s.target2, "t3": s.target3,
            "reason": s.reason, "eta": s.eta, "bars_above_85": s.bars_above_85,
        }

    @staticmethod
    def _trade_dict(t: TradeState) -> dict:
        return {
            "trade_id": t.trade_id, "instrument": t.instrument,
            "hypothesis": t.hypothesis, "strike": t.strike,
            "option_type": t.option_type, "entry_price": t.entry_price,
            "current_price": t.current_price, "sl": t.sl,
            "t1": t.target1, "unrealised_pnl": t.unrealised_pnl,
            "realised_pnl": t.realised_pnl, "state": t.state,
            "lots": t.lots, "quantity": t.quantity, "opened_at": t.opened_at,
        }


# Module-level singleton — shared between engine and API
_global_state: Optional[BrahmastraState] = None


def get_state() -> BrahmastraState:
    global _global_state
    if _global_state is None:
        _global_state = BrahmastraState()
    return _global_state


def reset_state() -> BrahmastraState:
    global _global_state
    _global_state = BrahmastraState()
    return _global_state
