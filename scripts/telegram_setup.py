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
    if not found:                                    # block absent — insert INSIDE notifications
        blk = (f"  {block}:\n    enabled: true\n"
               f'    bot_token: "{token}"\n    chat_id: "{chat_id}"\n')
        m = re.search(r"(?m)^notifications:[ \t]*\n", result)
        if m:
            # Insert right after the notifications: header. Appending at end-of-file
            # (the old behaviour) corrupted the YAML whenever another top-level
            # section (broker:, scout_mode:, …) followed notifications.
            result = result[:m.end()] + blk + result[m.end():]
        else:
            result += f"\nnotifications:\n{blk}"
    return result


def _mask(t):
    t = str(t or "")
    return (t[:6] + "…" + t[-3:]) if len(t) > 12 else ("(set)" if t else "(empty)")


def _mask_secrets(s: str) -> str:
    """Mask anything credential-shaped so broken-config excerpts are safe to share."""
    s = re.sub(r"\d{6,}:[A-Za-z0-9_-]{20,}", "***TOKEN***", s)
    s = re.sub(r"((?:api_secret|api_key|password|totp_secret|access_token|request_token)"
               r"\s*:\s*).+", r"\1***HIDDEN***", s)
    return s


def _yaml_error_report(path, err) -> None:
    """settings.local.yaml won't parse — show WHERE (secrets masked) + how to fix."""
    mark = getattr(err, "problem_mark", None) or getattr(err, "context_mark", None)
    line_no = (mark.line + 1) if mark else None
    print(f"\n  ✗ {path} is NOT valid YAML — the app cannot read it at all.")
    print("    Until fixed, the dashboard may start WITHOUT Kite credentials or bots.")
    print(f"    parser: {getattr(err, 'problem', err)}" + (f"  (line {line_no})" if line_no else ""))
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception:
        return
    lo, hi = (max(0, line_no - 4), min(len(lines), line_no + 3)) if line_no \
        else (max(0, len(lines) - 8), len(lines))
    print("\n    Offending area (secrets masked — SAFE to copy/paste when asking for help):")
    for i in range(lo, hi):
        marker = ">>" if (line_no and i == line_no - 1) else "  "
        print(f"     {marker} {i + 1:>3} | {_mask_secrets(lines[i])}")
    print("""
    How to fix (open the file in Notepad):
      • Bot blocks live UNDER `notifications:` — block names (telegram / news_desk /
        signal_bot) indented exactly 2 spaces, their keys (enabled / bot_token /
        chat_id) exactly 4 spaces. Spaces only, never Tab. Like this:

          notifications:
            telegram:
              enabled: true
              bot_token: "867301...your-token..."
              chat_id: "7381789793"
            news_desk:
              enabled: true
              bot_token: "857808...your-token..."
              chat_id: "7381789793"

      • Most likely cause here: a news_desk/signal_bot block was appended at the END
        of the file in the wrong place (an old bug in this script — now fixed).
        DELETE those stray lines (enabled/bot_token/chat_id under a lone news_desk:
        or signal_bot: at the bottom), save, re-run this doctor, then re-run the
        setup command — it now inserts the block in the right place.""")


def _load_merged():
    """Load config the SAME way the app does: settings.yaml ⊕ settings.local.yaml."""
    import yaml
    base_p = os.path.join(ROOT, "config", "settings.yaml")
    local_p = TARGET
    settings = {}
    if os.path.exists(base_p):
        settings = yaml.safe_load(open(base_p, encoding="utf-8")) or {}
    if os.path.exists(local_p):
        try:
            local = yaml.safe_load(open(local_p, encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            _yaml_error_report(local_p, e)
            sys.exit(1)

        def _merge(a, b):
            for k, v in b.items():
                if isinstance(v, dict) and isinstance(a.get(k), dict):
                    _merge(a[k], v)
                else:
                    a[k] = v
            return a
        settings = _merge(settings, local)
    return settings, os.path.exists(local_p)


def doctor():
    """Diagnose why a bot is enabled:false / not receiving — without printing secrets.
    Checks the merged config, token format, live getMe, and a set webhook (which 409s
    the News Desk long-poll)."""
    settings, has_local = _load_merged()
    notif = (settings.get("notifications", {}) or {})
    print("\n  Sa-Ra-L · Telegram config doctor")
    print("  ─────────────────────────────────")
    print(f"  config/settings.local.yaml present: {'yes' if has_local else 'NO — create it (copy the .example)'}")
    news_token = (notif.get("news_desk", {}) or {}).get("bot_token", "")

    def check(block, label, inbound=False):
        cfg = notif.get(block)
        print(f"\n  {label}  [notifications.{block}]")
        if not isinstance(cfg, dict):
            print(f"    ✗ block MISSING. Add it — run:  python scripts/telegram_setup.py <TOKEN> --block {block}")
            return
        en = bool(cfg.get("enabled"))
        tok = str(cfg.get("bot_token") or "")
        cid = str(cfg.get("chat_id") or "")
        tok_ok = bool(re.match(r"^\d{6,}:[A-Za-z0-9_-]{20,}$", tok))
        placeholder = tok.startswith("FILL_IN")
        print(f"    enabled : {en}")
        print(f"    token   : {_mask(tok)}  " + (
            "✗ PLACEHOLDER — paste the real token" if placeholder else
            "✓ valid format" if tok_ok else "✗ missing / malformed" if tok else "✗ empty"))
        print(f"    chat_id : {cid or '(empty)'}  " + ("✓" if cid else "✗ needed to reply / publish"))
        # The exact condition the app uses to enable each bot:
        app_enabled = en and bool(tok)
        if block == "signal_bot":
            if tok and news_token and tok == news_token:
                print("    ✗ CONFLICT: same token as News Desk — the two bots MUST be different bots.")
                app_enabled = False
        print(f"    ➜ app will treat this bot as: {'ENABLED' if app_enabled else 'DISABLED'}")
        if not app_enabled:
            if not en:
                print("      fix: set  enabled: true  in settings.local.yaml (or re-run the setup helper).")
            if not tok or placeholder or not tok_ok:
                print(f"      fix: run  python scripts/telegram_setup.py <TOKEN> --block {block}")
            return
        # Live checks — only if it looks configured.
        try:
            me = _api(tok, "getMe")
            if me.get("ok"):
                print(f"    live    : ✓ reachable — bot @{me.get('result', {}).get('username', '?')}")
            else:
                print(f"    live    : ✗ Telegram rejected token: {me.get('description')}")
                return
        except Exception as e:
            print(f"    live    : ✗ couldn't reach Telegram: {str(e)[:100]}")
            return
        if inbound:
            try:
                wh = _api(tok, "getWebhookInfo")
                url = (wh.get("result", {}) or {}).get("url", "")
                if url:
                    print(f"    webhook : ✗ a webhook is SET ({url[:40]}…) — this 409s the News Desk long-poll.")
                    print(f"      fix: python scripts/telegram_setup.py {'<TOKEN>'} --block {block}  (the app also auto-clears this on start),")
                    print("           or manually:  https://api.telegram.org/bot<TOKEN>/deleteWebhook")
                else:
                    print("    webhook : ✓ none set (long-poll can receive)")
            except Exception:
                pass

    check("news_desk", "Bot 1 — News Desk (inbound)", inbound=True)
    check("signal_bot", "Bot 2 — Signals (outbound)")
    check("telegram", "Legacy general-alert bot (signal_bot fallback)")
    print("\n  Reminder: the two bots must be DIFFERENT @BotFather bots with DIFFERENT tokens.\n")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("token", nargs="?", help="bot token from @BotFather; prompts if omitted")
    ap.add_argument("--chat-id", help="skip auto-detect and use this chat id")
    ap.add_argument("--block", default="telegram",
                    choices=["telegram", "news_desk", "signal_bot"],
                    help="which notifications block to write: telegram (default), "
                         "news_desk (Bot 1 inbound news), signal_bot (Bot 2 trade calls)")
    ap.add_argument("--doctor", action="store_true",
                    help="diagnose why a bot is disabled / not receiving (no token needed; prints no secrets)")
    ap.add_argument("--out", default=TARGET)
    ap.add_argument("--example", default=EXAMPLE)
    ap.add_argument("--no-test", action="store_true", help="don't send a test ping")
    args = ap.parse_args()

    if args.doctor:
        return doctor()

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
    new_text = set_telegram(text, token, chat_id, args.block)
    # NEVER write a config the app can't read back — validate BEFORE touching the file.
    import yaml
    try:
        yaml.safe_load(new_text)
    except yaml.YAMLError as e:
        print(f"✗ NOT written — the update would make {os.path.basename(args.out)} unreadable YAML.")
        try:
            yaml.safe_load(text)
            print("  Your current file is untouched and still fine. This is a script bug — report it.")
        except yaml.YAMLError as cur:
            print("  Your CURRENT file is ALREADY broken — fix that first. Run:")
            print("    python scripts/telegram_setup.py --doctor")
            _yaml_error_report(args.out, cur)
        return 1
    if os.path.exists(args.out):
        import shutil
        shutil.copyfile(args.out, args.out + ".bak")
    open(args.out, "w", encoding="utf-8").write(new_text)
    print(f"wrote {args.out}  ({args.block} enabled; backup saved as settings.local.yaml.bak)")

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
