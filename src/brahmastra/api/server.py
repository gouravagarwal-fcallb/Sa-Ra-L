"""
BRAHMASTRA FastAPI Server — Phase 7
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REST + WebSocket backend for the dashboard.

Endpoints:
  GET  /api/snapshot         — full state snapshot (JSON)
  GET  /api/session          — session stats
  GET  /api/ticks            — current prices for all instruments
  GET  /api/scenarios/{inst} — scenario states for one instrument
  GET  /api/indicators/{inst}— latest indicator values
  GET  /api/trades/open      — open positions
  GET  /api/trades/closed    — last 50 closed trades
  GET  /api/logs             — last N log lines (querystring ?n=100&cat=TRADE)
  GET  /api/health           — liveness check

  WS   /ws                   — WebSocket; broadcasts all state change events
                               Client receives JSON: {type, data}
                               Types: tick, scenarios, indicators, trade, log, session

  GET  /                     — serves React frontend (index.html)
  GET  /{path}               — serves static files

Run:
  python -m src.brahmastra.api.server
  or
  uvicorn src.brahmastra.api.server:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

from src.brahmastra.api.state import get_state, BrahmastraState

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                          "frontend", "brahmastra", "build")


def create_app() -> "FastAPI":
    if not _FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI not installed. Run: pip install fastapi uvicorn[standard]"
        )

    app = FastAPI(
        title       = "BRAHMASTRA_v1",
        description = "Market Intelligence Platform — Live Dashboard API",
        version     = "1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"],   # restrict in production
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # ── WebSocket connection manager ──────────────────────────────────────────

    class ConnectionManager:
        def __init__(self):
            self.active: list[WebSocket] = []

        async def connect(self, ws: WebSocket) -> None:
            await ws.accept()
            self.active.append(ws)

        def disconnect(self, ws: WebSocket) -> None:
            if ws in self.active:
                self.active.remove(ws)

        async def broadcast(self, data: dict) -> None:
            payload = json.dumps(data)
            dead    = []
            for ws in self.active:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.disconnect(ws)

    manager = ConnectionManager()

    # ── Background broadcaster ────────────────────────────────────────────────

    async def _broadcast_loop() -> None:
        """Drains the state's WS event queue and broadcasts to all clients."""
        state = get_state()
        while True:
            events = state.pop_ws_events(100)
            for event in events:
                await manager.broadcast(event)
            await asyncio.sleep(0.05)   # 50ms cadence

    @app.on_event("startup")
    async def startup_event():
        asyncio.create_task(_broadcast_loop())

    # ── REST endpoints ────────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "ts": datetime.now(IST).isoformat()}

    @app.get("/api/snapshot")
    async def snapshot():
        return get_state().snapshot()

    @app.get("/api/session")
    async def session_stats():
        return vars(get_state().session)

    @app.get("/api/ticks")
    async def ticks():
        state = get_state()
        return {k: vars(v) for k, v in state.ticks.items()}

    @app.get("/api/scenarios/{instrument}")
    async def scenarios(instrument: str):
        state = get_state()
        inst  = instrument.upper()
        scns  = state.scenarios.get(inst, [])
        return [state._scenario_dict(s) for s in scns]

    @app.get("/api/indicators/{instrument}")
    async def indicators(instrument: str):
        state = get_state()
        snap  = state.indicators.get(instrument.upper())
        if not snap:
            raise HTTPException(404, f"No indicator data for {instrument}")
        return vars(snap)

    @app.get("/api/trades/open")
    async def open_trades():
        state = get_state()
        return [state._trade_dict(t) for t in state.open_trades.values()]

    @app.get("/api/trades/closed")
    async def closed_trades():
        return list(get_state().closed_trades)

    @app.get("/api/logs")
    async def logs(
        n:   int = Query(default=100, le=500),
        cat: Optional[str] = Query(default=None),
    ):
        state = get_state()
        lines = list(state.log_lines)
        if cat:
            cats  = [c.strip().upper() for c in cat.split(",")]
            lines = [l for l in lines if l.get("category", "").upper() in cats]
        return lines[-n:]

    # ── WebSocket ─────────────────────────────────────────────────────────────

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await manager.connect(ws)
        # Send full snapshot on connect so client renders immediately
        try:
            await ws.send_text(json.dumps({
                "type": "snapshot",
                "data": get_state().snapshot(),
            }))
            while True:
                # Keep alive — client can send "ping"
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
        # CRA puts assets in build/static/ — mount that sub-directory at /static
        _static_assets = os.path.join(STATIC_DIR, "static")
        if os.path.isdir(_static_assets):
            app.mount("/static", StaticFiles(directory=_static_assets), name="static")

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str):
            file_path = os.path.join(STATIC_DIR, full_path)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))
    else:
        @app.get("/")
        async def root():
            return {
                "message": "BRAHMASTRA_v1 API is running",
                "note": "Frontend not built yet. Run: cd frontend/brahmastra && npm install && npm run build",
                "api_docs": "/docs",
            }

    return app


app = create_app() if _FASTAPI_AVAILABLE else None


def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    """Launch the BRAHMASTRA dashboard server."""
    try:
        import uvicorn
    except ImportError:
        raise ImportError("Run: pip install uvicorn[standard]")

    print(f"\n  BRAHMASTRA Dashboard → http://localhost:{port}")
    print(f"  API docs            → http://localhost:{port}/docs")
    print(f"  WebSocket           → ws://localhost:{port}/ws\n")

    uvicorn.run(
        "src.brahmastra.api.server:app",
        host    = host,
        port    = port,
        reload  = reload,
        log_level = "info",
    )


if __name__ == "__main__":
    run_server()
