#!/usr/bin/env bash
# ══════════════════════════════════════════════════════
#  Sa-Ra-L  |  Morning Auto-Login  (Linux / macOS)
#  Add to crontab:
#    crontab -e
#    30 2 * * 1-5  /path/to/Sa-Ra-L/scripts/morning_login.sh
#    (2:30 UTC = 8:00 IST on weekdays)
# ══════════════════════════════════════════════════════

cd "$(dirname "$0")/.."
python3 main.py --mode autologin

# Uncomment to auto-launch portfolio after login:
# python3 main.py --mode portfolio
