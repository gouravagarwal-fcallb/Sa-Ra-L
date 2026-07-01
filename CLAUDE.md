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

### Tata Power live-test button (spec — DECIDED, already built)
- **NSE TATAPOWER · CNC (delivery) · MARKET · qty 1**, BUY only.
- Guard: **arm → type exact phrase `BUY 1 TATAPOWER` → single-use token**.
- Built: `KiteBroker.place_equity_order`, `POST /api/livetest/equity/arm|confirm`,
  `EquityLiveTest` component on the Readiness page.
- Only re-open this if the operator asks to change product/order-type/qty.

### Telegram bots
- **Two separate bots, never mixed.** Bot 1 "Sa-Ra-L News Desk" = **inbound**
  (operator forwards news → impact analysis, **never trades**). Bot 2 "Sa-Ra-L
  Trade Signals" = **outbound** (publishes trade calls to the channel). Different
  tokens; if tokens collide, hard-disable and warn.

### Institutional framing (endorsed)
- Observe-only **Portfolio Risk** tab (net Greeks + exposure + VaR + stress) —
  built, read-only. Factor-decomposition framing endorsed as observe-only.

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
2. **Log-stream error lines** from `errors_30062026_0846.docx` (paste 3–4 lines) —
   the log pipeline itself is verified sound; need the specific error text.
3. **June-2026 backtest** must be run on the operator's laptop (needs Kite):
   `python main.py --mode backtest_all --source kite --from 2026-06-01 --to 2026-06-30`
   plus BRAHMASTRA / INRUSD / PASHUPATASTRA separately.

---

## Build / verify cheatsheet
- Frontend build: `cd frontend/brahmastra && CI=false GENERATE_SOURCEMAP=false npm run build`
  (the build output IS tracked in git — commit it).
- Server import smoke test: `python -c "import src.api.server"` (run from repo root).
- Run the app: `python main.py --mode unified`.
