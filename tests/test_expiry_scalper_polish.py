"""
EXPIRY_SCALPER polish flags — breakout confirmation (#1) + partial-book/trail (#2).
════════════════════════════════════════════════════════════════════════════════
These exercise the pure decision helpers directly (no broker / no run loop), so
they are fast and deterministic. The load-bearing guarantee is the FIRST test:
with every flag OFF the engine reduces EXACTLY to the original target/stop logic,
so turning the feature on can never silently change a strategy the operator hasn't
opted into.
"""
import datetime as _dt

import pytest

from src.live.expiry_scalper_live import ExpiryScalperLive, ScalperTrade


def _engine(**es_overrides) -> ExpiryScalperLive:
    """Build an engine with a minimal config; broker/network are never touched
    because we only call the pure helpers."""
    cfg = {
        "expiry_scalper": {
            "trade_budget_rs": 10000,
            "windows": [{
                "id": "W3", "name": "EOD", "start": "14:45", "end": "15:10",
                "momentum_threshold_pct": 0.10, "otm_strikes": 1,
                "max_premium_rs": 50, "min_premium_rs": 1.0,
                "target_multiplier": 5.0, "stop_loss_pct": 50,
                "require_score_direction": False,
            }],
            **es_overrides,
        },
        "instruments": {
            "nifty":  {"lot_size": 65, "strike_step": 50},
            "sensex": {"lot_size": 20, "strike_step": 100},
        },
        "backtest": {"slippage_pct": 0.2},
        "risk": {"daily_loss_limit": 10000},
    }
    return ExpiryScalperLive(cfg, broker=None, mode="paper")


def _trade(entry=10.0, target=50.0, stop=5.0, qty=650) -> ScalperTrade:
    t = ScalperTrade(
        window_id="W3", instrument="NIFTY", direction="BULLISH", option_type="CE",
        strike=24000, expiry_str="20260101", exchange="NFO",
        entry_price=entry, entry_time="14:50", quantity=qty,
        target_price=target, stop_price=stop, is_paper=True,
    )
    t.qty_remaining = qty
    t.peak_ltp = entry
    return t


# ── #0  The safety guarantee: flags OFF ⇒ original behaviour exactly ───────────
def test_flags_off_is_pure_target_stop():
    e = _engine()  # all polish flags default OFF
    assert e.confirm_breakout is False
    assert e.partial_book is False
    assert e.trail_stop is False

    # Below target and above stop → HOLD.
    t = _trade()
    assert e._exit_action(t, 30.0) == "HOLD"
    assert t.stop_price == 5.0          # stop NEVER moved when trailing is off
    # At/above target → TARGET_HIT.
    assert e._exit_action(_trade(), 50.0) == "TARGET_HIT"
    assert e._exit_action(_trade(), 60.0) == "TARGET_HIT"
    # At/below stop → STOP_LOSS (never TRAIL, because stop stays below entry).
    assert e._exit_action(_trade(), 5.0) == "STOP_LOSS"
    assert e._exit_action(_trade(), 3.0) == "STOP_LOSS"


def test_flags_off_confirmation_fires_immediately():
    e = _engine()
    win = e.windows[0]
    # No confirmation required → the very first qualifying tick fires.
    assert e._should_fire(win, "BULLISH") is True


# ── #1  Breakout confirmation ─────────────────────────────────────────────────
def test_confirmation_requires_persistence():
    e = _engine(confirm_breakout=True, confirmation_ticks=2)
    win = e.windows[0]
    # First tick: not yet confirmed.
    assert e._should_fire(win, "BULLISH") is False
    # Second consecutive same-direction tick: confirmed.
    assert e._should_fire(win, "BULLISH") is True


def test_confirmation_resets_on_direction_flip_and_fade():
    e = _engine(confirm_breakout=True, confirmation_ticks=3)
    win = e.windows[0]
    assert e._should_fire(win, "BULLISH") is False   # count 1
    assert e._should_fire(win, "BEARISH") is False    # direction flip → count resets to 1
    assert e._should_fire(win, "BEARISH") is False    # count 2
    e._reset_confirm(win)                              # breakout faded
    assert e._should_fire(win, "BEARISH") is False     # count restarts at 1


# ── #2  Partial-book + trailing stop ──────────────────────────────────────────
def test_partial_book_triggers_once_then_holds():
    e = _engine(partial_book=True, partial_trigger_mult=1.6, trail_stop=False,
                move_stop_to_breakeven=True)
    t = _trade(entry=10.0)          # trigger at 16.0
    assert e._exit_action(t, 15.0) == "HOLD"          # below trigger
    assert e._exit_action(t, 16.0) == "PARTIAL_BOOK"  # hits trigger
    # Simulate the loop booking the partial + moving stop to breakeven.
    t.partial_done = True
    t.stop_price = t.entry_price
    # Now it should NOT partial again, and holds until target/stop.
    assert e._exit_action(t, 20.0) == "HOLD"
    # A pullback to breakeven exits as a TRAIL (protecting a gain), not a loss.
    assert e._exit_action(t, 10.0) == "TRAIL_STOP"


def test_trailing_stop_ratchets_up_only():
    e = _engine(partial_book=False, trail_stop=True, trail_pct=0.30)
    t = _trade(entry=10.0, stop=5.0)
    # Premium rises to 40 → trail = 40*0.7 = 28 (raises stop from 5 to 28).
    assert e._exit_action(t, 40.0) == "HOLD"
    assert t.stop_price == pytest.approx(28.0)
    # Premium dips to 35 → peak stays 40, trail stays 28, stop does NOT drop.
    assert e._exit_action(t, 35.0) == "HOLD"
    assert t.stop_price == pytest.approx(28.0)
    # Falls through the trailed stop → TRAIL_STOP (stop 28 ≥ entry 10 → a win).
    assert e._exit_action(t, 27.0) == "TRAIL_STOP"


def test_target_beats_partial_and_trail():
    e = _engine(partial_book=True, partial_trigger_mult=1.6, trail_stop=True)
    t = _trade(entry=10.0, target=50.0)
    # A jump straight past target books the full target, not a partial.
    assert e._exit_action(t, 50.0) == "TARGET_HIT"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
