# NIFTY_INTRADAY_v1 — Live Rulebook

## Overview
Pure Nifty 50 options buying strategy. No Bank Nifty, no futures, no option selling.
One open trade at a time. Unlimited trades per day (with cooldowns). Auto-closes by 15:10.

---

## Account Rules (Non-Negotiable)

| Parameter | Value |
|---|---|
| Total capital | ₹50,000 |
| Max per trade | ₹10,000 (premium) |
| Hard stop per trade | ₹1,200 |
| Profit target per trade | ₹2,750 |
| Daily loss lock | ₹4,800 → stop all trades |
| Daily profit lock | ₹8,250 → half-size only |
| Cooldown after stop | 15 minutes |
| Cooldown after profit | 5 minutes |
| Hard close | 15:10 IST (no exceptions) |

---

## Position Sizing Formula

**Objective:** Guarantee max loss = ₹1,200 regardless of option behaviour.

```
max_lots    = floor(10,000 ÷ (entry_premium × 75))
qty         = max_lots × 75
stop_prem   = entry_premium − (1,200 ÷ qty)
target_prem = entry_premium + (2,750 ÷ qty)
be_prem     = entry_premium + (1,375 ÷ qty)   ← breakeven trigger at 50% target
```

### Example A — Bullish Trend Day (ORB Breakout)
- Nifty at 24,100. ORB high = 24,140. Breakout confirmed at 9:38.
- ATM CE 24,100 LTP = **₹120**
- `max_lots = floor(10,000 ÷ (120 × 75)) = 1 lot`
- `qty = 75`
- `stop_prem  = 120 − 1,200/75 = 120 − 16 = ₹104`
- `target_prem = 120 + 2,750/75 = 120 + 36.7 = ₹156.7`
- `be_prem = 120 + 1,375/75 = ₹138.3` (move stop to entry when LTP hits ₹138)
- Budget used: 120 × 75 = **₹9,000** ✓
- Max loss: (120−104) × 75 = **₹1,200** ✓
- Target gain: (156.7−120) × 75 = **₹2,750** ✓
- Risk:Reward = **1 : 2.29**

### Example B — Bearish Range Day (S/R Rejection)
- Nifty at 24,230. Resistance at 24,245. RSI = 68. Shooting star on 1m.
- ATM PE 24,200 LTP = **₹45**
- `max_lots = floor(10,000 ÷ (45 × 75)) = 2 lots`
- `qty = 150`
- `stop_prem  = 45 − 1,200/150 = 45 − 8 = ₹37`
- `target_prem = 45 + 2,750/150 = 45 + 18.3 = ₹63.3`
- `be_prem = 45 + 1,375/150 = ₹54.2`
- Budget used: 45 × 150 = **₹6,750** ✓
- Max loss: (45−37) × 150 = **₹1,200** ✓
- Target gain: (63.3−45) × 150 = **₹2,745 ≈ ₹2,750** ✓
- Risk:Reward = **1 : 2.29**

---

## Regime Classification (refreshed every 15 min)

| Signal | TREND | RANGE |
|---|---|---|
| ATR vs session median | > 1.2× median | ≤ 1.0× median |
| VWAP slope (1m bars) | Clear slope up/down | Flat (< 0.05% per bar) |
| Price vs ORB | Outside by >10% of range | Inside ORB |
| Score (need 2/3) | ≥ 2 → TREND | < 2 → RANGE |

**When in doubt, default to RANGE** (safer, avoids chasing).

---

## Entry Rules

### TREND Day — ORB Breakout
1. Opening range = first 15 min (9:15–9:30). Record OR_HIGH and OR_LOW.
2. Wait for 15-min candle to close **above OR_HIGH + 0.05% buffer** → BUY CE (ATM)
3. Or close **below OR_LOW − 0.05% buffer** → BUY PE (ATM)
4. Confirm: price must be on correct side of VWAP
5. Confirm: current bar volume ≥ 1.3× average of last 10 bars
6. Skip if price is more than 0.5% beyond ORB (already moved, don't chase)

### RANGE Day — Support/Resistance Rejection
1. Look back last 30 x 1-min bars. Resistance = max high, Support = min low.
2. At SUPPORT → BUY CE when:
   - Close within 0.15% of support level
   - RSI(14) < 35 (oversold)
   - Last bar is hammer OR bullish engulfing
3. At RESISTANCE → BUY PE when:
   - Close within 0.15% of resistance
   - RSI(14) > 65 (overbought)
   - Last bar is shooting star OR bearish engulfing

### Universal Skip Conditions
- First 10 min of session (9:15–9:24)
- LTP < ₹10 (too cheap, wide bid-ask, illiquid)
- LTP × qty > ₹10,000 (budget breach)
- qty = 0 (sizing math rejects trade)
- Another trade is already open
- In cooldown period
- Daily P&L gates triggered

---

## Exit Rules

| Condition | Action |
|---|---|
| LTP ≥ target_prem | Exit → TARGET_HIT → 5 min cooldown |
| LTP ≤ stop_prem (initial) | Exit → STOP_LOSS → 15 min cooldown |
| LTP first reaches be_prem | Move stop to entry_prem (breakeven trail) |
| LTP ≤ entry_prem (after BE) | Exit → BE_STOP → 5 min cooldown |
| Time ≥ 15:10 | Exit → TIME_EXIT → stop for day |

**No partial exits.** Full position exits in one order.

---

## Breakeven Trail Logic

After the position reaches **50% of the target profit** (`be_prem = entry + 1,375/qty`):
- System automatically moves the stop from `stop_prem` to `entry_prem`
- If price then falls back to entry, position exits at breakeven (₹0 P&L, not a loss)
- This protects accumulated gains without capping the upside

---

## Kill Switch Conditions

The system automatically halts when:
1. **Daily loss lock** hits ₹4,800 (state → LOCKED_LOSS)
2. **Order placement failure** — live BUY returns empty order_id → trade not tracked
3. **Manual Ctrl+C** — closes any open position, prints EOD summary

Additional kill switch triggers (manual, check before each session):
- Yesterday's realized loss > ₹4,800 → skip today
- News of major macro event in next 2 hours → don't run
- VIX > 35 → premiums too expensive, sizing breaks down

---

## Backtest Plan

**Period:** Jan 2024 – Jun 2026 (18 months, ~360 trading days)

**Required metrics:**
- Win rate (target: >45%)
- Avg win / Avg loss (target: ratio >2.0)
- Expectancy per trade: avg_win × win_rate − avg_loss × loss_rate > 0
- Profit factor: total_wins / total_losses (target: >1.5)
- Max drawdown (target: <₹15,000 = 3× daily loss lock)
- Sharpe ratio (target: >1.5 annualised)
- Slippage impact: compare theoretical vs slipped P&L

**Special emphasis:**
- Wednesday sessions (mid-week — often range-bound)
- Post-expiry weeks (Tuesday → new series, lower liquidity)
- High-VIX sessions (VIX > 20): check if sizing still holds

---

## Monitoring Checklist (Daily)

### Before session (8:45–9:10)
- [ ] Check yesterday's P&L — is daily lock still reset?
- [ ] Note India VIX level (if >22, expect expensive premiums)
- [ ] Note Dow/Gift Nifty direction for pre-market bias
- [ ] Verify `python main.py --mode login` ran (fresh Kite token)
- [ ] Start portfolio runner at 9:10: `python main.py --mode portfolio`

### During session
- [ ] Dashboard shows correct regime (TREND/RANGE) by 9:35
- [ ] ORB levels printed in analysis log by 9:31
- [ ] Cooldown timer counts down correctly after each trade
- [ ] P&L locks trigger at correct levels (₹4,800 loss / ₹8,250 profit)

### After session (15:30)
- [ ] All positions closed (no open trades)
- [ ] EOD summary matches Kite app P&L (within slippage tolerance)
- [ ] Review each trade: was the entry signal correct? Was stop appropriate?
- [ ] Log slippage (expected vs actual fill price)

---

## Weekly Review Checklist

- [ ] Win rate this week vs target (>45%)
- [ ] Average win size vs target (>₹2,750 × win_rate)
- [ ] Number of stop-outs vs targets — is stop too tight?
- [ ] Regime classification accuracy — did TREND days actually trend?
- [ ] Cooldown effectiveness — any re-entries immediately after stop?
- [ ] Slippage tracking — are fills within expected 0.3% of LTP?
- [ ] Any days where daily lock was hit — what happened?

---

## Developer Summary (10 Bullets)

1. **Strategy type:** `nifty_intraday` — plug into `portfolio_runner.py` same as expiry_scalper
2. **Regime switch:** TREND triggers ORB logic; RANGE triggers S/R logic — refreshed every 15 min
3. **ORB window:** 9:15–9:30 (15 min). ORB_HIGH and ORB_LOW locked once and held all session
4. **Sizing guarantee:** `qty = floor(10,000 / (LTP × 75)) × 75`. Then `stop = entry − 1,200/qty`. Never exceeds ₹1,200 loss or ₹10,000 budget
5. **Breakeven trail:** After LTP hits `entry + 1,375/qty` (50% target), stop moves to entry
6. **State machine:** IDLE → IN_TRADE → COOLDOWN (15min loss / 5min profit) → IDLE. LOCKED_LOSS/PROFIT are terminals until EOD reset
7. **No overnight:** Hard close at 15:10 via time check in main loop. If missed, broker MIS auto-squares at 15:20
8. **Live orders:** Same pattern as expiry_scalper — `_place_buy()` calls `broker.place_order()`. Empty order_id = order failed, position NOT tracked
9. **Paper mode:** LTP computed via Black-Scholes pricer (option_pricer.py). `mode="paper"` skips broker call but logs everything
10. **Backtest:** `strategy_type: nifty_intraday` needs BacktestEngine handler. Add `run_nifty_intraday()` to engine.py for historical validation
11. **Logging:** Every trade logged to `logs/trades_YYYY-MM-DD.csv` via portfolio_runner callback
12. **Kill switch:** Order failure → skip (no phantom P&L). Daily loss lock → LOCKED_LOSS state, no entries until EOD
