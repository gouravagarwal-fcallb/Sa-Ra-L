# Strategy Analysis Decoder

> Plain-language reference for what every strategy shows on the live dashboard,
> what its analysis line means, whether it can actually place a real order, and
> where its data comes from. Built from a code-level audit (2026-07-02).
> Keep this open next to the dashboard.

## The two things to remember first

1. **Paper premiums are MODELLED.** In paper mode every option strategy prices its
   options with a Black-Scholes math model, *not* live market quotes. So a green
   "premium / P&L" number in paper is an optimistic estimate, not a real fill.
2. **"Can trade" ≠ "will trade live."** Real orders are always gated behind the
   per-session arm + typed-confirm guard. Several strategies also *cannot* place an
   order at all as wired (shadow / evaluate-only / blocked) — see the table.

## Snapshot — all 14

| Strategy | What it is | Real order path? | Live status | Data health |
|---|---|---|---|---|
| NIFTY_INTRADAY | Intraday NIFTY option-buy scalper (breakout/reversal by regime) | ✅ yes | tradeable (paper now) | spot/OHLC — healthy |
| RAMS | Slot-scheduled option-buy on 1-min momentum confluence | ✅ yes (narrow gates) | tradeable (paper now) | spot/bars — healthy; paper premiums modelled |
| ATM_PULSE_BURST | ATM-call momentum burst, needs OI + premium confirm | ✅ yes (auto-live eligible) | **blocked by data** | ⚠️ OI from NSE = bot-blocked → score capped <75 → never fires |
| BLACK_SWAN | Tail-event catcher, one trade on extreme days | ✅ yes | tradeable, rare by design | spot/VIX — healthy, no OI needed |
| RANGE_SCALPER | Mean-revert a tight morning range | ✅ yes (SHADOW on trend days) | tradeable on range days | **skips expiry days entirely** |
| EXPIRY_SCALPER | Expiry-day momentum option buy (windows) | ✅ yes | tradeable on expiry only | ⚠️ minor: shadow line has a swallowed NameError |
| BB_EXPIRY_SCALPER | Expiry-day Bollinger breakout/squeeze | ✅ yes (**was broken — fixed**) | tradeable on expiry only | live-order bug fixed 2026-07-02 |
| BRAHMASTRA | 25-indicator confluence platform | ✅ yes (own runner) | registry **paused** — not auto-started | Kite ticks + backfill |
| GAP_FADE | Fade the opening gap | ❌ shadow only | watch-only | ⚠️ config/code label mismatch |
| TREND_RIDER | Ride an intraday trend | ❌ shadow only | watch-only | central feed |
| VIX_SELLER | Sell premium when VIX high | ❌ shadow only | watch-only | ⚠️ ignores configurable threshold (hard-coded 22) |
| INRUSD | USD/INR currency scalper | ❌ evaluate-only (no CDS routing) | watch-only | yfinance `USDINR=X` (Yahoo — can be sparse) |
| PASHUPATASTRA | Trapped-option-seller squeeze | ❌ shadow journal only | triple-blocked (paper/live_blocked/capital 0) | NSE chain |
| SRAL | Original fixed-qty 5-min (superseded) | ❌ never instantiated | archived — emits nothing | — |

---

## Tradeable strategies (have a real order path)

### NIFTY_INTRADAY_v1
- **Thesis:** buys weekly ATM CE/PE and scalps intraday. TREND day → buy an opening-range breakout (confirmed by VWAP + volume). RANGE day → buy reversals off support/resistance (RSI extreme + reversal candle). Tight stop ~₹1,200, target ~₹2,750, flat by 15:10.
- **Live line:** `Scanning [RANGE] 11:42 spot=24,850 RSI=57 orb=24,910/24,780 day_pnl=Rs.+0 trades=0`
  - `[RANGE]/[TREND]` = today's regime (which scanner runs). `RSI` = 14-period momentum. `orb=` = first-15-min high/low (breakout levels). `day_pnl`/`trades` = running totals.
- **Fires when:** not loss-locked · past first 10 min · a scanner returns a setup (breakout+VWAP+volume, or S/R touch+RSI+candle) · premium ≥ ₹10 · valid size.
- **Data:** NIFTY spot + 1m/15m bars + VIX. No OI needed → not affected by the OI block.

### RAMS_v1 (runs on the generic "Sa-Ra-L Live Engine")
- **Thesis:** pre-market bias + budget from Dow/GiftNifty/VIX, then buys one ATM CE/PE in fixed windows on a 1-min momentum confluence; can flip CE↔PE; ~27.5% target; flat 15:20.
- **Live line:** `T1 09:47 BULLISH Score=+4 spot=24851 [EMA5>EMA13, RSI7 62, vol 1.6x]`
  - `T1` = a trading window (`OW#` = off-window paper sim). `BULLISH/BEARISH/NEUTRAL` + `Score=±N` = the live conviction (fires at |score| ≥ 3). `[...]` = why.
- **Fires when:** flat · inside a real slot · non-neutral direction · LTP ≥ 0.5 · valid size · VIX ≤ 22 · day not loss-stopped. (Real order only in live mode + real slot.)
- **Data:** 1-min bars, VIX, Dow, Gift Nifty. Paper premiums modelled.

### ATM_PULSE_BURST_v1  ⚠️ data-blocked
- **Thesis:** buys the ATM call on a fresh opening-range breakout confirmed by trend + option-chain OI + a premium surge; targets ~25 premium points.
- **Live line:** `NIFTY 24139 | ATM:24150 CE₹73 | CHOP | OI:UNAVAILAB | PREM:NOT_READ | Score:35 | WATCHING | TREND_NOT_BULLISH`
  - `CHOP` = tiny range, no edge. `OI:UNAVAILAB` = option-chain OI couldn't be fetched. `PREM:NOT_READ` = premium breakout not confirmed yet (normal). `Score:35` (needs ≥75). `WATCHING` = pre-arm state. trailing word = the *first failing gate*.
- **Fires when:** in window · regime BULLISH_TREND/BREAKOUT · OI ≠ TRAP_RISK · score ≥ 75 · premium CONFIRMED · ARMED for a bar.
- **Data health — the blocker:** OI comes from NSE's web API, which blocks bots (HTTP 403). With OI `UNAVAILABLE` the OI layer scores neutral, capping the total near 65 — below the 75 threshold — so **it can never arm.** Live OI *is* available from Kite's quote API; wiring that is the fix (paused pending go-ahead). Backtest is impossible (no historical intraday OI); forward paper is its only evidence path.

### BLACK_SWAN_v1
- **Thesis:** on an extreme day (big gap or sharp intraday swing) buy one OTM option in the move's direction for a ~4× payoff; one trade/day.
- **Live line:** `10:14 spot=24,100 gap=+0.30% (need 1.5%) intraday=+0.80% (need 2.0%) VIX=13.2 → watching`
  - `gap` = move vs prev close (Trigger A, ≥1.5%). `intraday` = move vs 9:15 open (Trigger B, ≥2.0%, or ≥1.2% if VIX≥22). `→ watching` = no trigger.
- **Fires when:** not yet fired today · in 09:15–13:30 · a trigger fires · premium in [20,500] · valid size.
- **Data:** spot/VIX/prev-close/day-open. No OI. Acts only on rare days — mostly just "watching".

### RANGE_SCALPER_v1
- **Thesis:** on quiet range days, buy ATM CE at the range low / ATM PE at the range high, scalping bounces.
- **Live line (phase-driven):** `TRADING 11:20 spot=24,100 ↑0.12% to R_HIGH=24,130 ↓0.08% to R_LOW=24,080 trades=1/6`
  - Phases: `FORMING` (09:15–09:45 measuring range) → `VALIDATING` (holds?) → `TRADING` → `INVALIDATED` (broke) / `SHADOW` (filtered out).
- **Fires when:** phase TRADING · within 0.05% of a boundary · premium [15,120] · size ok · under 6 trades.
- **Data health:** **skips expiry days entirely** (defers to EXPIRY_SCALPER — no analysis line those days). Forces SHADOW (watch-only) if pre-market says it's a directional day (VIX>16, |Dow|>0.30%, |GiftNifty|>40).

### EXPIRY_SCALPER_v1
- **Thesis:** expiry-day only — buy slightly-OTM options on an intraday directional push inside up to 3 windows, ride gamma for 2.5×–5×.
- **Live line:** `W1 opened ref=24,000 (day open) now=24,060 (+0.25%) need ±0.25% prem Rs.25–150 target 2.5×`
  - `W1/W2/W3` = windows. `ref` = anchor (9:15 open). `need ±X%` = momentum threshold. `prem A–B` = allowed premium band. `target N×`.
- **Fires when:** in a window (not yet fired) · move ≥ threshold · (optional score-direction agree) · premium in band · size ok.
- **Data health:** expiry-only (else SHADOW). Minor bug: the *detailed* non-expiry shadow line references undefined vars and silently fails (caught by a bare except) — only the one-shot "Not expiry — SHADOW" shows. Cosmetic, not order-affecting.

### BB_EXPIRY_SCALPER_v1  (live-order bug FIXED 2026-07-02)
- **Thesis:** expiry-day only — Bollinger Bands on spot. Mode A: buy the breakout when spot holds beyond a band. Mode B: buy the expansion after a squeeze. A 0–100 score must clear its threshold.
- **Live line:** `10:40 | spot=24,150 VIX=13.2 ATM=24150 BW=0.42% SQUEEZE SQ=5bars score=68 (need≥65)`
  - `BW` = band width % (small = pinched/squeeze). `bb_state` = NORMAL/SQUEEZE/EXPANDING/BREAKOUT. `SQ=Nbars` = consecutive squeeze bars. `score` (0–100) vs threshold (A 65 / B 70).
  - ⚠️ Note: the dashboard's generic "score" tile actually shows *trade count*, and the "direction" field shows the *BB regime word* — the real 0–100 score is only inside the signal string.
- **Fires when:** before 15:15 · past 09:30 · no open trade · not in cooldown · day loss ok · VIX ≤ 28 · BB warmed · (Mode-A BREAKOUT & score≥65) or (Mode-B EXPANDING & score≥70) · premium [2,200] · under 5 trades.
- **Bug found & fixed:** its live `Order(...)` was built with wrong field names (`qty/side/product`) that the `Order` dataclass rejects — so a live order would have thrown `TypeError` and silently *not placed*. Now constructs `Order` correctly (exchange/option_type/strike/expiry/transaction/quantity) for both entry and exit.

### BRAHMASTRA_v1
- **Thesis:** 25-indicator confluence across 6 timeframes on NIFTY+SENSEX; trades a weekly ATM option only when a Bull/Bear scenario reaches CONFIRMED and passes a 13-gate check.
- **Live line:** `NIFTY BULL setup BUILDING score=64▲ +6 entry ~15m away` and `▲ BULLISH STRONG | score=+64 | agree=72% | bull=64 bear=18`
  - `agree=%` = how many of 25 indicators agree. `bull=/bear=` = competing sub-scores. `entry ~Xm away` = narrator ETA.
- **Live status:** registry **paused** → not auto-started by the unified runner's guard; it runs via its own brahmastra/unified path where live mode enables real orders. Has a HumanGate (can require manual approval) + cooperative stop.

---

## Cannot place a real order as wired (analysis / shadow / blocked)

### GAP_FADE_v1 / TREND_RIDER_v1 / VIX_SELLER_v1 — shared `ShadowMonitor`
- **All three are hard-forced to paper and NEVER place an order** (by design — the engine's job is to watch and log while the real engines are pending). They emit a per-strategy watch line off the central market feed.
- **GAP_FADE:** watches for a ≥0.5% opening gap to fade. ⚠️ *Bug:* its config `strategy_type` is `gap_fade` but the code branches on `opening_range`, so it currently renders the **generic** "monitoring {inst} {ltp}" line, not the intended gap text.
- **TREND_RIDER:** after 09:45, watches for a new 30-min extreme + momentum. (No numeric test — watch message only.)
- **VIX_SELLER:** `ARMED` when VIX > 22 (IV-crush setup "eligible" — never ordered). ⚠️ *Minor:* the 22 threshold is hard-coded, ignoring the configurable value.

### INRUSD_v1
- **Evaluate-only** — computes a USD/INR signal but has **no order-routing path** (NSE-CDS leg not built).
- **Live line:** `USDINR 83.2140 → HOLD` (or `→ NO_TRADE`). The `→` shows the engine's action; `HOLD/NONE/—` = no signal.
- **Data:** yfinance `USDINR=X` (Yahoo) — intraday FX can be sparse/delayed, a real reliability risk. Bias needs a pre-session snapshot; if it fails, it stands aside (NEUTRAL).

### PASHUPATASTRA_v1
- **Shadow journal only** — scans the option chain, scores a 0–100 "seller-trap", and writes would-be trades to a CSV. **No broker call exists anywhere in the engine.** Also triple-blocked in the registry (paper + live_blocked + capital 0).
- **Live line:** `NIFTY chain scan · trap=42 watching` (or `⚡ARMED` at ≥75). `[pashupatastra:shadow]` = journaling mode; `interval=90s` = rescans every 90s.

### SRAL_v1
- **Archived and inert** — never instantiated, emits no analysis, cannot order. Hard-guarded off every start path. Superseded by RAMS. Dashboard shows "Archived (intentional)".

---

## Issues surfaced by this audit (ranked)

1. **BB_EXPIRY live-order construction was broken** — would `TypeError` and silently not place any live order. **FIXED 2026-07-02.**
2. **ATM_PULSE OI is bot-blocked (NSE)** → score capped <75 → never fires. Fix = wire Kite live-OI (paused, needs go-ahead).
3. **USDINR chart was showing NIFTY data** — FIXED 2026-07-02 (wrong-token fallback + Yahoo symbol).
4. **GAP_FADE label mismatch** — config `strategy_type` vs code branch key; shows the generic line, not gap-fade text.
5. **EXPIRY_SCALPER non-expiry shadow line** — swallowed NameError; only the one-shot shadow message shows (cosmetic).
6. **VIX_SELLER threshold hard-coded** to 22, ignoring config (minor; shadow-only anyway).
7. **BB_EXPIRY dashboard fields** — "score" tile shows trade count, "direction" shows BB regime (confusing labels).
8. **Dead config** in BB_EXPIRY (`max_spread_pct`, `min_vol_surge` not enforced).
</content>
