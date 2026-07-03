# Sa-Ra-L / The Wealth Fortress — working agreement

> This file is auto-loaded every session. **Read the "SETTLED DECISIONS" section
> before asking the operator anything.** These are already decided — do NOT
> re-ask them. If a summary/compaction says one of these is "pending", the
> summary is wrong; this file wins.

The operator is a **tech beginner** — always explain in layman terms, then detail.

---

## SETTLED DECISIONS — do NOT re-ask

### Operating policy (real-money safety)
- **Auto-start is ON**: all eligible strategies come up **paper-active** by default.
- **Real orders always need per-session arm + typed confirm.** Auto-start never
  means auto-live. Keep this guard. Do not remove it.
- **HELD — do NOT build without a fresh explicit go-ahead:** strategy
  orchestration / regime-gating, and hands-off `auto_live_orders`.
- No strategy may be silently deactivated. Any live block/fallback must be
  visible in the UI.

### Per-strategy live posture (already chosen)
- Auto-live real orders allowed for: **ATM_PULSE_BURST_v1**, **INRUSD_v1**
  (still behind arm+confirm).
- **VIX_SELLER** stays paper. **GAP_FADE** + **TREND_RIDER** stay paper.
- **PASHUPATASTRA** stays live-blocked. **SRAL_v1** stays live-blocked, capital 0.

### Strategy model simplification (pg16-17 — decided)
- Exactly **two modes: paper / live**. Default **paper**.
- **Delete** the "archived" concept — do not keep/block, delete those strategies.
  (Waiting only on the operator's per-strategy keep/delete marks — see PENDING.)
- **Do NOT grey out the Live button** — every strategy shows a Live button; the
  operator pushes live per their own confidence.
- Capital is settable per strategy.

### Tata Power live-test button (spec — DECIDED, built + VERIFIED LIVE)
- **NSE TATAPOWER · CNC (delivery) · MARKET · qty 1**, BUY only.
- Guard: **arm → type exact phrase `BUY 1 TATAPOWER` → single-use token**.
- Built: `KiteBroker.place_equity_order`, `POST /api/livetest/equity/arm|confirm`,
  `EquityLiveTest` component on the Readiness page.
- MARKET is placed as a marketable LIMIT (~1% through LTP) — Zerodha blocks naked
  API market orders. Outbound forced to IPv4 (`src/utils/net.py`) so the Kite
  IP-whitelist stays matched across IPv6 rotation.
- **VERIFIED 2026-07-01: real order placed (order_id 260701191288578).** The
  live-order pipe works end-to-end. Do NOT re-open unless the operator asks to
  change product/order-type/qty.

### Closure-report P&L bug (fixed 2026-07-03 — read before trusting old reports)
- Until 2026-07-03 the daily closure report **silently showed ₹0 P&L and
  "disciplined" on every day**, because `_exit_event` matched an exact whitelist
  (EXIT/SL/TARGET/CLOSE) while the engines log closes under the REASON string
  (FORCE_CLOSE/STOP_LOSS/TARGET_HIT/…). Every close was dropped from the tally.
- **Any closure report generated before this fix is P&L-blind — do NOT cite its
  ₹0 / "behaved well" verdict as Phase-C evidence.** Re-generate from the trade
  CSVs. Example: 2026-07-03 truly closed **3W/7L, −₹9,668** (RAMS chopped −₹1,610
  over 9 trades on a VIX-11.8 grind; BLACK_SWAN a single −₹8,058 stop) — not ₹0.
- Fix in `src/api/closure_report.py` (token match + pnl backstop); regression test
  `tests/test_closure_exit_classification.py`.

### Validation status (2026-07-01)
- **Phase A (dashboard validation) + Phase B (1-share live proof): PASSED.**
- Confirmed live on the operator's laptop: Kite feed, paper auto-start, charts
  (incl. 1D/1W after the Kite-daily fix), FII net (-2,557 Cr), forward-impact
  direction, trade accounting UI, and the real Tata Power order.
- Next per the agreed path: Phase C (paper-run real sessions + read closure
  reports) → Phase D (tiny live on ATM_PULSE / INRUSD, arm+confirm) → Phase E
  (two-mode cleanup + delete archived, needs operator keep/delete marks).

### Strategy tradeability / validation reality (2026-07-01 — verified from code)
- **Structurally CANNOT trade (any scenario), as wired:** GAP_FADE, TREND_RIDER,
  VIX_SELLER run as `ShadowMonitor` (analysis-only, no order path); **INRUSD** has
  no order routing at all (evaluates only). Don't put these in Phase D.
- **ATM_PULSE & RAMS are OI-dependent → NOT backtestable.** ATM_PULSE's live score
  caps at **65 without live option-chain OI** but its entry threshold is **75**
  (the OI component adds up to +25). Historical intraday OI isn't available, so any
  faithful backtest yields 0 trades. Their ONLY evidence path is **forward
  paper-testing** — use the **"nearest miss" diagnostic** (peak score vs 75, in the
  closure report) to watch how close they get. Do NOT chase a backtest for them.
- **Backtest-validated (real backtests, use for Phase D):** EXPIRY_SCALPER
  (Sharpe 5.13), BB_EXPIRY (4.53), BLACK_SWAN (1.84), NIFTY_INTRADAY (2.03),
  GAP_FADE (2.14 — but shadow-only live, so backtest ≠ live-tradeable).
- Backtest caveats always apply: MODELLED premiums, ZERO costs, perfect fills →
  optimistic upper bound.
- Backtest data cache is keyed by futures-volume state (`kitefv`) so
  `--futures-volume` re-fetches fresh; but note the volume gate passes trivially at
  volume=0, so volume was never the ATM_PULSE/RAMS blocker (OI/score is).

### Telegram bots
- **Two separate bots, never mixed.** Bot 1 "Sa-Ra-L News Desk" = **inbound**
  (operator forwards news → impact analysis, **never trades**). Bot 2 "Sa-Ra-L
  Trade Signals" = **outbound** (publishes trade calls to the channel). Different
  tokens; if tokens collide, hard-disable and warn.

### Institutional framing (endorsed)
- Observe-only **Portfolio Risk** tab (net Greeks + exposure + VaR + stress) —
  built, read-only. Factor-decomposition framing endorsed as observe-only.

### BRAHMASTRA (decided 2026-07-02)
- **Scenario asymmetry (2 BULL / 1 BEAR) is a KNOWN, ACCEPTED design for now** —
  SCN1 BULL-5m, SCN2 BEAR-5m, SCN3 BULL-confirm_tf (so bull has two confirmation
  paths, bear one). Leave as-is; **revisit after observing a few trending days**.
  Do NOT make it symmetric without a fresh go-ahead.
- **85% CONFIRMED threshold stays** — it's demanding but reachable (needs confluence
  ~+50-70). Operator is fine with it being selective; do NOT lower it for more trades.
- **Stays registry-`paused` (not live-eligible) until its full order path is verified**
  by the signature-mismatch sweep. Only then may it be considered for un-pausing.

### GTI demand/supply zones — RESEARCH COMPLETE (2026-07-02). Do NOT re-litigate.
- Code lives in `src/research/gti/` (zones/backtest/fetcher/validate/confluence_ab).
  All backtests fixed for look-ahead (survivorship top-N truncation, entry-bar
  stop-skip, arm-before-departure) — earlier "90%+ win" numbers were those bugs.
- **Verdict on standalone edge:** real but THIN. Cost-viable ONLY at **15-minute**
  (confirmed NIFTY + SENSEX, ~+0.35R, survives ~2-4 pts cost). 3/5/10-min die at
  costs; 1-hour has no trades. **Too low-frequency (~8-9 trades/yr) to run solo.**
- **Zone STRENGTH score does NOT predict edge** (flat/inverted buckets everywhere).
  Use FRESHNESS, never strength, as the quality cue.
- **As a confluence filter on existing strategies (A/B tested, point-in-time):**
  - **NIFTY_INTRADAY: opposing-zone VETO works** — robust across 0.6-1.2% bands
    (vetoed trades consistently net losers; +3% to +42% total). The ONE validated,
    tradeable result. Marginal strategy though (PF 1.06→1.09), so ROI is modest.
    NOT yet wired live (would be per-strategy, behind a flag — HELD).
  - **Expiry scalpers (EXPIRY_SCALPER, BB_EXPIRY): do NOT apply the veto** — it
    removes their winners. Their best trades cluster near zones, but the clean
    aligned-selector sample is too thin (7-8 trades) to size on.
  - **No blanket/universal zone filter** — the sign flips by strategy archetype
    (momentum-buyer vs mean-reversion).
- **BUILT + live:** observe-only 15m zone overlay on the dashboard charts
  (`GET /api/market/{inst}/zones`, `gti_zones_live.py`, MultiTFChartPanel) — green
  demand / red supply bands, read-only, no order path. Keep as situational-awareness.

---

## HARD CONSTRAINTS
- Branch: **`claude/market-strategies-overview-83wczv`** only. Never push elsewhere
  without explicit permission.
- Repo scope: **`gouravagarwal-fcallb/sa-ra-l`** only.
- Never commit gitignored secrets: `settings.local.yaml`, `capital_overrides.json`,
  `telemetry_overlay.json`, `live_policy.yaml`, `config/.kite_token`.
- Never put the model identifier in commits, PR text, or code.
- Commit footer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` then
  `Claude-Session: <session url>`.
- Sandbox has **no Kite/network** — backtests and live tests must run on the
  operator's machine. Verify logic offline; hand over exact commands.

---

## PENDING — genuinely needs the operator (OK to surface these)
1. **Keep/delete marks** on the strategy table (to action the delete-archived +
   two-mode simplification). Nothing else about that plan is open.
2. ~~Log-stream error lines~~ — RESOLVED. The `errors_030726.docx` turned out to
   be a UI request (Activity feed auto-scrolled to "now" on refresh), fixed
   2026-07-03 with sticky scroll + "Jump to now" in `ActivityPage.js`.
3. **June-2026 backtest** must be run on the operator's laptop (needs Kite):
   `python main.py --mode backtest_all --source kite --from 2026-06-01 --to 2026-06-30`
   plus BRAHMASTRA / INRUSD / PASHUPATASTRA separately.

---

## Build / verify cheatsheet
- Frontend build: `cd frontend/brahmastra && CI=false GENERATE_SOURCEMAP=false npm run build`
  (the build output IS tracked in git — commit it).
- Server import smoke test: `python -c "import src.api.server"` (run from repo root).
- Run the app: `python main.py --mode unified`.
