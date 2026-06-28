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

## Notes
- The three previously-missing backtests (ATM Pulse Burst, BB Expiry Scalper,
  Black Swan) are now implemented and run via `--mode backtest`.
- INRUSD and Pashupatastra were merged in from their own branches; all 14
  strategies now live on one branch with one master `strategies/registry.yaml`.
