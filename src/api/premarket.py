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


_TIER_RANK = {"BEST": 0, "SUITED": 1, "ARMED": 2, "NEUTRAL": 3, "LESS_SUITED": 4, "OFF": 5}


def _fit_for_type(t: str, *, directional: bool, direction: str, strong: bool,
                  is_expiry: bool, vix_elev: bool, vix_panic: bool,
                  event_risk: bool) -> tuple[str, str]:
    """Map a strategy_type to (tier, reason) for the current pre-market scenario."""
    bull = direction == "BULLISH"
    if t in ("intraday_1min", "1min_confluence"):                      # RAMS
        if directional and strong:
            return "BEST", "Momentum scalper — thrives on a clear directional, conviction day."
        return "LESS_SUITED", "Momentum scalper — a flat/neutral open gives it little to ride (keeps scanning)."
    if t == "expiry_scalper":
        return ("BEST", "Expiry-day scalper — today is an expiry day.") if is_expiry \
            else ("OFF", "Trades only on expiry days — shadow-analyses today.")
    if t == "bb_expiry_scalper":
        return ("SUITED", "Bollinger expiry breakout — expiry day favours band expansion.") if is_expiry \
            else ("OFF", "Expiry-only — runs in shadow on non-expiry days.")
    if t == "nifty_intraday":
        return "SUITED", "Regime-adaptive (ORB on trend, S/R on range) — fits either way."
    if t == "range_scalper":
        if is_expiry:
            return "OFF", "Avoids expiry days (gamma risk) — idle today."
        if not directional and not vix_panic:
            return "BEST", "Mean-reversion — a neutral, range-bound open is its ideal."
        return "LESS_SUITED", "Mean-reversion — a strong trend day works against it (still validating range)."
    if t == "black_swan":
        if event_risk or vix_panic:
            return "ARMED", "Extreme-move scalper — event risk / panic VIX keeps it armed."
        return "NEUTRAL", "Waits for a gap/extreme trigger — monitors all day regardless."
    if t == "atm_pulse_burst":
        if bull:
            return "BEST", "ATM CE momentum — a bullish open is its sweet spot."
        if direction == "BEARISH":
            return "LESS_SUITED", "CE-only buyer — a bearish day is counter to it (keeps scoring)."
        return "NEUTRAL", "CE momentum — needs an upside push to fire."
    if t == "pashupatastra":
        if event_risk or strong:
            return "ARMED", "Seller-trap hunter — event/high-conviction days raise trap odds."
        return "NEUTRAL", "Rare-release — patiently watches the option chain for a trap."
    if t == "inrusd_futures":
        if not directional:
            return "SUITED", "USD/INR futures — uncorrelated alpha when equities are range-bound."
        return "NEUTRAL", "Currency-driven (DXY/USD-INR), largely independent of the equity bias."
    if t == "brahmastra":
        return "SUITED", "Adaptive multi-market platform — relevant in any regime."
    if t == "opening_range":                                           # gap_fade
        return ("SUITED", "Gap-fade — a gap open with calm VIX suits reversion.") if not vix_panic \
            else ("NEUTRAL", "Gap-fade — high VIX makes fades riskier.")
    if t == "trend_following":                                         # trend_rider
        return ("BEST", "Trend rider — a strong directional day is exactly its setup.") if (directional and strong) \
            else ("LESS_SUITED", "Trend rider — needs strong conviction to engage.")
    if t == "volatility_mean_reversion":                               # vix_seller
        return ("BEST", "VIX seller — panic-level VIX is its entry condition.") if vix_panic \
            else ("OFF", "Activates only when VIX > 22 — idle today.")
    if t == "5min_fixed_quantity":                                     # SRAL (archived)
        return "OFF", "Archived — superseded by RAMS."
    return "NEUTRAL", "Monitored — no specific scenario edge today."


def _strategy_fit(direction, conviction, vix, is_expiry, event_risk) -> dict:
    registry = _load_registry_strategies()
    directional = direction in ("BULLISH", "BEARISH")
    strong      = conviction in ("STRONG", "MODERATE")
    vix_elev    = isinstance(vix, (int, float)) and vix >= 18
    vix_panic   = isinstance(vix, (int, float)) and vix >= 22

    items = []
    for name, cfg in registry.items():
        t = (cfg.get("type") or cfg.get("strategy_type") or "").lower()
        tier, reason = _fit_for_type(
            t, directional=directional, direction=direction, strong=strong,
            is_expiry=is_expiry, vix_elev=vix_elev, vix_panic=vix_panic,
            event_risk=event_risk)
        items.append({"name": name, "type": t, "status": cfg.get("status"),
                      "tier": tier, "reason": reason})
    items.sort(key=lambda x: (_TIER_RANK.get(x["tier"], 9), x["name"]))
    return {
        "note": ("Advisory only — this ranks today's fit. EVERY strategy keeps "
                 "analysing and monitoring the market regardless of its tier; "
                 "nothing is paused or stopped."),
        "items": items,
    }


def _load_registry_strategies() -> dict:
    import yaml
    try:
        return yaml.safe_load(open("strategies/registry.yaml", encoding="utf-8")).get("strategies", {})
    except Exception:
        return {}


def _today_expiry() -> bool:
    try:
        from src.utils.market_calendar import is_nifty_expiry_day, is_sensex_expiry_day
        today = date.today()
        return bool(is_nifty_expiry_day(today) or is_sensex_expiry_day(today))
    except Exception:
        return False


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

    is_expiry  = _today_expiry()
    event_risk = bool(b.get("high_risk_events"))
    strategy_fit = _strategy_fit(direction, conviction, b.get("india_vix"),
                                 is_expiry, event_risk)

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
        "is_expiry_day": is_expiry,
        "strategy_fit": strategy_fit,
    }


def format_alert(out: dict) -> str:
    """Compose a concise pre-market briefing message (Telegram/email friendly)."""
    c = out.get("conclusion", {}) or {}
    b = out.get("briefing", {}) or {}
    if not c.get("available"):
        return (f"*Pre-Market — {out.get('date','')}*\n"
                f"Bias unavailable: {c.get('reason', 'no data')}")

    lines = [f"*Pre-Market Briefing — {out.get('date','')}*",
             f"*{c['direction']}*  ·  score {('+' if c['score'] > 0 else '')}{c['score']}/100  ·  {c['conviction']} conviction",
             f"➜ {c['action']}",
             f"_{c['posture']}_"]
    if c.get("rationale"):
        lines.append("\n*Why:*")
        lines += [f"• {r}" for r in c["rationale"]]
    if c.get("cautions"):
        lines.append("\n*Caution:*")
        lines += [f"⚠ {x}" for x in c["cautions"]]

    fit = (c.get("strategy_fit") or {}).get("items", [])
    best = [i["name"] for i in fit if i["tier"] == "BEST"]
    suited = [i["name"] for i in fit if i["tier"] in ("SUITED", "ARMED")]
    if best:
        lines.append("\n*Best fit today:* " + ", ".join(best))
    if suited:
        lines.append("*Also suited:* " + ", ".join(suited))
    lines.append("\n_Advisory only — every strategy keeps analysing regardless._")

    vix = b.get("india_vix")
    if vix is not None:
        lines.append(f"\nIndia VIX {vix} ({b.get('vix_trend','')}) · PCR {b.get('pcr','—')} ({b.get('pcr_label','')})")
    return "\n".join(lines)


def _demo_payload() -> dict:
    """Representative sample (env SARAL_PREMARKET_DEMO=1) so the tab can be
    previewed off-hours / without a live market feed. Never used in normal runs."""
    g = lambda name, sym, chg: {"name": name, "symbol": sym, "price": None,
                                "prev_close": None, "change_pct": chg,
                                "direction": "UP" if chg > 0.3 else "DOWN" if chg < -0.3 else "FLAT",
                                "source": "demo", "error": None}
    briefing = {
        "available": True, "date": date.today().isoformat(),
        "bias_score": 46, "bias_label": "STRONGLY_BULLISH",
        "global_markets": [
            g("SGX Nifty", "^SGXNIFTY", 0.62), g("S&P 500", "^GSPC", 0.85),
            g("Dow Jones", "^DJI", 0.74), g("Nikkei 225", "^N225", 1.10),
            g("Hang Seng", "^HSI", 0.40), g("Crude Oil (WTI)", "CL=F", -0.55),
            g("Gold", "GC=F", 0.20), g("USD/INR", "USDINR=X", -0.15),
        ],
        "india_vix": 13.4, "vix_trend": "FALLING",
        "pcr": 1.34, "pcr_label": "BULLISH", "max_pain": 24800,
        "fii_net_cr": 1820.0, "high_risk_events": [],
        "score_breakdown": {"SGX_Nifty": 25, "US_Markets": 15, "Asian_Markets": 8,
                            "India_VIX": 5, "PCR": 6, "FII_Net": 5, "Crude_Oil": -2, "USD_INR": 3},
        "news": [{"title": "Asian markets rally on cooling US inflation print"},
                 {"title": "FIIs turn net buyers for third straight session"}],
    }
    out = {"date": date.today().isoformat(),
           "generated_at": datetime.now(IST).isoformat(),
           "briefing": briefing,
           "direction_engine": {"available": True, "direction": "BULLISH", "score": 4,
                                "breakdown": {}, "reason": "Score 4 ≥ 3 → BUY CALL"},
           "demo": True}
    out["conclusion"] = _build_conclusion(out)
    return out


def build_premarket(force: bool = False) -> dict:
    import os
    if os.environ.get("SARAL_PREMARKET_DEMO") == "1":
        return _demo_payload()
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
