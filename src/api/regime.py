"""
Market regime engine (Phase 2, observe-only)
═════════════════════════════════════════════
One central, continuous regime classification per index so strategies and scoring
can be sliced by market state — WITHOUT gating anything yet (observe-only). Reuses
the multi-timeframe Bollinger metrics already computed for the Forward-Impact panel
(MarketFeed._tf_metrics), so there is no new data path.

Regimes: trend_up · trend_down · range · compression (squeeze) · expansion ·
chop · plus an event_risk flag overlaid from the pre-market calendar / news context.

This module only DESCRIBES the market. It does not start, stop, gate, or size any
strategy. Gating/orchestration is a later Phase-2 step, kept separate on purpose.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# Advisory: which strategy *families* tend to fit each regime (display only).
REGIME_FIT = {
    "trend_up":    ["momentum / trend (RAMS, NIFTY_INTRADAY, ATM_PULSE, TREND_RIDER)"],
    "trend_down":  ["momentum-short / trend (NIFTY_INTRADAY, TREND_RIDER, BLACK_SWAN)"],
    "range":       ["mean-reversion (RANGE_SCALPER, GAP_FADE, VIX_SELLER)"],
    "compression": ["breakout-ready (BB_EXPIRY, NIFTY_INTRADAY) — squeeze building"],
    "expansion":   ["momentum / breakout (RAMS, ATM_PULSE, BB_EXPIRY)"],
    "chop":        ["stand aside — no clean edge"],
    "unknown":     ["—"],
}


def classify(chart: dict) -> dict:
    """Classify one index from its chart_bars dict {tf: {bars, bb}}."""
    from src.api.market_feed import MarketFeed
    m5 = MarketFeed._tf_metrics((chart or {}).get("5m"))
    m15 = MarketFeed._tf_metrics((chart or {}).get("15m"))
    if not m5:
        return {"regime": "unknown", "confidence": 0, "volatility": None,
                "detail": "insufficient bars"}
    pctb, slope, wc = m5["pctb"], m5["slope"], m5["width_chg"]
    vol = MarketFeed._vol_regime(m5.get("width"))
    aligned = bool(m15) and ((slope > 0) == (m15["slope"] > 0))

    if wc < -0.12:
        regime = "compression"
    elif wc > 0.2 and (pctb > 0.7 or pctb < 0.3):
        regime = "expansion"
    elif pctb > 0.65 and slope > 0.0003 and aligned:
        regime = "trend_up"
    elif pctb < 0.35 and slope < -0.0003 and aligned:
        regime = "trend_down"
    elif abs(slope) < 0.0002 and 0.35 <= pctb <= 0.65:
        regime = "range"
    else:
        regime = "chop"

    # confidence: alignment + distance of %B from mid + width-change magnitude
    conf = 40 + 30 * (1 if aligned else 0) + min(30, int(abs(pctb - 0.5) * 60))
    if regime == "chop":
        conf = min(conf, 45)
    return {"regime": regime, "confidence": int(min(100, conf)), "volatility": vol,
            "pct_b": round(pctb, 2), "width_chg_pct": round(wc * 100, 1),
            "fit": REGIME_FIT.get(regime, ["—"])}


def _event_risk() -> bool:
    """Overlay flag from the (cached) pre-market briefing / News-Desk context."""
    try:
        from src.api.premarket import build_premarket
        b = build_premarket(False).get("briefing", {}) or {}
        if b.get("high_risk_events"):
            return True
    except Exception:
        pass
    try:
        from src.api.strategy_context import get_context
        eff = get_context().effective()
        if eff.get("high_risk_session"):
            return True
    except Exception:
        pass
    return False


def current(multi, indices=("NIFTY", "SENSEX")) -> dict:
    """Live regime per index from the shared market slot. Observe-only."""
    out = {"as_of": datetime.now(IST).strftime("%H:%M:%S"), "event_risk": _event_risk(),
           "indices": {}}
    try:
        cb = (multi.market().chart_bars if (multi and multi.has("_market")) else {}) or {}
    except Exception:
        cb = {}
    for ix in indices:
        out["indices"][ix] = classify(cb.get(ix, {}))
    return out
