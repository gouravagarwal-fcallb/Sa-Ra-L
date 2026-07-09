@echo off
REM CLIENT REVIEW build: live trading is hard-disabled (arm/confirm return 403).
REM Safe to hand to a sample client for a look at the interface.
set SARAL_REVIEW_MODE=1
start "" wscript "%~dp0run_hidden.vbs"
exit
