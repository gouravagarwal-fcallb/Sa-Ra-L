"""
Sa-Ra-L — Two Telegram bots
═══════════════════════════
The platform runs TWO independent Telegram bots (the user's design):

  • Bot 1  "News Desk"  (INBOUND)  — you forward a news item / headline to the bot;
    it analyses the likely market impact, says which strategies are affected and how,
    replies to you, AND stores the analysis so the dashboard's Market-News panel can
    show it.  Long-polls getUpdates in a background thread.

  • Bot 2  "Signals"    (OUTBOUND) — every time ANY of the 14 strategies generates a
    trade call (entry / exit), the call is pushed to the signals chat with its
    rationale.  Hooked centrally in the runner's trade-event mirror.

Config (config/settings.local.yaml — gitignored):

    notifications:
      news_desk:                 # Bot 1
        enabled: true
        bot_token: "<bot1 token>"
        chat_id:   "<your chat id>"     # who may talk to the desk / gets replies
      signal_bot:                # Bot 2
        enabled: true
        bot_token: "<bot2 token>"
        chat_id:   "<your chat id>"     # where trade calls are pushed
      telegram:                  # legacy general-alert bot — signal_bot falls back to it

Both bots use only the Python stdlib (urllib) — no extra dependency — and survive
AV/proxy TLS interception the same way the existing notifier does.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


# ── Low-level Telegram I/O (stdlib only, TLS-interception tolerant) ───────────
def _tg_call(token: str, method: str, params: dict, timeout: int = 35) -> dict:
    url  = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        resp = urllib.request.urlopen(url, data=data, timeout=timeout)
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY" in str(e) or isinstance(getattr(e, "reason", None), ssl.SSLError):
            resp = urllib.request.urlopen(url, data=data, timeout=timeout,
                                          context=ssl._create_unverified_context())
        else:
            raise
    with resp:
        return json.loads(resp.read())


def _send(token: str, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    if not token or not chat_id:
        return False
    try:
        r = _tg_call(token, "sendMessage",
                     {"chat_id": str(chat_id), "text": text, "parse_mode": parse_mode},
                     timeout=15)
        return bool(r.get("ok"))
    except Exception as e:
        print(f"[telegram] send failed: {str(e)[:120]}")
        return False


# ── News-impact analyser (heuristic; LLM-optional) ───────────────────────────
# Each theme: keywords → market bias (+ bullish / − bearish), the strategies it most
# affects, and a one-line "how it plays out" note. Curated for the 14-strategy book.
_THEMES = [
    {"kw": ["rate cut", "repo cut", "dovish", "cuts rate", "easing", "liquidity boost", "stimulus"],
     "bias": +2, "tag": "Dovish / rate-cut",
     "affects": ["NIFTY_INTRADAY", "ATM_PULSE_BURST", "BB_EXPIRY_SCALPER"],
     "note": "Risk-on. Supports CE momentum and trend-following; favour long-side setups."},
    {"kw": ["rate hike", "repo hike", "hawkish", "raises rate", "tightening", "higher for longer"],
     "bias": -2, "tag": "Hawkish / rate-hike",
     "affects": ["BLACK_SWAN", "VIX_SELLER", "RANGE_SCALPER"],
     "note": "Risk-off. Vol expands — favour hedges; fade-strategies get riskier."},
    {"kw": ["inflation", "cpi", "wpi", "price rise", "deflation"],
     "bias": -1, "tag": "Inflation print",
     "affects": ["BLACK_SWAN", "NIFTY_INTRADAY"],
     "note": "Hotter-than-expected CPI pressures rates outlook → caution; cooler is bullish."},
    {"kw": ["crude", "oil price", "opec", "brent", "wti"],
     "bias": -1, "tag": "Crude oil",
     "affects": ["NIFTY_INTRADAY", "INRUSD_v1"],
     "note": "Higher crude = import-bill / rupee headwind for India (bearish); lower is supportive."},
    {"kw": ["war", "attack", "geopolit", "conflict", "missile", "border", "tension", "sanction"],
     "bias": -2, "tag": "Geopolitical risk",
     "affects": ["BLACK_SWAN", "VIX_SELLER"],
     "note": "Tail-risk spike — VIX up, gap risk. BLACK_SWAN arms; size down everywhere."},
    {"kw": ["fii", "fpi", "foreign investor", "outflow", "inflow", "dii"],
     "bias": 0, "tag": "Institutional flows",
     "affects": ["NIFTY_INTRADAY", "RAMS_v1"],
     "note": "Sustained FII buying supports the index; heavy selling pressures it. Check direction."},
    {"kw": ["fed", "fomc", "powell", "us jobs", "nonfarm", "nfp", "jobless"],
     "bias": 0, "tag": "US macro / Fed",
     "affects": ["NIFTY_INTRADAY", "BLACK_SWAN"],
     "note": "Drives overnight US lead + FII risk appetite — sets the gap, watch direction."},
    {"kw": ["results", "earnings", "profit", "q1", "q2", "q3", "q4", "guidance", "miss", "beat"],
     "bias": 0, "tag": "Earnings",
     "affects": ["ATM_PULSE_BURST", "NIFTY_INTRADAY"],
     "note": "Heavyweight results swing the index — beats lift, misses drag; expect single-stock vol."},
    {"kw": ["rbi", "monetary policy", "mpc", "shaktikanta", "governor"],
     "bias": 0, "tag": "RBI policy",
     "affects": ["NIFTY_INTRADAY", "BLACK_SWAN", "VIX_SELLER"],
     "note": "Policy-day event risk — entries blocked in the 30-min window pre-decision; vol after."},
    {"kw": ["budget", "fiscal", "government spend", "disinvest"],
     "bias": +1, "tag": "Budget / fiscal",
     "affects": ["NIFTY_INTRADAY", "ATM_PULSE_BURST"],
     "note": "Budget-day whipsaw then trend — capex/positive surprises are bullish for the index."},
    {"kw": ["rally", "surge", "record high", "all-time high", "jumps", "soars", "gains"],
     "bias": +1, "tag": "Bullish momentum",
     "affects": ["NIFTY_INTRADAY", "ATM_PULSE_BURST", "TREND_RIDER"],
     "note": "Momentum tailwind — trend / CE-momentum strategies favoured."},
    {"kw": ["crash", "selloff", "plunge", "tumbles", "slumps", "sinks", "falls sharply", "rout"],
     "bias": -2, "tag": "Bearish momentum",
     "affects": ["BLACK_SWAN", "VIX_SELLER", "GAP_FADE"],
     "note": "Sharp down move — gap/extreme strategies engage; avoid bottom-fishing CE early."},
]


def analyze_news_impact(text: str) -> dict:
    """Rule-based market-impact read of a forwarded news item. Returns a structured
    verdict {sentiment, score, themes[], affected[], notes[], summary}. If an
    Anthropic key is configured the caller may swap in an LLM, but this heuristic is
    the always-available default (no network dependency, no key needed)."""
    t = (text or "").lower()
    hits, affected, notes, score = [], [], [], 0
    for theme in _THEMES:
        if any(k in t for k in theme["kw"]):
            hits.append(theme["tag"])
            notes.append(f"{theme['tag']}: {theme['note']}")
            affected.extend(theme["affects"])
            score += theme["bias"]

    # direction nudges from explicit up/down words when a flow/macro theme is neutral
    if any(w in t for w in ("buy", "inflow", "buying", "positive", "upgrade")):
        score += 1
    if any(w in t for w in ("sell", "outflow", "selling", "negative", "downgrade", "cut to")):
        score -= 1

    sentiment = ("BULLISH" if score >= 2 else "STRONGLY BEARISH" if score <= -3
                 else "BEARISH" if score <= -1 else "NEUTRAL / MIXED")
    affected = list(dict.fromkeys(affected))   # dedupe, keep order
    if not hits:
        summary = ("No strong, recognised market driver detected in this item. "
                   "Logged for context — monitor for follow-through.")
    else:
        summary = (f"{sentiment} tilt. Drivers: {', '.join(hits)}. "
                   f"Most affected: {', '.join(affected[:5]) if affected else '—'}.")
    return {"sentiment": sentiment, "score": score, "themes": hits,
            "affected": affected, "notes": notes, "summary": summary}


def _format_news_reply(text: str, verdict: dict) -> str:
    icon = {"BULLISH": "🟢", "NEUTRAL / MIXED": "🟡",
            "BEARISH": "🔴", "STRONGLY BEARISH": "🔴🔴"}.get(verdict["sentiment"], "🟡")
    lines = [f"{icon} *News Desk — impact read*",
             "━━━━━━━━━━━━━━━━━━━━",
             f"_{text.strip()[:240]}_",
             "",
             f"*Verdict:* {verdict['sentiment']}  (score {verdict['score']:+d})"]
    if verdict["themes"]:
        lines.append(f"*Drivers:* {', '.join(verdict['themes'])}")
    if verdict["affected"]:
        lines.append(f"*Strategies in focus:* {', '.join(verdict['affected'][:6])}")
    if verdict["notes"]:
        lines.append("")
        lines += [f"• {n}" for n in verdict["notes"][:4]]
    lines += ["", "_Advisory only — every strategy keeps its own discipline._"]
    return "\n".join(lines)


def _format_impact_reply(ni) -> str:
    """Rich reply for a structured NewsImpact (the production News Desk path)."""
    icon = {"BULLISH": "🟢", "NEUTRAL / MIXED": "🟡",
            "BEARISH": "🔴", "STRONGLY BEARISH": "🔴🔴"}.get(ni.expected_direction, "🟡")
    usable = "✅ actionable context" if ni.usable_for_trading else "ℹ context only"
    lines = [f"{icon} *Sa-Ra-L News Desk — impact read*",
             "━━━━━━━━━━━━━━━━━━━━",
             f"_{ni.extracted_text.strip()[:240]}_", "",
             f"*Class:* {ni.classification}   *Dir:* {ni.expected_direction}",
             f"*Strength:* {ni.impact_strength}   *Horizon:* {ni.horizon}",
             f"*Relevance:* {ni.relevance_score}   *Trust:* {ni.trust_score}   *Conf:* {ni.confidence}",
             f"*Affected:* {', '.join(ni.affected_instruments[:6]) or '—'}"]
    if ni.risk_flags:
        lines.append("*Risk:* " + "; ".join(ni.risk_flags))
    lines.append(f"\n{usable}")
    if ni.strategy_context_adjustment:
        p = ni.strategy_context_adjustment
        lines.append(f"➜ context note proposed: *{p['effect']} = {p['value']}* (expires automatically)")
    lines += ["", "_News Desk never places trades — at most an advisory, expiring context note._"]
    return "\n".join(lines)


# ── Shared news store (so the dashboard Market-News panel can read desk output) ──
class _NewsStore:
    def __init__(self, max_items: int = 50):
        self._items: list[dict] = []
        self._max = max_items
        self._lock = threading.Lock()
        self._path = os.path.join("logs", "news_desk",
                                  f"{datetime.now(IST).date().isoformat()}.jsonl")
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self._items.append(json.loads(line))
                self._items = self._items[-self._max:]
        except Exception:
            pass

    def add(self, item: dict) -> None:
        with self._lock:
            self._items.append(item)
            self._items = self._items[-self._max:]
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, default=str) + "\n")
        except Exception:
            pass

    def recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            return list(reversed(self._items[-n:]))


# ── Bot 1: News Desk (inbound long-poll) ─────────────────────────────────────
class NewsDesk:
    def __init__(self, settings: dict):
        cfg = (settings.get("notifications", {}) or {}).get("news_desk", {}) or {}
        self._enabled = bool(cfg.get("enabled")) and bool(cfg.get("bot_token"))
        self._token   = cfg.get("bot_token", "")
        self._chat_id = str(cfg.get("chat_id", "")) if cfg.get("chat_id") else ""
        self.store    = _NewsStore()
        self.impacts: list[dict] = []   # structured NewsImpact records (bots panel)
        self._offset  = 0
        self._stop    = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if not self._enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="news-desk", daemon=True)
        self._thread.start()
        print("  ✓ News Desk (Bot 1) listening for forwarded news.")

    def stop(self) -> None:
        self._stop.set()

    def _poll_loop(self) -> None:
        # Drain any backlog first so we only answer NEW messages from now on.
        try:
            init = _tg_call(self._token, "getUpdates", {"timeout": 0, "offset": -1}, timeout=15)
            for upd in init.get("result", []):
                self._offset = max(self._offset, upd.get("update_id", 0) + 1)
        except Exception:
            pass

        while not self._stop.is_set():
            try:
                data = _tg_call(self._token, "getUpdates",
                                {"timeout": 25, "offset": self._offset}, timeout=35)
                for upd in data.get("result", []):
                    self._offset = max(self._offset, upd.get("update_id", 0) + 1)
                    self._handle(upd)
            except Exception as e:
                print(f"[news-desk] poll error: {str(e)[:100]}")
                self._stop.wait(5)

    def _handle(self, upd: dict) -> None:
        from src.api.bot_core import audit, Cat
        msg  = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid  = str(chat.get("id", ""))
        text = msg.get("text") or msg.get("caption") or ""
        has_photo = bool(msg.get("photo"))
        has_doc   = bool(msg.get("document"))
        if not text and not has_photo and not has_doc:
            return
        # If a chat_id is configured, only serve that chat (ignore strangers).
        if self._chat_id and cid and cid != self._chat_id:
            return
        source_type = ("forwarded" if (msg.get("forward_date") or msg.get("forward_origin")) else
                       "photo" if has_photo else "document" if has_doc else
                       "link" if "http" in text else "text")
        # 1) Persist/audit the RAW inbound BEFORE any processing (Part 4B step 2).
        audit(Cat.INBOUND, "news_desk", "received", "ok", text[:120],
              ref=str(msg.get("message_id", "")), meta={"source_type": source_type, "chat": cid})

        if text.strip().lower() in ("/start", "/help"):
            _send(self._token, cid or self._chat_id,
                  "📰 *Sa-Ra-L News Desk* ready. Forward or paste any market news, headline, "
                  "link or screenshot caption and I'll classify it, score relevance/trust and "
                  "tell you the likely impact + which strategies it touches.\n\n"
                  "_I never place trades — at most I add an advisory, expiring context note._")
            return

        try:
            from src.api.news_pipeline import build_news_impact, impact_to_dict
            ni = build_news_impact(message_id=msg.get("message_id", ""), submitted_by=cid,
                                   source_type=source_type, body=msg.get("text", ""),
                                   caption=msg.get("caption", ""), tags=[])
            audit(Cat.ANALYSIS, "news_desk", "impact", ni.status, ni.engine_summary, ref=ni.impact_id)
            d = impact_to_dict(ni)
            self.impacts.append(d)
            self.impacts = self.impacts[-50:]
            # keep the legacy Market-News panel fed
            self.store.add({
                "ts": datetime.now(IST).strftime("%H:%M:%S"),
                "date": datetime.now(IST).date().isoformat(),
                "text": ni.extracted_text[:400], "sentiment": ni.expected_direction,
                "score": 0, "themes": [ni.classification], "affected": ni.affected_instruments,
                "summary": ni.analyst_summary, "source": "news_desk",
            })
            # 2) Controlled, gated context adjustment — NEVER a trade (Part 6).
            if ni.usable_for_trading and ni.strategy_context_adjustment:
                from src.api.strategy_context import get_context
                p = ni.strategy_context_adjustment
                get_context().add(p["effect"], p["value"], reason=ni.analyst_summary,
                                  source_impact_id=ni.impact_id, confidence=ni.confidence)
            _send(self._token, cid or self._chat_id, _format_impact_reply(ni))
        except Exception as e:
            audit(Cat.ERROR, "news_desk", "pipeline", "error", str(e)[:160])
            _send(self._token, cid or self._chat_id,
                  "⚠ Couldn't fully analyse that submission — it's logged for review.")


# ── Bot 2: Sa-Ra-L Trade Signals (outbound publishing gateway) ───────────────
class SignalBot:
    """Outbound-only publishing gateway. Every publication goes through the
    DeliveryQueue (audit → persisted DeliveryRecord → rate-limited send → retry/
    dead-letter), so nothing bypasses logging and Telegram failure can't break the
    engine. Publishes to the configured chat/channel (e.g. the 'Test TWF' channel)."""

    def __init__(self, settings: dict):
        notif = settings.get("notifications", {}) or {}
        cfg   = notif.get("signal_bot", {}) or {}
        legacy = notif.get("telegram", {}) or {}   # fall back to the general bot
        self._token   = cfg.get("bot_token") or legacy.get("bot_token", "")
        # chat_id may be a numeric id, a -100… channel id, or an @channelusername
        self._chat_id = str(cfg.get("chat_id") or cfg.get("channel") or legacy.get("chat_id", "") or "")
        self._enabled = bool(cfg.get("enabled", notif.get("telegram", {}).get("enabled", False))) \
            and bool(self._token) and bool(self._chat_id)
        self.store = None
        self._queue = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _ensure_queue(self):
        if self._queue is None and self._enabled:
            from src.api.bot_core import DeliveryStore, DeliveryQueue
            self.store = DeliveryStore()

            def _send_fn(chat_id, text):
                try:
                    r = _tg_call(self._token, "sendMessage",
                                 {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                                 timeout=15)
                    if r.get("ok"):
                        return True, (r.get("result", {}) or {}).get("message_id")
                    return False, r.get("description", "send failed")
                except Exception as e:
                    return False, str(e)[:160]

            self._queue = DeliveryQueue(_send_fn, self.store, default_chat_id=self._chat_id)
            self._queue.start()
        return self._queue

    def start(self):
        """Spin up the delivery queue/worker so the store exists for status reads."""
        self._ensure_queue()

    def stop(self):
        if self._queue:
            self._queue.stop()

    def publish(self, pub_type: str, text: str, *, dedupe_key: str = "",
                title: str = "", priority: int = 5):
        """Queue a publication for reliable delivery. Returns event_id or None."""
        q = self._ensure_queue()
        if not q:
            return None
        return q.enqueue(pub_type, text, dedupe_key=dedupe_key, title=title, priority=priority)

    def send_trade_call(self, strategy: str, ev: dict) -> None:
        """Publish one trade call (entry/exit) with its rationale. Non-blocking."""
        if not self._enabled:
            return
        try:
            from src.api.bot_core import PubType
            etype = (ev.get("event") or "TRADE").upper()
            inst, strike, opt = ev.get("instrument", ""), ev.get("strike", ""), ev.get("option_type", "")
            side, price, qty = ev.get("direction", ""), ev.get("price", ""), ev.get("quantity", "")
            pnl = ev.get("pnl", None)
            reason = ev.get("reason") or ev.get("rationale") or ev.get("signal") or ""
            mode = ev.get("mode", "")
            is_entry = etype in ("ENTRY", "ENTERED", "BUY", "SELL")
            icon = "🟢" if is_entry else ("✅" if (pnl is None or _num(pnl) >= 0) else "❌")
            lines = [f"{icon} *The Wealth Fortress — {strategy} {etype}*",
                     "━━━━━━━━━━━━━━━━━━━━",
                     f"{inst} {strike}{opt} {side}".strip(),
                     f"Price : ₹{price}" + (f"   Qty: {qty}" if qty != "" else "")]
            if pnl is not None and not is_entry:
                lines.append(f"P&L   : ₹{_fmt(pnl)}")
            if reason:
                lines.append(f"Why   : {str(reason)[:180]}")
            if mode:
                lines.append(f"_({mode})_")
            pub = PubType.TRADE_ENTRY if is_entry else PubType.TRADE_EXIT
            dk = f"{strategy}-{etype}-{ev.get('time','')}-{strike}-{opt}"
            self.publish(pub, "\n".join(lines), dedupe_key=dk,
                         title=f"{strategy} {etype}", priority=2)
        except Exception as e:
            print(f"[signal-bot] queue failed: {str(e)[:120]}")

    def publish_report(self, title: str, body: str, pub_type: str = "DAILY_CLOSURE"):
        """Publish a session/closure report summary (deduped per day per type)."""
        if not self._enabled:
            return
        from src.api.bot_core import PubType  # noqa
        text = f"📋 *The Wealth Fortress — {title}*\n━━━━━━━━━━━━━━━━━━━━\n{body}"
        self.publish(pub_type, text, dedupe_key=f"{pub_type}-{datetime.now(IST).date()}",
                     title=title, priority=4)

    def publish_alert(self, title: str, body: str, risk: bool = False):
        if not self._enabled:
            return
        from src.api.bot_core import PubType
        icon = "🛑" if risk else "⚠️"
        self.publish(PubType.RISK_ALERT if risk else PubType.SYSTEM_ALERT,
                     f"{icon} *{title}*\n{body}", title=title, priority=1)


def _num(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def _fmt(x):
    try:
        return f"{float(x):+.0f}"
    except Exception:
        return str(x)


# ── Singletons (one of each per process) ─────────────────────────────────────
_signal_bot: SignalBot | None = None
_news_desk: NewsDesk | None = None
_sig_lock = threading.Lock()


def get_signal_bot(settings: dict) -> SignalBot:
    global _signal_bot
    with _sig_lock:
        if _signal_bot is None:
            _signal_bot = SignalBot(settings or {})
        return _signal_bot


def get_news_desk(settings: dict) -> NewsDesk:
    global _news_desk
    with _sig_lock:
        if _news_desk is None:
            _news_desk = NewsDesk(settings or {})
        return _news_desk
