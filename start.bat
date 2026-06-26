@echo off
title BRAHMASTRA_v1 — Starting...
cd /d "%~dp0"

echo.
echo  ============================================================
echo   BRAHMASTRA_v1  ^|  Algo Trading System  ^|  India Indices
echo  ============================================================
echo.

REM ── Check credentials file exists ────────────────────────────
if not exist "config\settings.local.yaml" (
    echo  ERROR: config\settings.local.yaml not found!
    echo.
    echo  Run this once to create it:
    echo    copy config\settings.local.yaml.example config\settings.local.yaml
    echo.
    echo  Then open it in Notepad and fill in your Kite API key + secret.
    echo.
    pause
    exit /b 1
)

REM ── Check Python is available ─────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found in PATH.
    echo  Install Python from https://python.org and tick "Add to PATH".
    pause
    exit /b 1
)

REM ── Step 1: Kite Login ────────────────────────────────────────
echo  [1/3] Kite Login
echo        A browser window will open. Log in and authorise the app.
echo        Come back here when done.
echo.
python main.py --mode login --strategy BRAHMASTRA_v1
if %errorlevel% neq 0 (
    echo.
    echo  Login failed. Check your API key / secret in config\settings.local.yaml
    pause
    exit /b 1
)

echo.
echo  Login successful.
echo.

REM ── Step 2: Start dashboard in a new window ───────────────────
echo  [2/3] Starting BRAHMASTRA dashboard...
start "BRAHMASTRA_v1 — Live" cmd /k "cd /d "%~dp0" && python main.py --mode brahmastra_dashboard --strategy BRAHMASTRA_v1"

REM ── Step 3: Wait for server then open browser ─────────────────
echo  [3/3] Waiting for server to start...
timeout /t 6 /nobreak > nul
start "" http://localhost:8000

echo.
echo  ============================================================
echo   Dashboard is running at  http://localhost:8000
echo.
echo   To stop:  close the "BRAHMASTRA_v1 — Live" window
echo   To restart: double-click start.bat again
echo  ============================================================
echo.
pause
