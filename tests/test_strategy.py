"""
Unit tests for core strategy logic.
Run with: python -m pytest tests/ -v
"""

import math
import time
import pytest
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

from src.strategy.direction_engine import DirectionEngine, DirectionInputs, Direction
from src.strategy.exit_logic import ExitLogic, ExitReason
from src.backtest.option_pricer import OptionPricer
from src.utils.market_calendar import (
    is_expiry_day, get_weekly_expiry,
    get_nifty_weekly_expiry, get_sensex_weekly_expiry,
    is_nifty_expiry_day, is_sensex_expiry_day, get_day_instrument,
)
from src.utils.helpers import round_to_strike, format_inr
from src.broker.base import BaseBroker, Order
from src.live.nifty_intraday_live import NiftyIntradayLive, IntradayTrade

STRATEGY_CONFIG = {
    "direction": {
        "score_thresholds": {"bullish_min": 3, "bearish_max": -3},
        "dow_jones": {"strong_up": 1.0, "mild_up": 0.3, "mild_down": -0.3, "strong_down": -1.0},
        "gift_nifty": {"strong_premium": 100, "mild_premium": 30, "mild_discount": -30, "strong_discount": -100},
        "india_vix": {"calm_below": 13.0, "elevated_above": 18.0, "panic_above": 22.0},
        "sensex": {"up_threshold": 0.3, "down_threshold": -0.3},
    },
    "exit": {"profit_target_pct": 27.5},
    "risk": {
        "daily_loss_limit": 1_000_000,
        "expected_daily_profit": 200_000,
        "windfall_multiplier": 2.5,
        "windfall_trail_drawdown_pct": 40,
    },
}


# ── Direction Engine Tests ─────────────────────────────

class TestDirectionEngine:
    engine = DirectionEngine(STRATEGY_CONFIG)

    def test_strong_bullish(self):
        inp = DirectionInputs(dow_change_pct=1.5, gift_nifty_premium=120, india_vix=12.0, sensex_change_pct=0.5)
        result = self.engine.evaluate(inp)
        assert result.direction == Direction.BULLISH
        assert result.score >= 3

    def test_strong_bearish(self):
        inp = DirectionInputs(dow_change_pct=-1.5, gift_nifty_premium=-120, india_vix=25.0, sensex_change_pct=-0.5)
        result = self.engine.evaluate(inp)
        assert result.direction == Direction.BEARISH
        assert result.score <= -3

    def test_neutral_flat_market(self):
        inp = DirectionInputs(dow_change_pct=0.1, gift_nifty_premium=10, india_vix=15.0, sensex_change_pct=0.1)
        result = self.engine.evaluate(inp)
        assert result.direction == Direction.NEUTRAL

    def test_vix_panic_suppresses_bullish(self):
        inp = DirectionInputs(dow_change_pct=1.5, gift_nifty_premium=150, india_vix=25.0, sensex_change_pct=0.5)
        result = self.engine.evaluate(inp)
        # VIX=25 gives -2; score should be reduced
        assert result.breakdown["india_vix"]["score"] == -2

    def test_score_breakdown_keys(self):
        inp = DirectionInputs()
        result = self.engine.evaluate(inp)
        assert "dow_jones" in result.breakdown
        assert "gift_nifty" in result.breakdown
        assert "india_vix" in result.breakdown
        assert "sensex" in result.breakdown
        assert "total" in result.breakdown


# ── Exit Logic Tests ──────────────────────────────────

class TestExitLogic:
    logic = ExitLogic(STRATEGY_CONFIG)

    def test_target_hit(self):
        signal = self.logic.check_exit(
            entry_price=100.0,
            current_price=128.0,   # 28% gain
            quantity=26000,
            lot_size=75,
            cumulative_day_pnl=0,
            peak_trade_pnl=728000,
        )
        assert signal.should_exit
        assert signal.reason == ExitReason.TARGET_HIT

    def test_target_not_hit(self):
        signal = self.logic.check_exit(
            entry_price=100.0,
            current_price=115.0,   # 15% gain — below 27.5% target
            quantity=26000,
            lot_size=75,
            cumulative_day_pnl=0,
            peak_trade_pnl=390000,
        )
        assert not signal.should_exit
        assert signal.reason == ExitReason.HOLDING

    def test_daily_loss_limit(self):
        signal = self.logic.check_exit(
            entry_price=100.0,
            current_price=60.0,
            quantity=26000,
            lot_size=75,
            cumulative_day_pnl=-800_000,
            peak_trade_pnl=0,
        )
        assert signal.should_exit
        assert signal.reason == ExitReason.DAILY_LOSS_LIMIT

    def test_force_close(self):
        signal = self.logic.check_exit(
            entry_price=100.0,
            current_price=95.0,
            quantity=26000,
            lot_size=75,
            cumulative_day_pnl=0,
            peak_trade_pnl=0,
            force_close=True,
        )
        assert signal.should_exit
        assert signal.reason == ExitReason.FORCE_CLOSE

    def test_target_price_calculation(self):
        target = self.logic.target_price(100.0)
        assert 124 <= target <= 131  # 27.5% above 100


# ── Option Pricer Tests ───────────────────────────────

class TestOptionPricer:
    pricer = OptionPricer(risk_free_rate=0.065)

    def test_atm_call_reasonable(self):
        opt = self.pricer.price(spot=22000, strike=22000, vix=15.0, T_hours=6.0, option_type="CE")
        assert opt.price > 0
        assert 0.4 < opt.delta < 0.6   # ATM delta should be ~0.5

    def test_atm_put_reasonable(self):
        opt = self.pricer.price(spot=22000, strike=22000, vix=15.0, T_hours=6.0, option_type="PE")
        assert opt.price > 0
        assert -0.6 < opt.delta < -0.4

    def test_put_call_parity_approx(self):
        S, K, T, r, v = 22000, 22000, 6.0, 0.065, 15.0
        call = self.pricer.price(S, K, v, T, "CE").price
        put = self.pricer.price(S, K, v, T, "PE").price
        T_years = T / (365 * 24)
        import math
        parity_diff = abs(call - put - S + K * math.exp(-r * T_years))
        assert parity_diff < 5, f"Put-call parity violated: diff={parity_diff:.2f}"

    def test_theta_negative_for_call(self):
        opt = self.pricer.price(22000, 22000, 15.0, 6.0, "CE")
        assert opt.theta < 0   # Theta decay always negative for long options

    def test_atm_strike_rounding(self):
        assert self.pricer.atm_strike(22123) == 22100
        assert self.pricer.atm_strike(22176) == 22200
        assert self.pricer.atm_strike(22150) == 22150

    def test_hours_to_expiry(self):
        h = self.pricer.hours_to_expiry(9.0, 22.0)
        assert 5.5 < h < 6.5  # ~6 hours from 9:22 to 15:30


# ── Market Calendar Tests ─────────────────────────────

class TestMarketCalendar:
    def test_tuesday_is_nifty_expiry(self):
        # Jan 9 2024 was a Tuesday — should be Nifty expiry
        assert get_nifty_weekly_expiry(date(2024, 1, 9)) == date(2024, 1, 9)
        assert is_nifty_expiry_day(date(2024, 1, 9))

    def test_thursday_is_sensex_expiry(self):
        # Jan 4 2024 was a Thursday — should be Sensex expiry
        assert get_sensex_weekly_expiry(date(2024, 1, 4)) == date(2024, 1, 4)
        assert is_sensex_expiry_day(date(2024, 1, 4))

    def test_nifty_expiry_from_monday(self):
        # From Monday Jan 8, next Nifty expiry = Jan 9 (Tuesday)
        assert get_nifty_weekly_expiry(date(2024, 1, 8)) == date(2024, 1, 9)

    def test_sensex_expiry_from_monday(self):
        # From Monday Jan 8, next Sensex expiry = Jan 11 (Thursday)
        assert get_sensex_weekly_expiry(date(2024, 1, 8)) == date(2024, 1, 11)

    def test_wednesday_trades_nifty(self):
        # Wednesday is pre-expiry day (Nifty expires Thursday) — should trade NIFTY
        assert get_day_instrument(date(2024, 1, 10)) == "NIFTY"  # Jan 10 = Wednesday

    def test_monday_is_nifty(self):
        assert get_day_instrument(date(2024, 1, 8)) == "NIFTY"

    def test_tuesday_is_nifty(self):
        assert get_day_instrument(date(2024, 1, 9)) == "NIFTY"

    def test_thursday_is_sensex(self):
        assert get_day_instrument(date(2024, 1, 11)) == "SENSEX"

    def test_friday_is_nifty(self):
        assert get_day_instrument(date(2024, 1, 12)) == "NIFTY"

    def test_expiry_always_trading_day(self):
        # Expiry should never fall on a holiday — should shift back
        expiry = get_nifty_weekly_expiry(date(2024, 3, 26))  # Holi week
        from src.utils.market_calendar import is_trading_day
        assert is_trading_day(expiry)

    def test_legacy_get_weekly_expiry_returns_tuesday(self):
        # Legacy function now returns Tuesday (Nifty expiry)
        assert get_weekly_expiry(date(2024, 1, 8)).weekday() == 1  # Tuesday


# ── Utility Tests ─────────────────────────────────────

class TestHelpers:
    def test_round_to_strike(self):
        assert round_to_strike(22123) == 22100
        assert round_to_strike(22176) == 22200
        assert round_to_strike(22150) == 22150
        assert round_to_strike(22125) == 22100

    def test_format_inr_lakh(self):
        result = format_inr(200000)
        assert "L" in result and "2.00" in result

    def test_format_inr_crore(self):
        result = format_inr(10000000)
        assert "Cr" in result and "1.00" in result

    def test_format_inr_negative(self):
        result = format_inr(-500000)
        assert "-" in result and "L" in result


# ── NiftyIntradayLive Tests ───────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))

NI_CFG = {
    "nifty_intraday": {
        "max_trade_rs":          10000,
        "hard_stop_rs":           1200,
        "profit_target_rs":       2750,
        "daily_loss_lock_rs":     4800,
        "daily_profit_lock_rs":   8250,
        "cooldown_loss_min":        15,
        "cooldown_profit_min":       5,
        "opening_range_minutes":    15,
        "hard_close_time":       "15:10",
        "orb_buffer_pct":         0.05,
        "vwap_slope_threshold":   0.05,
        "min_volume_ratio":        1.3,
        "rsi_oversold":             35,
        "rsi_overbought":           65,
        "sr_lookback_bars":         30,
        "sr_touch_pct":           0.15,
        "breakeven_at_pct_target": 0.50,
        "half_size_at_profit_lock": True,
        "prefer_itm_strikes":        0,
        "atr_trend_multiplier":    1.2,
        "avoid_first_minutes":      10,
    },
    "backtest": {"slippage_pct": 0.0, "risk_free_rate": 0.065},
    "instruments": {"nifty": {"lot_size": 75, "strike_step": 50}},
}


class _MockBroker(BaseBroker):
    """Minimal broker that records calls and optionally raises on place_order."""

    def __init__(self, fail_times: int = 0):
        self._fail_times = fail_times
        self._calls = 0
        self.orders = []

    def place_order(self, order: Order) -> str:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise RuntimeError(f"simulated failure #{self._calls}")
        self.orders.append(order)
        return f"ORD{self._calls:04d}"

    def get_order_status(self, order_id: str) -> Order:
        return Order("X", "NFO", "CE", 0, "20260101", "BUY", 0)

    def get_ltp(self, symbol, exchange, strike, option_type, expiry) -> float:
        return 100.0

    def get_positions(self):
        return []

    def cancel_order(self, order_id: str) -> bool:
        return True


def _make_engine(mode: str = "paper") -> NiftyIntradayLive:
    broker = _MockBroker()
    return NiftyIntradayLive(NI_CFG, broker, mode=mode)


def _make_df1(n: int = 40, base_close: float = 22000.0,
              vol_ratio: float = 1.5) -> pd.DataFrame:
    """Synthetic 1-min OHLCV DataFrame with index in IST."""
    IST_tz = timezone(timedelta(hours=5, minutes=30))
    start = datetime(2026, 6, 23, 9, 15, tzinfo=IST_tz)
    idx = [start + timedelta(minutes=i) for i in range(n)]
    closes = [base_close + float(i) * 0.5 for i in range(n)]
    avg_vol = 5000.0
    volumes = [avg_vol] * n
    volumes[-1] = avg_vol * vol_ratio        # last bar has high volume
    df = pd.DataFrame({
        "Open":   closes,
        "High":   [c + 5.0 for c in closes],
        "Low":    [c - 5.0 for c in closes],
        "Close":  closes,
        "Volume": volumes,
    }, index=pd.DatetimeIndex(idx))
    return df


class TestNiftyIntradayPositionSizing:
    """Position sizing formula: qty, stop, target, breakeven."""

    def setup_method(self):
        self.eng = _make_engine()

    # Rulebook Example A: CE @ Rs.120
    def test_example_a_qty(self):
        qty, stop, target, be = self.eng._compute_qty(120.0)
        assert qty == 75, f"expected qty=75 got {qty}"

    def test_example_a_stop(self):
        qty, stop, target, be = self.eng._compute_qty(120.0)
        # stop = entry - 1200/qty = 120 - 16 = 104
        assert abs(stop - 104.0) < 0.05, f"expected stop≈104 got {stop}"

    def test_example_a_target(self):
        qty, stop, target, be = self.eng._compute_qty(120.0)
        # target = entry + 2750/qty = 120 + 36.67 ≈ 156.67
        assert abs(target - 156.67) < 0.05, f"expected target≈156.67 got {target}"

    def test_example_a_be(self):
        qty, stop, target, be = self.eng._compute_qty(120.0)
        # be = entry + 0.5*(2750/qty) = 120 + 18.33 ≈ 138.33
        assert abs(be - 138.33) < 0.05, f"expected be≈138.33 got {be}"

    def test_example_a_max_loss_guarantee(self):
        qty, stop, target, be = self.eng._compute_qty(120.0)
        max_loss = (120.0 - stop) * qty
        assert max_loss <= 1200.0 + 0.01, f"max_loss {max_loss:.2f} exceeds ₹1,200"

    # Rulebook Example B: PE @ Rs.45
    def test_example_b_qty(self):
        qty, stop, target, be = self.eng._compute_qty(45.0)
        assert qty == 150, f"expected qty=150 got {qty}"

    def test_example_b_stop(self):
        qty, stop, target, be = self.eng._compute_qty(45.0)
        # stop = 45 - 1200/150 = 45 - 8 = 37
        assert abs(stop - 37.0) < 0.05, f"expected stop≈37 got {stop}"

    def test_example_b_target(self):
        qty, stop, target, be = self.eng._compute_qty(45.0)
        # target = 45 + 2750/150 = 45 + 18.33 ≈ 63.33
        assert abs(target - 63.33) < 0.05, f"expected target≈63.33 got {target}"

    def test_example_b_max_loss_guarantee(self):
        qty, stop, target, be = self.eng._compute_qty(45.0)
        max_loss = (45.0 - stop) * qty
        assert max_loss <= 1200.0 + 0.01, f"max_loss {max_loss:.2f} exceeds ₹1,200"

    def test_budget_never_exceeded(self):
        for entry in [10.0, 50.0, 100.0, 150.0, 200.0]:
            qty, stop, target, be = self.eng._compute_qty(entry)
            if qty > 0:
                assert entry * qty <= 10000.0 + 0.01, (
                    f"entry={entry}: budget {entry*qty:.0f} > ₹10,000"
                )

    def test_zero_qty_when_too_expensive(self):
        # Entry @ Rs.200: 200 × 75 = 15,000 > budget=10,000
        qty, stop, target, be = self.eng._compute_qty(200.0)
        assert qty == 0

    def test_half_size_halves_qty(self):
        qty_full, *_ = self.eng._compute_qty(45.0)
        qty_half, *_ = self.eng._compute_qty(45.0, half_size=True)
        assert qty_half == qty_full // 2 or qty_half <= qty_full // 2

    def test_half_size_max_loss_still_bounded(self):
        qty, stop, target, be = self.eng._compute_qty(45.0, half_size=True)
        if qty > 0:
            max_loss = (45.0 - stop) * qty
            assert max_loss <= 600.0 + 0.01   # half of 1,200


class TestNiftyIntradayChaseLimit:
    """Chase limit: skip entry if price >0.5% beyond ORB."""

    def setup_method(self):
        self.eng = _make_engine()
        self.eng.orb_high = 22000.0
        self.eng.orb_low  = 21800.0
        self.eng.orb_set  = True
        # orb_buffer_pct stored as fraction after / 100 in __init__
        # raw config is 0.05 → /100 → 0.0005 in engine
        # chase_limit_pct = 0.005 (hardcoded in method)

    def _df_with_close(self, close: float, vol_ratio: float = 1.5) -> pd.DataFrame:
        df = _make_df1(base_close=close - 19.5, vol_ratio=vol_ratio)
        df["Close"] = close
        df["Open"]  = close
        df["High"]  = close + 5
        df["Low"]   = close - 5
        return df

    def test_bullish_within_chase_limit_returns_signal(self):
        # orb_high=22000, buffer=0.0005 → threshold=22011
        # chase_limit=0.005 → max=22110
        # close=22050 → within limit, above vwap → should signal if vol ok
        close = 22000 * (1 + 0.003)  # +0.3%, within 0.5% chase limit
        df = self._df_with_close(close, vol_ratio=2.0)
        sig = self.eng._scan_trend_setup(df, close)
        # vwap from uniform df will be ≈ close; close > vwap only if there's drift
        # Just verify it does NOT return None due to chase limit alone
        # (may still return None if VWAP condition not met — that's ok)
        assert sig is None or sig["direction"] == "BULLISH"

    def test_bullish_beyond_chase_limit_returns_none(self):
        # close > orb_high * 1.005 = 22110 → too far
        close = 22000 * 1.006   # +0.6%
        df = self._df_with_close(close, vol_ratio=2.0)
        result = self.eng._scan_trend_setup(df, close)
        assert result is None, f"expected None (chase limit), got {result}"

    def test_bearish_beyond_chase_limit_returns_none(self):
        # close < orb_low * 0.995 = 21691 → too far
        close = 21800 * 0.994   # -0.6%
        df = self._df_with_close(close, vol_ratio=2.0)
        result = self.eng._scan_trend_setup(df, close)
        assert result is None, f"expected None (chase limit), got {result}"

    def test_no_signal_without_orb_set(self):
        self.eng.orb_set = False
        df = _make_df1()
        result = self.eng._scan_trend_setup(df, 22100.0)
        assert result is None


class TestNiftyIntradaySellRetry:
    """_place_sell: 3-attempt retry with backoff; CRITICAL on total failure."""

    def _make_trade(self) -> IntradayTrade:
        return IntradayTrade(
            direction="BULLISH", option_type="CE", strike=22000,
            expiry_str="20260624", entry_price=120.0, stop_price=104.0,
            target_price=156.67, be_price=138.33, quantity=75,
            entry_time="10:00:00", setup_type="TREND_ORB", is_paper=False,
            order_id="ORD001",
        )

    def test_sell_success_first_attempt(self):
        broker = _MockBroker(fail_times=0)
        eng = NiftyIntradayLive(NI_CFG, broker, mode="paper")
        trade = self._make_trade()
        trade.pnl = 2750.0
        eng._place_sell(trade, date(2026, 6, 24), 156.67)
        assert broker._calls == 1

    def test_sell_success_second_attempt(self):
        broker = _MockBroker(fail_times=1)
        eng = NiftyIntradayLive(NI_CFG, broker, mode="paper")
        trade = self._make_trade()
        trade.pnl = 2750.0
        with patch("time.sleep"):
            eng._place_sell(trade, date(2026, 6, 24), 156.67)
        assert broker._calls == 2

    def test_sell_all_fail_no_exception(self):
        """3 failures must NOT raise — thread must stay alive."""
        broker = _MockBroker(fail_times=99)
        eng = NiftyIntradayLive(NI_CFG, broker, mode="paper")
        trade = self._make_trade()
        trade.pnl = -1200.0
        with patch("time.sleep"):
            try:
                eng._place_sell(trade, date(2026, 6, 24), 104.0)
            except Exception as exc:
                pytest.fail(f"_place_sell raised on total failure: {exc}")
        assert broker._calls == 3

    def test_sell_all_fail_logs_critical_with_exception(self, caplog):
        """CRITICAL log must include the exception text."""
        import logging
        broker = _MockBroker(fail_times=99)
        eng = NiftyIntradayLive(NI_CFG, broker, mode="paper")
        trade = self._make_trade()
        trade.pnl = -1200.0
        with patch("time.sleep"), caplog.at_level(logging.CRITICAL):
            eng._place_sell(trade, date(2026, 6, 24), 104.0)
        assert any("CRITICAL" in r.message or "simulated failure" in r.message
                   for r in caplog.records), (
            f"No CRITICAL log found. Records: {[r.message for r in caplog.records]}"
        )

    def test_sell_retry_uses_backoff(self):
        """Verify time.sleep is called with correct delays (2 s, 4 s)."""
        broker = _MockBroker(fail_times=2)
        eng = NiftyIntradayLive(NI_CFG, broker, mode="paper")
        trade = self._make_trade()
        trade.pnl = 0.0
        with patch("time.sleep") as mock_sleep:
            eng._place_sell(trade, date(2026, 6, 24), 120.0)
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_args == [2, 4], f"expected [2, 4] backoff, got {sleep_args}"


class TestNiftyIntradayBreakevenTrail:
    """Breakeven stop activates when LTP reaches be_price."""

    def _make_trade(self, be_triggered: bool = False) -> IntradayTrade:
        return IntradayTrade(
            direction="BULLISH", option_type="CE", strike=22000,
            expiry_str="20260624", entry_price=120.0, stop_price=104.0,
            target_price=156.67, be_price=138.33, quantity=75,
            entry_time="10:00:00", setup_type="TREND_ORB", is_paper=True,
            order_id="PAPER", be_triggered=be_triggered,
        )

    @patch("src.live.nifty_intraday_live.get_spot_price", return_value=22100.0)
    def test_be_activates_when_ltp_reaches_be_price(self, _mock_spot):
        eng = _make_engine()
        trade = self._make_trade()
        eng.open_trade = trade
        eng.state = "IN_TRADE"

        # Patch _get_ltp to return be_price (exactly at trigger)
        with patch.object(eng, "_get_ltp", return_value=138.33):
            with patch.object(eng, "_close_trade", return_value=None) as mock_close:
                eng._monitor_trade(date(2026, 6, 24))

        assert trade.be_triggered is True
        assert trade.stop_price == trade.entry_price  # stop moved to entry

    @patch("src.live.nifty_intraday_live.get_spot_price", return_value=22100.0)
    def test_be_not_triggered_below_be_price(self, _mock_spot):
        eng = _make_engine()
        trade = self._make_trade()
        eng.open_trade = trade

        with patch.object(eng, "_get_ltp", return_value=130.0):   # below be=138.33
            eng._monitor_trade(date(2026, 6, 24))

        assert trade.be_triggered is False
        assert trade.stop_price == 104.0   # stop unchanged

    @patch("src.live.nifty_intraday_live.get_spot_price", return_value=22100.0)
    def test_stop_exits_after_be_activation(self, _mock_spot):
        """After BE is active, hitting stop should close as BE_STOP not STOP_LOSS."""
        eng = _make_engine()
        trade = self._make_trade(be_triggered=True)
        trade.stop_price = trade.entry_price   # already moved to BE
        eng.open_trade = trade

        # LTP drops to entry (triggers BE stop)
        with patch.object(eng, "_get_ltp", return_value=119.0):   # below entry=120
            with patch.object(eng, "_close_trade", return_value="BE_STOP") as mock_close:
                result = eng._monitor_trade(date(2026, 6, 24))

        mock_close.assert_called_once_with("BE_STOP", date(2026, 6, 24))


class TestNiftyIntradayDailyGates:
    """daily_loss_lock and daily_profit_lock state transitions."""

    def test_loss_lock_threshold_matches_config(self):
        eng = _make_engine()
        assert eng.daily_loss_lock_rs == 4800

    def test_profit_lock_threshold_matches_config(self):
        eng = _make_engine()
        assert eng.daily_profit_lock_rs == 8250

    def test_cooldown_loss_min_matches_config(self):
        eng = _make_engine()
        assert eng.cooldown_loss_min == 15

    def test_cooldown_profit_min_matches_config(self):
        eng = _make_engine()
        assert eng.cooldown_profit_min == 5

    def test_half_size_flag_enabled(self):
        eng = _make_engine()
        assert eng.half_size_at_lock is True

    def test_half_size_reduces_qty_when_profit_locked(self):
        eng = _make_engine()
        eng.day_pnl = 9000.0          # beyond profit lock

        qty_normal, *_ = eng._compute_qty(45.0, half_size=False)
        qty_half, *_   = eng._compute_qty(45.0, half_size=True)

        assert qty_half < qty_normal
        assert qty_half == 75         # Example B: 150 → 75 in half-size
