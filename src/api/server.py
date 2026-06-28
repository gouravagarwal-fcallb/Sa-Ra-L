"""
Sa-Ra-L Unified Control API
───────────────────────────
One FastAPI app for ALL strategies: list/run/stop, per-strategy live snapshot
(WebSocket), backtest summaries, daily analysis, readiness ("properly checked"),
multi-timeframe charts with Bollinger Bands, and a guarded real-order arming flow.

Reuses the BRAHMASTRA server's proven structure (ConnectionManager, broadcast
loop, static React serving) but drives the multi-strategy runner instead of a
single engine.

Run:
  python main.py --mode unified
  uvicorn src.api.server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import json
import asyncio
import secrets
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Query
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from fastapi.middleware.cors import CORSMiddleware
    _FASTAPI = True
except ImportError:
    _FASTAPI = False

from src.api.state_registry import get_multi_state, MARKET_SLOT
from src.api.runner import ApiPortfolioRunner

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..",
                          "frontend", "brahmastra", "build")
REGISTRY_PATH = "strategies/registry.yaml"

# Live-order arming: token TTL and the exact phrase the user must type.
ARM_TTL_SECONDS = 60
def _live_phrase(name: str) -> str:
    return f"GO LIVE {name}"


def _load_settings() -> dict:
    try:
        from main import load_configs
        settings, _ = load_configs(None)
        return settings
    except Exception:
        return {}


def _load_registry() -> dict:
    import yaml
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f).get("strategies", {})


def create_app():
    if not _FASTAPI:
        raise ImportError("FastAPI not installed. Run: pip install fastapi uvicorn[standard]")

    app    = FastAPI(title="Sa-Ra-L Unified Control", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])

    multi  = get_multi_state()
    runner = ApiPortfolioRunner(registry_path=REGISTRY_PATH, settings=_load_settings(),
                                multi=multi)
    app.state.runner = runner
    app.state.arm_tokens: dict[str, dict] = {}      # name -> {token, expires}

    # ── WebSocket manager + broadcast loop (reused pattern) ───────────────────
    class ConnectionManager:
        def __init__(self): self.active: list[WebSocket] = []
        async def connect(self, ws):
            await ws.accept(); self.active.append(ws)
        def disconnect(self, ws):
            if ws in self.active: self.active.remove(ws)
        async def broadcast(self, data: dict):
            payload = json.dumps(data, default=str); dead = []
            for ws in self.active:
                try: await ws.send_text(payload)
                except Exception: dead.append(ws)
            for ws in dead: self.disconnect(ws)
    manager = ConnectionManager()

    async def _broadcast_loop():
        while True:
            for event in multi.pop_ws_events(200):
                await manager.broadcast(event)
            await asyncio.sleep(0.05)

    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(_broadcast_loop())

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _strategy_list_item(name: str, cfg: dict) -> dict:
        from src.api.readiness import check_readiness
        runtime = runner.runtime_status(name)
        try:
            readiness = check_readiness(name, cfg, runtime, multi)
            r_summary = {"overall": readiness["overall"],
                         "backtest_ok": readiness["backtest_ok"],
                         "backfill_ok": readiness["backfill_ok"],
                         "ticks_ok": readiness["ticks_ok"],
                         "config_audit_ok": readiness.get("config_audit_ok")}
        except Exception as e:
            r_summary = {"overall": "UNKNOWN", "error": str(e)[:100]}
        return {
            "name": name,
            "full_name": cfg.get("full_name", name),
            "strategy_type": cfg.get("type") or cfg.get("strategy_type"),
            "status": cfg.get("status"),
            "instruments": cfg.get("instruments", []),
            "risk_profile": cfg.get("risk_profile"),
            "capital_allocated_rs": cfg.get("capital_allocated_rs", 0),
            "capital_target_rs": cfg.get("capital_target_rs", 0),
            "runtime": runtime,
            "readiness": r_summary,
        }

    # ── REST: strategies ──────────────────────────────────────────────────────
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "ts": datetime.now(IST).isoformat()}

    @app.get("/api/strategies")
    async def strategies():
        reg = _load_registry()
        return [_strategy_list_item(n, c) for n, c in reg.items()]

    @app.get("/api/strategy/{name}/snapshot")
    async def strategy_snapshot(name: str):
        snap = multi.snapshot(name)
        if snap is None:
            # not yet running — return an empty-but-valid shell
            return {"session": {}, "ticks": {}, "scenarios": {}, "open_trades": [],
                    "closed_trades": [], "indicators": {}, "narrator": {},
                    "pending_signals": {}, "log_lines": [], "running": False}
        snap["running"] = runner.is_running(name)
        return snap

    @app.get("/api/strategy/{name}/readiness")
    async def strategy_readiness(name: str):
        from src.api.readiness import check_readiness
        reg = _load_registry()
        if name not in reg:
            raise HTTPException(404, f"Unknown strategy {name}")
        return check_readiness(name, reg[name], runner.runtime_status(name), multi)

    @app.get("/api/strategy/{name}/backtest-summary")
    async def strategy_backtest_summary(name: str):
        from src.api.backtests import load_summary
        reg = _load_registry()
        if name not in reg:
            raise HTTPException(404, f"Unknown strategy {name}")
        return load_summary(name, reg[name])

    @app.get("/api/backtests")
    async def backtests():
        from src.api.backtests import aggregate_summaries
        return aggregate_summaries(_load_registry())

    @app.get("/api/daily-analysis")
    async def daily_analysis():
        from src.api.daily_analysis import build_daily_analysis
        return build_daily_analysis(_load_registry(), multi, runner)

    @app.get("/api/premarket")
    async def premarket(force: bool = Query(False)):
        from src.api.premarket import build_premarket
        # run the (potentially slow, network-bound) fetch off the event loop
        return await asyncio.to_thread(build_premarket, force)

    @app.get("/api/market/{instrument}/chart")
    async def market_chart(instrument: str, tf: str = Query("5m")):
        from src.api.charts import get_chart
        return get_chart(multi, instrument.upper(), tf)

    # ── Control: run / stop (paper is free; live requires arming) ─────────────
    @app.post("/api/strategy/{name}/run")
    async def run_strategy(name: str, request: Request):
        body = await request.json() if await _has_body(request) else {}
        mode = (body.get("mode") or "paper").lower()
        if mode == "live":
            raise HTTPException(400, "Live mode requires /arm-live then /confirm-live")
        return runner.start_strategy(name, mode="paper")

    @app.post("/api/strategy/{name}/stop")
    async def stop_strategy(name: str):
        return runner.stop_strategy(name)

    @app.post("/api/control")
    async def control(request: Request):
        body = await request.json() if await _has_body(request) else {}
        cmd  = (body.get("command") or "").lower()
        if cmd == "stop":
            return runner.stop_all()
        raise HTTPException(400, f"Unknown command {cmd!r}")

    # ── Live-order safety guards (Phase 4) ────────────────────────────────────
    @app.post("/api/strategy/{name}/arm-live")
    async def arm_live(name: str):
        reg = _load_registry()
        if name not in reg:
            raise HTTPException(404, f"Unknown strategy {name}")
        cap = reg[name].get("capital_allocated_rs", 0)
        if not cap or cap <= 0:
            raise HTTPException(400,
                f"{name} has no allocated capital (paper/planned only) — cannot arm live")
        token = secrets.token_urlsafe(8)
        app.state.arm_tokens[name] = {
            "token": token,
            "expires": datetime.now(IST) + timedelta(seconds=ARM_TTL_SECONDS),
        }
        multi.get(name).add_log("LIVE", f"ARM requested (token issued, {ARM_TTL_SECONDS}s TTL)")
        return {
            "name": name, "armed": True, "confirm_token": token,
            "ttl_seconds": ARM_TTL_SECONDS,
            "capital_cap_rs": cap,
            "required_phrase": _live_phrase(name),
            "warning": f"This will place REAL orders for {name} (cap Rs.{cap:,}). "
                       f"Type exactly '{_live_phrase(name)}' to confirm.",
        }

    @app.post("/api/strategy/{name}/confirm-live")
    async def confirm_live(name: str, request: Request):
        body  = await request.json() if await _has_body(request) else {}
        token = body.get("token", "")
        typed = body.get("typed_confirmation", "")
        rec   = app.state.arm_tokens.get(name)
        if not rec:
            raise HTTPException(400, "Not armed — call /arm-live first")
        if datetime.now(IST) > rec["expires"]:
            app.state.arm_tokens.pop(name, None)
            raise HTTPException(400, "Arm token expired — re-arm")
        if not secrets.compare_digest(token, rec["token"]):
            raise HTTPException(403, "Invalid arm token")
        if typed.strip() != _live_phrase(name):
            raise HTTPException(403, "Confirmation phrase mismatch")
        app.state.arm_tokens.pop(name, None)
        multi.get(name).add_log("LIVE", "CONFIRMED — starting LIVE (real orders)")
        return runner.start_strategy(name, mode="live")

    # ── WebSocket ─────────────────────────────────────────────────────────────
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await manager.connect(ws)
        try:
            await ws.send_text(json.dumps({"type": "snapshot_all",
                                           "data": multi.snapshot_all()}, default=str))
            while True:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30)
                if msg == "ping":
                    await ws.send_text('{"type":"pong"}')
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except Exception:
            pass
        finally:
            manager.disconnect(ws)

    # ── Serve React frontend ──────────────────────────────────────────────────
    if os.path.isdir(STATIC_DIR):
        _assets = os.path.join(STATIC_DIR, "static")
        if os.path.isdir(_assets):
            app.mount("/static", StaticFiles(directory=_assets), name="static")

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str):
            fp = os.path.join(STATIC_DIR, full_path)
            if os.path.isfile(fp):
                return FileResponse(fp)
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))
    else:
        @app.get("/")
        async def root():
            return {"message": "Sa-Ra-L Unified API running",
                    "note": "Frontend not built. cd frontend/brahmastra && npm run build",
                    "docs": "/docs"}

    return app


async def _has_body(request) -> bool:
    try:
        body = await request.body()
        return len(body) > 0
    except Exception:
        return False


app = create_app() if _FASTAPI else None


def run_server(host: str = "0.0.0.0", port: int = 8000, autostart: bool = False):
    import uvicorn
    if autostart and app is not None:
        app.state.runner.autostart_from_registry()
    print(f"\n  Sa-Ra-L Unified Dashboard → http://localhost:{port}  (API docs: /docs)\n")
    uvicorn.run("src.api.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
