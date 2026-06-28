# Sa-Ra-L — Unified Control Dashboard

One web app for **all 14 strategies**: see them, run them, view backtests, read the
daily analysis, and confirm each is "properly checked" — plus live multi-timeframe
charts with Bollinger Bands and a forward-impact projection.

## Launch it

```bash
# 1. (first time only) install dependencies
pip install -r requirements.txt
cd frontend/brahmastra && npm install && npm run build && cd ../..

# 2. start the dashboard
python main.py --mode unified
```

Then open **http://localhost:8000** in your browser.

- Change the port: `set SARAL_PORT=9000` (Windows) before launching.
- Auto-start every live/paper strategy on boot: `set SARAL_AUTOSTART=1`.

## The four pages (top nav)

| Page | What it shows |
|------|---------------|
| **Strategies** | A card per strategy: status, a readiness traffic-light (backtest / backfill / ticks / config), live P&L, and **Run (paper)** / **Stop** / **Live** buttons. Click a card to open its detail view. |
| **Backtests** | Every strategy's backtest summary in one table (P&L, win %, Sharpe, drawdown). "Source" shows `summary.json`, `csv`, or `none — run backtest`. |
| **Daily Analysis** | Pre-market bias, each strategy's latest signal, and today's trades — all in one place. |
| **Readiness** | The "properly checked, like Brahmastra" board: ✓ / ✗ per strategy for backtest, backfill, live ticks, and config. |

### Strategy detail view
Opens with the **multi-timeframe charts always on screen** — 1m, 5m, 15m, 1h, 1day,
1week — each with **Bollinger Bands (20, 2σ) overlaid**, plus a **Forward Impact**
panel projecting the likely move over the next 15–30 minutes. Below that: live trades
and the log stream for that strategy.

## Going live (real money) — the safety guard

Live trading is **never one click**:
1. Click **Live** on a strategy card (only enabled if it has allocated capital).
2. The server issues a one-time token (60-second expiry).
3. You must type the exact phrase `GO LIVE <STRATEGY_NAME>` to confirm.

A red **LIVE** banner stays on screen while any strategy trades real money, and
**STOP ALL** (top-right) halts everything instantly.

## Make a strategy "READY" (green everywhere)

The readiness lights go green once each check passes:
- **backtest** → run `python main.py --mode backtest --strategy <NAME>` (writes
  `strategies/<NAME>/results/summary.json`).
- **backfill** → run `python main.py --mode backfill --strategy <NAME>` (needs
  internet for market data).
- **ticks** → start the strategy (paper or live); live ticks must be flowing.
- **config** → platform constants current (NIFTY lot = 65, Tuesday expiry).

You can also print the whole board from the terminal:
```bash
python main.py --mode readiness_check
```

## Morning routine (automation)

Each trading morning, two things should run before the open:

```bash
python main.py --mode autologin          # ~08:00 IST — refresh the Kite access token
python main.py --mode preflight          # ~09:00 IST — GO/NO-GO self-check (login,
                                          #              data feed, per-strategy readiness).
                                          #              Exit 0 = GO, 1 = NO-GO.
python main.py --mode premarket_alert    # ~08:10 IST — build pre-market analysis,
                                          #              push bias + conclusion + best-fit
                                          #              strategies to Telegram/email
```

Schedule them with **Windows Task Scheduler** (two daily triggers at 08:00 and 08:10),
or on Linux/Mac with cron:
```
0 8  * * 1-5  cd /path/to/Sa-Ra-L && python main.py --mode autologin
10 8 * * 1-5  cd /path/to/Sa-Ra-L && python main.py --mode premarket_alert
```
Notification channels (Telegram bot token / email) are configured under
`notifications:` in `config/settings.local.yaml`. If none are enabled, the
briefing is printed to the console instead.

## Deep backtests (data sources)

Intraday backtests need minute-level history, and the source decides how far back:

```bash
# Default (free Yahoo) — only the last ~30–60 days of intraday bars
python main.py --mode backtest --strategy NIFTY_INTRADAY_v1

# Kite (deep intraday history ~2015+) — needs a valid Kite login first
python main.py --mode login
python main.py --mode backtest --strategy NIFTY_INTRADAY_v1 --source kite --from 2018-01-01

# NET backtest — EVERY strategy in one command, consolidated table + saved report.
# This is the pre-open decision view. Use the deepest history you want:
python main.py --mode backtest_all --source kite --from 2018-01-01
#   → prints a per-strategy + portfolio table and writes reports/net_backtest_<date>.json
#   → bars are cached under data/intraday_cache/ so re-runs are fast

# 20-year structural backtest (daily bars + synthetic option pricing)
python main.py --mode brahmastra_bt --strategy BRAHMASTRA_v1
```

`--from YYYY-MM-DD` / `--to YYYY-MM-DD` override any strategy's backtest range.
With `--source kite` you get real intraday bars back to ~2015; without it, yfinance
caps every intraday backtest at the last ~60 days (the range is auto-clamped so it
never silently produces 0 trades).

Realistic horizons: **20 years** = structural/daily only (`brahmastra_bt`); **~10 years**
= real intraday via `--source kite`; **30–60 days** = free Yahoo. A true 20-year
*intraday options* backtest is not possible — minute data and weekly options don't
exist that far back. Kite index candles carry volume = 0, so volume-surge filters
won't trigger on them (a futures underlying would be a future refinement).

## Notes
- The three previously-missing backtests (ATM Pulse Burst, BB Expiry Scalper,
  Black Swan) are now implemented and run via `--mode backtest`.
- INRUSD and Pashupatastra were merged in from their own branches; all 14
  strategies now live on one branch with one master `strategies/registry.yaml`.
