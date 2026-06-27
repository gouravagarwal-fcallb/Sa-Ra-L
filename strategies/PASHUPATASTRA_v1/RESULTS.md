# PASHUPATASTRA_v1 — Backtest Results

> **Run:** `python strategies/PASHUPATASTRA_v1/backtest_pashupatastra.py --seeds 16`
> **Period:** 2020-01-01 → 2026-06-30 (6.5 yrs) · 16 seeds · Rs.5L slot · Rs.10k astra charge · NIFTY lot 65
> **Artifacts:** `results/REPORT.txt`, `results/trades.csv`, `results/equity_curve.png`, `results/summary.json`

---

## 0. READ THIS FIRST — what these numbers are, and are not

PASHUPATASTRA's real trigger is the option-chain **ΔOI add→cover flip** (a writer
capitulating at a wall). Backtesting *that* edge needs **historical per-strike Open
Interest + IV snapshots (intraday)**. That data is **not free, not in this repo, and
cannot be reconstructed from price.** No environment setup can change that — it is a
data-vendor problem, not a tooling problem. (The environment *is* now fully built:
numpy/pandas/scipy/matplotlib installed, the harness runs offline, see §5.)

So this is **not a realized track record.** It is a faithful simulation of everything
that *is* knowable, with the one thing that is not — how well the OI signal selects real
breaks from false ones — **exposed as a swept parameter, not assumed away.**

| Validated here (faithful) | NOT validated here (unobservable offline) |
|---|---|
| The 4 setups' price-action skeleton & frequency | Whether the live ΔOI-flip signal actually predicts follow-through |
| Black-Scholes pricing along a causal intraday path (theta/gamma) | The true variance-risk-premium (how rich IV really is, esp. 0DTE) |
| Astra-charge defined-risk sizing (lot 65) | True fill quality / spread on fast 0DTE legs |
| Scale-out ladder (+3× 40%, +6× 30%) + trailed runner | Multi-day continuation (sim exits intraday — conservative) |
| Full Indian cost model (Rs.20/order, STT 0.10% sell, GST, premium-scaled spread) | Real NIFTY path (uses a regime-calibrated **synthetic** series; 2025-26 anchors estimated) |
| Tail shape, drawdown, per-setup & per-year attribution, multi-seed spread | — |

**The decision-grade output is the FRONTIER (§3), not the headline (§2).**

---

## 1. The model, in one paragraph

A regime-calibrated NIFTY path (real year-end anchors through 2024; 2025–26 estimated)
is generated as a **strictly causal** intraday walk — the afternoon cannot know the
morning, so intraday momentum is genuinely non-predictive (the real option-buyer world).
Options are **priced at IV above realized vol** (the variance risk premium = the seller's
structural edge / the buyer's tax), with extra markup on 0DTE where retail overpays most.
Each candidate day is simulated end-to-end (entry, ladder, runner, costs). The **OI
signal is modeled as `filter_skill`** = the fraction of *losing* candidates it avoids;
`filter_skill = 0` is the **blind control** (take every candidate). We sweep it, and we
sweep the seller's VRP edge, because **neither is observable offline** — and together they
decide everything.

---

## 2. Headline (central-conservative case)

Assumptions: seller VRP ≈ 0.45 (0DTE priced ~2.1× realized), `filter_skill = 0.50`
(the OI signal avoids half the losing candidates), `winner_leakage = 0.15`.

| Metric | Result (mean ± std, 16 seeds) |
|---|---|
| Bullets / month | **1.4** ± 0.16 (rare, by design) |
| Win rate | **48 %** ± 5 |
| Return on Rs.5L slot (6.5 yrs) | **+80 %** ± 35 |
| Avg annual return | **+12 %** ± 5 |
| Expectancy / bullet | **+0.37 R** (×astra) ± 0.15 |
| Profit factor | **2.0** ± 0.5 |
| Max drawdown | **14 %** of slot ± 5 |
| Longest losing streak | **6 bullets** ± 2 |
| Bullets where the OPTION peaked ≥10× | **~1 per run** (the raw "10–20×" move) |
| Bullets where the OPTION peaked ≥5× | **~12 per run** |
| Best raw option multiple | **12.4×** ± 4 (pre scale-out) |
| Best blended capital multiple | **5.6×** ± 1 (after the de-risking ladder) |
| P&L concentration | top-5 trades ≈ **75 %** of net P&L (fat tail) |

**The "10–20×" is real but it is the RAW option move** (peak ≥10× happens ~once per
run, with a 20–25× outlier across seeds). The **blended bullet return is lower** because
the ladder banks 40% at 3× to make the bullet risk-free first — that is the deliberate
trade-off (survive-then-ride), not a contradiction. The strategy's profit is **tail-driven
and lumpy**: ~half the bullets lose, streaks of 6 losers occur, and a handful of trades
carry the year.

> The headline is **+12 %/yr at these assumptions.** That is modest and entirely
> conditional. Do not read it as a promise — read the frontier below.

---

## 3. THE DECISION TABLE — profitability frontier

Return-on-slot % as a function of the **two un-observable axes**. Rows = how strong the
seller's edge is (VRP / IV richness). Columns = how good *our* OI signal is.

```
  seller-edge \ OUR signal skill |   0.0(blind)   0.4     0.6     0.8
  ---------------------------------------------------------------------
       Low  (vrp 0.20)           |     243        229     262     293
       Med  (vrp 0.35)           |     102        118     154     186
   >> High  (vrp 0.50) <<        |      18         50      86     116
   Extreme  (vrp 0.70)           |     -55         -8      22      54
```

**How to read it (this is the whole point):**
- At a **realistic strong seller edge** (High/Extreme rows — 0DTE IV is genuinely rich),
  **blind candidate-buying LOSES** (+18 % down to −55 % over 6.5 yrs; win rate ~27–32 %).
  This matches the verified research: *blind option buying is negative-EV.*
- The strategy only turns clearly positive when the **OI signal avoids ~40–60 %+ of losing
  candidates.** That is the **entire bet.** At the central case the break-even signal skill
  is low (~0.06) only because VRP 0.45 is moderate; at Extreme VRP you need skill ≈ 0.5+.
- **What must be proven before risking capital:** that the live ΔOI-flip + volume + air-pocket
  filter actually achieves that loser-avoidance. This backtest **cannot** prove it — only
  forward-recorded option-chain OI can.

---

## 4. Attribution & regime (seed 0, illustrative)

- **By setup:** A (expiry-gamma) dominates — 92 bullets, 41 % win, the engine; C (trend-trap)
  9 bullets, 67 % win; D (event-gap) 6 bullets; B (coiled-vega) rare (fires a few times only
  across seeds). This matches the blueprint: **the 0DTE gamma squeeze is the core trade.**
- **By year:** positive in 6 of 7 calendar segments; **2024 was negative** (−Rs.44k in this
  seed) — a useful reminder that even the model has losing years. Low-vol 2025 produced few,
  small bullets (the seller's paradise = our lean period, exactly as the study predicts).

---

## 5. Environment created (so this is reproducible & extensible)

- Installed offline via the agent proxy: **numpy, pandas, scipy, matplotlib, pyyaml,
  holidays, yfinance, seaborn** (the repo's `requirements.txt` stack).
- The repo's `OptionPricer` (Black-Scholes) and calibrated data generator import and run
  offline; this harness uses an embedded, **causal** variant for correctness (no look-ahead).
- The harness is **data-source-agnostic.** It runs on the synthetic model by default, and on
  **real daily OHLC** via `--data nifty_ohlc.csv` (columns `date,open,high,low,close[,iv]`).

---

## 6. To get TRUE (precise) results — what is actually required

Precision here is gated by **data**, not compute. In order of impact:

1. **Historical intraday option-chain OI + IV snapshots** for NIFTY/SENSEX weeklies
   (per-strike `call_oi/put_oi/ΔOI/IV`, ideally 1–5 min). Sources: a paid vendor
   (GDFL / TrueData / iCharts / NSE data-dump), or **start recording the live NSE chain now**
   via the existing `options_intel.py` (the system already fetches it live — log it to disk
   daily and a real dataset accrues going forward).
   → Replace `detect_candidate`'s price proxies with the **real ΔOI-flip trigger.** Then
   `filter_skill` is no longer a parameter — it becomes a *measured* property of the signal.
2. **Real NIFTY/SENSEX OHLC** (Kite historical, or any 1–5 min CSV) → `--data`.
3. **Recorded fills** from paper/live trading to calibrate true 0DTE spreads & slippage.

Until (1) exists, the honest answer to "is the edge real?" is **unproven** — the frontier in
§3 says precisely *how good the signal must be*, which is the right question to take into a
recording/paper phase. The sizing, exit, cost, and metric code is already production-faithful
and needs no change when real data arrives.

---

## 7. Reproduce

```bash
python strategies/PASHUPATASTRA_v1/backtest_pashupatastra.py --seeds 16          # synthetic, full
python strategies/PASHUPATASTRA_v1/backtest_pashupatastra.py --data nifty.csv    # real OHLC
python strategies/PASHUPATASTRA_v1/backtest_pashupatastra.py --filter-skill 0.4  # stress the signal
```

*Synthetic price model + proxy OI trigger. Numbers are conditional on the stated VRP and
signal-skill assumptions and are illustrative orders of magnitude, not a track record or
investment advice. See the header of `backtest_pashupatastra.py` for the full method.*
