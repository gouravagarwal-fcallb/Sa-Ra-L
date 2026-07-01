"""
Portfolio Risk — observe-only book aggregation
──────────────────────────────────────────────
Rolls every running strategy's OPEN option positions into one book-level risk
view: net Greeks, exposure, premium-at-risk (the true max loss for long options),
stress scenarios, and a delta-normal 1-day VaR.

Everything here is READ-ONLY. It places no orders and changes no state — it just
reads open_trades from each strategy's snapshot and market quotes, and does the
arithmetic. It is intentionally transparent about its assumptions:

  • Greeks use Black-Scholes with the implied vol PROXIED from India VIX
    (σ = VIX/100). Index options track VIX closely, but this is an approximation,
    not the per-strike IV a broker terminal shows — it is labelled as such.
  • Positions are treated as LONG (these strategies are option BUYERS). A book of
    long options has a hard, knowable max loss = the premium still outstanding,
    which is reported as the primary risk number.
  • Time-to-expiry is estimated from the next weekly expiry (NIFTY→Tue, SENSEX→Thu).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
_R = 0.065                      # risk-free rate (~India 1y)
_MIN_T = 0.3 / 365.0           # floor on time-to-expiry (avoid div-by-zero on expiry day)
_VIX_FALLBACK = 0.15           # 15% IV if VIX is unavailable
# Weekly expiry weekday per underlying (Mon=0 … Sun=6). NIFTY=Tuesday, SENSEX=Thursday.
_EXPIRY_WEEKDAY = {"NIFTY": 1, "SENSEX": 3}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _days_to_expiry(underlying: str, now: datetime) -> float:
    """Approximate years-to-expiry from the next weekly expiry weekday."""
    wd = _EXPIRY_WEEKDAY.get(underlying.upper(), 1)
    ahead = (wd - now.weekday()) % 7
    # On expiry day (ahead == 0) use the remaining fraction of the session.
    if ahead == 0:
        mins_left = max(0.0, (15 * 60 + 30) - (now.hour * 60 + now.minute))
        return max(_MIN_T, (mins_left / (60.0 * 24.0)) / 365.0)
    return ahead / 365.0


def _bs(spot: float, strike: float, t: float, sigma: float, opt: str) -> dict:
    """Per-unit Black-Scholes price + Greeks for one option. Greeks scaled to the
    conventional per-1%-vol (vega) and per-day (theta) units traders read."""
    if spot <= 0 or strike <= 0 or t <= 0 or sigma <= 0:
        return {"price": None, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    srt = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (_R + 0.5 * sigma * sigma) * t) / srt
    d2 = d1 - srt
    nd1, nd2 = _norm_cdf(d1), _norm_cdf(d2)
    pdf = _norm_pdf(d1)
    disc = math.exp(-_R * t)
    is_call = opt.upper() == "CE"
    if is_call:
        price = spot * nd1 - strike * disc * nd2
        delta = nd1
        theta = (-(spot * pdf * sigma) / (2 * math.sqrt(t)) - _R * strike * disc * nd2)
    else:
        price = strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = nd1 - 1.0
        theta = (-(spot * pdf * sigma) / (2 * math.sqrt(t)) + _R * strike * disc * _norm_cdf(-d2))
    gamma = pdf / (spot * srt)
    vega = spot * pdf * math.sqrt(t) / 100.0      # per 1% change in vol
    return {"price": price, "delta": delta, "gamma": gamma,
            "vega": vega, "theta": theta / 365.0}  # theta per calendar day


# Index moves used for the stress ladder (fraction of spot).
_STRESS = [-0.05, -0.02, -0.01, 0.01, 0.02, 0.05]


def compute_portfolio_risk(multi, runner) -> dict:
    """Aggregate open option positions across all running strategies into a
    book-level, observe-only risk snapshot."""
    from src.api.market import get_market_summary
    now = datetime.now(IST)
    summ = get_market_summary() or {}
    spot_of = {"NIFTY": summ.get("nifty"), "SENSEX": summ.get("sensex")}
    vix = summ.get("vix")
    sigma = (float(vix) / 100.0) if vix else _VIX_FALLBACK

    legs = []
    try:
        names = multi.names()
    except Exception:
        names = []
    for name in names:
        try:
            if not runner.is_running(name):
                continue
            snap = multi.get(name).snapshot()
        except Exception:
            continue
        open_trades = (snap or {}).get("open_trades") or {}
        rows = open_trades.values() if isinstance(open_trades, dict) else open_trades
        for t in rows:
            try:
                opt = str(t.get("option_type") or "").upper()
                if opt not in ("CE", "PE"):
                    continue                      # only option legs carry Greeks
                inst = str(t.get("instrument") or "").upper()
                underlying = "SENSEX" if "SENSEX" in inst else "NIFTY"
                qty = float(t.get("quantity") or t.get("lots") or 0) or 0.0
                if qty <= 0:
                    continue
                strike = float(t.get("strike") or 0) or 0.0
                prem = float(t.get("ltp") if t.get("ltp") is not None else
                             (t.get("current_price") or t.get("entry_price") or 0)) or 0.0
                spot = spot_of.get(underlying)
                tau = _days_to_expiry(underlying, now)
                g = _bs(spot or 0, strike, tau, sigma, opt) if spot else \
                    {"price": None, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
                legs.append({
                    "strategy": name, "underlying": underlying, "instrument": inst,
                    "strike": strike, "option_type": opt, "qty": qty,
                    "premium": prem, "premium_at_risk": prem * qty,
                    "spot": spot, "t_years": tau, "iv_used": sigma,
                    # Greeks scaled by position size (long → positive qty).
                    "delta": g["delta"] * qty, "gamma": g["gamma"] * qty,
                    "vega": g["vega"] * qty, "theta": g["theta"] * qty,
                })
            except Exception:
                continue

    # ── Aggregate ─────────────────────────────────────────────────────────────
    def _sum(k):
        return round(sum(l[k] for l in legs), 2)

    net_delta = _sum("delta")
    premium_at_risk = _sum("premium_at_risk")
    # Directional lean in ₹ per 1% index move (uses each leg's own spot).
    delta_rupees_1pct = round(sum(l["delta"] * (l["spot"] or 0) * 0.01 for l in legs), 0)

    by_underlying = {}
    for l in legs:
        u = by_underlying.setdefault(l["underlying"], {
            "positions": 0, "premium_at_risk": 0.0, "net_delta": 0.0,
            "ce": 0, "pe": 0, "spot": l["spot"]})
        u["positions"] += 1
        u["premium_at_risk"] += l["premium_at_risk"]
        u["net_delta"] += l["delta"]
        u["ce" if l["option_type"] == "CE" else "pe"] += 1
    for u in by_underlying.values():
        u["premium_at_risk"] = round(u["premium_at_risk"], 2)
        u["net_delta"] = round(u["net_delta"], 2)

    by_strategy = {}
    for l in legs:
        s = by_strategy.setdefault(l["strategy"], {"positions": 0, "premium_at_risk": 0.0, "net_delta": 0.0})
        s["positions"] += 1
        s["premium_at_risk"] = round(s["premium_at_risk"] + l["premium_at_risk"], 2)
        s["net_delta"] = round(s["net_delta"] + l["delta"], 2)

    # ── Stress ladder: full BS reprice at each shocked spot (captures gamma) ────
    stress = []
    for m in _STRESS:
        pnl = 0.0
        for l in legs:
            if not l["spot"]:
                continue
            g2 = _bs(l["spot"] * (1 + m), l["strike"], l["t_years"], l["iv_used"], l["option_type"])
            if g2["price"] is None:
                continue
            pnl += (g2["price"] - l["premium"]) * l["qty"]
        stress.append({"move_pct": round(m * 100, 1), "pnl": round(pnl, 0)})

    # ── VaR: delta-normal 1-day 95% (σ_daily from VIX). Reported alongside the
    #    hard max-loss (premium_at_risk), which is the true worst case for longs. ─
    sig_daily = sigma / math.sqrt(252.0)
    var_1d_95 = 0.0
    for l in legs:
        if l["spot"]:
            var_1d_95 += abs(l["delta"]) * l["spot"] * sig_daily
    var_1d_95 = round(1.645 * var_1d_95, 0)

    largest = max(legs, key=lambda l: l["premium_at_risk"], default=None)

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "positions": len(legs),
        "legs": sorted(legs, key=lambda l: -l["premium_at_risk"]),
        "net_greeks": {"delta": net_delta, "gamma": _sum("gamma"),
                       "vega": _sum("vega"), "theta": _sum("theta")},
        "delta_rupees_per_1pct": delta_rupees_1pct,
        "directional_lean": ("BULLISH" if net_delta > 0.01 else
                             "BEARISH" if net_delta < -0.01 else "NEUTRAL"),
        "premium_at_risk": premium_at_risk,       # max loss for a long-options book
        "var_1d_95": var_1d_95,
        "stress": stress,
        "by_underlying": by_underlying,
        "by_strategy": by_strategy,
        "largest_position": largest,
        "inputs": {"vix": vix, "iv_used_pct": round(sigma * 100, 2),
                   "nifty": spot_of["NIFTY"], "sensex": spot_of["SENSEX"],
                   "rate_pct": _R * 100},
        "assumptions": [
            "IV proxied from India VIX (σ = VIX/100) — not per-strike broker IV.",
            "Positions treated as long (option buyers); max loss = premium outstanding.",
            "Time-to-expiry from next weekly expiry (NIFTY→Tue, SENSEX→Thu).",
            "Observe-only: no orders placed, no state changed.",
        ],
    }
