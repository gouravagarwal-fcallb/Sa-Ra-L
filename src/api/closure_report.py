"""
Daily Closure Report + No-Trade Audit
═════════════════════════════════════
The platform must never end a session with "zero trades and zero explanation"
(UI_FE_Pg2 PART X). This module produces, on demand or at EOD, a complete evidence
trail per strategy:

  • what it analysed (analysis cycles),
  • what it rejected and WHY (no-trade reason histogram),
  • what it traded and the outcome (from the trade log),
  • a usefulness verdict for the day.

DESIGN — zero new write-hooks in the live trading path (lowest risk):
It reconstructs everything from data the platform ALREADY persists:
  1. logs/dashboard/<name>_<date>.jsonl   — every strategy's log lines {ts,category,message}
  2. logs/trades_<date>.csv               — every entry/exit event
  3. the cached pre-market conclusion      — market context for the day
Each non-trade analysis line is classified into a no-trade reason bucket by a
keyword engine. Limitations (no rigorous predicted-vs-actual outcome scoring yet)
are stated honestly in the report rather than faked.
"""
from __future__ import annotations

import os
import csv
import glob
import json
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
_DASH_DIR = "logs/dashboard"


# ── No-trade reason classifier ───────────────────────────────────────────────
# Ordered keyword → (bucket, human label). First match wins. Buckets mirror the
# rejection taxonomy in the spec (no setup / low confidence / filter / cooldown /
# stale data / broker / expiry / warming up / event window / regime).
_REASON_RULES = [
    (("warming", "insufficient bar", "need more bar", "not enough data", "building range",
      "reconstruct"), ("warming_up", "Warming up / insufficient bars")),
    (("vix", "volatility too", "vol filter", "atr too", "too volatile", "panic"),
     ("vol_filter", "Volatility filter")),
    (("cooldown", "one trade", "already in", "max trades", "position open", "daily limit",
      "trade cap", "locked"), ("cooldown", "Cooldown / one-trade rule")),
    (("expiry", "non-expiry", "only on expiry", "not expiry"),
     ("expiry_block", "Expiry-day rule")),
    (("event", "rbi", "fomc", "budget", "blackout", "news window"),
     ("event_block", "Event-risk window")),
    (("stale", "incomplete data", "no data", "feed", "disconnect", "missing bar"),
     ("stale_data", "Stale / missing data")),
    (("broker", "order reject", "margin", "execution", "qty=0", "lot", "capital", "budget",
      "stop rule"), ("broker_block", "Execution / capital constraint")),
    (("trend", "against trend", "wrong side", "counter-trend", "adx"),
     ("trend_filter", "Trend filter")),
    (("liquidity", "thin", "spread too", "low volume", "volume below"),
     ("liquidity_filter", "Liquidity filter")),
    (("regime", "range day", "not a trend day", "chop", "no edge", "neutral", "sideways"),
     ("regime_mismatch", "Regime mismatch / no edge")),
    (("confidence", "score too low", "below threshold", "weak signal", "not strong",
      "rsi not", "no breakout", "not oversold", "not overbought"),
     ("low_confidence", "Setup too weak / below threshold")),
    (("no setup", "no signal", "skip", "stand aside", "waiting", "watching", "monitor",
      "scanning", "idle", "no trade"), ("no_setup", "No setup found")),
]


def classify_no_trade(message: str):
    """Map an analysis/signal log line to a (bucket, label) no-trade reason, or None
    if the line isn't a recognisable no-trade/analysis statement."""
    m = (message or "").lower()
    for keys, out in _REASON_RULES:
        if any(k in m for k in keys):
            return out
    return None


# ── Raw data loaders ─────────────────────────────────────────────────────────
def _today() -> str:
    return datetime.now(IST).date().isoformat()


def _read_strategy_logs(day: str) -> dict:
    """name -> [ {ts, category, message}, ... ] from logs/dashboard/<name>_<day>.jsonl."""
    out: dict[str, list] = {}
    for path in glob.glob(os.path.join(_DASH_DIR, f"*_{day}.jsonl")):
        base = os.path.basename(path)
        name = base[: -len(f"_{day}.jsonl")]
        if name in ("_market", "market"):
            continue
        rows = []
        try:
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        rows.append(json.loads(ln))
        except Exception:
            continue
        out[name] = rows
    return out


def _read_trades(day: str) -> list:
    path = f"logs/trades_{day}.csv"
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _fnum(x):
    try:
        return float(x)
    except Exception:
        return 0.0


# ── Per-strategy analysis ────────────────────────────────────────────────────
def _exit_event(ev: str) -> bool:
    return (ev or "").upper() in ("EXIT", "EXITED", "SL", "TARGET", "T1", "T2", "T3",
                                  "BOOKING", "SQUAREOFF", "SQUARE_OFF", "CLOSE", "CLOSED")


def _strategy_block(name: str, cfg: dict, runtime: dict, logs: list, trades: list) -> dict:
    cats = defaultdict(int)
    reasons = defaultdict(int)
    no_trade_cycles = 0
    analysis_cycles = 0
    errors = 0
    for e in logs:
        cat = (e.get("category") or "").upper()
        cats[cat] += 1
        if cat == "ERROR":
            errors += 1
        if cat in ("ANALYSIS", "SIGNAL", "ANALYSE"):
            analysis_cycles += 1
            r = classify_no_trade(e.get("message", ""))
            if r:
                no_trade_cycles += 1
                reasons[r[1]] += 1

    # Trade outcomes from the CSV (entries vs exits; PnL on exits).
    my_trades = [t for t in trades if (t.get("strategy") or "").upper() == name.upper()]
    exits = [t for t in my_trades if _exit_event(t.get("event"))]
    entries = [t for t in my_trades if (t.get("event") or "").upper() in ("ENTRY", "ENTERED", "BUY", "SELL")]
    pnls = [_fnum(t.get("pnl")) for t in exits if t.get("pnl") not in (None, "")]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    total_pnl = round(sum(pnls), 2)
    win_rate = round(wins / len(pnls) * 100, 1) if pnls else None

    exit_breakdown = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for t in exits:
        rk = (t.get("exit_reason") or "exit").upper()
        exit_breakdown[rk]["count"] += 1
        exit_breakdown[rk]["pnl"] = round(exit_breakdown[rk]["pnl"] + _fnum(t.get("pnl")), 2)

    score, verdict, headline = _usefulness(
        analysis_cycles=analysis_cycles, trades=len(exits) or len(entries),
        wins=wins, losses=losses, total_pnl=total_pnl, win_rate=win_rate,
        no_trade_cycles=no_trade_cycles, errors=errors, status=cfg.get("status"))

    return {
        "name": name,
        "full_name": cfg.get("full_name", name),
        "status": cfg.get("status"),
        "mode": runtime.get("mode"),
        "running": runtime.get("running", False),
        "analysis_cycles": analysis_cycles,
        "no_trade_cycles": no_trade_cycles,
        "log_lines": len(logs),
        "errors": errors,
        "trades": len(exits) or len(entries),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "pnl": total_pnl,
        "best_trade": round(max(pnls), 2) if pnls else None,
        "worst_trade": round(min(pnls), 2) if pnls else None,
        "no_trade_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "exit_breakdown": [{"reason": k, **v} for k, v in exit_breakdown.items()],
        "category_counts": dict(cats),
        "usefulness_score": score,
        "verdict": verdict,
        "headline": headline,
    }


def _usefulness(*, analysis_cycles, trades, wins, losses, total_pnl, win_rate,
                no_trade_cycles, errors, status):
    """Daily usefulness score (0-100) + a plain verdict. A no-trade day is judged
    intelligently — standing aside on a no-edge day is GOOD, not a failure."""
    # Broken / blind first.
    if errors >= 5 and analysis_cycles == 0:
        return 5, "Disable Pending Review", "Errors with no analysis logged — looks broken; check the engine."
    if analysis_cycles == 0 and trades == 0:
        return 20, "Blind — no analysis logged", "No analysis trail today; either it didn't run or it logs nothing."

    if trades > 0:
        if total_pnl > 0 and (win_rate is None or win_rate >= 55):
            return min(100, 70 + (wins - losses) * 3), "Highly Useful", \
                f"Traded and net positive (₹{total_pnl:+.0f}, {wins}W/{losses}L)."
        if total_pnl < 0:
            return max(15, 45 - losses * 3), "Needs Recalibration", \
                f"Traded but net negative (₹{total_pnl:+.0f}, {wins}W/{losses}L) — review exits/thresholds."
        return 50, "Neutral / Inconclusive", f"Traded near break-even (₹{total_pnl:+.0f})."

    # No trades, but it analysed.
    blocked_heavy = no_trade_cycles >= max(20, analysis_cycles * 0.6)
    if blocked_heavy:
        return 55, "Useful but possibly Over-Filtered", \
            f"Analysed {analysis_cycles}× and stood aside every time — confirm a real move wasn't missed."
    return 65, "Useful — stood aside", \
        f"Analysed {analysis_cycles}× and found no qualifying edge — disciplined no-trade day."


# ── Market context + narrative ───────────────────────────────────────────────
def _scored_headline(b: dict, sc: dict) -> str:
    ntc = sc.get("no_trade_correctness")
    of = sc.get("over_filtered_rate") or 0
    mr = sc.get("missed_opportunity_rate") or 0
    if of >= 0.4 or mr >= 0.4:
        return (f"Graded: {int((of+mr)*100)}% of no-trades were over-filtered/missed vs actual "
                f"index moves — thresholds look too strict.")
    if ntc is not None and ntc >= 0.7:
        return f"Graded: {int(ntc*100)}% of no-trades were correct vs actual moves — disciplined."
    return f"Graded against actuals: correctness {int((ntc or 0)*100)}%, over-filtered {int(of*100)}%."


def _market_context() -> dict:
    try:
        from src.api.premarket import build_premarket
        pm = build_premarket(False)
        c = pm.get("conclusion", {}) or {}
        b = pm.get("briefing", {}) or {}
        return {
            "available": bool(c.get("available")),
            "direction": c.get("direction"),
            "conviction": c.get("conviction"),
            "score": c.get("score"),
            "india_vix": b.get("india_vix"),
            "is_expiry_day": c.get("is_expiry_day"),
            "high_risk_events": b.get("high_risk_events", []),
        }
    except Exception:
        return {"available": False}


def _no_trade_review(strategies: list, ctx: dict, total_trades: int) -> list:
    """Generate the mandatory 'why no trades today?' narrative bullets."""
    out = []
    if total_trades > 0:
        out.append(f"{total_trades} trade(s) were taken today — see per-strategy outcomes above.")
        return out
    # zero-trade day — explain WHY using the aggregate evidence
    blind = [s["name"] for s in strategies if s["analysis_cycles"] == 0]
    analysed = [s for s in strategies if s["analysis_cycles"] > 0]
    if analysed:
        out.append(f"{len(analysed)} strategy(ies) actively analysed the market and none found a "
                   "qualifying setup — the most common reason was: "
                   + (_dominant_reason(analysed) or "no setup found") + ".")
    if blind:
        out.append("⚠ These logged NO analysis at all (either not running, or they don't emit "
                   "per-cycle reasoning): " + ", ".join(blind) + ".")
    if ctx.get("available"):
        conv = (ctx.get("conviction") or "").upper()
        if conv in ("STRONG", "MODERATE"):
            out.append(f"Pre-market read was {ctx.get('direction')} ({conv} conviction) — if the index "
                       "actually trended, some strategies may have been over-filtered; review their thresholds.")
        else:
            out.append("Pre-market read was low-conviction / rangey — a quiet, no-edge day where standing "
                       "aside is the correct professional outcome.")
    if ctx.get("is_expiry_day"):
        out.append("Today was an F&O expiry day — several strategies deliberately avoid expiry gamma risk.")
    out.append("Note: rigorous predicted-vs-actual outcome scoring (was each no-trade provably correct?) "
               "is not computed in this v1 — it reports what was analysed and why, not a hindsight verdict.")
    return out


def _dominant_reason(strategies: list):
    agg = defaultdict(int)
    for s in strategies:
        for k, v in (s.get("no_trade_reasons") or {}).items():
            agg[k] += v
    if not agg:
        return None
    return max(agg.items(), key=lambda kv: kv[1])[0]


def _top_lessons(strategies: list, ctx: dict) -> list:
    lessons = []
    if ctx.get("available"):
        regime = ("trending" if (ctx.get("conviction") or "").upper() in ("STRONG", "MODERATE")
                  else "range / low-conviction")
        lessons.append(f"Market regime read: {regime} ({ctx.get('direction')}, VIX {ctx.get('india_vix')}).")
    useful = [s["name"] for s in strategies if s["verdict"] in ("Highly Useful", "Useful — stood aside")]
    recal  = [s["name"] for s in strategies if s["verdict"] in ("Needs Recalibration", "Useful but possibly Over-Filtered")]
    broken = [s["name"] for s in strategies if "Blind" in s["verdict"] or "Disable" in s["verdict"]]
    if useful:
        lessons.append("Behaved well today: " + ", ".join(useful) + ".")
    if recal:
        lessons.append("Review before next session (recalibrate / check over-filtering): " + ", ".join(recal) + ".")
    if broken:
        lessons.append("⚠ Needs attention (no analysis trail / errors): " + ", ".join(broken) + ".")
    return lessons


# ── Public entrypoint ────────────────────────────────────────────────────────
def build_closure_report(registry: dict, multi=None, runner=None, day: str | None = None) -> dict:
    day = day or _today()
    is_today = (day == _today())
    strategies_cfg = (registry or {}).get("strategies", registry or {})
    logs_by_name = _read_strategy_logs(day)
    trades = _read_trades(day)

    blocks = []
    for name, cfg in strategies_cfg.items():
        if not isinstance(cfg, dict):
            continue
        runtime = {}
        try:
            # Live runtime only makes sense for today; a historical report reads files.
            if runner is not None and is_today:
                runtime = runner.runtime_status(name) or {}
        except Exception:
            runtime = {}
        logs = logs_by_name.get(name, [])
        # only include strategies that have a trail or ran today (keeps the report focused)
        blocks.append(_strategy_block(name, cfg, runtime, logs, trades))

    # order: most useful / most active first
    blocks.sort(key=lambda b: (-b["usefulness_score"], -b["analysis_cycles"], b["name"]))

    total_trades = sum(b["trades"] for b in blocks)
    total_pnl = round(sum(b["pnl"] for b in blocks), 2)
    total_wins = sum(b["wins"] for b in blocks)
    total_losses = sum(b["losses"] for b in blocks)
    total_analysis = sum(b["analysis_cycles"] for b in blocks)
    total_no_trade = sum(b["no_trade_cycles"] for b in blocks)
    total_errors = sum(b["errors"] for b in blocks)
    # ── Redesign v2: grade cycles vs actuals, benchmark, telemetry, trust ──────
    scoring = benchmarks = None
    try:
        from src.api.scoring import score_session
        scoring = score_session(registry, day)
    except Exception:
        scoring = None
    try:
        from src.api.benchmarks import benchmark_session
        benchmarks = benchmark_session(day)
    except Exception:
        benchmarks = None

    blind = []
    for b in blocks:
        b["telemetry_ok"] = b["analysis_cycles"] > 0
        if not b["telemetry_ok"]:
            blind.append(b["name"])
        sc = (scoring or {}).get("per_strategy", {}).get(b["name"]) if scoring else None
        if sc:
            b["scored"] = {k: sc[k] for k in ("labels", "no_trade_correctness",
                          "missed_opportunity_rate", "over_filtered_rate", "avg_score",
                          "verifiable", "scored")}
            # Overlay a graded verdict (provisional trust — not persisted on view).
            if sc.get("verifiable"):
                try:
                    from src.api.trust import compute_session, trust_weight
                    metrics = {
                        "telemetry_ok": b["telemetry_ok"], "scored": sc["scored"],
                        "trades": b["trades"], "expectancy": (b["pnl"] / b["trades"]) if b["trades"] else 0,
                        "no_trade_correctness": sc["no_trade_correctness"],
                        "over_filtered_rate": sc["over_filtered_rate"],
                        "missed_opportunity_rate": sc["missed_opportunity_rate"],
                    }
                    t = compute_session(b["name"], day, metrics)
                    b["trust_score"] = t["trust_score"]
                    b["trust_delta"] = t["trust_delta"]
                    b["trust_weight"] = trust_weight(t["trust_score"])
                    b["verdict"] = t["classification"]
                    b["usefulness_score"] = int(t["trust_score"])
                    b["headline"] = _scored_headline(b, sc)
                except Exception:
                    pass

    telemetry = {
        "blind": blind,
        "action": ("Auto-demote to paper next session + fix per-cycle analysis emission "
                   "before re-arming live." if blind else "All reporting strategies emit a trail."),
    }

    active = [b["name"] for b in blocks if b["running"]]
    # Pre-market context is a live read — only attach it for today's report.
    ctx = _market_context() if is_today else {
        "available": False, "note": "historical day — live pre-market context not re-derived"}

    return {
        "date": day,
        "generated_at": datetime.now(IST).isoformat(),
        "summary": {
            "strategies_reported": len(blocks),
            "strategies_running": len(active),
            "total_analysis_cycles": total_analysis,
            "total_no_trade_cycles": total_no_trade,
            "total_trades": total_trades,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": round(total_wins / (total_wins + total_losses) * 100, 1)
                        if (total_wins + total_losses) else None,
            "net_pnl": total_pnl,
            "data_incidents": total_errors,
        },
        "market_context": ctx,
        "per_strategy": blocks,
        "benchmarks": benchmarks,
        "scoring": {"verifiable": bool(scoring and scoring.get("verifiable")),
                    "thresholds": (scoring or {}).get("thresholds"),
                    "filter_audit": (scoring or {}).get("filter_audit", []),
                    "note": (scoring or {}).get("note")},
        "telemetry": telemetry,
        "no_trade_review": _no_trade_review(blocks, ctx, total_trades),
        "top_lessons": _top_lessons(blocks, ctx),
        "caveat": ("Closure v2: no-trade cycles are graded against the replayed index path "
                   "(correct stand-aside vs over-filtered/missed). When index actuals are "
                   "unavailable the grade is omitted and cycles are marked unverifiable. Trust "
                   "shown is provisional on view; it is committed once per day by the EOD job."),
    }


# ── Exports ──────────────────────────────────────────────────────────────────
def to_markdown(rep: dict) -> str:
    s = rep["summary"]
    L = [f"# Daily Closure Report — {rep['date']}",
         f"_generated {rep['generated_at']}_", "",
         "## Executive summary",
         f"- Strategies reported: **{s['strategies_reported']}** ({s['strategies_running']} running)",
         f"- Analysis cycles: **{s['total_analysis_cycles']}** · no-trade cycles: **{s['total_no_trade_cycles']}**",
         f"- Trades: **{s['total_trades']}** · W/L: {s['wins']}/{s['losses']} · "
         f"win-rate: {s['win_rate'] if s['win_rate'] is not None else '—'}%",
         f"- Net P&L: **₹{s['net_pnl']:+.0f}** · data incidents: {s['data_incidents']}", ""]
    ctx = rep.get("market_context", {})
    if ctx.get("available"):
        L += ["## Market context",
              f"- Pre-market: **{ctx.get('direction')}** ({ctx.get('conviction')}, score {ctx.get('score')})",
              f"- India VIX: {ctx.get('india_vix')} · expiry day: {ctx.get('is_expiry_day')}", ""]
    L += ["## Why no trades / what happened"]
    L += [f"- {b}" for b in rep.get("no_trade_review", [])]
    L += ["", "## Per-strategy"]
    for b in rep["per_strategy"]:
        L.append(f"### {b['name']} — {b['verdict']} (score {b['usefulness_score']})")
        L.append(f"- {b['headline']}")
        L.append(f"- status {b['status']} · mode {b['mode']} · analysis {b['analysis_cycles']} · "
                 f"trades {b['trades']} (W{b['wins']}/L{b['losses']}) · P&L ₹{b['pnl']:+.0f}")
        if b["no_trade_reasons"]:
            rs = ", ".join(f"{k} ×{v}" for k, v in b["no_trade_reasons"].items())
            L.append(f"- no-trade reasons: {rs}")
    L += ["", "## Top lessons"]
    L += [f"- {x}" for x in rep.get("top_lessons", [])]
    L += ["", f"> {rep.get('caveat','')}"]
    return "\n".join(L)


def to_csv(rep: dict) -> str:
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["strategy", "status", "mode", "running", "analysis_cycles", "no_trade_cycles",
                "trades", "wins", "losses", "win_rate", "pnl", "usefulness_score", "verdict",
                "top_no_trade_reason"])
    for b in rep["per_strategy"]:
        top_r = next(iter(b["no_trade_reasons"]), "")
        w.writerow([b["name"], b["status"], b["mode"], b["running"], b["analysis_cycles"],
                    b["no_trade_cycles"], b["trades"], b["wins"], b["losses"],
                    b["win_rate"] if b["win_rate"] is not None else "", b["pnl"],
                    b["usefulness_score"], b["verdict"], top_r])
    return buf.getvalue()
