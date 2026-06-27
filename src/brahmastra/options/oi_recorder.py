"""
OI Recorder — PASHUPATASTRA measurement loop, step 1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Logs the live NSE option chain to disk every `interval_sec` during market hours,
computes the Seller-Trap Score (trap_score.py) on each fetch, and persists BOTH
the aggregate snapshot and the per-strike rows (OI, ΔOI, IV, LTP) so the edge can
be MEASURED later (oi_analyze.py): "when trap_score ≥ X, how often did the 2× come?"

This is the single highest-leverage build in PASHUPATASTRA — it converts the
backtest's assumed `filter_skill` into a measured number (V2_REENGINEERED.md §2).

Storage (append-only, one JSON object per fetch):
    data/oi_recordings/{INSTRUMENT}/{YYYY-MM-DD}.jsonl

Run live (during market hours, after `python main.py --mode login`):
    python record_oi.py                      # NIFTY, every 120s
    python record_oi.py --instruments NIFTY SENSEX --interval 60
    python record_oi.py --once               # single snapshot (smoke test)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from .trap_score import compute_trap_score, TrapScore

IST = timezone(timedelta(hours=5, minutes=30))

# Strikes within ±BAND_STRIKES of spot are persisted (enough to track walls + the
# candidate option's LTP forward for outcome labelling). Keeps files compact.
BAND_STRIKES = 20


def _safe(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f
    except Exception:
        return None


class OIRecorder:
    def __init__(
        self,
        instruments: Optional[list[str]] = None,
        out_dir: str = "data/oi_recordings",
        interval_sec: int = 120,
        trap_threshold: float = 75.0,
        strike_step: dict | None = None,
        engine=None,
        logger=None,
        alerter=None,
    ):
        self.instruments = instruments or ["NIFTY"]
        self.alerter = alerter
        self.out_dir = out_dir
        self.interval_sec = interval_sec
        self.trap_threshold = trap_threshold
        self.strike_step = strike_step or {"NIFTY": 50, "SENSEX": 100, "BANKNIFTY": 100}
        self._prev: dict[str, object] = {}          # instrument -> last OptionsSnapshot
        self.log = logger or _default_logger()
        if engine is None:
            from .options_intel import OptionsIntelEngine
            engine = OptionsIntelEngine()
        self.engine = engine

    # ── paths ──────────────────────────────────────────────────────────────────
    def _path(self, instrument: str, day) -> str:
        d = os.path.join(self.out_dir, instrument)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{day.isoformat()}.jsonl")

    # ── one fetch ──────────────────────────────────────────────────────────────
    def record_once(self, now: Optional[datetime] = None) -> list[dict]:
        now = now or datetime.now(IST)
        out = []
        for inst in self.instruments:
            try:
                rec = self._record_instrument(inst, now)
                if rec is not None:
                    out.append(rec)
            except Exception as e:                  # never let one instrument kill the loop
                self.log.warning(f"[oi_recorder] {inst} fetch/record failed: {e}")
        return out

    def _record_instrument(self, inst: str, now: datetime) -> Optional[dict]:
        step = self.strike_step.get(inst, 50)
        snap = self.engine.get_snapshot(inst, force=True)
        if snap is None or getattr(snap, "error", None):
            self.log.warning(f"[oi_recorder] {inst}: no snapshot ({getattr(snap,'error','None')})")
            return None

        # tag expiry-day for the trap-score gamma factor
        try:
            from src.utils.market_calendar import is_nifty_expiry_day, is_sensex_expiry_day
            snap._is_expiry = (is_nifty_expiry_day() if inst == "NIFTY"
                               else is_sensex_expiry_day() if inst == "SENSEX" else False)
        except Exception:
            snap._is_expiry = True

        trap = compute_trap_score(snap, self._prev.get(inst), strike_step=step)
        self._prev[inst] = snap

        rec = self._to_record(inst, snap, trap, now)
        path = self._path(inst, now.date())
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

        if self.alerter is not None:
            try:
                self.alerter.on_chain(inst, snap, trap, now)
            except Exception as e:
                self.log.warning(f"[oi_recorder] alerter failed: {e}")

        if trap.fires(self.trap_threshold):
            self.log.info(
                f"[oi_recorder] 🎯 TRAP {inst} {trap.side} score={trap.score} "
                f"wall={trap.wall_strike} buy={trap.candidate_strike} spot={rec['spot']}"
            )
        else:
            self.log.info(f"[oi_recorder] {inst} logged spot={rec['spot']} "
                          f"trap={trap.score} pcr={rec['pcr']} -> {os.path.basename(path)}")
        return rec

    def _to_record(self, inst: str, snap, trap: TrapScore, now: datetime) -> dict:
        spot = _safe(snap.spot_price) or 0.0
        rows = []
        for s in sorted(getattr(snap, "strike_data", []) or [], key=lambda x: x.strike):
            if abs(s.strike - spot) > BAND_STRIKES * self.strike_step.get(inst, 50):
                continue
            rows.append({
                "k": s.strike,
                "ce_oi": _safe(s.call_oi), "pe_oi": _safe(s.put_oi),
                "ce_doi": _safe(s.call_chg_oi), "pe_doi": _safe(s.put_chg_oi),
                "ce_iv": _safe(s.call_iv), "pe_iv": _safe(s.put_iv),
                "ce_ltp": _safe(s.call_ltp), "pe_ltp": _safe(s.put_ltp),
            })
        return {
            "ts": now.isoformat(),
            "instrument": inst,
            "expiry": getattr(snap, "expiry", ""),
            "spot": round(spot, 2),
            "pcr": _safe(snap.pcr),
            "max_pain": getattr(snap, "max_pain", None),
            "atm_iv": _safe(snap.iv_current),
            "iv_percentile": _safe(snap.iv_percentile),
            "oi_buildup": getattr(snap, "oi_buildup", None),
            "top_call_oi": list(getattr(snap, "top_call_oi_strikes", []) or []),
            "top_put_oi": list(getattr(snap, "top_put_oi_strikes", []) or []),
            "trap": {
                "score": trap.score, "side": trap.side, "wall": trap.wall_strike,
                "candidate": trap.candidate_strike, "factors": trap.factors,
            },
            "strikes": rows,
        }

    # ── market-hours loop ──────────────────────────────────────────────────────
    def run(self, market_hours_only: bool = True, close_hhmm: str = "15:35"):
        ch, cm = int(close_hhmm[:2]), int(close_hhmm[3:])
        self.log.info(f"[oi_recorder] START instruments={self.instruments} "
                      f"interval={self.interval_sec}s out={self.out_dir}")
        try:
            from src.utils.market_calendar import is_trading_day
            if market_hours_only and not is_trading_day(datetime.now(IST).date()):
                self.log.info("[oi_recorder] not a trading day — exiting.")
                return
        except Exception:
            pass
        n = 0
        while True:
            now = datetime.now(IST)
            if market_hours_only and (now.hour, now.minute) >= (ch, cm):
                self.log.info(f"[oi_recorder] market close reached — recorded {n} cycles.")
                return
            self.record_once(now)
            n += 1
            time.sleep(self.interval_sec)


def _default_logger():
    try:
        from src.utils.logger import setup_logger
        return setup_logger("oi_recorder")
    except Exception:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        return logging.getLogger("oi_recorder")
