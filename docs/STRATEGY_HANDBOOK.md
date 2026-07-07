# Sa-Ra-L — Strategy Handbook

_A plain-language guide to every strategy in the platform: what it is, the bet it
makes, how it decides to trade, how it exits, how much it risks, and whether it's
proven. Written so a non-programmer can read top-to-bottom and understand each one._

_Last updated: 2026-07-07. Source of truth: `strategies/registry.yaml`, each
strategy's `config.yaml` and live engine, and the validation notes in `CLAUDE.md`._

---

## How to read this document

Each strategy below follows the **same template** so you can compare them:

- **In one line** — what it does, no jargon.
- **The bet** — why it should make money (the edge).
- **Trades** — which index, calls (CE) or puts (PE), which expiry.
- **How it enters** — the trigger, in plain words then the technical rule.
- **How it exits** — target, stop-loss, and time-based close.
- **Sizing & capital** — how big each trade is and how much money is allocated.
- **Status today** — is it live, paper, testing, paused, or archived — and *can it
  actually place a real order as currently wired?*
- **Evidence** — backtest / walk-forward / live-paper results, with honest caveats.
- **Watch-outs** — the known weaknesses.

### Five ideas you need first

1. **We only BUY options (mostly).** Buying a call (CE) profits if the index rises;
   buying a put (PE) profits if it falls. Your maximum loss is the premium you paid.
   The one exception is VIX_SELLER, which *sells* options (different risk shape).
2. **Paper vs live.** "Paper" = the strategy runs on real live data but places
   pretend orders — no real money. "Live" = real orders, real money. **Every
   strategy starts in paper. A real order is never placed without you arming the
   session and typing a confirmation phrase.** Auto-start never means auto-live.
3. **The scores.** Most strategies score a setup out of some maximum and only trade
   above a threshold (e.g. ATM_PULSE needs ≥ 75/100). A high "no-trade" count on a
   quiet day is the strategy behaving correctly, not failing.
4. **Backtests are optimistic.** Historical tests here use *modelled* option prices
   with *zero* costs and perfect fills. Treat every backtest number as a best-case
   ceiling, not a promise. Small trade counts (5–15) are especially unreliable.
5. **"Can it trade?" is a real question.** A few strategies are wired as
   *analysis-only* — they score the market and log what they *would* do, but have no
   order path at all. Those are flagged clearly below.

---

## The whole roster at a glance

| # | Strategy | In five words | Instrument | Registry status | Live capital | Can place a real order? |
|---|----------|---------------|-----------|-----------------|-------------|--------------------------|
| 1 | **RAMS_v1** | 1-min momentum options scalper | NIFTY / SENSEX | live | ₹50,000 | ✅ yes |
| 2 | **NIFTY_INTRADAY_v1** | regime-adaptive Nifty options buyer | NIFTY | live | ₹10,000 | ✅ yes |
| 3 | **ATM_PULSE_BURST_v1** | ATM call momentum burst | NIFTY | live | ₹10,000 | ✅ yes (needs live OI) |
| 4 | **EXPIRY_SCALPER_v1** | expiry-day 3-window scalper | NIFTY / SENSEX | live | ₹10,000 | ✅ yes |
| 5 | **BB_EXPIRY_SCALPER_v1** | Bollinger-band expiry scalper | NIFTY / SENSEX | live | ₹10,000 | ✅ yes |
| 6 | **BLACK_SWAN_v1** | extreme-move momentum lottery | NIFTY | live | ₹20,000 | ✅ yes |
| 7 | **RANGE_SCALPER_v1** | range-day mean reversion | NIFTY / SENSEX | paper | ₹0 | ✅ yes (paper) |
| 8 | **INRUSD_v1** | USDINR futures trend scalper | USDINR | live | ₹5,00,000 | ⚠️ verify order path |
| 9 | **GAP_FADE_v1** | fade the opening gap | NIFTY / BANKNIFTY | paper | ₹10,000 | ❌ analysis-only |
| 10 | **TREND_RIDER_v1** | hold a strong trend all day | NIFTY / SENSEX | testing | ₹0 | ❌ analysis-only |
| 11 | **VIX_SELLER_v1** | sell volatility when VIX spikes | NIFTY | paper | ₹0 | ❌ analysis-only |
| 12 | **PASHUPATASTRA_v1** | rare seller-capitulation buy | NIFTY / SENSEX | paper | ₹0 | 🚫 live-blocked |
| 13 | **BRAHMASTRA_v1** | multi-market intelligence platform | NIFTY / SENSEX | paused | ₹10,000 | 🚫 paused (order path unverified) |
| 14 | **SRAL_v1** | original fixed-qty 5-min (retired) | NIFTY / SENSEX | archived | ₹0 | 🚫 archived — must not run |

> "Registry status" is the slot's designation. Regardless of it, **the operator's
> current live posture** is: real orders are only entertained for **ATM_PULSE_BURST**
> and **INRUSD** (both still behind arm + typed confirm). Everything else stays paper
> until explicitly promoted. VIX_SELLER, GAP_FADE, TREND_RIDER stay paper;
> PASHUPATASTRA and SRAL stay live-blocked; BRAHMASTRA stays paused.

---

## Shared machinery (so we don't repeat it 14 times)

**Pre-market scoring.** Several strategies (RAMS, SRAL, and in spirit others) score
the mood before 09:15 from overnight cues — the US Dow, SGX/Gift Nifty, India VIX,
and the SENSEX — producing a **direction bias** (BULLISH / BEARISH / NEUTRAL) and,
for RAMS, a **budget** (bigger conviction → bigger trade). INRUSD does the currency
version of this at 08:45 using DXY, crude, US 10-year yield, EUR/USD and VIX.

**Trade windows.** Intraday strategies evaluate at specific windows rather than
firing on every tick. RAMS/SRAL use named windows (T1 morning, T3 post-lunch, plus
"OW" off-window paper-simulation slots). Expiry scalpers use W1/W2/W3 by time of day.

**Exit types you'll see everywhere.**
- **Target** — a profit level (a % gain on the option, or a rupee amount, or a
  multiple like "3× the premium").
- **Stop-loss (SL)** — the loss level that force-exits the trade.
- **Time stop / hard close** — an unconditional exit by a clock time (e.g. 15:10,
  15:20) so nothing is carried overnight. MIS = intraday product, auto-squared.

**Position sizing.** Because option premiums vary, most strategies size by *budget*:
`quantity = floor(budget ÷ premium ÷ lot_size) × lot_size`. So the number of lots
changes with the option's price to keep the rupee exposure near the intended budget.
(NIFTY lot = **65** since 30-Dec-2025; SENSEX lot = 20.)

---

# The strategies

## Group A — Intraday momentum options buyers

These buy CE (or PE) expecting a move to continue. They "lose small, win big":
low win-rate, but winners are multiples of losers.

### 1. RAMS_v1 — Regime-Adaptive Momentum Scalper  ·  _live · ₹50,000_

**In one line.** Buys Nifty/Sensex weekly options on 1-minute momentum, scaling
trade size to how strong the signal is.

**The bet.** On a trending day, short bursts of momentum repeat. Catch several of
them; the few that run to a big target pay for the many that get stopped.

**Trades.** NIFTY or SENSEX, weekly options, CE when bullish / PE when bearish.

**How it enters.** A **3-layer confluence** must line up on the 1-minute chart:
- *Layer 1 (context):* 5-min EMA 9/21 + RSI-14 + VWAP — which way is the tide.
- *Layer 2 (trigger):* 1-min EMA 5/13 + RSI-7 + volume — is momentum firing now.
- *Layer 3 (quality):* candle body ≥ 40% of range, a breakout, consecutive candles.
The pre-market bias sets the *budget*; the live 1-min read sets the *direction*.

**How it exits.** **27.5% profit target**, or a **dynamic stop** (sized so each
trade's loss is a fixed fraction of position value), or auto-square at **15:20**.

**Sizing & capital.** Budget scales with conviction: score 3 → ₹5,000, score 4 →
₹10,000, score 5+ → ₹15,000. Live capital ₹50,000 (target ₹5,00,000). Daily loss
limit **₹3,500** on *net* P&L.

**Status today.** Live-eligible, running paper by default. **Can place real orders**
(runs on the generic Sa-Ra-L Live Engine with a full order path).

**Evidence.**
- *Walk-forward (22-Jun-2026):* 8 folds, 60% consistency, verdict **MODERATE**,
  combined +₹50,506. Real but not spectacular — a genuine, modest edge.
- *Live paper (06-Jul-2026):* 23 trades, 8W/15L (34.8%), net **+₹8,743**, profit
  factor 1.65, realized **R:R ≈ 3.1:1**, expectancy +₹380/trade. The entire edge
  was 6 target-hits (~28–30% option gains) paying for 15 small stops.

**Watch-outs.**
- The **off-window ("OW") paper-simulation slots** force a trade using the *stale
  pre-market direction* when live momentum is NEUTRAL, and there is **no re-entry
  cooldown** — on a choppy day this manufactures back-to-back stop-outs (e.g.
  03-Jul: −₹1,610 chop). A "no-trade when NEUTRAL" fix is proposed. Live T1/T3
  windows (which require real score ≥ 4 confluence) are unaffected.
- Real per-trade loss (~₹900 avg) runs larger than the config's ₹350 "per-trade
  max loss" target — stops gap/overshoot. The ₹3,500 *daily net* cap is the real
  backstop.

---

### 2. NIFTY_INTRADAY_v1 — Pure Nifty Options Buyer  ·  _live · ₹10,000_

**In one line.** Buys only Nifty options, switching tactics based on whether the
day is trending or range-bound.

**The bet.** Different days need different playbooks. Detect the regime, then use the
right entry for it, with tightly-controlled rupee risk per trade.

**Trades.** NIFTY only. No BankNifty, no futures, no selling.

**How it enters.** Regime is refreshed every 15 min (ATR vs its median, VWAP slope,
opening-range position):
- *Trend day:* 15-minute **opening-range breakout** + price on the right side of
  VWAP + a 1.3× volume surge.
- *Range day:* price closes within 0.15% of support/resistance + RSI < 35 or > 65
  + a reversal candle.

**How it exits.** **₹2,750 target** or **₹1,200 stop** (moves to breakeven after
50% of target), or hard close **15:10**. One open trade at a time; unlimited trades
with cooldowns.

**Sizing & capital.** Position sized so max loss ≈ ₹1,200 per trade regardless of
premium. Capital ₹10,000 (target ₹5,00,000). Daily stop ₹4,800.

**Status today.** Live-eligible, paper by default. **Can place real orders.**

**Evidence.** Backtest results here are **tiny-sample and unstable** (10–15 trades,
Sharpe has swung positive and negative between runs) — do not size on them.
GTI-zone research found a validated **opposing-zone veto** improves it slightly
(profit factor 1.06 → 1.09), not yet wired live.

**Watch-outs.** Marginal edge; needs many live-paper days to judge. Regime
misclassification on transition days is the main failure mode.

---

### 3. ATM_PULSE_BURST_v1 — ATM Call Momentum Burst  ·  _live · ₹10,000_

**In one line.** Buys at-the-money Nifty calls when four independent signals — trend,
option open-interest, premium breakout, and a composite score — all agree.

**The bet.** When price breaks out *and* option positioning (OI) supports it *and*
the call premium itself is breaking out, the move has fuel behind it.

**Trades.** NIFTY only, at-the-money CE (call buying only).

**How it enters.** A **4-layer model**, gated by a score ≥ **75/100**:
1. Regime — opening-range breakout + EMA 9/21 bullish.
2. OI structure — support from the NSE option chain (dual absolute + base filter).
3. Trigger — CE premium breaks a 5-bar high.
4. Score gate — the composite must reach 75.
Avoids the first 15 minutes; max 4 trades/day.

**How it exits.** Sell **50% at +15 points** (move stop to breakeven), trail the rest
to **+25 points**; hard stop **−8 points**; close **15:10**.

**Sizing & capital.** Fixed ₹10,000 budget. Daily stop ₹3,000.

**Status today.** Live-eligible and **one of only two strategies cleared for
auto-live** (behind arm + confirm). **Can place real orders.**

**⚠️ The catch — it needs a live option-chain OI feed.** The OI layer contributes up
to +25 of the score. Without a live OI feed the score **caps at ~65**, below the 75
threshold — so it will essentially **never fire** until the OI feed is connected.
This also makes it **impossible to backtest** (historical intraday OI isn't
available). Its only proof path is forward paper-testing; the closure report's
"nearest miss" line shows how close it gets (e.g. peaked 42/75 on 06-Jul).

**Watch-outs.** Data-dependent; treat any ₹0-trade day as "OI feed not connected,"
not "strategy broken."

---

### 4. TREND_RIDER_v1 — Strong-Trend Day Rider  ·  _testing · ₹0_

**In one line.** The opposite of RAMS — instead of scalping many bursts, it takes
*one* position on a strongly trending day and holds it.

**The bet.** Some days trend hard from the open. On those, holding through the noise
beats scalping in and out.

**Trades.** NIFTY / SENSEX options.

**How it enters.** After 09:45, when the index makes a **new 30-minute extreme** with
expanding volume and RSI > 65 (or < 35).

**How it exits.** **50% target** (wide) or **10% stop** (also wide) or force close
**14:45**. Holds through lunch — no re-entry churn.

**Sizing & capital.** Budget ₹10,000–25,000. Daily stop ₹5,000. Capital ₹0 (not
funded).

**Status today.** **Analysis-only.** Wired to the shared `ShadowMonitor` — it scores
and logs what it *would* do but **has no order path**. Cannot trade, even on paper,
until a bespoke live engine is built.

**Evidence.** Backtest sample is too small to trust.

**Watch-outs.** Needs a real engine before it means anything live.

---

## Group B — Expiry-day scalpers

Expiry days have unique behaviour (rapid theta decay, gamma). These two only come
alive on expiry.

### 5. EXPIRY_SCALPER_v1 — Multi-Window Expiry Scalper  ·  _live · ₹10,000_

**In one line.** On expiry day, buys cheap out-of-the-money options on momentum
breakouts, in three time windows, each hungrier than the last.

**The bet.** On expiry, small index moves make cheap OTM options explode in
percentage terms. Buy the breakout, ride the gamma.

**Trades.** NIFTY / SENSEX, expiry-day options, 2 strikes OTM in the breakout
direction.

**How it enters.** Momentum breakout + 1.3× volume surge, threshold easing through
the day:
- *W1 Morning (09:30–11:30):* 0.40% move, must be score-direction confirmed,
  premium ₹25–150, target 2.5×.
- *W2 Midday (13:00–14:30):* 0.30% move, premium ₹5–60, target 3.5×.
- *W3 End-of-day (14:45–15:10):* 0.25% move, premium ₹0.5–25, target 5× (pure
  gamma lottery).

**How it exits.** Per-window target (2.5× / 3.5× / 5×) or per-window stop
(35% / 45% / 50%), hard close **15:29**.

**Sizing & capital.** ₹10,000 per window trade. Daily stop ₹30,000 (3 × ₹10k).

**Status today.** Live-eligible, paper by default. **Can place real orders.**

**Evidence.** **Backtest-validated** — one of the stronger backtests (high Sharpe /
profit factor), though on a **small sample (~13 trades)** and modelled premiums, so
still an optimistic ceiling. Optimizer found **mom_thr = 0.15** holds up
out-of-sample (OOS profit factor ~2.87) while the tighter 0.25 setting overfits.

**Watch-outs.** Very-high risk profile; the W3 gamma trades are near-lottery. Small
backtest sample.

---

### 6. BB_EXPIRY_SCALPER_v1 — Bollinger-Band Expiry Scalper  ·  _live · ₹10,000_

**In one line.** On expiry day, uses Bollinger Bands on the index to time CE/PE
entries — either a band breakout or a squeeze-then-expansion.

**The bet.** Bollinger Bands (20-period, 2σ) mark when price stretches or coils.
A confirmed breakout or a squeeze release precedes a directional pop.

**Trades.** NIFTY / SENSEX expiry-day CE/PE.

**How it enters.**
- *Mode A (breakout):* spot breaks the upper/lower band and stays out for 2+ bars
  → buy CE / PE.
- *Mode B (squeeze):* bandwidth < 0.5% for 5+ bars, then expands → buy ATM CE or PE,
  requires score ≥ 70.
- *Mode C (mean reversion):* disabled — gamma risk too high on expiry.
Non-expiry days: runs in **shadow** (full BB analysis, no trades).

**How it exits.** 2.5–3× target or hard stop at 35–40% of entry premium; force
close **15:15**.

**Sizing & capital.** ₹10,000 budget. Daily stop ₹25,000.

**Status today.** Live-eligible, paper by default. **Can place real orders** (a
prior live-order signature bug was fixed 02-Jul-2026).

**Evidence.** **Backtest-validated** (strong Sharpe / profit factor ~2.05 on ~10
trades). An anti-overfit optimizer grid (score × squeeze × stop) exists to tune it.
Small sample caveat applies.

**Watch-outs.** Expiry-only; tiny backtest sample; the squeeze mode needs enough
warm-up bars.

---

## Group C — Mean-reversion & range strategies

These bet on price coming *back*, not continuing.

### 7. RANGE_SCALPER_v1 — Range-Day Mean Reversion  ·  _paper · ₹0_

**In one line.** On a quiet, range-bound day, buys calls at the bottom of the range
and puts at the top, expecting a bounce back.

**The bet.** On low-volatility days the index oscillates inside a tight band. Fade
the edges.

**Trades.** NIFTY / SENSEX, ATM options. Not active on expiry days.

**How it enters.** A **three-phase gate** must pass first:
- *Forming (09:15–09:45):* collect 30 min of range; it must be ≤ 0.20% of the open.
- *Validating (09:45–10:00):* 15 min with no breakout confirms the range is real.
- *Trading (10:00–13:00):* buy CE within 0.05% of the range low, PE within 0.05% of
  the range high.
Skips entirely if VIX > 16, Dow moved > ±0.30%, or Gift Nifty is outside ±40 pts.

**How it exits.** +40% target or −25% stop or 13:00 hard close, or if the range
breaks (2 consecutive bars outside → close all, stop for the day).

**Sizing & capital.** Budget ₹10,000–60,000. Daily stop ₹20,000. Capital ₹0.

**Status today.** Paper. **Can place real orders** (has a live engine) but is not
funded.

**Evidence.** No meaningful backtest yet. Closure grading shows it stands aside
correctly on non-range days.

**Watch-outs.** Only works on genuinely range-bound days; the validation gate makes
it rare, which is by design.

---

### 8. GAP_FADE_v1 — Opening-Gap Reversion  ·  _paper · ₹10,000_

**In one line.** When the market opens with a gap that then reverses in the first
half hour, it fades the gap with OTM options.

**The bet.** Many gap-opens are emotional and revert. Bet on the reversal.

**Trades.** NIFTY / BANKNIFTY, OTM options opposite the gap.

**How it enters.** Gap > 0.5% + a 5-min reversal candle + RSI divergence. Only if
VIX < 15 and premiums are reasonable.

**How it exits.** 15% target or a 30-minute window close.

**Sizing & capital.** Budget ₹5,000–10,000. Daily stop ₹2,000 (conservative).

**Status today.** **Analysis-only.** Wired to `ShadowMonitor` — scores and logs, but
**no order path**. Cannot trade even on paper.

**Evidence.** Its GTI/backtest numbers look attractive but are **shadow-only**, so
"backtest ≠ live-tradeable." Do not read the backtest as deployable.

**Watch-outs.** Needs a real engine; and the veto/zone research found the expiry
scalpers should *not* borrow its logic.

---

### 9. VIX_SELLER_v1 — Sell Volatility on Spikes  ·  _paper · ₹0_

**In one line.** The only *selling* strategy — when fear (VIX) spikes but the index
stays range-bound, it sells option premium and profits as volatility subsides.

**The bet.** After a volatility spike, implied volatility usually "crushes" back
down. Selling options harvests that decay.

**Trades.** NIFTY, ATM strangle / iron condor (sold).

**How it enters.** Only when **India VIX > 22** and the index is inside the prior
day's range.

**How it exits.** 30% premium decay (profit) or a 2-day max hold.

**Sizing & capital.** Budget ₹50,000–100,000 — **much higher**, because selling
options needs margin, not just premium. Daily stop ₹15,000. Capital ₹0.

**Status today.** **Analysis-only** (`ShadowMonitor`, no order path) — and explicitly
kept **paper** by policy. Selling is higher-risk (losses aren't capped at premium),
so it's not recommended until ₹5L+ is allocated.

**Evidence.** Not validated; small/negative backtest sample.

**Watch-outs.** Undefined-risk profile (short options) — the most dangerous archetype
here. Stays paper deliberately.

---

## Group D — Event & tail strategies

Rare-fire strategies that sit quiet most days.

### 10. BLACK_SWAN_v1 — Extreme-Move Momentum Lottery  ·  _live · ₹20,000_

**In one line.** Sits idle until the index makes a violent move, then buys one cheap
OTM option in that direction hoping the move accelerates.

**The bet.** Big gaps and intraday panics tend to extend. A single cheap OTM option
can multiply many times if it does.

**Trades.** NIFTY only, 1-strike OTM weekly CE or PE. **One trade per day, max.**

**How it enters (any trading day).**
- Gap ≥ 1.5% from previous close, **or**
- Intraday move ≥ 2.0% from the 09:15 open (threshold drops to 1.2% when VIX ≥ 22).
Entry window 09:15–13:30.

**How it exits.** 4× target, or 40% stop, or a **90-minute time stop**, or hard
close 15:20.

**Sizing & capital.** Fixed ₹20,000 budget. Daily stop ₹20,000 (= one full budget).

**Status today.** Live-eligible, paper by default. **Can place real orders.**

**Evidence.** **Backtest-validated** (Sharpe ~1.84 per settled notes) — a real, if
lumpy, edge that depends on rare big wins.

**Watch-outs.** It is **not really "crash insurance"** — mechanically it's a
**2%-breakout continuation lottery**. On a calm day a 2% move that then reverts hands
it a full −40% stop (e.g. 03-Jul: a single −₹8,058 loss, correctly stopped). Expect
frequent −40% stops punctuated by rare large wins; judge it over *many* days against
its expectancy, not on one trade. One −40% stop = ~40% of the ₹20k budget, the
biggest single-line risk in the book.

---

### 11. PASHUPATASTRA_v1 — The Seller-Hunter  ·  _paper · live-blocked · ₹0_

**In one line.** A patient, rare-release strategy that buys only when the option
chain shows option *sellers* being forced to capitulate — "study time ≫ trade time."

**The bet.** The people who *sell* index options are usually right — until they're
trapped. When a big open-interest "wall" breaks on volume and the sellers flip from
*defending* (adding OI) to *covering* (capitulating), with thin OI beyond (an "air
pocket"), price can rip. Buy that rare moment asymmetrically.

**Trades.** NIFTY / SENSEX options. **Most weeks: zero trades.**

**How it enters.** A strict two-stage gate:
- *Global gate ("fuel"):* an OI wall ≥ 1.5× median, OR IV percentile < 25, OR a BB
  squeeze — and it must **not** be a scheduled IV-crush, a choppy tape, or the
  expiry theta-deathzone.
- *Flare (trigger):* the wall breaks by 0.05% on ≥ 1.5× volume, its change-in-OI
  flips from adding to covering, and there's an air pocket beyond.
Four mutually-exclusive setups (A OI-wall-break gamma squeeze 0DTE, B coiled-vega
squeeze, C trend-trap runner, D event/gap). Max-Pain and PCR are **context only**,
never triggers ("folklore, not signals").

**How it exits.** Scale-out ladder: sell 40% at +3×, 30% at +6×, let a 30% runner
ride trailed 40% off the peak (or exit if OI re-adds). 0DTE legs time-stopped at
~15 min if no follow-through; hard close 15:10 (NIFTY) / 15:15 (SENSEX). **Never
averages down.**

**Sizing & capital.** Budget ₹10,000–30,000 per "astra charge" (premium fully at
risk). Daily stop ₹10,000. Capital ₹0.

**Status today.** **Live-blocked** by design — runs paper/observe only. Not armable.

**Evidence.** Standalone study, backtest profit factor ~1.84 on ~358 modelled
trades (heavily caveated). The research also surfaced the **stale-constants** issue
(NIFTY lot 65 not 75; NIFTY weekly expiry now Tuesday) that triggered a
platform-wide audit.

**Watch-outs.** Complex, rare, and deliberately never live yet. Its value right now
is the *seller's-eye-view* it gives the rest of the platform.

---

## Group E — Other markets

### 12. INRUSD_v1 — USDINR Futures Trend Scalper  ·  _live · ₹5,00,000_

**In one line.** Trades USDINR currency futures (not options) on a blend of global
macro cues and intraday technicals.

**The bet.** The rupee's direction is driven by global dollar strength, crude, US
yields and risk sentiment; combine that macro bias with a technical trigger.

**Trades.** USDINR monthly futures, NSE currency segment. Hours 09:00–17:00 IST.

**How it enters.** A pre-session bias at 08:45 from DXY (35%), crude (20%), US
10-year yield (20%), EUR/USD inverse (15%), VIX (10%). Only trades when bias ≥ +20
(long) or ≤ −20 (short). Intraday trigger: EMA 9/21 crossover + RSI > / < 50 + MACD
histogram direction + price vs EMA-50 — needs **3 of 4** conditions.

**How it exits.** 1.5× ATR target, 1.0× ATR stop, or 16:45 hard close. Max 3
trades/day.

**Sizing & capital.** Budget ₹3,000–15,000. Daily stop ₹5,000. Allocated ₹5,00,000
(the single largest slot).

**Status today.** Registry says live; **cleared for auto-live** (behind arm +
confirm) alongside ATM_PULSE. **⚠️ However**, a prior code audit flagged that
INRUSD's live *order routing* needed verification (it evaluated signals without a
confirmed order path). **Verify the order path end-to-end before any real trade.**

**Evidence.** Largest backtest sample here (~9,200 modelled trades, profit factor
~1.23, Sharpe ~1.99) — but currency-futures modelling caveats apply.

**Watch-outs.** Confirm the order path; it's a different segment (currency) with its
own margin and hours.

---

## Group F — The platform & the ancestor

### 13. BRAHMASTRA_v1 — Multi-Market Intelligence Platform  ·  _paused · ₹10,000_

**In one line.** Not one strategy but a whole intelligence engine that reads many
markets and timeframes and acts only on very high-conviction setups.

**The bet.** Confluence across dozens of indicators, multiple timeframes, options
positioning, and a forward-looking narrator finds rare, high-quality trades a single
signal would miss.

**Trades.** NIFTY / SENSEX (whichever offers the best setup), options.

**How it works.** A 25-indicator engine (EMA/RSI/MACD/ADX/Ichimoku/Supertrend/BB/
VWAP/StochRSI + candlestick patterns) across 1m/5m/15m/1h/1D/1W, a **scenario
engine** (Bull-5m, Bear-5m, Bull-confirm), a **Layer-5 options** read (PCR/IV/OI/
max-pain), a forward **Narrator**, and a **Human-Gate** approval mode. Entry requires
a 13-gate confluence *and* a scenario reaching **CONFIRMED (85%)** *and* Layer-5
options confirmation.

**How it exits.** Scenario-driven targets/SL; MIS auto-square 15:20.

**Sizing & capital.** Budget ₹10,000–50,000. Daily stop ₹5,000.

**Status today.** **Paused** — deliberately. Two decided design points: the 2-bull /
1-bear scenario asymmetry is a **known, accepted** choice (revisit after observing
trending days), and the **85% CONFIRMED threshold stays** (selective by intent, not
to be lowered for more trades). It stays paused until its **full order path is
verified** by the signature sweep; only then may it be considered for un-pausing.

**Evidence.** Has its own 16-year structural backtest (2008–2024). Live-run backtest
here shows near-break-even on huge sample (PF ~1.06 on ~4,472 trades) — i.e. very
selective, thin per-trade edge.

**Watch-outs.** The most complex component; keep it away from live money until the
order path is proven.

---

### 14. SRAL_v1 — The Original 5-min Strategy  ·  _archived · ₹0_

**In one line.** The first strategy ever built here — fixed-quantity, 5-minute
signals. **Retired; must not run.**

**The bet (historical).** Pre-market score + 5-min EMA/RSI/VWAP, fixed 347 Nifty
lots per trade, 27.5% target.

**Why it's archived.** Superseded by RAMS_v1, which adds 1-min 3-layer confluence,
budget-based scaling, and continuous re-entry. SRAL used a **fixed huge quantity**
(≈₹26,000/trade) with no scaling — too blunt and too risky. Kept only for reference
and benchmarking.

**Status today.** **Archived + live-blocked, capital 0.** Never armable, never live.

---

## Cross-cutting truths worth remembering

- **Three strategies cannot place any order as wired** — GAP_FADE, TREND_RIDER,
  VIX_SELLER all run on the shared `ShadowMonitor` (analysis-only). Their backtests,
  however good-looking, are **not deployable** until each gets a real engine.
- **Two strategies are data-gated** — ATM_PULSE needs a live OI feed to clear its
  score, and INRUSD needs its order path verified. Until then, a ₹0-trade day from
  them is infrastructure, not strategy.
- **Backtests are a ceiling, not a promise.** Modelled premiums, zero costs, perfect
  fills, and (for most) tiny samples. The most trustworthy evidence here is RAMS's
  walk-forward (MODERATE) and live-paper R:R — and even those are paper.
- **The whole book is momentum-heavy.** Most strategies are option *buyers* betting
  on continuation or a pop. That means most days are many small losses waiting for a
  few big wins; the daily *net* loss limits are the real risk backstop.
- **Safety is structural.** Auto-start brings strategies up in **paper**. A real
  order needs a per-session arm plus a typed confirmation. No strategy is ever
  silently deactivated, and any live block/fallback is visible in the dashboard.

## Glossary

- **CE / PE** — Call / Put option. Buy CE to profit from a rise, PE from a fall.
- **ATM / OTM** — At-the-money (strike ≈ spot) / Out-of-the-money (strike beyond
  spot; cheaper, riskier, bigger % swings).
- **Premium** — the price of the option; for a buyer, the maximum loss.
- **OI (Open Interest)** — how many option contracts are live at a strike; reveals
  where big players are positioned.
- **VWAP** — volume-weighted average price; an intraday "fair value" line.
- **ORB** — Opening Range Breakout; the high/low of the first N minutes.
- **RSI / EMA / MACD / ATR / Bollinger Bands** — standard technical indicators for
  momentum, trend, and volatility.
- **VIX** — India's volatility ("fear") index; high VIX = big expected swings.
- **Profit factor** — gross profit ÷ gross loss (> 1 = profitable).
- **R:R** — reward-to-risk; average win ÷ average loss.
- **Expectancy** — average P&L per trade over many trades.
- **Walk-forward (WFV)** — testing on rolling out-of-sample windows; the honest way
  to check a strategy isn't just curve-fit to the past.
- **MIS** — an intraday product type; positions auto-square the same day.
