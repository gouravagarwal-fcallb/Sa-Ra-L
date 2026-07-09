# Sa-Ra-L — Desktop App & Client-Review Build

Run the whole dashboard as a **desktop app with no black command window** on screen,
and (optionally) package it into a single `.exe` you can hand to a sample client for
review. There is ONE server — every strategy (including BRAHMASTRA) runs inside it —
so "the app" is just that server plus your browser pointed at it.

---

## The three ways to run it

### A) Right now, no build — hidden console (needs Python installed)
Double-click **`Start Sa-Ra-L.bat`** (or `run_hidden.vbs`).
- It launches the server with **`pythonw`** (windowless Python) — nothing shows on
  screen, it just runs in the background.
- Your browser opens to `http://127.0.0.1:8000` after a few seconds.
- Logs go to `logs/dashboard.log` (since there's no console to print to).
- To stop it: end the `pythonw.exe` process in Task Manager (or use **STOP ALL** in
  the UI to halt trading, then close the process).

This is the fastest path and is exactly the "cmd window won't be visible" behaviour
you asked for.

### B) A single self-contained `.exe` (no Python needed on the target machine)
Build once on your Windows machine, then the `.exe` runs anywhere:

```bat
pip install pyinstaller
build_exe.bat
```
That builds the frontend and produces **`dist\Sa-Ra-L.exe`** — a hidden-console,
single-file app. Double-clicking it starts the server (no window) and opens the
browser. `build_exe.bat` bundles the frontend build, `strategies/`, and `config/`
into the exe (the app finds them via the PyInstaller bundle path — already wired in
`server.py`).

**Before running the exe for real (live/paper) trading**, put your gitignored
`settings.local.yaml` and `config/.kite_token` **next to the exe** (in `dist\`). The
launcher `chdir`s to the exe's folder, so it reads those and writes `logs/` and
`data/` there.

### C) Client-review / demo build — live trading permanently OFF
Double-click **`Start Sa-Ra-L (Review Mode).bat`**, or run the exe with the env var
`SARAL_REVIEW_MODE=1`.
- The server comes up with **live order placement hard-disabled**: the `arm-live`
  and `confirm-live` endpoints return `403` no matter what anyone clicks. A client
  literally cannot place a real order from a review build.
- Everything else (charts, analysis, paper trades, closure reports, readiness) works
  so they can evaluate the interface and the strategies' behaviour.

---

## Sharing with sample clients — the honest guidance

**Safe:** hand them a **Review-Mode** build (option C). Live trading is off at the
server, so there's zero real-money risk even if they poke every button.

**What they'll see depends on data:**
- With **no Kite session** on their machine, the market feed falls back to delayed
  Yahoo data (or shows empty pre-open). They'll see the full UI and can start paper
  strategies, but live ticks/option-chain will be limited. Good enough for a UI/UX
  and workflow review.
- If you want them to see a **populated, realistic** dashboard without giving them
  your Kite login, bundle a **snapshot** of sample outputs — copy a few
  `logs/closure_*.md`, `logs/trades_*.csv`, and a `data/` sample next to the exe.
  The Closure Report, Activity, and Daily-Analysis pages will then show real
  historical days.

**Never** share a *non*-review build that contains your `settings.local.yaml` /
`config/.kite_token` — that's your live brokerage session. Review builds + no
credential files = safe to distribute.

**Networking:** the server always binds to `127.0.0.1` (localhost only) — it is not
reachable from other machines on purpose (it can place real orders in normal mode).
Each client runs their own local copy; you are not hosting it for them. If you ever
need to demo it hosted, do it in Review Mode behind auth/VPN, never raw on a public
IP.

---

## Environment knobs (all optional)

| Variable | Effect |
|---|---|
| `SARAL_PORT` | Port to serve on (default 8000). |
| `SARAL_AUTOSTART` | `1` = auto-start all eligible strategies in paper on boot (launcher sets this by default). |
| `SARAL_REVIEW_MODE` | `1` = client-review build; live arm/confirm disabled (403). |
| `SARAL_NO_BROWSER` | `1` = don't auto-open the browser. |

---

## Files added for this

- `launcher.py` — starts the server console-free (redirects output to
  `logs/dashboard.log`), waits for the port, opens the browser. Works both as
  `pythonw launcher.py` and frozen by PyInstaller.
- `run_hidden.vbs` — one-liner that runs `pythonw launcher.py` with a hidden window.
- `Start Sa-Ra-L.bat` — double-click launcher (normal).
- `Start Sa-Ra-L (Review Mode).bat` — double-click launcher with live trading OFF.
- `build_exe.bat` — builds the frontend + a single `dist\Sa-Ra-L.exe`.
- `server.py` — resolves the static UI from the PyInstaller bundle when frozen, and
  honours `SARAL_REVIEW_MODE` to disable the live-order endpoints.

## Troubleshooting
- **"Port 8000 already in use"** — the dashboard is already running; just open
  `http://localhost:8000`. To run a second copy: `set SARAL_PORT=8001` first.
- **Nothing opens** — check `logs/dashboard.log`. Most often a missing dependency
  (run `pip install -r requirements.txt`) or, in the exe, a missing `settings.local.yaml`.
- **Blank/old UI after an update** — hard-refresh the browser (Ctrl-Shift-R); the app
  shows a "server is newer — refresh" note when the bundle changes.
