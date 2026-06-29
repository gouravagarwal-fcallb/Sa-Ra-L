"""
Telegram setup helper — PASHUPATASTRA alerts
============================================
One command to wire up Telegram alerts. It:
  1. auto-detects your chat_id from getUpdates (no manual URL editing),
  2. creates config/settings.local.yaml from the template if missing,
  3. fills in your bot_token + chat_id (preserving the file's comments),
  4. sends a test ping so you KNOW it works.

USAGE (run on YOUR machine, from the repo root):
    python scripts/telegram_setup.py <BOT_TOKEN>
    python scripts/telegram_setup.py <BOT_TOKEN> --chat-id 123456789   # if you already know it

BEFORE running: open your bot in Telegram and tap Start / send it any message,
so getUpdates has something to read (otherwise it can't find your chat_id).
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

# Proper fix for corporate/antivirus HTTPS interception: use the OS trust store
# (where the AV's injected root cert lives). No-op if truststore isn't installed.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass


def _open(url, data=None, timeout=15):
    """urlopen that survives AV/proxy TLS interception: verified first, then a
    last-resort unverified retry (safe enough for Telegram pings / public NSE data)."""
    try:
        return urllib.request.urlopen(url, data=data, timeout=timeout)
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY" in str(e) or isinstance(getattr(e, "reason", None), ssl.SSLError):
            print("note: TLS verification failed (antivirus/proxy?) — retrying without "
                  "verification. For a clean fix run:  pip install truststore")
            return urllib.request.urlopen(url, data=data, timeout=timeout,
                                          context=ssl._create_unverified_context())
        raise

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "config", "settings.local.yaml.example")
TARGET = os.path.join(ROOT, "config", "settings.local.yaml")


def _api(token, method, params=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with _open(url, timeout=15) as r:
        return json.loads(r.read())


def fetch_chat_id(token):
    """Return the chat_id of the most recent message to the bot, or None."""
    data = _api(token, "getUpdates")
    if not data.get("ok"):
        raise RuntimeError(f"Telegram says: {data.get('description')}")
    results = data.get("result", [])
    if not results:
        return None
    for upd in reversed(results):                    # newest first
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            return str(chat["id"]), chat.get("first_name") or chat.get("title") or ""
    return None


def set_telegram(text, token, chat_id, block="telegram"):
    """Fill bot_token/chat_id/enabled inside the given notifications block
    (telegram | news_desk | signal_bot), preserving comments."""
    # fast path only applies to the default telegram block's placeholders
    if block == "telegram" and (
            "FILL_IN_BOT_TOKEN_FROM_BOTFATHER" in text or "FILL_IN_YOUR_TELEGRAM_CHAT_ID" in text):
        text = text.replace("FILL_IN_BOT_TOKEN_FROM_BOTFATHER", token)
        text = text.replace("FILL_IN_YOUR_TELEGRAM_CHAT_ID", str(chat_id))
        return text
    # line-walk the chosen block
    lines = text.splitlines()
    out, in_blk, blk_indent, found = [], False, None, False
    blk_re = re.compile(rf"^\s*{re.escape(block)}:\s*$")
    for line in lines:
        if blk_re.match(line):
            in_blk, found = True, True
            blk_indent = len(line) - len(line.lstrip())
            out.append(line)
            continue
        if in_blk:
            cur = len(line) - len(line.lstrip())
            if line.strip() and cur <= blk_indent:
                in_blk = False
            else:
                if re.match(r"^\s*bot_token:", line):
                    out.append(re.sub(r"(bot_token:\s*).*", rf'\1"{token}"', line)); continue
                if re.match(r"^\s*chat_id:", line):
                    out.append(re.sub(r"(chat_id:\s*).*", rf'\1"{chat_id}"', line)); continue
                if re.match(r"^\s*enabled:", line):
                    out.append(re.sub(r"(enabled:\s*).*", r"\1true", line)); continue
        out.append(line)
    result = "\n".join(out) + ("\n" if not text.endswith("\n") else "")
    if not found:                                    # block absent — append it under notifications
        if "notifications:" in result:
            result += (f"  {block}:\n    enabled: true\n"
                       f'    bot_token: "{token}"\n    chat_id: "{chat_id}"\n')
        else:
            result += (f"\nnotifications:\n  {block}:\n    enabled: true\n"
                       f'    bot_token: "{token}"\n    chat_id: "{chat_id}"\n')
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("token", nargs="?", help="bot token from @BotFather; prompts if omitted")
    ap.add_argument("--chat-id", help="skip auto-detect and use this chat id")
    ap.add_argument("--block", default="telegram",
                    choices=["telegram", "news_desk", "signal_bot"],
                    help="which notifications block to write: telegram (default), "
                         "news_desk (Bot 1 inbound news), signal_bot (Bot 2 trade calls)")
    ap.add_argument("--out", default=TARGET)
    ap.add_argument("--example", default=EXAMPLE)
    ap.add_argument("--no-test", action="store_true", help="don't send a test ping")
    args = ap.parse_args()

    # prompt for the token if not given on the command line (avoids the placeholder trap)
    raw = args.token
    if not raw:
        print("Get your token from @BotFather:  /mybots  ->  your bot  ->  API Token")
        raw = input("Paste your bot token here (e.g. 8970812497:AAG...): ")
    token = raw.strip().strip("<>")                  # forgive accidental brackets/spaces
    if token != raw.strip():
        print("note: stripped < > brackets from the token.")

    # validate the token BEFORE writing anything (catches placeholders / revoked tokens)
    if not re.match(r"^\d{6,}:[A-Za-z0-9_-]{20,}$", token):
        print(f"✗ '{token}' is not a bot token. That looks like the PLACEHOLDER word.\n"
              "  Paste your ACTUAL token from @BotFather — it looks like  8970812497:AAG...xyz")
        return 1
    try:
        me = _api(token, "getMe")
        if not me.get("ok"):
            print(f"✗ Telegram rejected this token: {me.get('description')}\n"
                  "  It's likely revoked or wrong. In @BotFather: /revoke for a fresh token.")
            return 1
        print(f"✓ token valid — bot @{me.get('result', {}).get('username', '?')}")
    except Exception as e:
        print(f"✗ couldn't validate token with Telegram: {e}")
        return 1

    # resolve chat_id
    chat_id, who = args.chat_id, ""
    if not chat_id:
        try:
            res = fetch_chat_id(token)
        except Exception as e:
            print(f"ERROR talking to Telegram: {e}\n"
                  f"  → check the token is correct (from @BotFather, no brackets).")
            return 1
        if not res:
            print("No messages found for this bot yet.\n"
                  "  → Open your bot in Telegram, tap Start (or send 'hi'), then re-run this.\n"
                  "  → Or pass it directly:  python scripts/telegram_setup.py <token> --chat-id <id>")
            return 1
        chat_id, who = res
    print(f"chat_id = {chat_id}" + (f"  ({who})" if who else ""))

    # write config
    if os.path.exists(args.out):
        text = open(args.out, encoding="utf-8").read()
    elif os.path.exists(args.example):
        text = open(args.example, encoding="utf-8").read()
    else:
        text = "notifications:\n  telegram:\n    enabled: true\n"
    open(args.out, "w", encoding="utf-8").write(set_telegram(text, token, chat_id, args.block))
    print(f"wrote {args.out}  ({args.block} enabled, token + chat_id filled in)")

    # test ping
    if not args.no_test:
        ping = {
            "telegram":   "✅ Sa-Ra-L alerts wired up. You'll get notifications here.",
            "news_desk":  "📰 News Desk (Bot 1) wired up. Forward any market news here and I'll analyse its impact.",
            "signal_bot": "📈 Signals bot (Bot 2) wired up. Every strategy trade call will be pushed here.",
        }.get(args.block, "✅ Sa-Ra-L alerts wired up.")
        try:
            r = _api(token, "sendMessage", {"chat_id": chat_id, "text": ping})
            if r.get("ok"):
                print("test ping SENT — check your Telegram ✅")
            else:
                print(f"test ping failed: {r.get('description')}")
        except Exception as e:
            print(f"test ping error: {e}")

    print("\nConfirm anytime with:  python scripts/test_alert.py")
    print("Then run on a trading day:  python pashupatastra_shadow.py --instruments NIFTY SENSEX --interval 90")
    return 0


if __name__ == "__main__":
    sys.exit(main())
