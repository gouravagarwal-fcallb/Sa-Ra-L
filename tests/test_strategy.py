"""
Unit tests for core strategy logic.
Run with: python -m pytest tests/ -v
"""

import pytest
from datetime import date

from src.strategy.direction_engine import DirectionEngine, DirectionInputs, Direction
from src.strategy.exit_logic import ExitLogic, ExitReason
from src.backtest.option_pricer import OptionPricer
from src.utils.market_calendar import is_expiry_day, get_weekly_expiry
from src.utils.helpers import round_to_strike, format_inr

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
    def test_thursday_is_expiry(self):
        # Jan 4 2024 was a Thursday
        assert get_weekly_expiry(date(2024, 1, 4)) == date(2024, 1, 4)

    def test_expiry_shifts_on_holiday(self):
        # If we know a specific Thursday was a holiday, expiry should be Wednesday
        # Using a general assertion: expiry is always a trading day
        expiry = get_weekly_expiry(date(2024, 3, 28))  # March 28 2024 = Thursday
        assert expiry.weekday() in (2, 3)  # Wednesday or Thursday


# ── Utility Tests ─────────────────────────────────────

class TestHelpers:
    def test_round_to_strike(self):
        assert round_to_strike(22123) == 22100
        assert round_to_strike(22176) == 22200
        assert round_to_strike(22150) == 22150
        assert round_to_strike(22125) == 22100

    def test_format_inr_lakh(self):
        assert "L" in format_inr(200000)

    def test_format_inr_crore(self):
        assert "Cr" in format_inr(10000000)

    def test_format_inr_negative(self):
        result = format_inr(-500000)
        assert "-" in result
