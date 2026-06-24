@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM  Sa-Ra-L  |  Daily Strategy Report Exporter
REM  Exports today's per-strategy logs to plain-text report files.
REM  Run at end of trading day.
REM ═══════════════════════════════════════════════════════════════════════

title Sa-Ra-L Report Export

for /f "tokens=1-3 delims=/" %%a in ("%DATE%") do (
    set TODAY=%%c-%%a-%%b
)

echo.
echo  Sa-Ra-L  ^|  Exporting daily reports for %TODAY%
echo.

set REPORT_DIR=reports\%TODAY%
if not exist "%REPORT_DIR%" mkdir "%REPORT_DIR%"

REM ── Export each strategy log ──────────────────────────────────────────
python tail_log.py --strategy NIFTY_INTRADAY_v1 --analysis --no-color --lines 9999 --export "%REPORT_DIR%\NIFTY_INTRADAY_v1.txt" --date %TODAY%
echo   NIFTY_INTRADAY_v1  done.

python tail_log.py --strategy BLACK_SWAN_v1 --analysis --no-color --lines 9999 --export "%REPORT_DIR%\BLACK_SWAN_v1.txt" --date %TODAY%
echo   BLACK_SWAN_v1      done.

python tail_log.py --strategy EXPIRY_SCALPER_v1 --analysis --no-color --lines 9999 --export "%REPORT_DIR%\EXPIRY_SCALPER_v1.txt" --date %TODAY%
echo   EXPIRY_SCALPER_v1  done.

python tail_log.py --strategy RANGE_SCALPER_v1 --analysis --no-color --lines 9999 --export "%REPORT_DIR%\RANGE_SCALPER_v1.txt" --date %TODAY%
echo   RANGE_SCALPER_v1   done.

echo.
echo  Reports saved to %REPORT_DIR%\
echo  Open with Notepad, Word, or attach to your daily trading report.
echo.
explorer "%REPORT_DIR%"
pause
