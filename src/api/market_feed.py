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
    TIMEFRAMES = ["1m", "5m", "15m", "1h", "1d", "1w"]

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
            except Exception:
                pass
            self._cycle += 1
            self._stop.wait(self._quote_every)

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

    def _forward_impact(self, inst, mkt):
        """Project the likely next-15–30-min move from the latest 5m bars + BB."""
        import types
        cb = (getattr(mkt, "chart_bars", {}) or {}).get(inst, {}).get("5m")
        if not cb:
            return None
        bars = cb.get("bars") or []
        bb = cb.get("bb") or {}
        closes = [b.get("c") for b in bars if b.get("c") is not None]
        if len(closes) < 6:
            return None
        last = closes[-1]
        up = (bb.get("upper") or [None])[-1]
        low = (bb.get("lower") or [None])[-1]
        pctb = ((last - low) / (up - low)) if (up and low and up != low) else None
        base = closes[-6]
        mom = (last - base) / base if base else 0.0
        if pctb is not None and pctb > 0.8 and mom > 0:
            d, tier, head = "UP", "ARMED", f"{inst} pressing the upper band, momentum up — breakout building"
        elif pctb is not None and pctb < 0.2 and mom < 0:
            d, tier, head = "DOWN", "ARMED", f"{inst} pressing the lower band, momentum down — breakdown building"
        elif mom > 0.0015:
            d, tier, head = "UP", "WATCH", f"{inst} drifting up from the BB mid-line"
        elif mom < -0.0015:
            d, tier, head = "DOWN", "WATCH", f"{inst} drifting down from the BB mid-line"
        else:
            d, tier, head = "RANGE", "CALM", f"{inst} range-bound near the BB mid — mean-reversion likely"
        return types.SimpleNamespace(
            timestamp=datetime.now(IST).strftime("%H:%M:%S"),
            score=max(-100, min(100, round(mom * 4000))),
            score_dir=d, bars_to_entry=None, alert_tier=tier, headline=head)

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
