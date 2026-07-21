"""
ATM_PULSE late-start warmup — reconstructs today's opening range from the market
instead of sitting out.

`_warmup_from_backfill()` is the pure seam (it just consumes a warmup dict from the
BackfillManager), so we monkeypatch BackfillManager.compute_warmup_state to return a
known state and assert the engine seeds its ORB / EMA / VWAP / bar_count correctly.
No network / Kite needed.
"""
import pytest

import src.data.backfill as bf_mod
from src.live.atm_pulse_burst_live import ATMPulseBurstLive


def _engine():
    cfg = {
        "strategy_type": "atm_pulse_burst",
        "atm_pulse_burst": {"orb_minutes": 15, "ema_short": 9, "ema_long": 21},
        "instruments": {"nifty": {"lot_size": 65, "strike_step": 50}},
        "backtest": {"slippage_pct": 0.5, "risk_free_rate": 0.065},
        "risk": {},
    }
    return ATMPulseBurstLive(cfg, broker=None, mode="paper")


def test_warmup_locks_orb_on_late_start(monkeypatch):
    """Started ~30 min late → warmup returns a LOCKED ORB → engine adopts it and is
    ready to trade (no more ORB building)."""
    warm = {
        "bar_count": 30, "orb_locked": True, "orb_high": 24300.0, "orb_low": 24250.0,
        "ema9": 24280.0, "ema21": 24270.0, "vwap": 24275.0,
        "bars": [{"c": 24260.0 + i, "h": 24261.0 + i, "l": 24259.0 + i, "t": "09:%02d" % (15 + i)} for i in range(30)],
    }
    monkeypatch.setattr(bf_mod.BackfillManager, "compute_warmup_state",
                        lambda self, inst, sc: warm)
    e = _engine()
    e.instrument = "NIFTY"
    assert e.orb_locked is False and e._bar_count == 0     # fresh engine
    e._warmup_from_backfill()
    assert e.orb_locked is True
    assert e.orb_high == 24300.0 and e.orb_low == 24250.0
    assert e._bar_count == 30
    assert e._ema9 == 24280.0 and e._ema21 == 24270.0
    assert e._vwap == 24275.0 and e._vwap_cnt == 30


def test_warmup_partial_orb_continues_building(monkeypatch):
    """Started ~8 min late → ORB not yet locked → seed the partial bars so it locks
    correctly as live ticks carry it past 09:30."""
    warm = {
        "bar_count": 8, "orb_locked": False, "orb_high": 0.0, "orb_low": 0.0,
        "ema9": 24280.0, "ema21": 24270.0, "vwap": 24275.0,
        "bars": [{"c": 24260.0 + i, "h": 24261.0 + i, "l": 24259.0 + i, "t": "09:%02d" % (15 + i)} for i in range(8)],
    }
    monkeypatch.setattr(bf_mod.BackfillManager, "compute_warmup_state",
                        lambda self, inst, sc: warm)
    e = _engine()
    e.instrument = "NIFTY"
    e._warmup_from_backfill()
    assert e.orb_locked is False
    assert len(e._orb_bars) == 8          # partial bars seeded
    assert e._bar_count == 8


def test_warmup_noop_before_open(monkeypatch):
    """Started before the open → no elapsed bars → warmup is a clean no-op (build live)."""
    monkeypatch.setattr(bf_mod.BackfillManager, "compute_warmup_state",
                        lambda self, inst, sc: {"bar_count": 0, "bars": []})
    e = _engine()
    e.instrument = "NIFTY"
    e._warmup_from_backfill()
    assert e.orb_locked is False and e._bar_count == 0


def test_warmup_survives_backfill_failure(monkeypatch):
    """Data source down → warmup must not crash the engine (degrade to building live)."""
    def _boom(self, inst, sc):
        raise RuntimeError("no data feed")
    monkeypatch.setattr(bf_mod.BackfillManager, "compute_warmup_state", _boom)
    e = _engine()
    e.instrument = "NIFTY"
    e._warmup_from_backfill()          # must not raise
    assert e._bar_count == 0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
