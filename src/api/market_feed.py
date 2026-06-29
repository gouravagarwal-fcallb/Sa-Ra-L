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
