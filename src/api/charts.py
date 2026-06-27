"""
Multi-Timeframe Charts + Bollinger Bands
────────────────────────────────────────
Serves OHLC candles + Bollinger Bands (20, 2σ) per instrument per timeframe for
the always-on chart panel. Timeframes: 1m, 5m, 15m, 1h, 1d, 1w.

Two data paths:
  1. Live: the MarketFeed writes rolling windows into the shared market state slot
     (multi.market().chart_bars) on each bar close — read those first.
  2. Fallback: compute on demand from the BackfillManager so charts render even
     when no feed/engine is running (e.g. reviewing during off-hours).
"""
from __future__ import annotations

from statistics import mean, pstdev

# tf -> yfinance interval used by BackfillManager
_TF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m",
                "1h": "60m", "1d": "1d", "1w": "1wk"}
_INTRADAY = {"1m", "5m", "15m", "1h"}
BB_PERIOD = 20
BB_MULT = 2.0


def bollinger(closes: list[float], period: int = BB_PERIOD,
              mult: float = BB_MULT) -> dict:
    """Rolling Bollinger Bands; arrays align 1:1 with `closes` (None before warmup)."""
    upper, mid, lower = [], [], []
    for i in range(len(closes)):
        if i + 1 < period:
            upper.append(None); mid.append(None); lower.append(None); continue
        window = closes[i + 1 - period:i + 1]
        m = mean(window); sd = pstdev(window)
        mid.append(round(m, 2))
        upper.append(round(m + mult * sd, 2))
        lower.append(round(m - mult * sd, 2))
    return {"upper": upper, "mid": mid, "lower": lower,
            "period": period, "mult": mult}


def _compute_from_backfill(instrument: str, tf: str) -> dict:
    interval = _TF_INTERVAL.get(tf)
    if interval is None:
        return {"instrument": instrument, "tf": tf, "bars": [], "bb": {},
                "source": "error", "reason": f"unsupported tf {tf}"}
    try:
        from src.data.backfill import BackfillManager
        bf = BackfillManager()
        if tf in _INTRADAY:
            bars = bf.get_today_bars(instrument, interval=interval)
            if not bars:
                bars = bf.get_bars(instrument, interval=interval, days_back=5)
        else:
            days = 400 if tf == "1d" else 1500
            bars = bf.get_bars(instrument, interval=interval, days_back=days)
    except Exception as e:
        return {"instrument": instrument, "tf": tf, "bars": [], "bb": {},
                "source": "error", "reason": str(e)[:140]}
    bars = bars or []
    closes = [b.get("c") for b in bars if b.get("c") is not None]
    return {"instrument": instrument, "tf": tf,
            "bars": bars[-250:], "bb": _slice_bb(bollinger(closes), len(bars), 250),
            "source": "backfill", "n": len(bars)}


def _slice_bb(bb: dict, total: int, keep: int) -> dict:
    if total <= keep:
        return bb
    start = total - keep
    return {"upper": bb["upper"][start:], "mid": bb["mid"][start:],
            "lower": bb["lower"][start:], "period": bb["period"], "mult": bb["mult"]}


def get_chart(multi, instrument: str, tf: str) -> dict:
    instrument = instrument.upper()
    if tf not in _TF_INTERVAL:
        return {"instrument": instrument, "tf": tf, "bars": [], "bb": {},
                "source": "error", "reason": f"unsupported tf {tf}",
                "supported": list(_TF_INTERVAL.keys())}
    # 1) live market slot
    try:
        if multi is not None and multi.has("_market"):
            cb = multi.market().chart_bars.get(instrument, {}).get(tf)
            if cb and cb.get("bars"):
                return {"instrument": instrument, "tf": tf,
                        "bars": cb["bars"][-250:], "bb": cb.get("bb", {}),
                        "source": "live", "updated": cb.get("updated")}
    except Exception:
        pass
    # 2) compute from backfill
    return _compute_from_backfill(instrument, tf)
