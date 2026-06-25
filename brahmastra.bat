@echo off
setlocal EnableDelayedExpansion
REM =======================================================================
REM  BRAHMASTRA_v1  |  One-Click Launcher
REM  Double-click this file (or run from cmd) to start the platform.
REM  Dashboard opens automatically in your browser.
REM
REM  First run: takes ~30 seconds (installs packages).
REM  Every run after: starts in ~5 seconds.
REM
REM  For custom URL (brahmastra.local), run setup_hostname.bat once as Admin.
REM =======================================================================

title BRAHMASTRA_v1 - Starting...
color 0A

REM -- Move to the folder where this .bat file lives ----------------------
cd /d "%~dp0"

echo.
echo  +--------------------------------------------------------------+
echo  ^|                                                              ^|
echo  ^|     BRAHMASTRA_v1    Market Intelligence Platform           ^|
echo  ^|                                                              ^|
echo  +--------------------------------------------------------------+
echo.

REM -- Activate virtual environment if present ----------------------------
if exist ".venv\Scripts\activate.bat" (
    echo  [SETUP] Activating .venv...
    call ".venv\Scripts\activate.bat"
    goto :venv_done
)
if exist "venv\Scripts\activate.bat" (
    echo  [SETUP] Activating venv...
    call "venv\Scripts\activate.bat"
)
:venv_done

REM -- Python check -------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] Python not found.
    echo          Install Python 3.10+ from: https://python.org
    echo          Tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  [OK]    %%v found

REM -- Check required Python packages ------------------------------------
python -c "import fastapi, uvicorn" >nul 2>&1
if not errorlevel 1 goto :pkgs_ok

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

:pkgs_ok

REM -- Install kiteconnect if missing ------------------------------------
python -c "import kiteconnect" >nul 2>&1
if errorlevel 1 (
    echo  [SETUP] Installing kiteconnect...
    pip install kiteconnect --quiet
)

REM -- Build React dashboard if build folder is missing -----------------
if exist "frontend\brahmastra\build\index.html" goto :build_ok

echo.
echo  [SETUP] Building dashboard UI (first time only, ~30 seconds)...
echo.

where npm >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] npm not found.
    echo          Install Node.js LTS from: https://nodejs.org
    echo          Restart cmd after install, then run brahmastra.bat again.
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
    echo          Try manually:
    echo            cd frontend\brahmastra
    echo            npm install
    echo            npm run build
    echo.
    pause
    exit /b 1
)
echo  [OK]    Dashboard UI built.

:build_ok

REM -- Determine dashboard URL -------------------------------------------
set DASH_URL=http://localhost:8000
findstr /C:"brahmastra.local" "%SystemRoot%\System32\drivers\etc\hosts" >nul 2>&1
if not errorlevel 1 set DASH_URL=http://brahmastra.local:8000

REM -- Open browser after 5 seconds (server needs time to bind) ----------
echo  [OK]    Launching dashboard at: !DASH_URL!  (opens in 5s)
start "BRAHMASTRA-browser" /min cmd /c "timeout /t 5 /nobreak >nul && start "" !DASH_URL!"

REM -- Launch banner then start ------------------------------------------
echo.
echo  +--------------------------------------------------------------+
echo  ^|  Dashboard : !DASH_URL!
echo  ^|  API Docs  : http://localhost:8000/docs
echo  ^|  WebSocket : ws://localhost:8000/ws
echo  ^|                                                              ^|
echo  ^|  Press Ctrl+C to stop.                                      ^|
echo  +--------------------------------------------------------------+
echo.
title BRAHMASTRA_v1 - Running at !DASH_URL!

python main.py --mode brahmastra_dashboard

echo.
echo  BRAHMASTRA engine stopped.
pause
