"""
Self-test for the PASHUPATASTRA real-time trap alerter.

Asserts the anti-spam logic and both notifier integration paths:
  1. fires once on the first qualifying trap,
  2. stays QUIET on a repeat within cooldown (no escalation),
  3. RE-fires when the score escalates by >= escalate_delta,
  4. RE-fires again after the cooldown elapses,
  5. works with a notifier exposing send_trap_alert(**kwargs) AND a plain send(text) one.

Run:  python tests/test_trap_alert.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.brahmastra.options.trap_score import TrapScore
from src.brahmastra.options.trap_alert import TrapAlerter

IST = timezone(timedelta(hours=5, minutes=30))


class _Snap:
    def __init__(self, spot): self.spot_price = spot


class _RichNotifier:
    def __init__(self): self.calls = []
    def send_trap_alert(self, **kw): self.calls.append(kw)


class _PlainNotifier:
    def __init__(self): self.msgs = []
    def send(self, text): self.msgs.append(text)


def _trap(score, side="CE"):
    return TrapScore(score=score, side=side, wall_strike=25100, candidate_strike=25150,
                     factors={"wall_ratio": 3.1, "covering": True})


def run():
    n = _RichNotifier()
    al = TrapAlerter(notifier=n, threshold=75.0, cooldown_min=15, escalate_delta=8.0)
    t0 = datetime(2026, 6, 30, 13, 0, tzinfo=IST)
    snap = _Snap(25130)

    a1 = al.on_chain("NIFTY", snap, _trap(80), t0)
    assert a1 and a1["side"] == "CE" and a1["candidate_strike"] == 25150, "first trap should fire"
    assert a1["escalation"] is False

    a2 = al.on_chain("NIFTY", snap, _trap(81), t0 + timedelta(minutes=5))
    assert a2 is None, "repeat within cooldown (no escalation) must stay quiet"

    a3 = al.on_chain("NIFTY", snap, _trap(90), t0 + timedelta(minutes=6))
    assert a3 and a3["escalation"] is True, "score jump >= escalate_delta must re-fire (escalation)"

    a4 = al.on_chain("NIFTY", snap, _trap(90), t0 + timedelta(minutes=25))
    assert a4 is not None, "after cooldown elapses, must re-fire"

    # below threshold or no side -> nothing
    assert al.on_chain("NIFTY", snap, _trap(50), t0 + timedelta(minutes=40)) is None
    assert al.on_chain("NIFTY", snap, TrapScore(0, None, None, None, {}), t0) is None

    assert len(n.calls) == 3, f"rich notifier should have 3 sends, got {len(n.calls)}"

    # plain send(text) path
    p = _PlainNotifier()
    al2 = TrapAlerter(notifier=p, threshold=75.0)
    al2.on_chain("SENSEX", _Snap(82000),
                 TrapScore(88, "PE", 82000, 81900, {"wall_ratio": 2.4, "covering": True}), t0)
    assert p.msgs and "PASHUPATASTRA" in p.msgs[0] and "81900" in p.msgs[0], "plain-send formatting"

    print(f"OK  fire/dedup/escalate/cooldown logic correct (3 rich sends)")
    print(f"OK  plain send(text) path formatted:\n      {p.msgs[0].splitlines()[0]} ...")
    print("\nALL TRAP-ALERTER TESTS PASSED")


def test_trap_alerter():        # pytest entry point
    run()


if __name__ == "__main__":
    run()
