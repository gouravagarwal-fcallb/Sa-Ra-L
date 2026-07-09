# Sa-Ra-L Dashboard — UI Guide (what we present, where, and why)

_A screen-by-screen map of the whole interface, written so a first-time viewer can
understand every number on the page — and a prioritized list of UX fixes to make it
legacy-grade. Audited 2026-07-09 against the live frontend (`frontend/brahmastra/src`)._

> **The dashboard is one web app** (served by the unified server on
> `http://localhost:8000`). Every strategy — including BRAHMASTRA — runs inside this
> one app; you never open a separate window per strategy.

---

## 0. Read this first — which UI is actually live

`index.js` mounts **`UnifiedApp`** — the light-themed **"The Wealth Fortress · Sa-Ra-L"**
shell. There is a second, older shell (`App.js`, the dark **"⚡ BRAHMASTRA v1"** screen)
plus ~11 components that **only it uses** — they are **dead code, shown to nobody**:
`Header`, `ConfluenceBar`, `ControlPanel`, `EquityChart`, `IndicatorPanel`,
`MultiTFPanel`, `NarratorPanel`, `OptionsPanel`, `PendingSignalPanel`, `PreMarketPanel`,
`ScenarioPanel`.

**Why this matters:** several rich panels (full indicator dump, options intelligence,
scenario engine, human-approval cards) exist in the code but **never appear** on the
running app. This is the #1 cleanup: either delete the legacy shell or port its best
panels into the live pages. Everything documented in Sections 1–3 below is what a user
**actually sees**; the dead components are listed in Section 4 so you know they exist.

**Colour language (live shell):** green = good/positive, red = bad/negative/real-money,
amber = caution, blue = paper/action, cyan = live-market/section headers, purple =
charts, slate-grey = neutral/"—".

---

## 1. The shell — always on screen

### 1.1 Top header bar
| Item | What it shows |
|---|---|
| **The Wealth Fortress · Sa-Ra-L** | Brand (gold gradient). |
| **Live clock** | IST clock to the millisecond (cyan). |
| **Nav** (11 buttons) | Pre-Market · Strategies · Backtests · Activity · Closure Report · Scoreboard · Portfolio Risk · Bots · Daily Analysis · Readiness · About. Active page = light-blue pill. Default landing = **Strategies**. |
| **⏹ STOP ALL** | Red button, top-right. Confirms, then halts **every** running strategy (`/api/control stop`). Shows "STOPPING…", then an acknowledged banner. The emergency brake. |

### 1.2 Market ticker (under the header)
Live `NIFTY · SENSEX · VIX` — last price, ▲/▼ change, %, plus a feed-source tag:
**`live · Kite`** / `delayed · Yahoo` / `no feed`. Refreshes every 5s.

### 1.3 Banner stack (only when relevant)
- **Amber "server older than page"** — the running server is behind this browser tab; restart it.
- **Amber "started after 09:15"** — launched mid-session; opening-range strategies can't build their morning range.
- **Solid red "● LIVE — real-money orders are active"** — appears whenever any strategy runs in `live` mode. Your at-a-glance real-money warning.
- **Green/red STOP-ALL result** and **red data-error** banners.

---

## 2. The pages (in nav order)

### 2.1 Pre-Market — the morning go/no-go read
Answers: *"Before the open, what's the net bias, why, what could derail it, and which strategies fit today?"* Loads once; the **frozen** overnight→open snapshot doesn't move intraday (a "❄ frozen" note explains this) — only the two live cards below update.
- **Preflight (GO / NO-GO)** — a one-glance safety verdict + per-check ✓/✗ (trading day, session timing, market feed, Kite login, per-strategy readiness), blockers, cautions, and per-strategy `cfg/bt/bf` chips (config / backtest / backfill).
- **Pre-Market Conclusion** — direction (BULLISH/BEARISH/NEUTRAL) + score/100 + conviction, an actionable "➜ do this" line, rationale bullets, cautions, and **"strategies that fit this scenario"** (each with a fit tier: BEST FIT / SUITED / NEUTRAL / LESS SUITED / NOT TODAY).
- **Live Market (cyan)** — real-time NIFTY/SENSEX/VIX/PCR/Max-Pain, with the feed source. Explicitly separate from the frozen read.
- **Today's Bias** — a −100…+100 gauge and the direction-engine breakdown (Dow / Gift Nifty / VIX / Sensex).
- **Global Markets** — table of overnight cues (Dow, etc.) + a "RISK-ON / RISK-OFF / MIXED" net verdict.
- **India Internals** — VIX, PCR, Max-Pain, FII net (₹ cr). Hover any label for its meaning.
- **Score Breakdown** — each factor's signed contribution; click one to see the math (input → rule → points).
- **High-Risk Events** and **Market News** (News-Desk impact reads + headlines).

### 2.2 Strategies — the fleet control board
Answers: *"Which strategies are running, how ready/fit is each, and which do I start / stop / take live?"* Cards, ordered best-fit-first once pre-market loads.
- **Status guide** (collapsible) explains every lifecycle status: `live` (cleared for real orders, still needs arm+confirm) · `paper` (live data, simulated fills) · `testing` · `paused` · `archived` · `planned`.
- **Each card:** name + full name · status pill · fit-tier pill · readiness dot (READY/PARTIAL/NOT_READY) · instruments · a runtime status label · **four readiness lights** (backtest / backfill / ticks / config) · an editable **capital** field · running state + **P&L** · and **Paper / Stop / Live** buttons. The **Live** button opens the arm→type-phrase guard.

### 2.3 Strategy Detail — the deep-dive cockpit
Opened by clicking a card. Anchored sections: **Charts · Forward Impact · Summary · Trades · Logs.**
- **Multi-Timeframe Charts + Bollinger Bands** — 7 charts (1m/3m/5m/15m/1h/1d/1w), each with BB(20,2σ) and an observe-only GTI demand/supply zone overlay (toggle). Green = demand/support, red = supply/resistance; solid = fresh, dashed = tested.
- **Forward Impact · next 15–30 min** — per instrument: an alert tier (STRIKE/ARMED/WATCH/CALM), direction, a **conviction** chip (building/steady/fading — trajectory, *not* price direction), confidence/100, structure, vol regime, key levels (target/SL), and a risk line.
- **Session summary** — State · Mode · **Session P&L** · Trades · Wins.
- **Trades** — OPEN / CLOSED tabs with a reconciled Total P&L (realised + open), per-row entry/LTP/SL/target and P&L.
- **Log Stream** — filterable (TICK/BAR/ANALYSE/TRADE/SCENARIO/SYSTEM/ERROR), sticky auto-scroll with "Jump to now".

### 2.4 Backtests — historical proof
Answers: *"How did each strategy's backtest look, and can I re-run it now?"*
- **Net Backtest** — one apples-to-apples run of all strategies on the same Kite history: portfolio net P&L, total trades, avg P&L/trade, peak capital deployed, a per-strategy table (trades / P&L / win% / Sharpe / max-DD / per-trade capital / period), a capital-model explainer, and **caveats** (short-window / model / 0-trade flags).
- **Per-strategy summaries** — each strategy's own latest run, with a **Source** badge (`summary.json` / `csv` / `none`) and a quality badge (short-window / model / 0-trade). Click a name → the full **Backtest Report** (8 headline tiles — P&L, trades, win rate, profit factor, max drawdown, Sharpe, avg win, avg loss — an equity-curve image, and breakdowns by exit reason / instrument / window). **▶ Run** executes a backtest on demand.

### 2.5 Activity — everything, one live feed
Every log line / signal / trade-call across all strategies, tagged by strategy, updating every 4s. Filter chips (ALL/TRADE/ANALYSIS/SIGNAL/CTRL/ERROR) + a "Trade calls only" toggle. Sticky scroll with "Jump to now". Click a strategy name to open its detail.

### 2.6 Closure Report — the EOD accountability audit
Answers: *"At end of day, what did each strategy analyse, why did it (not) trade, and was standing aside justified?"*
- **Executive summary** — strategies reported · analysis cycles · trades (W/L) · win rate · **Net P&L** · data incidents.
- **Regime ribbon** + a **"Commit trust (EOD)"** button (persists the day's trust scores).
- **Why no trades / benchmark** — was there an edge to capture (buy-hold / range / ORB / trend per index).
- **Over-filtering diagnostics** — each rejection reason graded against the actual index path (justified vs unjustified %).
- **Per-strategy accountability** — verdict, usefulness score, analysis/no-trade counts, trades, **P&L**, and the new **stats line** (win-rate · R:R · profit factor · avg win/loss · expectancy · best/worst). Expand a row for no-trade reasons, "nearest miss", grading, and exit breakdown.
- **Top lessons**, plus MD/CSV/JSON export and a date picker.

### 2.7 Scoreboard — trust over time
Read-only view of what the EOD commit persisted, over a rolling window.
- **Book P&L vs Index** chart per committed session.
- **Rolling review** — book net P&L · avg win-rate · days the book beat the index · filter trends.
- **Strategy Trust (EWMA)** table — trust score, Δ, a trend sparkline, the advisory capital-attention **weight** (0–1, never arms live), classification, and session count.

### 2.8 Portfolio Risk — the book's live exposure (observe-only)
Answers: *"Across all open option positions right now, what's my net Greek exposure, worst-case loss, and stress/VaR?"* Places no orders.
- **Headline cards** — Premium at risk (max loss if all longs expire worthless) · 1-day VaR (95%) · directional lean · Δ per +1% index · open positions.
- **Net Greeks** — Delta / Gamma / Vega (per 1% vol) / Theta (per day).
- **Stress test** — full option reprice at each index shock (captures gamma).
- **Breakdowns** by underlying and by strategy, and an **open-legs** table (per leg: qty, premium, premium-at-risk, Δ, Γ, Θ). Each metric has a hover definition; assumptions are always shown.

### 2.9 Bots — Telegram ops
Read-only health of the two bots (never mixed): **Trade Signals** (outbound publishing — delivered/pending/failed) and **News Desk** (inbound news → impact analysis, never trades). Plus the advisory **strategy context** the News Desk has applied, the outbound publication log, and the inbound news-impact analyses. Includes a collapsible beginner "how to use" guide.

### 2.10 Daily Analysis — the daily rollup
One consolidated view of today: the pre-market bias (bias / score / VIX), each running strategy's latest signal + P&L, and a table of every trade so far (time / strategy / event / instrument / type / strike / price / P&L).

### 2.11 Readiness — the QA board
Answers: *"Is each strategy properly validated?"* A table of ✓/✗/– for Backtest · Backfill · Live ticks · Config, plus an **Overall** verdict. Also hosts the **Broker live-test** (place 1 real share — the arm→type-phrase→single-use-token real-money connectivity check).

### 2.12 About — the manifesto
Static page: who we are, vision (₹50L → ₹5cr), mission (prove before deploying · protect capital · deliberate real-money actions · stay honest · one source of truth), and values. No live data.

---

## 3. The real-money guards (how "go live" works)
Three surfaces, all with the same discipline — **you can never place a real order by accident**:
1. **Strategy "Live" button** → `LiveGuardModal`: auto-arms, shows a capital cap + a 60s token, requires typing the exact phrase `GO LIVE <NAME>`, then "PLACE REAL ORDERS".
2. **Readiness "Broker live-test"** → same arm→type-`BUY 1 TATAPOWER`→single-use-token flow for one real share.
3. **STOP ALL** halts everything instantly.
`auto_live_orders` is OFF by default; a paper strategy can never place a real order.

---

## 4. Dead code — panels that exist but nobody sees
These live only in the un-mounted legacy `App.js` shell. Rich, but invisible on the running app. **Recommend: delete the legacy shell, or port the starred ones into the live pages.**
- ★ `IndicatorPanel` — ~30 raw indicators per instrument (the live app shows charts only, so users lost these numbers).
- ★ `OptionsPanel` — PCR / max-pain / IV-percentile / OI walls per instrument.
- ★ `ScenarioPanel` — active trade scenarios with state/confidence/levels/P&L.
- ★ `PendingSignalPanel` — human-approval cards **with a real live countdown** (ironically the live guard modals lack one — see fixes).
- `Header`, `ConfluenceBar`, `ControlPanel`, `EquityChart`, `MultiTFPanel`, `NarratorPanel`, `PreMarketPanel` — all duplicated-and-diverged versions of live features.

---

## 5. UX fixes — prioritized backlog to make this legacy-grade

### P0 — correctness / clarity (do first)
1. **Kill the dead legacy shell** (`App.js` + its 11 components) or port `IndicatorPanel`/`OptionsPanel`/`ScenarioPanel` into the live pages. It's the biggest source of drift (two design systems, three different VIX colour scales, duplicated concepts).
2. **P&L realness is invisible.** The Strategies grid, Strategy Detail, and Daily Analysis all sum `real_pnl + paper_pnl` into one "₹X" — a paper strategy and a live one look identical. Label or split them ("paper P&L" vs "live P&L"). Single biggest content risk in a trading tool.
3. **Live-order tokens don't count down.** `LiveGuardModal` and the Broker live-test show a static "expires in Ns" — an expired token only reveals itself on submit failure. Port the real countdown that already exists in `PendingSignalPanel`.
4. **Two P&L numbers disagree on one screen.** Strategy Detail's "Session P&L" (engine-reported) vs the Trades panel's "Total P&L" (summed from visible rows) can differ. Reconcile or label distinctly.

### P1 — consistency
5. **One currency glyph.** `Rs.` (Backtest Report) vs `₹` (everywhere else). Standardize on `₹` with `toLocaleString('en-IN')`.
6. **One VIX regime scale.** Three different colour thresholds exist across components. Define one shared helper.
7. **Disambiguate "ARMED"** — it means three things (a fit tier, the live-arm action, and a Forward-Impact alert tier). Rename the fit tier (e.g. "ON WATCH").
8. **Page-level staleness indicator.** Every page polls (3–30s) but none shows "last updated / disconnected" (except Portfolio Risk's raw timestamp). If the backend stalls, screens silently freeze on old data. Add a shared freshness badge.
9. **Consistent error handling.** Closure / Scoreboard / Portfolio Risk blank the whole page on any transient error; Bots / Backtests swallow errors silently; Activity uses a non-blocking banner (the good pattern). Adopt the banner pattern everywhere and keep last-good data.

### P2 — first-time-viewer comprehension
10. **Empty states** on the Strategies grid and Readiness table (they render blank when the list is empty).
11. **Explain the jargon inline.** Profit factor, Sharpe, Max-DD, ORB, EWMA trust, "nearest miss", the `cfg/bt/bf` and `ST/StK/BB%B` chips rely on hover tooltips that are invisible on touch/keyboard. Add a collapsible "how to read this" block per dense page (Bots already does this well — copy it).
12. **Frozen-vs-live PCR/Max-Pain appears in 3 places** on Pre-Market. Consider one canonical value with a frozen/live toggle.
13. **Move the real-money Broker live-test off the read-only Readiness page** into a dedicated Diagnostics area (a QA board shouldn't host an order button).
14. **Nav is 11 flat items** — group into Prepare / Trade / Review, and pin STOP ALL to a fixed corner.

### P3 — polish
15. Chart panel: 7 charts in one scroll row is cramped — offer a 2–3 TF focus view. · Scoreboard chart: rupees and % share one Y-axis (add a second axis + legend). · Colour Delta/Vega by sign, not just Theta. · Daily Analysis trades table needs a day-total row and column tooltips.

---

## 6. Glossary (the terms on screen)
- **Paper / Live** — simulated fills on real data / real-money orders (real needs arm+confirm).
- **Readiness lights** — backtest (has a valid backtest) · backfill (historical data cached) · ticks (receiving live data) · config (lot size / expiry weekday audit passed).
- **Fit tier** — advisory ranking of how well a strategy suits today's scenario.
- **Alert tier (Forward Impact)** — STRIKE (fire) / ARMED / WATCH / CALM.
- **Conviction** — whether the signal score is *building/steady/fading* over time — a trajectory, NOT price direction.
- **Trust (EWMA)** — a slow rolling score (α=0.3) of how justified each strategy's decisions were; **weight** (0–1) is an advisory capital-attention multiplier that never arms live.
- **PCR** — put OI ÷ call OI. **Max-Pain** — the strike where most option buyers lose (a pin magnet). **VaR** — the loss a normal day shouldn't exceed 19 times in 20. **Premium at risk** — max loss if every long option expired worthless.
- **Greeks** — Delta (move sensitivity) · Gamma (Δ's rate of change) · Vega (per 1% IV) · Theta (per-day decay).
- **ORB** — opening-range breakout. **GTI zones** — observe-only demand/supply bands (green support / red resistance).
