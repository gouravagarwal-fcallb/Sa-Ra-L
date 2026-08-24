"""
Observe-only GTI demand/supply zones for the dashboard chart overlay.
────────────────────────────────────────────────────────────────────
Read-only. Computes current, un-mitigated 15-minute demand/supply zones for an
instrument and returns them as horizontal proximal/distal bands the frontend can
draw over the candles. **Touches no order logic** — this is exactly the same
"observe-only" posture as the Portfolio Risk tab.

Why 15-minute only (by default): that's the one timeframe where the zone edge
survived realistic costs on BOTH NIFTY and SENSEX in the research backtests
(~+0.35R, cost-viable to ~4-8 pts). Lower timeframes don't clear costs; 1-hour
has too few trades. The overlay therefore defaults to 15m so what you SEE lines
up with what actually validated. Other timeframes are allowed but flagged
non-validated.

The zone *strength* score is shown for context only — the research explicitly
found it does NOT rank edge (flat buckets), so the UI must not treat a high
score as "better". Freshness (untested) is the property that matters.
"""
from __future__ import annotations

import time
import threading
from datetime import datetime

# The timeframe whose zone edge actually validated (NIFTY + SENSEX, cost-viable).
VALIDATED_TF = "15m"

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 60.0  # zones move slowly on 15m; a 60 s cache is plenty


def _parse_ts(t):
    """Bars carry 't' as an epoch (int/float) or a date/datetime string."""
    if isinstance(t, (int, float)):
        try:
            return datetime.fromtimestamp(t)
        except Exception:
            return None
    s = str(t)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])
    except Exception:
        return None


def _bars_to_df(bars: list):
    """Convert chart bars ({t,o,h,l,c,v}) to the OHLC DataFrame detect_zones wants."""
    import pandas as pd
    rows, idx = [], []
    for b in bars or []:
        o, h, l, c = b.get("o"), b.get("h"), b.get("l"), b.get("c")
        if None in (o, h, l, c):
            continue
        dt = _parse_ts(b.get("t"))
        if dt is None:
            continue
        idx.append(dt)
        rows.append({"open": float(o), "high": float(h), "low": float(l),
                     "close": float(c), "volume": float(b.get("v") or 0.0)})
    if not rows:
        return None
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx)).sort_index()
    return df[~df.index.duplicated(keep="last")]


def compute_zones(instrument: str, tf: str = VALIDATED_TF,
                  max_distance_pct: float = 2.5, max_zones: int = 12) -> dict:
    """Return current un-mitigated zones near price for the chart overlay.

    { instrument, tf, validated_tf, is_validated_tf, ltp, source,
      zones:[{ side, type, proximal, distal, strength, fresh, mitigated,
               created_time, distance_pct }], note }
    Never raises — degrades to an empty zone list with a reason.
    """
    instrument = instrument.upper()
    key = (instrument, tf)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and (now - hit[0]) < _CACHE_TTL:
            return hit[1]

    out = {"instrument": instrument, "tf": tf, "validated_tf": VALIDATED_TF,
           "is_validated_tf": tf == VALIDATED_TF, "ltp": None, "source": None,
           "zones": [], "note": ""}
    try:
        from src.api.charts import fresh_chart
        from src.research.gti.gti_zones import detect_zones, ZoneConfig, active_zones

        chart = fresh_chart(instrument, tf)
        bars = chart.get("bars") or []
        out["source"] = chart.get("source")
        df = _bars_to_df(bars)
        if df is None or len(df) < 40:
            out["note"] = "not enough bars to detect zones"
            return out

        ltp = float(df["close"].iloc[-1])
        out["ltp"] = round(ltp, 2)

        # Keep ALL zones (max_active_zones=0): the default top-N truncation ranks
        # by future-informed strength — fine for a live glance, but we want every
        # nearby un-mitigated level, then filter by proximity for the overlay.
        zones = detect_zones(df, ZoneConfig(max_active_zones=0))
        near = active_zones(zones, ltp, max_distance_pct=max_distance_pct,
                            include_mitigated=False)
        rows = []
        for z in near[:max_zones]:
            rows.append({
                "side": z.side, "type": z.ztype,
                "proximal": round(z.proximal, 2), "distal": round(z.distal, 2),
                "strength": round(z.strength, 1), "fresh": z.tests == 0,
                "mitigated": z.mitigated,
                "created_time": z.created_time.isoformat() if z.created_time else None,
                "distance_pct": round(abs(z.proximal - ltp) / ltp * 100, 2),
            })
        out["zones"] = rows
        if not tf == VALIDATED_TF:
            out["note"] = (f"{tf} zones are NOT the validated timeframe — only "
                           f"{VALIDATED_TF} cleared costs in backtests. Context only.")
    except Exception as e:  # never break the dashboard over an overlay
        out["note"] = f"zone computation failed: {str(e)[:140]}"
        return out

    if out["zones"]:
        with _CACHE_LOCK:
            _CACHE[key] = (now, out)
    return out
