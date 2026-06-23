@echo off
REM ══════════════════════════════════════════════════════
REM  Sa-Ra-L  |  Morning Auto-Login  (Windows)
REM  Schedule with Task Scheduler to run at 8:00 AM IST
REM ══════════════════════════════════════════════════════

cd /d "%~dp0.."
python main.py --mode autologin

REM After login succeeds, launch portfolio runner automatically:
REM Uncomment the line below if you want it to start trading right after login.
REM python main.py --mode portfolio
