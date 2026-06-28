"""
Pre-Market Analysis (global basis)
──────────────────────────────────
The most important read of the day: where global markets point before the
Indian open, turned into a single tradeable bias. Wraps the existing
`fetch_premarket_briefing` (SGX Nifty, S&P 500, Dow, Nikkei, Hang Seng, Crude,
Gold, USD/INR + India VIX / PCR / Max Pain / FII flows + risk events + news) and
the lighter `DirectionEngine` score, and serves both to the dashboard.

Cached per day (the briefing does ~30-60s of network I/O); `force=True` refetches.
Runs the fetch under a timeout so a blocked/slow network degrades gracefully
instead of hanging the request.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

IST = timezone(timedelta(hours=5, minutes=30))

_cache: dict = {}
_lock = threading.Lock()
FETCH_TIMEOUT_SECONDS = 90


def _load_config() -> dict:
    import yaml
    for path in ("strategies/BRAHMASTRA_v1/config.yaml", "config/strategy_config.yaml"):
        try:
            return yaml.safe_load(open(path, encoding="utf-8")) or {}
        except Exception:
            continue
    return {}


def _snap_dict(s) -> dict:
    return {
        "name": s.name, "symbol": s.symbol,
        "price": s.price, "prev_close": s.prev_close,
        "change_pct": (round(s.change_pct, 2) if s.change_pct is not None else None),
        "direction": s.direction, "source": s.source, "error": s.error,
    }


def _direction_engine_score(cfg: dict) -> dict:
    """The lighter 4-factor DirectionEngine score (Dow/Gift/VIX/Sensex)."""
    try:
        from src.strategy.direction_engine import DirectionEngine
        res = DirectionEngine(cfg).evaluate_from_live_data()
        return {"available": True, "direction": res.direction.value,
                "score": res.score, "breakdown": res.breakdown, "reason": res.reason}
    except Exception as e:
        return {"available": False, "reason": str(e)[:160]}


def _fetch_briefing(cfg: dict) -> dict:
    from src.brahmastra.data.fetchers.premarket_fetch import fetch_premarket_briefing
    b = fetch_premarket_briefing(cfg)
    return {
        "available": True,
        "date": str(b.date),
        "bias_score": b.bias_score,
        "bias_label": b.bias_label,
        "global_markets": [_snap_dict(s) for s in b.snapshots.values()],
        "india_vix": b.india_vix,
        "vix_trend": b.vix_trend,
        "pcr": b.pcr, "pcr_label": b.pcr_label, "max_pain": b.max_pain,
        "fii_net_cr": b.fii_net_cr,
        "high_risk_events": b.high_risk_events,
        "score_breakdown": b.score_breakdown,
        "news": (b.news or [])[:12],
    }


def _build_conclusion(out: dict) -> dict:
    """Turn the raw pre-market numbers into a clear score + actionable verdict
    for the day's initial trades."""
    b  = out.get("briefing", {}) or {}
    de = out.get("direction_engine", {}) or {}

    score = b.get("bias_score")
    label = b.get("bias_label")
    # Fall back to the direction-engine score (scaled to a -100..100 feel) if the
    # full global briefing is unavailable.
    if score is None and de.get("available"):
        s = de.get("score", 0) or 0
        score = max(-100, min(100, s * 12))
        label = de.get("direction")
    if score is None:
        return {"available": False, "reason": "no pre-market score could be computed"}

    lab = (label or "").upper()
    if "BULL" in lab or score >= 20:
        direction = "BULLISH"
    elif "BEAR" in lab or score <= -20:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    mag = abs(score)
    conviction = ("STRONG" if mag >= 40 else "MODERATE" if mag >= 20
                  else "WEAK" if mag >= 8 else "NONE")

    if direction == "BULLISH":
        action  = "Favour CALL (long) entries — trade with the trend."
        posture = "Trend-following day: ORB / momentum strategies favoured; avoid fighting the move with shorts."
    elif direction == "BEARISH":
        action  = "Favour PUT (short) entries — trade with the trend."
        posture = "Trend-following day (down): momentum PUT setups favoured; avoid bottom-fishing CALLs early."
    else:
        action  = "No clear directional edge — wait for intraday confirmation before the first trade."
        posture = "Range / mean-reversion day likely: prefer range-scalper setups or sit out until a signal forms."

    rationale, cautions = [], []
    bd = b.get("score_breakdown") or {}
    if bd:
        contrib = sorted(bd.items(), key=lambda kv: -abs(kv[1] if isinstance(kv[1], (int, float)) else 0))
        top = [f"{k.replace('_', ' ')} {'+' if (v or 0) > 0 else ''}{v}"
               for k, v in contrib[:3] if isinstance(v, (int, float)) and v]
        if top:
            rationale.append("Main drivers: " + ", ".join(top) + ".")
    if de.get("available") and de.get("reason"):
        rationale.append(f"Direction engine: {de['reason']}.")

    vix = b.get("india_vix")
    if isinstance(vix, (int, float)):
        if vix >= 22:
            cautions.append(f"India VIX {vix} (panic zone) — reduce size, expect whipsaws.")
        elif vix >= 18:
            cautions.append(f"India VIX {vix} (elevated) — size down and widen stops.")
    if b.get("high_risk_events"):
        cautions.append("Event risk today: " + "; ".join(b["high_risk_events"][:3])
                        + " — avoid aggressive early entries.")
    if (b.get("pcr_label") or "").upper() in ("BULLISH", "BEARISH"):
        rationale.append(f"Options PCR reads {b['pcr_label'].lower()}.")

    sign = "+" if score > 0 else ""
    headline = f"{direction} bias ({sign}{score}/100, {conviction.lower()} conviction)"

    return {
        "available": True,
        "score": score,
        "label": label,
        "direction": direction,
        "conviction": conviction,
        "headline": headline,
        "action": action,
        "posture": posture,
        "rationale": rationale,
        "cautions": cautions,
    }


def build_premarket(force: bool = False) -> dict:
    today = date.today().isoformat()
    with _lock:
        if not force and _cache.get("date") == today and _cache.get("data"):
            return _cache["data"]

    cfg = _load_config()
    out = {
        "date": today,
        "generated_at": datetime.now(IST).isoformat(),
        "briefing": {"available": False, "reason": "not fetched"},
        "direction_engine": {"available": False, "reason": "not fetched"},
    }
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_brief = ex.submit(_fetch_briefing, cfg)
            f_dir   = ex.submit(_direction_engine_score, cfg)
            try:
                out["briefing"] = f_brief.result(timeout=FETCH_TIMEOUT_SECONDS)
            except FTimeout:
                out["briefing"] = {"available": False, "reason": "fetch timed out (network slow/blocked)"}
            except Exception as e:
                out["briefing"] = {"available": False, "reason": str(e)[:160]}
            try:
                out["direction_engine"] = f_dir.result(timeout=30)
            except Exception as e:
                out["direction_engine"] = {"available": False, "reason": str(e)[:160]}
    except Exception as e:
        out["error"] = str(e)[:160]

    out["conclusion"] = _build_conclusion(out)

    with _lock:
        _cache.update(date=today, data=out)
    return out
