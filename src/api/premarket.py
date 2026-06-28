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

    with _lock:
        _cache.update(date=today, data=out)
    return out
