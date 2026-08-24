# Operator Validation — prove it works on YOUR laptop (Phase A)

> Plain-language, do-this-then-check-that. This is the **gate**: don't go live on
> any strategy until every step here is green. If a step is wrong, copy the exact
> screen text / error and send it — that's all I need to fix it.
>
> You need: your laptop, internet, and your Zerodha Kite login. ~20 minutes.

---

## Step 0 — Get the latest code
```bash
git checkout claude/market-strategies-overview-83wczv
git pull origin claude/market-strategies-overview-83wczv
```
**Good =** it says "Already up to date" or pulls new files without errors.

## Step 1 — Connect Kite (fresh daily token)
```bash
python main.py --mode autologin      # or:  python main.py --mode login
```
**Good =** it prints your name / "Connected as …". 
**If wrong =** send the error; usually the API key/secret in `settings.local.yaml`.

## Step 2 — Start the platform
```bash
python main.py --mode unified
```
Then open **http://localhost:8000** (or the URL it prints) in your browser.
**Good =** the dashboard loads; the top ticker shows NIFTY / SENSEX / VIX with a
"live · Kite" tag (not "no feed").

## Step 3 — Strategies auto-start PAPER-active
Open the **Strategies** tab.
**Good =** strategies show up already running in **paper** (green), no manual push
needed. None should say "live" unless you armed it. SRAL / PASHUPATASTRA must NOT
be live.
**Check the safety line:** no strategy is silently off; any block is visible.

## Step 4 — Charts load, including 1D / 1W
Open any strategy → **Charts**. Look at the **1D** and **1W** panels.
**Good =** candles + Bollinger bands draw on all timeframes, including 1D and 1W
(these now come from Kite — the old blank/gappy weekly is fixed).
**If wrong =** tell me which timeframe is blank and whether the ticker says "Kite".

## Step 5 — FII net shows a real number
Open the **Pre-Market** tab → India internals.
**Good =** "FII net" shows something like **+₹1,820 Cr** (green) or a negative red
number — NOT blank / 0 / "—" during market hours. (It's previous-session data, so
a small "—" before ~9:20 is normal; hit ↻ Refresh after the open.)

## Step 6 — Forward Impact points the RIGHT way
Open any strategy → **Forward Impact**. Compare its arrow to the actual index.
**Good =** if the index is grinding **up**, it reads **▲ Up** (green); if **down**,
**▼ Down** (red). The "conviction building/fading" chip is separate from the arrow.
**If wrong =** screenshot it next to the live index and send it.

## Step 7 — Watch ONE paper trade flow end-to-end
Leave it running during a live session until a strategy takes a paper trade.
Open that strategy → **Trades**.
**Good =** the trade appears under **OPEN** with an entry price and a live
(unrealised) P&L; when it exits it moves to **CLOSED** with entry, exit, reason,
and a computed ₹ P&L. The **Total P&L** header adds up to what's on screen.
**This is the most important check** — everything institutional rests on these
numbers being right.

## Step 8 — Portfolio Risk populates (observe-only)
Once at least one position is open, open the **Portfolio Risk** tab.
**Good =** it shows premium-at-risk, net Greeks, a stress ladder, and per-strategy
rows. It is read-only — it never places orders.

## Step 9 — The ONE real-money proof: buy 1 Tata Power
Only when Steps 1–8 are green, and the market is open:
Open **Readiness** → the **🧪 Broker live-test** card.
1. Leave defaults (BUY 1 TATAPOWER · NSE · CNC · MARKET). Press **Arm**.
2. Type exactly **`BUY 1 TATAPOWER`** in the box.
3. Press **● PLACE REAL ORDER**.
**Good =** it returns an **order_id**, and the order shows in your **Kite orderbook**
as 1 share of Tata Power. This proves the real-order path works with the smallest
possible trade.
**If wrong =** send the on-screen error (funds/margin/market-hours are the usual
causes).

## Step 10 — End-of-day closure report
After the session closes, open the **Closure Report** tab (or run the EOD job).
**Good =** it shows the day's trades, no-trade audit, and per-strategy analysis.

---

## What to send me after the run
Just tell me the step number(s) that weren't green and paste the exact text.
If all 10 are green: say "Phase A clean" and we move to Phase C (paper-run a few
real sessions) → then tiny live on ATM_PULSE / INRUSD with arm+confirm.

## Still needed from you (separate, any time)
1. **Keep/delete marks** on the strategy table (to delete archived + finish the
   two-mode simplification).
2. **Log-stream error lines** from your errors doc (paste 3–4).
3. This validation run itself (needs your laptop + Kite).
