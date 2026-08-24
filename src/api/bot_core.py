"""
Telegram bot production core — audit, delivery state machine, retries
═════════════════════════════════════════════════════════════════════
Shared infrastructure for the two bots (UI bot spec Parts 3D, 7, 12):

  • BotAudit          — append-only, category-tagged audit trail. EVERY bot action
                        (inbound, outbound, delivery, parse, analysis, context,
                        error) is recorded BEFORE/AFTER it happens, so nothing
                        bypasses logging.
  • DeliveryRecord    — per-publication state machine
                        (PENDING→QUEUED→SENDING→DELIVERED | RETRY_SCHEDULED→…→
                        EXHAUSTED | CANCELLED) persisted to disk.
  • DeliveryQueue     — background worker: idempotent, rate-limited, retry with
                        exponential backoff, dead-letters on exhaustion. Telegram
                        failure NEVER propagates into the trading engine.

stdlib only — no new dependency. Designed so a future swap to python-telegram-bot
only changes the low-level send function, not this control plane.
"""
from __future__ import annotations

import os
import json
import time
import queue
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
_BOT_DIR = "logs/bots"


# ── Audit categories (Part 7) ────────────────────────────────────────────────
class Cat:
    OUTBOUND = "BOT_OUTBOUND"
    INBOUND = "BOT_INBOUND"
    DELIVERY = "BOT_DELIVERY"
    PARSE = "BOT_PARSE"
    ANALYSIS = "BOT_ANALYSIS"
    CONTEXT = "BOT_CONTEXT"
    ERROR = "BOT_ERROR"


# ── Delivery + publication enums (Part 3A/3D) ────────────────────────────────
class DState:
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    EXHAUSTED = "EXHAUSTED"
    CANCELLED = "CANCELLED"


class PubType:
    TRADE_ENTRY = "TRADE_ENTRY"
    TRADE_EXIT = "TRADE_EXIT"
    BLOCKED_TRADE = "BLOCKED_TRADE"
    FORWARD_IMPACT = "FORWARD_IMPACT"
    SESSION_SUMMARY = "SESSION_SUMMARY"
    DAILY_CLOSURE = "DAILY_CLOSURE"
    SYSTEM_ALERT = "SYSTEM_ALERT"
    RISK_ALERT = "RISK_ALERT"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NEWS_REPLY = "NEWS_REPLY"


def _today() -> str:
    return datetime.now(IST).date().isoformat()


def _now() -> str:
    return datetime.now(IST).isoformat()


def _append_jsonl(path: str, obj: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, default=str) + "\n")
    except Exception:
        pass


def _read_jsonl(path: str) -> list:
    out = []
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        out.append(json.loads(ln))
    except Exception:
        pass
    return out


# ── Audit trail ──────────────────────────────────────────────────────────────
_audit_lock = threading.Lock()


def audit(category: str, bot: str, stage: str, status: str = "ok",
          detail: str = "", ref: str = "", meta: dict | None = None) -> None:
    """Record one bot action. Cheap, append-only, never raises."""
    entry = {
        "ts": datetime.now(IST).strftime("%H:%M:%S"),
        "iso": _now(), "category": category, "bot": bot, "stage": stage,
        "status": status, "detail": str(detail)[:400], "ref": ref, "meta": meta or {},
    }
    with _audit_lock:
        _append_jsonl(os.path.join(_BOT_DIR, f"audit_{_today()}.jsonl"), entry)


def read_audit(day: str | None = None, category: str | None = None, limit: int = 300) -> list:
    rows = _read_jsonl(os.path.join(_BOT_DIR, f"audit_{day or _today()}.jsonl"))
    if category:
        rows = [r for r in rows if r.get("category") == category]
    return rows[-limit:]


# ── Delivery record ──────────────────────────────────────────────────────────
@dataclass
class DeliveryRecord:
    event_id: str
    pub_type: str
    state: str
    dedupe_key: str = ""
    attempts: int = 0
    max_attempts: int = 4
    chat_id: str = ""
    message_id: int | None = None
    last_error: str = ""
    title: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    delivered_at: str | None = None


class DeliveryStore:
    """Persists delivery records to logs/bots/outbound_<date>.jsonl (append-only event
    log; latest state per event_id wins on read). In-memory index for fast status."""

    def __init__(self):
        self._lock = threading.Lock()
        self._index: dict[str, DeliveryRecord] = {}
        self._delivered_keys: set[str] = set()
        self._load_today()

    def _path(self) -> str:
        return os.path.join(_BOT_DIR, f"outbound_{_today()}.jsonl")

    def _load_today(self) -> None:
        for row in _read_jsonl(self._path()):
            try:
                rec = DeliveryRecord(**{k: row.get(k) for k in DeliveryRecord.__dataclass_fields__})
                self._index[rec.event_id] = rec
                if rec.state == DState.DELIVERED and rec.dedupe_key:
                    self._delivered_keys.add(rec.dedupe_key)
            except Exception:
                continue

    def already_delivered(self, dedupe_key: str) -> bool:
        if not dedupe_key:
            return False
        with self._lock:
            return dedupe_key in self._delivered_keys

    def upsert(self, rec: DeliveryRecord) -> None:
        rec.updated_at = _now()
        with self._lock:
            self._index[rec.event_id] = rec
            if rec.state == DState.DELIVERED and rec.dedupe_key:
                self._delivered_keys.add(rec.dedupe_key)
            _append_jsonl(self._path(), asdict(rec))

    def all(self) -> list:
        with self._lock:
            return [asdict(r) for r in self._index.values()]

    def summary(self) -> dict:
        recs = list(self._index.values())
        by_state, by_type = {}, {}
        for r in recs:
            by_state[r.state] = by_state.get(r.state, 0) + 1
            by_type[r.pub_type] = by_type.get(r.pub_type, 0) + 1
        return {
            "total": len(recs),
            "delivered": by_state.get(DState.DELIVERED, 0),
            "failed": by_state.get(DState.FAILED, 0) + by_state.get(DState.EXHAUSTED, 0),
            "pending": by_state.get(DState.QUEUED, 0) + by_state.get(DState.RETRY_SCHEDULED, 0)
                       + by_state.get(DState.SENDING, 0),
            "by_state": by_state, "by_type": by_type,
        }


# ── Delivery queue + worker ──────────────────────────────────────────────────
@dataclass
class _QItem:
    rec: DeliveryRecord
    text: str
    not_before: float = 0.0


class DeliveryQueue:
    """Outbound publishing control plane. enqueue() is non-blocking; a single worker
    thread sends to Telegram with rate-limiting + exponential-backoff retries and
    dead-letters after max_attempts. send_fn(chat_id, text) -> (ok, message_id|err)."""

    def __init__(self, send_fn, store: DeliveryStore, default_chat_id: str = "",
                 rate_limit_s: float = 1.1):
        self._send = send_fn
        self.store = store
        self._chat = default_chat_id
        self._rate = rate_limit_s
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_send = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="bot-delivery", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def enqueue(self, pub_type: str, text: str, *, chat_id: str = "", dedupe_key: str = "",
                title: str = "", priority: int = 5, max_attempts: int = 4) -> str | None:
        """Queue a publication. Returns event_id, or None if suppressed as a duplicate."""
        if dedupe_key and self.store.already_delivered(dedupe_key):
            audit(Cat.DELIVERY, "signals", "dedupe_skip", "skipped",
                  f"{pub_type} already delivered", ref=dedupe_key)
            return None
        self._seq += 1
        event_id = f"{int(time.time()*1000)}-{self._seq}"
        rec = DeliveryRecord(event_id=event_id, pub_type=pub_type, state=DState.QUEUED,
                             dedupe_key=dedupe_key, max_attempts=max_attempts,
                             chat_id=chat_id or self._chat, title=title)
        self.store.upsert(rec)
        audit(Cat.OUTBOUND, "signals", "enqueue", "ok", f"{pub_type}: {title}", ref=event_id)
        self._q.put((priority, self._seq, _QItem(rec=rec, text=text)))
        return event_id

    def _worker(self):
        while not self._stop.is_set():
            try:
                priority, seq, item = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            # respect not_before for scheduled retries
            wait = item.not_before - time.time()
            if wait > 0:
                self._q.put((priority, seq, item))
                self._stop.wait(min(wait, 2.0))
                continue
            self._deliver(item, priority, seq)

    def _deliver(self, item: _QItem, priority: int, seq: int):
        rec = item.rec
        # rate limit
        gap = self._rate - (time.time() - self._last_send)
        if gap > 0:
            self._stop.wait(gap)
        rec.state = DState.SENDING
        rec.attempts += 1
        self.store.upsert(rec)
        try:
            ok, info = self._send(rec.chat_id, item.text)
        except Exception as e:
            ok, info = False, str(e)[:160]
        self._last_send = time.time()
        if ok:
            rec.state = DState.DELIVERED
            rec.message_id = info if isinstance(info, int) else None
            rec.delivered_at = _now()
            self.store.upsert(rec)
            audit(Cat.DELIVERY, "signals", "delivered", "ok", rec.title, ref=rec.event_id)
            return
        rec.last_error = str(info)[:160]
        if rec.attempts >= rec.max_attempts:
            rec.state = DState.EXHAUSTED
            self.store.upsert(rec)
            audit(Cat.DELIVERY, "signals", "exhausted", "error",
                  f"{rec.title}: {rec.last_error}", ref=rec.event_id)
            return
        backoff = min(60, 2 ** rec.attempts)
        rec.state = DState.RETRY_SCHEDULED
        self.store.upsert(rec)
        audit(Cat.DELIVERY, "signals", "retry", "warn",
              f"attempt {rec.attempts}/{rec.max_attempts} in {backoff}s: {rec.last_error}",
              ref=rec.event_id)
        item.not_before = time.time() + backoff
        self._q.put((priority, seq, item))


# ── Daily bot activity summary (Part 8) ──────────────────────────────────────
def daily_bot_summary(store: DeliveryStore, day: str | None = None) -> dict:
    day = day or _today()
    out = store.summary() if day == _today() else {"note": "live store only for today"}
    a = read_audit(day, limit=100000)
    inbound = [r for r in a if r.get("category") == Cat.INBOUND]
    analysis = [r for r in a if r.get("category") == Cat.ANALYSIS]
    context = [r for r in a if r.get("category") == Cat.CONTEXT]
    errors = [r for r in a if r.get("category") == Cat.ERROR]
    return {
        "date": day,
        "outbound": out,
        "inbound_received": len(inbound),
        "news_analysed": len(analysis),
        "context_changes": len(context),
        "errors": len(errors),
        "recent_errors": [f"{r['ts']} {r['bot']}/{r['stage']}: {r['detail']}" for r in errors[-8:]],
    }
