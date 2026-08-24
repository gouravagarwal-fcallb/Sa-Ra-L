@echo off
title Sa-Ra-L — Starting...
cd /d "%~dp0"

echo.
echo  ============================================================
echo   Sa-Ra-L  ^|  The Wealth Fortress  ^|  Unified Dashboard
echo  ============================================================
echo.

REM -- Check credentials file exists ----------------------------
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

REM -- Check Python is available --------------------------------
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found in PATH.
    echo  Install Python from https://python.org and tick "Add to PATH".
    pause
    exit /b 1
)

REM -- Step 1: Auto-Login to Kite -------------------------------
echo  [1/3] Auto-login to Kite Connect...
echo.
python main.py --mode autologin
if %errorlevel% neq 0 (
    echo.
    echo  Auto-login failed. Check config\settings.local.yaml has:
    echo    broker.kite.user_id, password, totp_secret, api_key, api_secret
    pause
    exit /b 1
)

echo.
echo  Login successful.
echo.

REM -- Step 2: Start the UNIFIED dashboard (all strategies) -----
echo  [2/3] Starting the Sa-Ra-L unified dashboard...
start "Sa-Ra-L — Live" cmd /k "cd /d "%~dp0" && python main.py --mode unified"

REM -- Step 3: Wait for server then open browser ----------------
echo  [3/3] Waiting for server to start...
timeout /t 6 /nobreak > nul
start "" http://localhost:8000

echo.
echo  ============================================================
echo   Dashboard is running at  http://localhost:8000
echo.
echo   To stop:    close the "Sa-Ra-L - Live" window
echo   To restart: double-click start.bat again
echo  ============================================================
echo.
pause
