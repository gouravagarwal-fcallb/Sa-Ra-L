"""
Sa-Ra-L — Desktop launcher
──────────────────────────
Starts the unified dashboard server WITHOUT a visible console window and opens the
browser to it. Used two ways:

  1. No-build (Python already installed):   pythonw launcher.py     (or run_hidden.vbs)
  2. Frozen .exe (PyInstaller --noconsole):  double-click Sa-Ra-L.exe

Environment knobs (optional):
  SARAL_PORT=8000          port to serve on
  SARAL_AUTOSTART=1        auto-start all eligible strategies (paper) on boot
  SARAL_REVIEW_MODE=1      client-review/demo build — live trading hard-disabled
  SARAL_NO_BROWSER=1       don't auto-open the browser

The server always binds to 127.0.0.1 (localhost) — it is never reachable from other
machines, because the dashboard can place real orders. See docs/DESKTOP_APP.md.
"""
from __future__ import annotations

import os
import sys
import time
import socket
import threading
import webbrowser


def _log_path() -> str:
    # Next to the .exe when frozen, else the repo's logs/ folder.
    root = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(root, "logs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "dashboard.log")


def _redirect_output() -> None:
    """With no console (pythonw / --noconsole) stdout/stderr are None; anything that
    prints would crash. Send them to a rolling log file instead."""
    if getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None:
        try:
            f = open(_log_path(), "a", buffering=1, encoding="utf-8")
            sys.stdout = sys.stderr = f
        except Exception:
            pass


def _wait_and_open(port: int) -> None:
    if os.environ.get("SARAL_NO_BROWSER", "").lower() in ("1", "true", "yes"):
        return
    for _ in range(120):                       # up to ~60s for the server to bind
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    try:
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass


def _kite_autologin() -> None:
    """Headless Kite login BEFORE the server starts, so the token is ready when
    strategies auto-start. This is what makes the one-click launcher a single step
    again (previously login had to be run by hand). Never fatal: if it fails we log
    it and still start the dashboard, which surfaces a clear Kite NO-GO on the
    Readiness page instead of dying in a hidden window. Skipped in review mode."""
    if os.environ.get("SARAL_REVIEW_MODE", "").lower() in ("1", "true", "yes"):
        print("[launcher] review mode — skipping Kite auto-login."); return
    if os.environ.get("SARAL_SKIP_LOGIN", "").lower() in ("1", "true", "yes"):
        print("[launcher] SARAL_SKIP_LOGIN set — skipping Kite auto-login."); return
    try:
        from src.broker.kite_auto_login import auto_login
        print("[launcher] Kite auto-login...")
        auto_login(verbose=True)
        print("[launcher] Kite auto-login OK — token saved.")
    except Exception as e:
        print(f"[launcher] Kite auto-login FAILED ({e}). Dashboard will still start; "
              f"the Readiness page will show Kite NO-GO. Fix credentials in "
              f"config/settings.local.yaml or run: python main.py --mode autologin")


def main() -> int:
    _redirect_output()

    # When frozen, the working dir is the exe's dir; make sure the repo data
    # (strategies/, config/, logs/) is found relative to it.
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    os.environ.setdefault("SARAL_AUTOSTART", "1")
    port = int(os.environ.get("SARAL_PORT", "8000"))

    # One-click login: authenticate to Kite before serving (idempotent, non-fatal).
    _kite_autologin()

    threading.Thread(target=_wait_and_open, args=(port,), daemon=True).start()

    import uvicorn
    from src.api.server import app
    # log_level warning + our file redirect keeps it quiet and console-free.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
