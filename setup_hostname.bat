@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM  BRAHMASTRA_v1  |  One-Time Hostname Setup
REM
REM  Run this ONCE as Administrator to give your dashboard a proper name:
REM      http://brahmastra.local:8000
REM  instead of:
REM      http://localhost:8000
REM
REM  How to run as Admin:
REM    Right-click setup_hostname.bat → "Run as administrator"
REM
REM  What this does:
REM    Adds one line to your Windows hosts file:
REM      127.0.0.1    brahmastra.local
REM    This is a local-only change. Nothing goes to the internet.
REM    You can remove it anytime by editing:
REM      C:\Windows\System32\drivers\etc\hosts
REM ═══════════════════════════════════════════════════════════════════════

title BRAHMASTRA_v1  |  Hostname Setup
color 0B

REM ── Check for administrator privileges ────────────────────────────────
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [ERROR] This script must be run as Administrator.
    echo.
    echo  How to fix:
    echo    Right-click setup_hostname.bat
    echo    Select "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo.
echo  ╔══════════════════════════════════════════════════════════════╗
echo  ║         BRAHMASTRA_v1   Hostname Setup                      ║
echo  ╚══════════════════════════════════════════════════════════════╝
echo.

set HOSTS_FILE=%SystemRoot%\System32\drivers\etc\hosts
set ENTRY=127.0.0.1    brahmastra.local

REM ── Check if entry already exists ─────────────────────────────────────
findstr /C:"brahmastra.local" "%HOSTS_FILE%" >nul 2>&1
if not errorlevel 1 (
    echo  [OK]  brahmastra.local is already set up.
    echo.
    echo  Your dashboard URL:  http://brahmastra.local:8000
    echo.
    pause
    exit /b 0
)

REM ── Add the entry ─────────────────────────────────────────────────────
echo.
echo  Adding brahmastra.local to hosts file...
echo %ENTRY% >> "%HOSTS_FILE%"

if errorlevel 1 (
    echo.
    echo  [ERROR] Could not write to hosts file.
    echo          Make sure you are running as Administrator.
    echo.
    pause
    exit /b 1
)

REM ── Flush DNS cache so change takes effect immediately ────────────────
ipconfig /flushdns >nul 2>&1

echo.
echo  ╔══════════════════════════════════════════════════════════════╗
echo  ║                                                              ║
echo  ║  [DONE]  Setup complete!                                     ║
echo  ║                                                              ║
echo  ║  Your dashboard is now available at:                        ║
echo  ║                                                              ║
echo  ║      http://brahmastra.local:8000                           ║
echo  ║                                                              ║
echo  ║  Run brahmastra.bat to start the platform.                  ║
echo  ║                                                              ║
echo  ╚══════════════════════════════════════════════════════════════╝
echo.
pause
