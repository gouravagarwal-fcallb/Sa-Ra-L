"""
Send a sample TRAP_CMCD pre-entry TRIGGER alert to the configured Signals bot
(Bot 2), to verify the Telegram pipeline end-to-end. Clearly marked as a TEST.

Uses the platform's OWN outbound bot + token (from config/settings.local.yaml) —
no token is stored or printed here. Run:

    python scripts/trap_test_alert.py
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import load_configs
from src.api.telegram_bots import get_signal_bot, _send


def main() -> int:
    settings, _ = load_configs(None)
    bot = get_signal_bot(settings)
    cv = bot.config_view()
    if not cv.get("enabled"):
        print("Signals bot is NOT enabled. Configure notifications.signal_bot in "
              "config/settings.local.yaml (or run scripts/telegram_setup.py). "
              f"Current: {cv}")
        return 1

    msg = (
        "🟢 *The Wealth Fortress — TRAP\\_CMCD\\_v1 · PRE-ENTRY TRIGGER* _(TEST)_\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*NIFTY 24000 CE*  (Whale 'W')\n"
        "Price : ₹112.5   Qty: 130\n"
        "Why   : Manipulation detected — Black candles trapped in a *Buying Zone* "
        "+ whale-volume cluster; price below the *Golden Line* (VWAP) → magnet pulls up.\n"
        "SL/Tgt: 35 pts hard SL  |  25+ then *zone-to-zone* (ride to the opposite supply)\n"
        "Size  : 10% capital (Brain-Freeze guard)   ·   Mode: *PAPER* (live-blocked)\n"
        "_This is a TEST of the pre-entry alert pipeline — not a live signal._"
    )
    ok = _send(bot._token, bot._chat_id, msg)   # synchronous, deterministic
    print("Telegram accepted the message:" , ok, "→ chat", cv.get("chat_id"))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
