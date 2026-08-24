"""
BRAHMASTRA telemetry fix — regression test.

BRAHMASTRA read as "Market closed · 0 analysis cycles" in the daily closure report
for two sessions because:
  1. its state was registered via multi.register() with persist_name=None, so its
     log trail was never mirrored to logs/dashboard/<name>_<date>.jsonl (the file the
     closure report counts), and
  2. its per-tick scan line was logged as category DECISION, which the closure
     counter (ANALYSIS/SIGNAL/ANALYSE) doesn't tally.

These tests lock both halves.
"""
from src.api.state_registry import MultiStrategyState, MARKET_SLOT
from src.brahmastra.api.state import BrahmastraState


def test_register_stamps_persist_name():
    m = MultiStrategyState()
    s = BrahmastraState()                       # the get_state() singleton: persist_name=None
    assert getattr(s, "_persist_name", None) is None
    m.register("BRAHMASTRA_v1", s)
    assert s._persist_name == "BRAHMASTRA_v1"   # now its trail mirrors to disk


def test_register_never_persists_the_market_slot():
    m = MultiStrategyState()
    s = BrahmastraState()
    m.register(MARKET_SLOT, s)
    assert getattr(s, "_persist_name", None) is None


def test_register_does_not_clobber_existing_persist_name():
    m = MultiStrategyState()
    s = BrahmastraState(persist_name="ALREADY")
    m.register("BRAHMASTRA_v1", s)
    assert s._persist_name == "ALREADY"


def test_scan_line_uses_analyse_category_so_it_is_counted():
    # The closure counter tallies ANALYSIS/SIGNAL/ANALYSE. BRAHMASTRA's per-tick scan
    # line must be emitted as ANALYSE (not DECISION) so it registers as a cycle.
    import inspect
    from src.brahmastra.brahmastra_live import BrahmastraLive
    src = inspect.getsource(BrahmastraLive._log_scan_state)
    assert "self.log.analyse(" in src, "the SCANNING scan line must be an ANALYSE cycle"
    assert "SCANNING |" in src
