@echo off
setlocal EnableDelayedExpansion
REM ═══════════════════════════════════════════════════════════════════════
REM  BRAHMASTRA_v1  |  One-Click Launcher
REM  Double-click this file from anywhere to start the platform.
REM  Dashboard opens automatically in your browser.
REM
REM  First run: takes ~30 seconds (builds React UI).
REM  Every run after: starts in ~5 seconds.
REM
REM  For custom URL (brahmastra.local), run setup_hostname.bat once as Admin.
REM ═══════════════════════════════════════════════════════════════════════

title BRAHMASTRA_v1 - Starting...
color 0A

REM ── Move to the folder where this .bat file lives ─────────────────────
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════════════════════════╗
echo  ║                                                              ║
echo  ║     BRAHMASTRA_v1    Market Intelligence Platform           ║
echo  ║                                                              ║
echo  ╚══════════════════════════════════════════════════════════════╝
echo.

REM ── Activate virtual environment if present ───────────────────────────
if exist ".venv\Scripts\activate.bat" (
    echo  [SETUP] Activating virtual environment...
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    echo  [SETUP] Activating virtual environment...
    call "venv\Scripts\activate.bat"
)

REM ── Python check ──────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python not found.
    echo          Install Python 3.10+ from: https://python.org
    echo          Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  [OK]    %%v found

REM ── Check and install required Python packages ────────────────────────
python -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo  [SETUP] Installing server packages (fastapi + uvicorn)...
    pip install fastapi "uvicorn[standard]" --quiet
    if errorlevel 1 (
        echo.
        echo  [ERROR] pip install failed. Check your internet connection.
        echo.
        pause
        exit /b 1
    )
    echo  [OK]    Packages installed.
)

REM ── Check and install kiteconnect if not present ──────────────────────
python -c "import kiteconnect" >nul 2>&1
if errorlevel 1 (
    echo  [SETUP] Installing kiteconnect...
    pip install kiteconnect --quiet
)

REM ── Build React dashboard if build folder is missing ─────────────────
if not exist "frontend\brahmastra\build\index.html" (
    echo.
    echo  [SETUP] Building dashboard UI... (first time only, ~30 seconds)
    echo.
    where npm >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] npm not found.
        echo          Install Node.js from: https://nodejs.org
        echo          Choose the LTS version, then restart and try again.
        echo.
        pause
        exit /b 1
    )
    pushd frontend\brahmastra
    echo  [SETUP] Running npm install...
    call npm install --silent 2>nul
    echo  [SETUP] Running npm run build...
    call npm run build 2>nul
    popd
    if not exist "frontend\brahmastra\build\index.html" (
        echo.
        echo  [ERROR] React build failed.
        echo          Try manually: cd frontend\brahmastra && npm install && npm run build
        echo.
        pause
        exit /b 1
    )
    echo  [OK]    Dashboard UI built successfully.
    echo.
)

REM ── Determine dashboard URL ───────────────────────────────────────────
REM  If brahmastra.local is in the hosts file, use it. Otherwise localhost.
set DASH_URL=http://localhost:8000
findstr /C:"brahmastra.local" "%SystemRoot%\System32\drivers\etc\hosts" >nul 2>&1
if not errorlevel 1 (
    set DASH_URL=http://brahmastra.local:8000
)

REM ── Open browser after 5 seconds (server needs time to start) ─────────
echo  [OK]    Launching dashboard in 5 seconds at: !DASH_URL!
start "BRAHMASTRA-browser" /min cmd /c "timeout /t 5 /nobreak >nul && start "" !DASH_URL!"

REM ── Final banner then launch ──────────────────────────────────────────
echo.
echo  ┌──────────────────────────────────────────────────────────────┐
echo  │  Dashboard  :  !DASH_URL!
echo  │  API Docs   :  http://localhost:8000/docs
echo  │  WebSocket  :  ws://localhost:8000/ws
echo  │                                                              │
echo  │  Press Ctrl+C to stop the engine.                           │
echo  └──────────────────────────────────────────────────────────────┘
echo.
title BRAHMASTRA_v1 - Running at !DASH_URL!

python main.py --mode brahmastra_dashboard

REM ── If server exits cleanly, pause so user can read output ────────────
echo.
echo  BRAHMASTRA engine stopped.
pause
