"""
News Desk ingestion + impact pipeline (bot spec Parts 4 & 5)
════════════════════════════════════════════════════════════
Turns a raw inbound submission (text / caption / link / forwarded message) into a
structured, auditable NewsImpact, and proposes — but never applies without the
gate in strategy_context — a controlled context adjustment.

Pipeline stages (each audited by the caller):
  extract → classify → relevance → trust → impact → NewsImpact → (context proposal)

Heuristic + rule based so it works with no external model. OCR/PDF text extraction
is pluggable (see extract_text): if a library is present it is used, otherwise the
caption/body text is used and the item is flagged accordingly. No stage can place a
trade — the only downstream effect is an advisory ContextAdjustment.
"""
from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta

from src.api.telegram_bots import analyze_news_impact   # reuse the theme engine

IST = timezone(timedelta(hours=5, minutes=30))

# Theme tag → classification bucket (Part 4C)
_CLASS_MAP = {
    "Dovish / rate-cut": "rbi_govt_regulation",
    "Hawkish / rate-hike": "rbi_govt_regulation",
    "RBI policy": "rbi_govt_regulation",
    "Budget / fiscal": "india_macro_policy",
    "Inflation print": "india_macro_policy",
    "US macro / Fed": "macro_global",
    "Crude oil": "macro_global",
    "Geopolitical risk": "geopolitical",
    "Earnings": "earnings",
    "Institutional flows": "liquidity_risk",
    "Bullish momentum": "market_structure_expiry",
    "Bearish momentum": "market_structure_expiry",
}
_RUMOR_WORDS = ("rumor", "rumour", "unconfirmed", "unverified", "hearsay", "buzz",
                "speculation", "may", "might", "reportedly", "sources say")
_URL_RE = re.compile(r"https?://\S+")


def content_hash(text: str) -> str:
    return hashlib.sha1((text or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def extract_text(*, body: str = "", caption: str = "", doc_path: str = "") -> tuple[str, str]:
    """Return (extracted_text, source_note). OCR/PDF parsing is attempted only if a
    library is installed; otherwise we fall back to the caption/body and say so."""
    base = (body or caption or "").strip()
    if doc_path:
        # best-effort, fully optional — never a hard dependency
        try:
            if doc_path.lower().endswith(".pdf"):
                from pypdf import PdfReader  # type: ignore
                txt = "\n".join((p.extract_text() or "") for p in PdfReader(doc_path).pages)
                if txt.strip():
                    return (base + "\n" + txt).strip(), "pdf_text"
            else:
                import pytesseract  # type: ignore
                from PIL import Image  # type: ignore
                txt = pytesseract.image_to_string(Image.open(doc_path))
                if txt.strip():
                    return (base + "\n" + txt).strip(), "ocr"
        except Exception:
            return base, "attachment_unparsed (no OCR/PDF lib — used caption/body)"
    return base, "text"


def classify(themes: list) -> str:
    for t in themes:
        if t in _CLASS_MAP:
            return _CLASS_MAP[t]
    return "not_relevant"


def relevance_score(themes: list, text: str) -> float:
    if not themes:
        return 0.1
    score = min(1.0, 0.35 + 0.2 * len(themes))
    if any(k in text.lower() for k in ("nifty", "sensex", "index", "rbi", "fed", "market")):
        score = min(1.0, score + 0.1)
    return round(score, 2)


def trust_score(*, text: str, source_type: str, tags: list) -> float:
    t = (text or "").lower()
    score = 0.6
    if _URL_RE.search(text or ""):
        score += 0.15                      # has a source link
    if source_type == "forwarded":
        score += 0.05
    if any(w in t for w in _RUMOR_WORDS) or "rumor" in [x.lower() for x in tags]:
        score -= 0.35                      # explicit rumor / hedged language
    if len(t) < 25:
        score -= 0.1                       # too thin to verify
    return round(max(0.05, min(1.0, score)), 2)


@dataclass
class NewsImpact:
    impact_id: str
    source_bot: str
    source_message_id: str
    received_at: str
    submitted_by: str
    source_type: str                       # text | forwarded | photo | document | link
    raw_text: str
    extracted_text: str
    source_url: str
    classification: str
    relevance_score: float
    trust_score: float
    affected_indices: list
    affected_instruments: list
    expected_direction: str                # BULLISH | BEARISH | NEUTRAL/MIXED
    impact_strength: str                   # low | medium | high
    confidence: float                      # 0..1
    horizon: str                           # intraday | 1-3d | structural
    rationale: str
    risk_flags: list
    expiry_time: str
    usable_for_trading: bool
    context_only: bool
    strategy_context_adjustment: dict | None
    analyst_summary: str
    engine_summary: str
    status: str                            # analysed | low_relevance | rumor | error


def build_news_impact(*, message_id: str, submitted_by: str, source_type: str,
                      body: str = "", caption: str = "", doc_path: str = "",
                      tags: list | None = None, source_bot: str = "news_desk") -> NewsImpact:
    tags = tags or []
    extracted, note = extract_text(body=body, caption=caption, doc_path=doc_path)
    verdict = analyze_news_impact(extracted)
    themes = verdict.get("themes", [])
    cls = classify(themes)
    rel = relevance_score(themes, extracted)
    trust = trust_score(text=extracted, source_type=source_type, tags=tags)
    url_m = _URL_RE.search(extracted or "")

    score = verdict.get("score", 0)
    strength = "high" if abs(score) >= 3 else "medium" if abs(score) >= 1 else "low"
    confidence = round(max(0.05, min(0.95, 0.4 + 0.12 * abs(score))) * (0.5 + trust / 2), 2)
    horizon = ("intraday" if cls in ("market_structure_expiry", "liquidity_risk")
               else "1-3d" if cls in ("earnings", "macro_global", "geopolitical")
               else "structural" if cls in ("india_macro_policy",) else "intraday")

    is_rumor = trust < 0.4
    usable = (rel >= 0.5 and trust >= 0.5 and strength != "low" and not is_rumor)
    risk_flags = []
    if is_rumor:
        risk_flags.append("low_trust / possible rumor")
    if "attachment_unparsed" in note:
        risk_flags.append(note)
    if not themes:
        risk_flags.append("no recognised market driver")

    # propose (not apply) a controlled context adjustment
    proposal = None
    if usable:
        if verdict["sentiment"].startswith("BULL"):
            proposal = {"effect": "macro_bias", "value": "BULLISH"}
        elif "BEAR" in verdict["sentiment"]:
            proposal = {"effect": "macro_bias", "value": "BEARISH"}
        if cls == "geopolitical" or strength == "high":
            proposal = {"effect": "high_risk_session", "value": True}
    elif is_rumor:
        proposal = None  # rumors never adjust context

    status = ("error" if note.startswith("error") else "rumor" if is_rumor
              else "low_relevance" if rel < 0.4 else "analysed")

    return NewsImpact(
        impact_id=f"news-{content_hash(extracted)}",
        source_bot=source_bot, source_message_id=str(message_id),
        received_at=datetime.now(IST).isoformat(), submitted_by=str(submitted_by),
        source_type=source_type, raw_text=(body or caption or "")[:1000],
        extracted_text=extracted[:2000], source_url=(url_m.group(0) if url_m else ""),
        classification=cls, relevance_score=rel, trust_score=trust,
        affected_indices=[i for i in ("NIFTY", "SENSEX") if i.lower() in extracted.lower()] or ["NIFTY", "SENSEX"],
        affected_instruments=verdict.get("affected", []),
        expected_direction=verdict["sentiment"], impact_strength=strength,
        confidence=confidence, horizon=horizon, rationale=verdict.get("summary", ""),
        risk_flags=risk_flags,
        expiry_time=(datetime.now(IST) + timedelta(hours=(6 if horizon != "intraday" else 2))).isoformat(),
        usable_for_trading=usable, context_only=not usable,
        strategy_context_adjustment=proposal,
        analyst_summary=verdict.get("summary", ""),
        engine_summary=(f"{cls} · {verdict['sentiment']} · strength {strength} · "
                        f"rel {rel} · trust {trust} · conf {confidence}"),
        status=status)


def impact_to_dict(ni: NewsImpact) -> dict:
    return asdict(ni)
