# PASHUPATASTRA_v1 — The Complete Picture

> *The seller-hunter.* We are index option **buyers**; our enemy is the option **seller**.
> We study the seller until we can sit flat for days and strike only when one is **trapped**
> and forced to cover — booking the 2–3× that happens most expiries, re-entering on momentum,
> and leaving a thin runner for the rare 10–20×. This file is the map to everything we built.

---

## 1. The whole thing in one diagram

```
  STUDY                 BACKTEST                MEASUREMENT LOOP            EXECUTION
  ─────                 ────────                ────────────────            ─────────
  Know the seller   →   Model the doctrine  →   Record the LIVE chain   →   Shadow engine
  (psychology,          (mechanics, costs,      + score traps + measure     (real logic,
   greeks, SWOT,         tail, frontier,         the REAL 2x-hit-rate         NO orders, P&L
   OI tells)             honest targets)         = the true edge)             journalled)
       │                     │                        │                          │
  BLUEPRINT.md         backtest_*.py +          oi_recorder.py +            pashupatastra_live.py
  V2_REENGINEERED.md   RESULTS.md + reports     trap_score.py +             pashupatastra_shadow.py
                                                oi_analyze.py
       └──────────────────── all gated on ONE fact: does the trap actually spring? ───────────┘
                              (only live recordings can answer it)
```

---

## 2. File index — what to open, in reading order

| # | File | What it is |
|---|---|---|
| 1 | **`BLUEPRINT.md`** | The deep study: seller taxonomy, theta/vega/gamma kill-mechanics, psychology, the SWOT, reading the option chain, the 10–20× windows, the original plan. *Start here.* |
| 2 | **`V2_REENGINEERED.md`** | The re-engineering: book-2× + re-entry doctrine, and the architecture I added — self-calibrating trap signal, sizing that can't blow up, throughput math to Rs.5Cr, staged deployment. |
| 3 | **`RESULTS.md`** | The full backtest narrative — incl. §3.6 (selectivity) and §3.7 (your design's honest numbers) and the win-rate/CAGR target analysis. |
| 4 | **`results/BACKTEST_REPORT.md`** | The detailed report: scorecard, trade distribution, attribution, frontier, limitations. |
| 5 | **`results/BACKTEST_SUMMARY.md`** | The one-page summary. |
| 6 | **`results/equity_curve.png`** | The picture: model equity vs the blind-buying baseline. |
| 7 | **`RECORDER.md`** | The measurement loop: how to record the live chain and read the edge table. |
| 8 | **`config.yaml`** | All knobs: conviction tier, trap threshold, book-2× ladder, re-entry, sizing. |
| 9 | **`backtest_pashupatastra.py`** | The runnable backtest (synthetic now, `--data` real CSV later). |

**Code (under `src/` and repo root):**

| File | Role |
|---|---|
| `src/brahmastra/options/trap_score.py` | Seller-Trap Score (0–100) from a chain snapshot |
| `src/brahmastra/options/oi_recorder.py` | Logs the live chain + score to disk |
| `src/brahmastra/options/oi_analyze.py` | Measures the real 2×-hit-rate (the edge table) |
| `src/brahmastra/live/pashupatastra_live.py` | The shadow/live engine (doctrine, P&L journal) |
| `record_oi.py` · `pashupatastra_shadow.py` | Runners |
| `tests/test_oi_recorder.py` · `tests/test_pashupatastra_live.py` | Self-tests (pass offline) |

---

## 3. See each piece yourself (commands)

```bash
# read the study & results
less strategies/PASHUPATASTRA_v1/BLUEPRINT.md
less strategies/PASHUPATASTRA_v1/results/BACKTEST_SUMMARY.md     # one page
open strategies/PASHUPATASTRA_v1/results/equity_curve.png        # the chart

# re-run the backtest yourself (numbers + reports + chart)
python strategies/PASHUPATASTRA_v1/backtest_pashupatastra.py --seeds 16

# prove the live stack works (offline self-tests)
python -m pytest tests/ -q

# record the LIVE chain (your machine, market hours, after `python main.py --mode login`)
python pashupatastra_shadow.py --instruments NIFTY SENSEX --interval 90

# after a few expiries — the verdict that decides everything:
python -m src.brahmastra.options.oi_analyze data/oi_recordings/NIFTY
```

---

## 4. Status — done vs gated

| Built & tested (offline) | Gated on live data (your move) |
|---|---|
| ✅ Study + SWOT + plan | ⏳ Record real NIFTY/SENSEX expiries |
| ✅ Backtest + detailed/summary reports | ⏳ Read the edge table → measured 2×-hit-rate |
| ✅ OI recorder + trap score + analyzer | ⏳ Calibrate the trap threshold |
| ✅ Shadow live engine (no orders, P&L journal) | ⏳ Micro-live → scale (only if the edge is real) |
| ✅ 62/62 tests passing | |

---

## 5. The honest headline (and the asterisk)

Backtest (synthetic model, book-2× + re-entry, conservative 2% sizing):

| Tier | trades/yr | win% | annual return on slot |
|---|---|---|---|
| Sniper | ~39 | **72%** | **+52%** |
| Assassin | ~36 | **79%** | **+57%** |

Blind buying (no signal) **loses** (−19%) — so the edge is entirely the trap signal.
**The asterisk:** the win-rate climb assumes the cleanest traps follow through more — *plausible,
unproven offline.* The recorder removes the asterisk by **measuring** it. Numbers are conditional
and illustrative, not a track record. Compounding is across years (~50%/yr → 7.6×/slot in 5y →
the Rs.5Cr portfolio goal), never the naive per-trade fantasy.

---

## 6. What happens next

1. **You:** run `pashupatastra_shadow.py` on the next ~4–6 expiries (it records *and* shadow-trades).
2. **Then:** `oi_analyze` → the measured edge table.
3. **Decide:** strong follow-through in the high score buckets → calibrate & go micro-live.
   Weak → retune the trap score, having risked nothing.

*Optional parallel builds I can add anytime: real-time trap alerts (phone ping on a live fire),
auto-calibration (edge table → config), a hands-free daily scheduler.*
