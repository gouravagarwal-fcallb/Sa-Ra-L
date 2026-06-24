@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM  Sa-Ra-L  |  Strategy Log Watcher
REM  Opens a separate terminal window for each active strategy log.
REM  Run this AFTER starting the portfolio: Start.bat
REM ═══════════════════════════════════════════════════════════════════════

title Sa-Ra-L Log Launcher

echo.
echo  Sa-Ra-L  ^|  Opening log windows for all active strategies...
echo  Each strategy gets its own colour-coded terminal.
echo.

REM ── NIFTY_INTRADAY_v1 (cyan) ─────────────────────────────────────────
start "NIFTY_INTRADAY Log" cmd /k "color 0B && python tail_log.py --strategy NIFTY_INTRADAY_v1 --analysis --lines 80"

REM ── BLACK_SWAN_v1 (magenta) ──────────────────────────────────────────
start "BLACK_SWAN Log" cmd /k "color 0D && python tail_log.py --strategy BLACK_SWAN_v1 --analysis --lines 80"

REM ── EXPIRY_SCALPER_v1 (yellow) ───────────────────────────────────────
start "EXPIRY_SCALPER Log" cmd /k "color 0E && python tail_log.py --strategy EXPIRY_SCALPER_v1 --analysis --lines 80"

REM ── RANGE_SCALPER_v1 (green) ─────────────────────────────────────────
start "RANGE_SCALPER Log" cmd /k "color 0A && python tail_log.py --strategy RANGE_SCALPER_v1 --analysis --lines 80"

REM ── Combined master log (white) ───────────────────────────────────────
start "Combined Log" cmd /k "color 07 && python tail_log.py --lines 100"

echo.
echo  Opened 5 log windows. Close any you don't need.
echo  Tip: use --export report.txt to save a session log for reporting.
echo.
pause
