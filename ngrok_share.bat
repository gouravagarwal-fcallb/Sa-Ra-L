@echo off
:: ════════════════════════════════════════════════════════════════
::  BRAHMASTRA — Share Dashboard via ngrok
::  Run this AFTER brahmastra.bat is already running (dashboard live).
::
::  What it does:
::    1. Checks ngrok is installed (https://ngrok.com/download)
::    2. Creates a public HTTPS tunnel to localhost:8000
::    3. Prints the public URL — share with seniors / mentors
::
::  One-time setup (run once, then this bat just works):
::    ngrok config add-authtoken YOUR_TOKEN_FROM_NGROK_DASHBOARD
::
::  Security note:
::    The URL is a random subdomain — safe to share over WhatsApp/Telegram.
::    Stop this bat to revoke access instantly.
:: ════════════════════════════════════════════════════════════════

echo.
echo  BRAHMASTRA — ngrok Dashboard Sharing
echo  ======================================
echo.

:: Check ngrok is on PATH
where ngrok >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: ngrok not found.
    echo.
    echo  Install steps:
    echo    1. Go to https://ngrok.com/download
    echo    2. Download the Windows zip, extract ngrok.exe
    echo    3. Move ngrok.exe to C:\Windows\System32\  (or any folder in PATH)
    echo    4. Run:  ngrok config add-authtoken YOUR_TOKEN
    echo       (token from https://dashboard.ngrok.com/get-started/your-authtoken)
    echo.
    pause
    exit /b 1
)

:: Check dashboard is running
curl -s http://localhost:8000 >nul 2>&1
if %errorlevel% neq 0 (
    echo  WARNING: Dashboard not responding on localhost:8000
    echo  Make sure brahmastra.bat is running first.
    echo  Continuing anyway...
    echo.
)

echo  Starting ngrok tunnel to localhost:8000...
echo  (Press Ctrl+C to stop sharing — URL becomes invalid instantly)
echo.
echo  The public URL will appear in the ngrok window below.
echo  Share it over Telegram/WhatsApp with your seniors.
echo.

ngrok http 8000 --log=stdout

pause
