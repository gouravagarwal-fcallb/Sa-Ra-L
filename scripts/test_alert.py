"""
Test the PASHUPATASTRA Telegram alert pipeline — end to end.
===========================================================
Sends a realistic SAMPLE trap alert to your configured channel, through the SAME
notifier the live engine uses, and reports a clear SENT/FAILED. Run it anytime to
confirm alerts are working (after setup, or if a ping ever seems missing).

    python scripts/test_alert.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# OS trust store so antivirus/proxy TLS interception doesn't block the send
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass


def _load_settings():
    try:
        import yaml
    except Exception:
        return {}
    for p in ("config/settings.local.yaml", "config/settings.yaml"):
        if os.path.exists(p):
            with open(p) as f:
                s = yaml.safe_load(f) or {}
            if s.get("notifications"):
                return s
    return {}


def main():
    settings = _load_settings()
    if not settings.get("notifications"):
        print("✗ No notifications config found in config/settings.local.yaml.")
        print("  Run:  python scripts/telegram_setup.py YOUR_TOKEN --chat-id YOUR_CHAT_ID")
        return 1

    from src.brahmastra.notifications.notifier import BrahmastraNotifier, format_trap_alert
    n = BrahmastraNotifier(settings)
    if not n.any_enabled:
        print("✗ No channel enabled. In config/settings.local.yaml set:")
        print("    notifications:\n      telegram:\n        enabled: true")
        return 1

    msg = format_trap_alert(
        instrument="NIFTY", side="CE", score=82, spot=24056,
        wall_strike=24100, candidate_strike=24150,
        factors={"wall_ratio": 3.1, "covering": True}, time_str="TEST", escalation=False,
    )
    print("Sending a sample TRAP alert to your configured Telegram channel...\n")
    print(msg + "\n")

    ok = False
    if getattr(n, "_telegram", None):                 # definitive synchronous send + result
        ok = n._telegram.send(msg)
    if ok:
        print("✅ SENT — check your Telegram. If you see the message above, alerts are LIVE.")
        return 0
    print("✗ FAILED to send. Check, in config/settings.local.yaml:")
    print("  • telegram.enabled is true")
    print("  • bot_token is the real token from @BotFather (no < > brackets, not revoked)")
    print("  • chat_id is correct and you tapped Start / messaged the bot at least once")
    print("  • if behind antivirus/proxy:  pip install truststore")
    return 1


if __name__ == "__main__":
    sys.exit(main())
