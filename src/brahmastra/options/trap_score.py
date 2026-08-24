"""
Seller-Trap Score (v1) — PASHUPATASTRA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Turns an option-chain snapshot into a 0–100 score for "is a seller genuinely
trapped at a wall right now, about to be forced to cover?" — the actual alpha
of PASHUPATASTRA (see strategies/PASHUPATASTRA_v1/V2_REENGINEERED.md §2a).

This is v1: a sensible, transparent first cut. Its weights are NOT tuned — they
are meant to be CALIBRATED by the measurement loop (oi_analyze.py), which records
each fire and measures how often the 2× actually comes. We log the score live so
that, in a few weeks of recorded expiries, "filter_skill" stops being an
assumption and becomes a measured number.

Factors (per V2 §2a), each contributing to the 0–100 score:
  naked wall size      25  — wall OI ÷ median nearby OI (more = more trapped fuel)
  ΔOI flip velocity    25  — wall OI being COVERED (falling) as price presses it
  activity confirm     15  — magnitude of OI change at the wall (real action)
  air pocket           10  — thin OI on the next strike beyond the wall
  IV cheapness         10  — low IV-percentile = vega tailwind, no crush trap
  gamma / time         10  — later in session / 0DTE = sharper squeeze
  naked-vs-sticky       5  — one-sided naked wall (squeezes) vs symmetric condor
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrapScore:
    score:            float                 # 0–100
    side:             Optional[str]         # "CE" (up-squeeze) | "PE" (down-squeeze) | None
    wall_strike:      Optional[int]         # the heavily-written strike under pressure
    candidate_strike: Optional[int]         # the option we'd BUY (just beyond the wall)
    factors:          dict = field(default_factory=dict)

    def fires(self, threshold: float = 75.0) -> bool:
        return self.side is not None and self.score >= threshold


def _median(xs):
    return statistics.median(xs) if xs else 0.0


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def compute_trap_score(
    snap,
    prev=None,
    strike_step: int = 50,
    press_dist_pct: float = 0.0025,   # "pressing" the wall = spot within 0.25%
    band: int = 6,                    # ± strikes considered "nearby"
) -> TrapScore:
    """
    snap, prev : OptionsSnapshot (from OptionsIntelEngine.get_snapshot). `prev` is the
                 previous fetch for the same instrument (for intraday ΔOI velocity).
    Returns a TrapScore. side=None means no trap (score reported for logging anyway).
    """
    strikes = getattr(snap, "strike_data", None) or []
    spot = float(getattr(snap, "spot_price", 0) or 0)
    if not strikes or spot <= 0:
        return TrapScore(0.0, None, None, None, {"reason": "no chain/spot"})

    by_strike = {s.strike: s for s in strikes}
    nearby = sorted(strikes, key=lambda s: abs(s.strike - spot))[:2 * band + 1]
    med_oi = _median([s.call_oi + s.put_oi for s in nearby]) or 1.0

    # Heaviest call/put wall NEAR spot (not strictly above/below) — the trap stays the
    # trap as price CROSSES it; filtering by side loses the wall the moment it breaks.
    call_wall = max(nearby, key=lambda s: s.call_oi, default=None)
    put_wall = max(nearby, key=lambda s: s.put_oi, default=None)

    prev_by_strike = {s.strike: s for s in (getattr(prev, "strike_data", None) or [])}

    def evaluate(wall, kind):
        """Return (score, candidate_strike, factor_dict) for a CE/PE trap at `wall`."""
        if wall is None:
            return 0.0, None, {}
        oi = wall.call_oi if kind == "CE" else wall.put_oi
        chg = wall.call_chg_oi if kind == "CE" else wall.put_chg_oi
        # 1) wall size vs median (25)
        ratio = oi / med_oi if med_oi else 0.0
        f_size = _clamp((ratio - 1.0) / 2.0) * 25.0          # ratio 1→3 maps 0→25

        # 2) ΔOI flip velocity — covering = OI FALLING as price presses (25)
        pressing = (spot >= wall.strike - press_dist_pct * spot) if kind == "CE" \
            else (spot <= wall.strike + press_dist_pct * spot)
        intraday_doi = 0.0
        pw = prev_by_strike.get(wall.strike)
        if pw is not None:
            intraday_doi = (wall.call_oi - pw.call_oi) if kind == "CE" else (wall.put_oi - pw.put_oi)
        covering = intraday_doi < 0 or chg < 0
        cover_mag = _clamp(abs(min(intraday_doi, chg, 0.0)) / (oi * 0.10 + 1.0))  # vs 10% of wall
        f_flip = (25.0 if pressing else 12.0) * cover_mag if covering else 0.0

        # 3) activity confirmation (15) — magnitude of change at the wall
        f_act = _clamp(abs(chg) / (med_oi * 0.5 + 1.0)) * 15.0

        # 4) air pocket — thin OI on the next strike beyond the wall (10)
        nxt = wall.strike + strike_step if kind == "CE" else wall.strike - strike_step
        nxt_s = by_strike.get(nxt)
        nxt_oi = (nxt_s.call_oi if kind == "CE" else nxt_s.put_oi) if nxt_s else 0.0
        f_air = _clamp(1.0 - nxt_oi / (oi + 1.0)) * 10.0

        # 5) IV cheapness (10)
        ivp = getattr(snap, "iv_percentile", None)
        f_iv = (_clamp((40.0 - ivp) / 40.0) * 10.0) if ivp is not None else 5.0

        # 6) gamma / time-of-day (10)
        ts = getattr(snap, "fetched_at", None)
        hod = (ts.hour + ts.minute / 60.0) if ts else 12.0
        is_expiry = bool(getattr(snap, "expiry", "")) and getattr(snap, "_is_expiry", True)
        f_gamma = _clamp((hod - 9.25) / (15.5 - 9.25)) * (10.0 if is_expiry else 5.0)

        # 7) naked-vs-sticky (5): penalise a symmetric opposing wall (condor = won't run)
        opp = put_wall if kind == "CE" else call_wall
        opp_oi = (opp.put_oi if kind == "CE" and opp else
                  (opp.call_oi if opp else 0.0))
        symmetry = _clamp(opp_oi / (oi + 1.0))          # 1 = symmetric (sticky)
        f_naked = (1.0 - symmetry) * 5.0

        total = f_size + f_flip + f_act + f_air + f_iv + f_gamma + f_naked
        candidate = wall.strike + strike_step if kind == "CE" else wall.strike - strike_step
        return round(total, 1), candidate, {
            "wall_oi": oi, "wall_ratio": round(ratio, 2), "pressing": pressing,
            "covering": covering, "intraday_doi": intraday_doi, "chg_oi": chg,
            "f_size": round(f_size, 1), "f_flip": round(f_flip, 1), "f_act": round(f_act, 1),
            "f_air": round(f_air, 1), "f_iv": round(f_iv, 1), "f_gamma": round(f_gamma, 1),
            "f_naked": round(f_naked, 1),
        }

    ce_score, ce_cand, ce_f = evaluate(call_wall, "CE")
    pe_score, pe_cand, pe_f = evaluate(put_wall, "PE")

    if ce_score >= pe_score and ce_score > 0:
        return TrapScore(ce_score, "CE", call_wall.strike if call_wall else None, ce_cand, ce_f)
    if pe_score > 0:
        return TrapScore(pe_score, "PE", put_wall.strike if put_wall else None, pe_cand, pe_f)
    return TrapScore(0.0, None, None, None, {"reason": "no qualifying wall"})
