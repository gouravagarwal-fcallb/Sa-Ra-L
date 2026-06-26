"""
Elliott Wave Options Strategy — Kite Connect integration.

Fetches SENSEX/NIFTY OHLC from Kite Historical API, identifies the
current Elliott Wave position, and outputs the options trade recommendation.

Incorporates insights from the Seller-perspective methodology:
  - Wave Theory for daily bias (Sell on Rise vs Buy on Dip)
  - Gamma play: enter only when sellers are forced to cover (short covering)
  - Expiry-day specialization: high R:R on Nifty (Tue) and Sensex (Thu)
  - Zone-based entry, not arbitrary support/resistance levels
  - 30% capital rule: deploy at most 30% of account per trade
  - Fixed daily loss cap (configurable)

Usage (standalone):
    python -m src.brahmastra.strategies.elliott_wave_strategy \
        --instrument SENSEX --timeframe 15m --mode paper

Usage (from BRAHMASTRA live engine):
    from src.brahmastra.strategies.elliott_wave_strategy import ElliottWaveStrategy
    strategy = ElliottWaveStrategy(kite, config)
    recommendation = strategy.run()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.brahmastra.indicators.elliott_wave import (
    ElliottWaveAnalyzer,
    ElliottWaveResult,
    WaveLabel,
    WaveType,
    OptionsAction,
)

log = logging.getLogger("brahmastra.elliott_wave_strategy")

IST = timezone(timedelta(hours=5, minutes=30))

# ── Instrument metadata ───────────────────────────────────────────────────────

INSTRUMENT_META = {
    "SENSEX": {
        "kite_symbol":   "SENSEX",
        "exchange":      "BSE",
        "lot_size":      20,
        "strike_step":   100,
        "expiry_day":    3,          # Thursday (0=Mon)
        "options_exchange": "BFO",
        "token":         1,          # placeholder — set from instrument list
    },
    "NIFTY": {
        "kite_symbol":   "NIFTY 50",
        "exchange":      "NSE",
        "lot_size":      75,
        "strike_step":   50,
        "expiry_day":    1,          # Tuesday
        "options_exchange": "NFO",
        "token":         256265,
    },
}

# ── Trade recommendation ──────────────────────────────────────────────────────

@dataclass
class TradeRecommendation:
    instrument:      str
    timeframe:       str
    wave_result:     ElliottWaveResult
    action:          OptionsAction
    atm_strike:      int
    recommended_strike: int
    expiry_date:     Optional[str]
    lots:            int
    max_premium:     float           # max premium to pay per lot
    sl_pct:          float           # stop loss % from premium paid
    target_pct:      float           # target % from premium paid
    capital_to_use:  float           # ₹ amount to deploy
    reasoning:       str
    timestamp:       datetime = field(default_factory=lambda: datetime.now(IST))

    def display(self) -> str:
        lines = [
            "=" * 70,
            f"  ELLIOTT WAVE STRATEGY — {self.instrument} ({self.timeframe})",
            "=" * 70,
            f"  Wave:      {self.wave_result.current_wave.value} "
            f"({self.wave_result.wave_type.value.upper()}) "
            f"| Confidence: {self.wave_result.confidence:.0f}%",
            f"  Action:    {self.action.value}",
            f"  Strike:    {self.recommended_strike}",
            f"  ATM:       {self.atm_strike}",
            f"  Expiry:    {self.expiry_date or 'nearest'}",
            f"  Lots:      {self.lots}",
            f"  Max Premium (per lot): ₹{self.max_premium:,.0f}",
            f"  Capital:   ₹{self.capital_to_use:,.0f}",
            f"  SL:        {self.sl_pct:.0f}% of premium",
            f"  Target:    {self.target_pct:.0f}% of premium",
            "-" * 70,
            f"  Strike guidance: {self.wave_result.strike_guidance}",
            "-" * 70,
            f"  Reasoning: {self.reasoning}",
            "=" * 70,
        ]
        return "\n".join(lines)


# ── Strategy class ────────────────────────────────────────────────────────────

class ElliottWaveStrategy:
    """
    Elliott Wave options strategy integrated with Kite Connect.

    Parameters
    ----------
    kite : KiteConnect instance (from kiteconnect library)
    instrument : 'SENSEX' | 'NIFTY'
    timeframe : Kite interval string ('15minute', '60minute', 'day')
    account_capital : total account value in ₹ (for 30% rule sizing)
    daily_loss_cap : max daily loss in ₹ before going flat
    """

    KITE_INTERVAL_MAP = {
        "1m":   "minute",
        "5m":   "5minute",
        "15m":  "15minute",
        "1h":   "60minute",
        "1D":   "day",
    }

    def __init__(
        self,
        kite,
        instrument:       str   = "SENSEX",
        timeframe:        str   = "15m",
        lookback_days:    int   = 30,
        account_capital:  float = 100_000,
        daily_loss_cap:   float = 5_000,
        min_confidence:   float = 55.0,
        paper_mode:       bool  = True,
    ):
        self.kite             = kite
        self.instrument       = instrument
        self.meta             = INSTRUMENT_META[instrument]
        self.timeframe        = timeframe
        self.lookback_days    = lookback_days
        self.account_capital  = account_capital
        self.daily_loss_cap   = daily_loss_cap
        self.min_confidence   = min_confidence
        self.paper_mode       = paper_mode

        self.analyzer = ElliottWaveAnalyzer(
            timeframe=timeframe,
            instrument=instrument,
            min_swing_pct=1.2 if timeframe in ("15m", "1h") else 2.0,
            lookback=300,
            strike_step=self.meta["strike_step"],
        )

        self._daily_loss_so_far: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> Optional[TradeRecommendation]:
        """Fetch data, analyze waves, and return trade recommendation."""
        ohlcv = self._fetch_ohlcv()
        if not ohlcv:
            log.warning("No OHLCV data received from Kite.")
            return None

        result = self._analyze(ohlcv)
        log.info("Wave result: %s", result.summary())

        if result.confidence < self.min_confidence:
            log.info(
                "Confidence %.0f%% below threshold %.0f%%. No trade.",
                result.confidence, self.min_confidence
            )
            return None

        if result.action == OptionsAction.WAIT:
            log.info("Strategy says WAIT. No trade.")
            return None

        if self._daily_loss_so_far >= self.daily_loss_cap:
            log.warning("Daily loss cap ₹%.0f hit. No new trades.", self.daily_loss_cap)
            return None

        return self._build_recommendation(result, ohlcv[-1]["close"])

    def update_daily_loss(self, loss_amount: float) -> None:
        """Call with positive loss amount when a trade closes at a loss."""
        self._daily_loss_so_far += loss_amount

    def reset_daily_loss(self) -> None:
        self._daily_loss_so_far = 0.0

    # ── Data fetching ─────────────────────────────────────────────────────────

    def _fetch_ohlcv(self) -> list[dict]:
        from datetime import date

        kite_interval = self.KITE_INTERVAL_MAP.get(self.timeframe, "15minute")
        token = self.meta["token"]
        end_dt   = datetime.now(IST)
        start_dt = end_dt - timedelta(days=self.lookback_days)

        try:
            data = self.kite.historical_data(
                instrument_token=token,
                from_date=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                to_date=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                interval=kite_interval,
                continuous=False,
            )
            log.info("Fetched %d bars for %s %s", len(data), self.instrument, self.timeframe)
            return data
        except Exception as exc:
            log.error("Kite historical_data error: %s", exc)
            return []

    # ── Analysis ──────────────────────────────────────────────────────────────

    def _analyze(self, ohlcv: list[dict]) -> ElliottWaveResult:
        from src.brahmastra.indicators.elliott_wave import analyze_ohlcv
        return analyze_ohlcv(
            ohlcv,
            instrument=self.instrument,
            min_swing_pct=1.2 if self.timeframe in ("15m", "1h") else 2.0,
            strike_step=self.meta["strike_step"],
        )

    # ── Recommendation builder ────────────────────────────────────────────────

    def _build_recommendation(
        self, result: ElliottWaveResult, current_price: float
    ) -> TradeRecommendation:
        step = self.meta["strike_step"]
        atm  = round(current_price / step) * step

        # Determine strike direction from action
        if result.action in (OptionsAction.LONG_ATM_CE, OptionsAction.SHORT_ATM_CE):
            recommended_strike = atm
        elif result.action == OptionsAction.LONG_OTM_CE:
            recommended_strike = atm + step
        elif result.action in (OptionsAction.LONG_OTM_PE, OptionsAction.LONG_ATM_PE):
            recommended_strike = atm - step
        elif result.action == OptionsAction.BEAR_PUT_SPREAD:
            recommended_strike = atm - step
        else:
            recommended_strike = atm

        # Position sizing: 30% rule (FULL=30%, HALF=15%, QUARTER=8%, NONE=0%)
        size_map = {"FULL": 0.30, "HALF": 0.15, "QUARTER": 0.08, "NONE": 0.0}
        capital_to_use = self.account_capital * size_map.get(result.position_size, 0.08)

        # Estimate lots
        # rough premium = 0.5% of spot for OTM, 1% for ATM (expiry week rough guide)
        est_premium = current_price * 0.005
        lot_size    = self.meta["lot_size"]
        cost_per_lot = est_premium * lot_size
        lots = max(1, int(capital_to_use / cost_per_lot)) if cost_per_lot > 0 else 1

        # SL/Target based on wave
        sl_map     = {WaveLabel.W3: 40, WaveLabel.WC: 40, WaveLabel.W5: 30}
        target_map = {WaveLabel.W3: 150, WaveLabel.WC: 150, WaveLabel.W5: 80}
        sl_pct     = sl_map.get(result.current_wave, 50)
        target_pct = target_map.get(result.current_wave, 100)

        expiry = self._nearest_expiry()

        return TradeRecommendation(
            instrument=self.instrument,
            timeframe=self.timeframe,
            wave_result=result,
            action=result.action,
            atm_strike=atm,
            recommended_strike=recommended_strike,
            expiry_date=expiry,
            lots=lots,
            max_premium=est_premium,
            sl_pct=sl_pct,
            target_pct=target_pct,
            capital_to_use=capital_to_use,
            reasoning=result.reasoning,
        )

    def _nearest_expiry(self) -> str:
        """Return YYYY-MM-DD string of the nearest upcoming expiry for this instrument."""
        today    = datetime.now(IST).date()
        exp_day  = self.meta["expiry_day"]   # 0=Mon … 6=Sun
        days_ahead = (exp_day - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        expiry = today + timedelta(days=days_ahead)
        return expiry.strftime("%Y-%m-%d")



# ── CLI runner ────────────────────────────────────────────────────────────────

def _cli_run():
    """
    Offline demo using yfinance as a Kite substitute.
    Run: python -m src.brahmastra.strategies.elliott_wave_strategy
    """
    import sys
    instrument = "SENSEX"
    timeframe  = "15m"

    print(f"\nElliott Wave Strategy — {instrument} {timeframe} (offline demo)\n")

    try:
        import yfinance as yf
        ticker_map = {"SENSEX": "^BSESN", "NIFTY": "^NSEI"}
        tf_map     = {"15m": "15m", "1h": "60m", "1D": "1d"}
        ticker = yf.Ticker(ticker_map[instrument])
        period = "60d" if timeframe == "1D" else "5d"
        df = ticker.history(period=period, interval=tf_map.get(timeframe, "15m"))

        if df.empty:
            print("No data from yfinance. Exiting.")
            sys.exit(1)

        ohlcv = [
            {
                "date":   idx.to_pydatetime(),
                "open":   row["Open"],
                "high":   row["High"],
                "low":    row["Low"],
                "close":  row["Close"],
                "volume": row["Volume"],
            }
            for idx, row in df.iterrows()
        ]

        from src.brahmastra.indicators.elliott_wave import analyze_ohlcv
        result = analyze_ohlcv(
            ohlcv,
            instrument=instrument,
            min_swing_pct=1.2,
            strike_step=100,
        )

        print(f"Wave: {result.current_wave.value}  ({result.wave_type.value})")
        print(f"Confidence: {result.confidence:.0f}%")
        print(f"Action: {result.action.value}")
        print(f"Strike guidance: {result.strike_guidance}")
        print(f"Position size: {result.position_size}")
        print(f"\nReasoning: {result.reasoning}")

        if result.completed_waves:
            print("\nCompleted waves:")
            for seg in result.completed_waves:
                fib = "✓ Fib" if seg.fib_valid else "  ---"
                r = f"retrace={seg.retrace_of:.2f}" if seg.retrace_of else ""
                print(f"  Wave {seg.label.value}: {seg.move_pct:+.1f}%  {r}  {fib}")

    except ImportError:
        print("yfinance not installed. Run: pip install yfinance")
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _cli_run()
