@echo off
REM ── Build Sa-Ra-L.exe (hidden console, self-contained) ───────────────────────
REM Run from the repo root on Windows. Needs: pip install pyinstaller
cd /d "%~dp0"

echo [1/2] Building the frontend...
pushd frontend\brahmastra
call npm run build
popd

echo [2/2] Building the exe (a few minutes)...
pyinstaller --noconfirm --clean --onefile --noconsole --name "Sa-Ra-L" ^
  --add-data "frontend\brahmastra\build;frontend/brahmastra/build" ^
  --add-data "strategies;strategies" ^
  --add-data "config;config" ^
  --collect-submodules uvicorn ^
  --collect-all kiteconnect ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  launcher.py

echo.
echo Done. Your app is:  dist\Sa-Ra-L.exe
echo Put settings.local.yaml + config\.kite_token next to it before running live.
