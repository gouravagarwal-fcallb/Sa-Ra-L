"""
Flow metrics — observe-only VWAP + relative-volume (RVOL)
═════════════════════════════════════════════════════════
The first market-microstructure layer for the dashboard. It answers "is real money
behind this move?" with two honest, standard reads:

  • Session VWAP (+σ) — the institutional benchmark price: where the day's volume
    actually traded. Price above/below VWAP shows who is in control right now.
  • Relative Volume (RVOL) — the latest bar's volume vs the recent-bar average.
    >1 = participation rising (move backed by volume); <1 = thin / fading.

Honesty rules baked in (mirroring the rest of this codebase's culture):
  • Index SPOT carries volume = 0, so VWAP/RVOL on it would be meaningless. We source
    the near-month FUTURES series (real traded volume) instead, and LABEL it so. If
    futures volume can't be resolved (Kite off / no token), we return
    available=False with a plain reason — we never fabricate a VWAP.
  • This module only DESCRIBES flow. It does not gate, size, start, stop, or place
    any order — read-only, exactly like regime.py. Wiring flow into live decisions is
    a separate, deliberately HELD step that needs its own explicit go-ahead plus
    per-strategy A/B backtests (see CLAUDE.md "no universal filter" finding).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# RVOL buckets (observe-only, tunable): the latest bar's volume vs the trailing mean.
_RVOL_HIGH = 1.5
_RVOL_LOW = 0.6
_RVOL_LOOKBACK = 20          # trailing bars used as the "normal" baseline

# How stale a cached flow read may be before an on-demand refetch (seconds). The
# background feed refreshes on its own slow cycle; this just bounds direct reads.
_TTL_S = 90.0

_lock = threading.Lock()
_LATEST: dict[str, dict] = {}     # inst -> flow block
_TS: dict[str, float] = {}        # inst -> epoch of last refresh


def _now() -> datetime:
    return datetime.now(IST)


def _typical(bar: dict):
    """Typical price (H+L+C)/3 — the standard VWAP input. Falls back to close."""
    h, l, c = bar.get("h"), bar.get("l"), bar.get("c")
    if c is None:
        return None
    if h is None or l is None:
        return float(c)
    return (float(h) + float(l) + float(c)) / 3.0


# ── pure computation (fully unit-testable, no Kite / no I/O) ─────────────────────
def session_vwap(bars: list):
    """Volume-weighted average price + σ over the supplied (session) bars, each
    weighted by its volume. Returns None if no positive volume is present (e.g. a
    spot index) so the caller can report 'no volume' rather than a fake number."""
    cum_pv = cum_v = cum_pv2 = 0.0
    used = 0
    last_c = None
    for b in bars or []:
        tp = _typical(b)
        if tp is None:
            continue
        last_c = b.get("c")
        v = b.get("v") or 0
        if v <= 0:
            continue
        cum_pv += tp * v
        cum_v += v
        cum_pv2 += tp * tp * v
        used += 1
    if cum_v <= 0 or used == 0:
        return None
    vwap = cum_pv / cum_v
    var = max((cum_pv2 / cum_v) - vwap * vwap, 0.0)
    sigma = var ** 0.5
    return {"vwap": round(vwap, 2), "sigma": round(sigma, 2),
            "last": (round(float(last_c), 2) if last_c is not None else None),
            "n": used}


def relative_volume(bars: list, lookback: int = _RVOL_LOOKBACK):
    """Latest bar volume ÷ mean of the prior `lookback` bars' volume. None if there
    is no positive baseline (all-zero volume, or too few bars)."""
    vols = [float(b.get("v") or 0) for b in (bars or [])]
    if len(vols) < 2:
        return None
    last = vols[-1]
    base_window = [x for x in vols[-(lookback + 1):-1] if x > 0]
    if not base_window:
        return None
    base = sum(base_window) / len(base_window)
    if base <= 0:
        return None
    return last / base


def _position(last: float, vwap: float) -> str:
    if last > vwap * 1.0005:
        return "ABOVE"
    if last < vwap * 0.9995:
        return "BELOW"
    return "AT"


def _rvol_state(rvol):
    if rvol is None:
        return None
    if rvol >= _RVOL_HIGH:
        return "HIGH"
    if rvol < _RVOL_LOW:
        return "LOW"
    return "NORMAL"


def compute_flow(bars: list, source: str, session_date: str | None = None) -> dict:
    """Pure: build the observe-only flow block from a list of {t,o,h,l,c,v} bars.
    Never raises; returns available=False with a plain reason when volume is absent.
    `session_date` set (non-None) means these bars are a PAST session (weekend/
    holiday fallback), not today — flagged as stale so the UI can say so."""
    vw = session_vwap(bars)
    if not vw:
        return {"available": False, "source": source,
                "note": "no traded volume in the series (index spot is volume-0) — "
                        "near-month futures volume needed to light this up",
                "as_of": _now().strftime("%H:%M:%S")}
    last, vwap = vw["last"], vw["vwap"]
    rvol = relative_volume(bars)
    vs_bps = (round((last - vwap) / vwap * 10000, 1)
              if (last is not None and vwap) else None)
    return {
        "available": True,
        "source": source,
        "vwap": vwap,
        "sigma": vw["sigma"],
        "last": last,
        "position": (_position(last, vwap) if last is not None else None),
        "vs_vwap_bps": vs_bps,
        "rvol": (round(rvol, 2) if rvol is not None else None),
        "rvol_state": _rvol_state(rvol),
        "n_bars": vw["n"],
        "session_date": session_date,
        "stale": bool(session_date),
        "note": ("last completed session (market closed today) — "
                 if session_date else
                 "observe-only — VWAP = where volume traded; RVOL>1 = participation rising"),
        "as_of": _now().strftime("%H:%M:%S"),
    }


# ── live sourcing + cache (the only impure part; degrades safely) ────────────────
def _session_window():
    """Today's session window in IST (09:00 → now) for intraday futures bars."""
    now = _now()
    frm = now.replace(hour=9, minute=0, second=0, microsecond=0)
    return frm, now


def _bar_date(b: dict) -> str:
    """Date portion of a bar's 't' ('YYYY-MM-DD HH:MM' -> 'YYYY-MM-DD')."""
    return str(b.get("t") or "")[:10]


def _last_session_bars(inst: str, kite_historical):
    """Weekend/holiday fallback: fetch the last few days of near-month futures bars
    and return just the MOST RECENT session's bars (+ its date), so the panel can
    show the last real VWAP/RVOL instead of a blank. ([], None) if nothing found."""
    now = _now()
    frm = now - timedelta(days=6)
    bars = kite_historical.fetch_futures_range(inst, frm, now, "5m")
    if not bars:
        return [], None
    dates = [d for d in (_bar_date(b) for b in bars) if d]
    if not dates:
        return bars, None
    last_date = max(dates)
    return [b for b in bars if _bar_date(b) == last_date], last_date


def _fetch_bars(inst: str):
    """(bars, source_label, session_date). Prefer TODAY's near-month FUTURES (real
    volume). On a weekend/holiday (today empty) fall back to the last completed
    session. ([], reason, None) if Kite is off or the token can't resolve — the
    caller then reports 'no volume' honestly."""
    try:
        from src.data import kite_historical
    except Exception:
        return [], f"{inst} (no data module)", None
    try:
        if not kite_historical.is_enabled():
            return [], f"{inst} (Kite historical off — no live volume)", None
        frm, to = _session_window()
        bars = kite_historical.fetch_futures_range(inst, frm, to, "5m")
        if bars:
            return bars, f"{inst} FUT · near-month · 5m", None      # today, live
        # market closed today → show the last completed session instead of blank
        sess, sdate = _last_session_bars(inst, kite_historical)
        if sess:
            return sess, f"{inst} FUT · {sdate} session", sdate
    except Exception as e:
        return [], f"{inst} (futures fetch error: {type(e).__name__})", None
    return [], f"{inst} (near-month futures volume unavailable)", None


def refresh(inst: str) -> dict:
    """Recompute + cache the flow block for one instrument. Safe to call from the
    background feed loop; never raises."""
    inst = inst.upper()
    try:
        bars, source, session_date = _fetch_bars(inst)
        block = compute_flow(bars, source, session_date)
    except Exception as e:
        block = {"available": False, "source": inst,
                 "note": f"flow refresh error: {type(e).__name__}",
                 "as_of": _now().strftime("%H:%M:%S")}
    with _lock:
        _LATEST[inst] = block
        _TS[inst] = _now().timestamp()
    return block


def latest(inst: str) -> dict:
    """Cached flow block, refreshing on-demand if stale/missing. Returns a benign
    'warming up' block rather than raising if nothing is cached yet."""
    inst = inst.upper()
    with _lock:
        blk = _LATEST.get(inst)
        ts = _TS.get(inst, 0.0)
    if blk is not None and (_now().timestamp() - ts) < _TTL_S:
        return blk
    return refresh(inst)
