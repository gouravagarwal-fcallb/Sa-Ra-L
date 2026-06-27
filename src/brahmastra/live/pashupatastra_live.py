"""
PASHUPATASTRA — Live Engine (SHADOW by default)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The doctrine, made live: fuel gate → Seller-Trap Score → BOOK-2× (bread & butter)
+ RE-ENTRY → thin runner for the bonus tail. Sizing on the MEASURED edge.

Runs the SAME logic that will trade real money, but in `mode="shadow"` it places
NO orders — it journals every would-be trade and its P&L from the live option-chain
LTP series. This (a) validates the engine against the live tape with zero risk and
(b) accrues more labelled fires for calibration. Flip to mode="live" only after the
recorder's edge table (oi_analyze.py) confirms the trap signal is real.

See strategies/PASHUPATASTRA_v1/{V2_REENGINEERED.md, RECORDER.md, config.yaml}.

Run (shadow, during market hours, after `python main.py --mode login`):
    python pashupatastra_shadow.py
    python pashupatastra_shadow.py --instruments NIFTY SENSEX --interval 90
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.brahmastra.options.trap_score import compute_trap_score, TrapScore

IST = timezone(timedelta(hours=5, minutes=30))


# ── config ─────────────────────────────────────────────────────────────────────
@dataclass
class LiveCfg:
    slot_rs: float = 500_000.0
    astra_charge_rs: float = 10_000.0
    lot_size: dict = field(default_factory=lambda: {"NIFTY": 65, "SENSEX": 20})
    trap_threshold: float = 75.0
    # book-2x ladder
    scale1_mult: float = 2.0
    scale1_size: float = 0.85
    scale2_mult: float = 4.0
    scale2_size: float = 0.10
    runner_trail: float = 0.55
    # re-entry
    max_per_expiry: int = 4
    cooldown_min: int = 10
    # costs (mirror the backtest)
    slippage: float = 0.01           # 1% one-way on shadow fills (conservative)
    brokerage_per_order: float = 20.0
    stt_sell: float = 0.001
    exch_txn: float = 0.0005
    gst: float = 0.18
    # session
    hard_close: dict = field(default_factory=lambda: {"NIFTY": "15:10", "SENSEX": "15:15"})
    expiry_only: bool = False         # shadow can run any day to gather data


def load_cfg(path: str = "strategies/PASHUPATASTRA_v1/config.yaml") -> LiveCfg:
    cfg = LiveCfg()
    try:
        import yaml
        with open(path) as f:
            p = (yaml.safe_load(f) or {}).get("pashupatastra", {})
        cfg.astra_charge_rs = p.get("astra_charge_rs", cfg.astra_charge_rs)
        cfg.trap_threshold = p.get("trigger", {}).get("trap_threshold", cfg.trap_threshold)
        ex = p.get("exit", {})
        cfg.scale1_mult = ex.get("scale1_mult", cfg.scale1_mult)
        cfg.scale1_size = ex.get("scale1_size_pct", cfg.scale1_size)
        cfg.scale2_mult = ex.get("scale2_mult", cfg.scale2_mult)
        cfg.scale2_size = ex.get("scale2_size_pct", cfg.scale2_size)
        cfg.runner_trail = ex.get("runner_trail_pct", cfg.runner_trail)
        re = p.get("re_entry", {})
        cfg.max_per_expiry = re.get("max_per_expiry", cfg.max_per_expiry)
        cfg.cooldown_min = re.get("cooldown_min", cfg.cooldown_min)
    except Exception:
        pass
    return cfg


# ── shadow position ──────────────────────────────────────────────────────────
@dataclass
class Position:
    inst: str
    side: str               # CE / PE
    strike: int
    entry_ltp: float
    entry_fill: float
    qty: int
    cost: float
    entry_time: str
    peak: float
    pos_frac: float = 1.0
    booked1: bool = False
    booked2: bool = False
    realized: float = 0.0   # net proceeds booked so far


class PashupatastraLive:
    """
    Feed it option-chain snapshots via on_chain(); it runs the WATCH→ENTER→MANAGE→DONE
    doctrine and journals would-be trades. mode='shadow' (default) places no orders.
    """

    def __init__(self, cfg: Optional[LiveCfg] = None, mode: str = "shadow",
                 engine=None, recorder=None, notifier=None, logger=None,
                 journal: str = "strategies/PASHUPATASTRA_v1/results/shadow_trades.csv"):
        self.cfg = cfg or load_cfg()
        self.mode = mode
        self.notifier = notifier
        self.log = logger or _default_logger()
        self.journal = journal
        self._pos: dict[str, Optional[Position]] = {}
        self._expiry_count: dict[tuple, int] = {}      # (inst, date) -> entries taken
        self._cooldown_until: dict[str, datetime] = {}
        self.engine = engine
        self.recorder = recorder
        os.makedirs(os.path.dirname(journal), exist_ok=True)
        if not os.path.exists(journal):
            with open(journal, "w", newline="") as f:
                csv.writer(f).writerow([
                    "entry_time", "exit_time", "instrument", "mode", "side", "strike",
                    "entry_ltp", "qty", "cost", "net_pnl", "mult_on_capital", "peak_mult",
                    "exit_reason", "trap_score"])

    # ── helpers ────────────────────────────────────────────────────────────────
    @staticmethod
    def _ltp(snap, strike, side):
        for s in getattr(snap, "strike_data", []) or []:
            if s.strike == strike:
                return s.call_ltp if side == "CE" else s.put_ltp
        return None

    def _buy_charges(self, val):
        c = self.cfg
        return c.brokerage_per_order + c.exch_txn * val + c.gst * (c.brokerage_per_order + c.exch_txn * val)

    def _sell_charges(self, val):
        c = self.cfg
        return (c.brokerage_per_order + c.stt_sell * val + c.exch_txn * val
                + c.gst * (c.brokerage_per_order + c.exch_txn * val))

    def _past_close(self, inst, now):
        hh = self.cfg.hard_close.get(inst, "15:15")
        return (now.hour, now.minute) >= (int(hh[:2]), int(hh[3:]))

    # ── main entry point ─────────────────────────────────────────────────────────
    def on_chain(self, inst: str, snap, trap: TrapScore, now: datetime) -> list[dict]:
        events = []
        pos = self._pos.get(inst)

        # MANAGE open position
        if pos is not None:
            ev = self._manage(inst, pos, snap, now)
            if ev:
                events.append(ev)
            if self._pos.get(inst) is not None:     # still open -> no new entry this tick
                return events

        # ENTER — only if flat, signal fires, gates pass
        if self._can_enter(inst, trap, now):
            ev = self._open(inst, snap, trap, now)
            if ev:
                events.append(ev)
        return events

    def _can_enter(self, inst, trap, now) -> bool:
        if not trap.fires(self.cfg.trap_threshold):
            return False
        if self._past_close(inst, now):
            return False
        cu = self._cooldown_until.get(inst)
        if cu and now < cu:
            return False
        key = (inst, now.date())
        if self._expiry_count.get(key, 0) >= self.cfg.max_per_expiry:
            return False
        return True

    def _open(self, inst, snap, trap, now) -> Optional[dict]:
        ltp = self._ltp(snap, trap.candidate_strike, trap.side)
        if not ltp or ltp <= 0:
            return None
        lot = self.cfg.lot_size.get(inst, 65)
        fill = ltp * (1 + self.cfg.slippage)
        qty = int(self.cfg.astra_charge_rs // (fill * lot)) * lot
        if qty <= 0:
            return None
        cost = fill * qty + self._buy_charges(fill * qty)
        pos = Position(inst=inst, side=trap.side, strike=trap.candidate_strike,
                       entry_ltp=ltp, entry_fill=fill, qty=qty, cost=cost,
                       entry_time=now.strftime("%H:%M:%S"), peak=ltp)
        self._pos[inst] = pos
        key = (inst, now.date())
        self._expiry_count[key] = self._expiry_count.get(key, 0) + 1
        self.log.info(f"[pashupatastra:{self.mode}] ENTER {inst} {trap.side} {trap.candidate_strike} "
                      f"@ Rs.{ltp:.2f} qty={qty} cost=Rs.{cost:,.0f} trap={trap.score}")
        self._notify("entry", pos, trap_score=trap.score)
        return {"event": "ENTER", "inst": inst, "side": trap.side, "strike": trap.candidate_strike,
                "ltp": ltp, "qty": qty, "trap_score": trap.score}

    def _manage(self, inst, pos: Position, snap, now) -> Optional[dict]:
        v = self._ltp(snap, pos.strike, pos.side)
        if v is None or v <= 0:
            v = 0.05
        pos.peak = max(pos.peak, v)
        c = self.cfg
        # scale-out ladder (vs entry_fill)
        if not pos.booked1 and v >= c.scale1_mult * pos.entry_fill:
            self._book(pos, c.scale1_size, v); pos.booked1 = True
        if not pos.booked2 and v >= c.scale2_mult * pos.entry_fill:
            self._book(pos, c.scale2_size, v); pos.booked2 = True
        # runner trail (after first book) or hard close -> full exit
        if pos.booked1 and pos.pos_frac > 0 and v <= pos.peak * (1 - c.runner_trail):
            return self._close(inst, pos, v, "TRAIL", now)
        if self._past_close(inst, now):
            return self._close(inst, pos, v, "EOD", now)
        return None

    def _book(self, pos: Position, frac: float, v: float):
        frac = min(frac, pos.pos_frac)
        sv = v * (1 - self.cfg.slippage) * frac * pos.qty
        pos.realized += sv - self._sell_charges(sv)
        pos.pos_frac -= frac

    def _close(self, inst, pos: Position, v: float, reason: str, now) -> dict:
        if pos.pos_frac > 0:
            self._book(pos, pos.pos_frac, v)
        net = pos.realized - pos.cost
        mult = net / pos.cost if pos.cost else 0.0
        peak_mult = pos.peak / pos.entry_ltp if pos.entry_ltp else 0.0
        with open(self.journal, "a", newline="") as f:
            csv.writer(f).writerow([
                pos.entry_time, now.strftime("%H:%M:%S"), inst, self.mode, pos.side, pos.strike,
                round(pos.entry_ltp, 2), pos.qty, round(pos.cost, 0), round(net, 0),
                round(mult, 3), round(peak_mult, 2), reason, ""])
        self._pos[inst] = None
        self._cooldown_until[inst] = now + timedelta(minutes=self.cfg.cooldown_min)
        self.log.info(f"[pashupatastra:{self.mode}] EXIT {inst} {pos.side} {pos.strike} "
                      f"{reason} net=Rs.{net:,.0f} ({mult:+.1%}) peak={peak_mult:.1f}x")
        self._notify("exit", pos, net=net, reason=reason)
        return {"event": "EXIT", "inst": inst, "net_pnl": round(net, 0),
                "mult": round(mult, 3), "reason": reason}

    def _notify(self, kind, pos, **kw):
        if not self.notifier:
            return
        try:
            msg = (f"PASHUPATASTRA[{self.mode}] {kind.upper()} {pos.inst} {pos.side} "
                   f"{pos.strike} @ Rs.{pos.entry_ltp:.1f}")
            if kind == "exit":
                msg += f" -> net Rs.{kw.get('net',0):,.0f} ({kw.get('reason','')})"
            send = getattr(self.notifier, "send_message", None) or getattr(self.notifier, "send", None)
            if send:
                send(msg)
        except Exception:
            pass

    # ── live loop ────────────────────────────────────────────────────────────────
    def run_live(self, instruments=None, interval_sec=90, market_hours_only=True):
        import time
        instruments = instruments or ["NIFTY"]
        if self.engine is None:
            from src.brahmastra.options.options_intel import OptionsIntelEngine
            self.engine = OptionsIntelEngine()
        prev = {}
        self.log.info(f"[pashupatastra:{self.mode}] START {instruments} interval={interval_sec}s")
        try:
            from src.utils.market_calendar import is_trading_day
            if market_hours_only and not is_trading_day(datetime.now(IST).date()):
                self.log.info("[pashupatastra] not a trading day — exiting."); return
        except Exception:
            pass
        while True:
            now = datetime.now(IST)
            if market_hours_only and (now.hour, now.minute) >= (15, 30):
                self.log.info("[pashupatastra] close reached — done."); return
            for inst in instruments:
                try:
                    snap = self.engine.get_snapshot(inst, force=True)
                    if snap is None or getattr(snap, "error", None):
                        continue
                    step = {"NIFTY": 50, "SENSEX": 100}.get(inst, 50)
                    trap = compute_trap_score(snap, prev.get(inst), strike_step=step)
                    prev[inst] = snap
                    if self.recorder is not None:
                        try:
                            self.recorder._record_instrument(inst, now)   # also log raw chain
                        except Exception:
                            pass
                    self.on_chain(inst, snap, trap, now)
                except Exception as e:
                    self.log.warning(f"[pashupatastra] {inst} tick failed: {e}")
            time.sleep(interval_sec)


def _default_logger():
    try:
        from src.utils.logger import setup_logger
        return setup_logger("pashupatastra")
    except Exception:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        return logging.getLogger("pashupatastra")
