"""
Always-on Market Feed
─────────────────────
The single source of live market data for the dashboard. A background thread that,
every few seconds, pulls live NIFTY/SENSEX/VIX quotes and multi-timeframe charts and
pushes them into the dashboard state — into the shared market slot AND into every
RUNNING strategy's slot.

This is what makes live ticks, live charts and the live market view populate for ALL
strategies at once, instead of depending on each engine to wire its own ticks (only
BRAHMASTRA did). It only reads market data and writes display state — it never places
or touches orders.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


class MarketFeed:
    INSTRUMENTS = ["NIFTY", "SENSEX"]
    TIMEFRAMES = ["1m", "3m", "5m", "15m", "1h", "1d", "1w"]

    def __init__(self, multi, runner, quote_every: float = 5.0):
        self.multi = multi
        self.runner = runner
        self._quote_every = quote_every
        self._stop = threading.Event()
        self._thread = None
        self._prev: dict[str, float] = {}
        self._cycle = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="market-feed")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ──────────────────────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._refresh_quotes()
                # Charts are heavier (Kite calls) — refresh every ~4th cycle (~20s).
                if self._cycle % 4 == 0:
                    self._refresh_charts()
                # Telemetry heartbeat (~every 6th cycle ≈ 30s) so every running
                # strategy keeps a verifiable liveness trail even if its engine is
                # quiet — fixes "blind / no analysis logged" without touching engines.
                if self._cycle % 6 == 0:
                    self._heartbeat()
                # Self-recovery supervisor (~every 12th cycle ≈ 60s): restart any
                # strategy that SHOULD be active but isn't (crash/exit), unless the
                # operator stopped it. Bounded, audited, recovers in PAPER (safe).
                if self._cycle % 12 == 0:
                    self._supervise()
            except Exception:
                pass
            self._cycle += 1
            self._stop.wait(self._quote_every)

    def _supervise(self) -> None:
        """Restart strategies that should be active but aren't (crashed/exited),
        respecting operator stops and a bounded retry budget. Recovers in PAPER —
        never auto-arms real orders. Audited + logged on the strategy's own trail."""
        try:
            import yaml
            from src.api.operating_policy import load_policy, decide
            from src.api.bot_core import audit
        except Exception:
            return
        if not hasattr(self, "_recover_counts"):
            self._recover_counts = {}
        policy = load_policy()
        if not policy.get("auto_start", True):
            return
        try:
            reg = yaml.safe_load(open("strategies/registry.yaml", encoding="utf-8")).get("strategies", {})
        except Exception:
            return
        for name, cfg in reg.items():
            try:
                if decide(name, cfg, policy)["action"] != "start":
                    continue
                if self.runner.is_running(name):
                    self._recover_counts[name] = 0          # healthy → reset budget
                    continue
                if self.runner.was_operator_stopped(name):
                    continue                                # operator intent — leave it down
                n = self._recover_counts.get(name, 0)
                if n >= 3:
                    continue                                # give up after 3; already escalated
                self._recover_counts[name] = n + 1
                self.runner.start_strategy(name, mode="paper")
                audit("STRATEGY_LIFECYCLE", name, "auto_recover", "warn",
                      f"restart #{n + 1} (paper) — was not running")
                try:
                    self.multi.get(name).add_log("CTRL", f"Self-recovery: auto-restarted (paper), attempt {n + 1}")
                    from src.api.telegram_bots import get_signal_bot
                    get_signal_bot(self.runner.settings).publish_alert(
                        f"{name} auto-recovered", f"Restarted in paper (attempt {n + 1}) after going inactive.")
                except Exception:
                    pass
            except Exception:
                continue

    def _heartbeat(self) -> None:
        """Emit a HEARTBEAT cycle line for any running strategy that has gone silent
        past the telemetry window. Liveness only — does not fabricate signals."""
        try:
            from src.api.telemetry import last_cycle_age_s, HEARTBEAT_MAX_GAP_S
        except Exception:
            return
        for name in self._strategy_instruments():
            try:
                if not self.runner.is_running(name):
                    continue
                st = self.multi.get(name)
                age = last_cycle_age_s(st)
                if age is None or age > HEARTBEAT_MAX_GAP_S:
                    st.add_log("HEARTBEAT", "alive — monitoring; no qualifying setup this cycle")
            except Exception:
                continue

    def _strategy_instruments(self) -> dict:
        out = {}
        try:
            import yaml
            reg = yaml.safe_load(open("strategies/registry.yaml", encoding="utf-8")).get("strategies", {})
            for name, cfg in reg.items():
                out[name] = [str(i).upper() for i in (cfg.get("instruments") or [])]
        except Exception:
            pass
        return out

    def _refresh_quotes(self) -> None:
        from src.api.market import get_market_summary
        summ = get_market_summary()
        if not summ:
            return
        mkt = self.multi.market()
        inst_map = self._strategy_instruments()
        running = [n for n in inst_map if self.runner.is_running(n)]
        for inst in self.INSTRUMENTS:
            q = summ.get(inst.lower())
            if not q or q.get("ltp") is None:
                continue
            price = float(q["ltp"])
            prev = self._prev.get(inst, price)
            self._prev[inst] = price
            try:
                mkt.update_tick(inst, price, prev)
            except Exception:
                pass
            # Drive live ticks for every running strategy that trades this instrument.
            for name in running:
                if inst in inst_map.get(name, []):
                    try:
                        self.multi.get(name).update_tick(inst, price, prev)
                    except Exception:
                        pass

            # Forward Impact — a live projection from the latest 5m bars + Bollinger
            # Bands (populated by _refresh_charts). Fed to the market slot AND every
            # running strategy so the Forward-Impact panel shows for all of them.
            fi = self._forward_impact(inst, mkt)
            if fi:
                try:
                    mkt.update_narrator(inst, fi)
                except Exception:
                    pass
                for name in running:
                    if inst in inst_map.get(name, []):
                        try:
                            self.multi.get(name).update_narrator(inst, fi)
                        except Exception:
                            pass

    # TFs blended into the 15–30 min view, weighted toward the medium horizon.
    _FI_WEIGHTS = {"1m": 0.12, "3m": 0.20, "5m": 0.34, "15m": 0.34}

    @staticmethod
    def _tf_metrics(cb):
        """Per-timeframe structure: %B, relative band width + its change, short SMA
        slope and momentum. Returns None if too few bars."""
        if not cb:
            return None
        bars = cb.get("bars") or []
        bb = cb.get("bb") or {}
        closes = [b.get("c") for b in bars if b.get("c") is not None]
        if len(closes) < 6:
            return None
        last = closes[-1]
        up = bb.get("upper") or []
        lo = bb.get("lower") or []
        mid = bb.get("mid") or []
        u = up[-1] if up else None
        l = lo[-1] if lo else None
        m = mid[-1] if mid else None
        pctb = (last - l) / (u - l) if (u and l and u != l) else 0.5
        pctb = max(0.0, min(1.0, pctb))
        width = ((u - l) / m) if (u and l and m) else None
        width_prev = None
        if len(up) >= 6 and up[-6] and lo[-6] and mid[-6]:
            width_prev = (up[-6] - lo[-6]) / mid[-6]
        width_chg = ((width - width_prev) / width_prev) if (width and width_prev) else 0.0
        base = closes[-6]
        mom = (last - base) / base if base else 0.0

        def sma(n, off=0):
            seg = closes[-(n + off):len(closes) - off] if off else closes[-n:]
            return (sum(seg) / len(seg)) if seg else last
        denom = sma(3, 3)
        slope = ((sma(3) - denom) / denom) if (len(closes) >= 6 and denom) else 0.0
        return {"last": last, "pctb": pctb, "width": width, "width_chg": width_chg,
                "mom": mom, "slope": slope, "u": u, "l": l, "m": m}

    @staticmethod
    def _vol_regime(width):
        """Volatility regime from relative band width (≈ 4·σ/price)."""
        if width is None:
            return "Normal"
        if width < 0.0025:
            return "Low"
        if width < 0.006:
            return "Normal"
        if width < 0.012:
            return "Elevated"
        return "Extreme"

    @staticmethod
    def _structure(m, dscore):
        pctb, wc, slope = m["pctb"], m["width_chg"], m["slope"]
        if wc < -0.12:
            return "Range compression (squeeze building)"
        if wc > 0.15 and (pctb > 0.7 or pctb < 0.3):
            return "Breakout attempt / range expansion"
        if pctb > 0.8 and slope > 0.0002:
            return "Trend continuation (riding upper band)"
        if pctb < 0.2 and slope < -0.0002:
            return "Trend continuation (riding lower band)"
        if pctb > 0.85 and slope <= 0:
            return "Mean reversion from upper band"
        if pctb < 0.15 and slope >= 0:
            return "Mean reversion from lower band"
        if abs(dscore) < 0.08:
            return "Chop / no edge"
        return "Trend developing"

    def _forward_impact(self, inst, mkt):
        """Multi-timeframe micro-forecast for the next ~15–30 min (UI_FE_Pg2 spec):
        a graded direction, volatility regime, structural context, conviction +
        confidence (0–100), key levels and caveats — built purely from price /
        Bollinger structure across 1m/3m/5m/15m. Returns a narrator object carrying
        the legacy fields (score, score_dir, alert_tier, headline) plus the rich ones."""
        import types
        chart = (getattr(mkt, "chart_bars", {}) or {}).get(inst, {})
        metrics = {}
        for tf in self._FI_WEIGHTS:
            mm = self._tf_metrics(chart.get(tf))
            if mm:
                metrics[tf] = mm
        ref = metrics.get("5m") or metrics.get("3m") or metrics.get("15m") or metrics.get("1m")
        if not ref:
            return None

        # Net directional score blended across timeframes (~ -1..+1).
        dir_num, wsum, slopes = 0.0, 0.0, []
        for tf, mm in metrics.items():
            w = self._FI_WEIGHTS[tf]
            comp = mm["slope"] * 60 + (mm["pctb"] - 0.5) * 1.2 + mm["mom"] * 25
            dir_num += w * comp
            wsum += w
            slopes.append(1 if mm["slope"] > 0.0003 else -1 if mm["slope"] < -0.0003 else 0)
        dscore = dir_num / wsum if wsum else 0.0

        net_sign = 1 if dscore > 0 else -1 if dscore < 0 else 0
        graded = [s for s in slopes if s != 0]
        alignment = (sum(1 for s in graded if s == net_sign) / len(graded)) if graded else 0.0

        vol = self._vol_regime(ref.get("width"))
        structure = self._structure(ref, dscore)

        a = abs(dscore)
        if net_sign == 0 or a < 0.08:
            direction_label, d = "Sideways", "RANGE"
        elif a >= 0.28:
            direction_label = "Strong Up" if net_sign > 0 else "Strong Down"
            d = "UP" if net_sign > 0 else "DOWN"
        else:
            direction_label = "Mild Up" if net_sign > 0 else "Mild Down"
            d = "UP" if net_sign > 0 else "DOWN"

        conf = 100 * (0.55 * alignment + 0.35 * min(1.0, a / 0.35))
        if vol == "Extreme":
            conf *= 0.7
        elif vol == "Elevated":
            conf *= 0.9
        if structure.startswith("Chop"):
            conf *= 0.5
        confidence = int(max(0, min(100, round(conf))))
        conviction = "High" if confidence >= 66 else "Medium" if confidence >= 40 else "Low"

        awaiting = (direction_label == "Sideways" or structure.startswith("Chop")
                    or alignment < 0.5 or confidence < 30 or len(metrics) < 2)

        u5, l5 = ref.get("u"), ref.get("l")
        levels = f"{round(min(l5, u5))}–{round(max(l5, u5))}" if (u5 and l5) else "—"

        if awaiting:
            tier = "CALM"
            headline = f"{inst} · awaiting signal — no statistically clean edge right now"
            reason = (f"Mixed timeframes (alignment {int(alignment * 100)}%), "
                      f"{structure.lower()}, {vol.lower()} volatility.")
            d, direction_label = "RANGE", "Sideways"
        else:
            tier = "ARMED" if confidence >= 60 else "WATCH"
            arrow = "up" if d == "UP" else "down"
            headline = (f"{inst} · {direction_label} · {structure} · "
                        f"conf {confidence}/100 · watch {levels}")
            reason = (f"{int(alignment * 100)}% timeframe alignment {arrow}; "
                      f"5m %B {ref['pctb']:.2f}, band width "
                      f"{'+' if ref['width_chg'] >= 0 else ''}{int(ref['width_chg'] * 100)}%; "
                      f"{vol.lower()} volatility.")

        risk = ""
        if vol in ("Elevated", "Extreme"):
            risk = f"{vol} volatility — wider stops, smaller size."
        if structure.startswith("Mean reversion"):
            risk = (risk + " " if risk else "") + "Counter-trend fade — invalid if price closes through the band."

        return types.SimpleNamespace(
            timestamp=datetime.now(IST).strftime("%H:%M:%S"),
            score=max(-100, min(100, round(dscore * 280))),
            score_dir=d, bars_to_entry=None, alert_tier=tier, headline=headline,
            direction_label=direction_label, volatility=vol, conviction=conviction,
            confidence=confidence, structure=structure, levels=levels,
            reason=reason, risk=risk, awaiting=awaiting)

    def _refresh_charts(self) -> None:
        from src.api import charts as charts_mod
        mkt = self.multi.market()
        for inst in self.INSTRUMENTS:
            for tf in self.TIMEFRAMES:
                try:
                    ch = charts_mod.fresh_chart(inst, tf)
                    if ch.get("bars"):
                        mkt.update_chart(inst, tf, ch["bars"], ch.get("bb", {}))
                except Exception:
                    pass
