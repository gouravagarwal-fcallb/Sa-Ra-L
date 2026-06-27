# PASHUPATASTRA — Re-Engineered (v2 architecture)

> *Co-designed.* This document folds your refined doctrine (book the 2–3× bread-and-butter,
> re-enter on momentum, the 10–20× as bonus) into a complete system, and adds the pieces I
> believe are needed to make it **deep, big, and — above all — real** rather than a backtest
> that flatters us. Everything here serves one end: **Rs.50L → Rs.5Cr**, survived and compounded.

---

## 0. The common goal, stated as math (so every design choice can be checked against it)

```
Goal:  Rs.50,00,000  ->  Rs.5,00,00,000   (10x)   in ~5 years
Path:  10 strategy slots x Rs.5L each. PASHUPATASTRA is ONE slot.
Per-slot job: compound ~45-55% / year, ACROSS years, without a ruinous drawdown.
  1.50 ^ 5 = 7.6x   ->  Rs.5L -> Rs.38L per slot ;  x10 slots (blended) -> ~Rs.5Cr.
```

Two numbers decide whether PASHUPATASTRA earns its slot: **annual return on the slot** and
**max drawdown**. The backtest (RESULTS §3.7) puts the honest, *non-fantasy* version of the
first at **~50–57%/yr at 2% risk** with **~14–18% drawdown** — *if the trap signal is real.*
That last clause is the whole game, and §2 is how we stop assuming it and start measuring it.

**Design rule we never break:** anything that raises the headline number by *assuming* a better
signal is worthless. Only things that (a) measure the real edge, or (b) protect the compounding
from ruin, count. The seller blew up by ignoring its tail; we will not return the favor.

---

## 1. The refined doctrine (your idea, locked)

```
WHAT WE DO                                   WHAT WE DON'T
- Hunt TRAPPED sellers on expiry days        - Chase only the 10-20x jackpot
- Book the 2-3x bread-and-butter EVERY       - Hold winners to zero hoping for 20x
  time the OI/seller signal is clear         - Trade dead, low-fuel expiries
- Re-see momentum -> RE-ENTER (up to 4/exp)  - Average down a loser (the seller's disease)
- Leave a thin 5% runner for the bonus tail  - Size by ego; size by the MEASURED edge
- Sit flat when there is no trapped seller   - Compound per-trade to fantasy numbers
```

Profile: **high win rate (selectivity) × frequent small wins (book 2×) × re-entry throughput ×
annual compounding × 2 instruments.** The "big" is *throughput of a high-probability small edge*,
not the size of any one trade. That is a durable way to 10×; "swing for 20× every time" is not.

---

## 2. MY CONTRIBUTION #1 — the Self-Calibrating Trap Signal (turns the assumption into a number)

Every honest result above carries one asterisk: *we assume the cleanest trap setups follow
through more often.* The single most important thing we can build is the machine that **measures
whether that is true** and feeds the measurement back into sizing. This converts PASHUPATASTRA
from "a backtest with an assumption" into "a system that knows its own edge and updates it."

### 2a. The Seller-Trap Score (0–100) — the actual alpha, made computable

Fire ONLY when score ≥ `trap_threshold` (start 75). Computed live from `options_intel.py`:

| Factor | What it measures | Pts | Source field |
|---|---|---|---|
| **Naked wall size** | strike OI ÷ median nearby OI (bigger = more trapped fuel) | 25 | `top_*_oi_strikes`, per-strike OI |
| **ΔOI flip velocity** | rate the wall flips ADD→COVER (the flare igniting) | 25 | intraday Δ of `change-in-OI` |
| **Volume confirmation** | break bar volume vs avg (real break, not a poke) | 15 | tick/bar volume |
| **Air pocket** | thin OI on the next strike beyond the wall | 10 | per-strike OI |
| **IV cheapness** | IV-percentile low = vega tailwind, no crush trap | 10 | `iv_percentile` |
| **Gamma/time factor** | later on expiry = higher gamma = sharper squeeze | 10 | time-of-day, DTE |
| **Naked-vs-sticky** | retail naked OI (squeezes) vs covered/condor (won't) | 5 | OI pattern heuristic |

This is buildable today against the live chain. It is the honest home of the edge — not Max
Pain, not PCR (both demoted to context, per the study).

### 2b. The measurement loop (the de-risker)

```
  OI RECORDER  ──>  TRAP JOURNAL  ──>  OUTCOME LABELS  ──>  ROLLING EDGE TABLE  ──>  SIZING
  (log live NSE     (every fire:       (did it hit 2x       ("trap_score>=80 ->     (size on the
   chain to disk     score, factors,    before stop/EOD?)    2x-hit-rate = 71%      MEASURED edge,
   every 1-5 min)    strikes, time)                          over last 200 fires")   never assumed)
```

After a few weeks of recorded expiries we can answer the only question that matters:
**"when trap_score ≥ X, how often does the 2× actually come?"** That measured hit-rate *replaces*
`filter_skill`. If it's 70%+, the §3.7 numbers are earned and we scale. If it's 50%, we learn
that cheaply, on paper, before risking a rupee. Either way we win — we stop guessing.

> **Build order starts here.** The recorder is ~a day of work on top of the existing
> `options_intel.py` fetch. It is the highest-leverage thing in this whole project.

---

## 3. MY CONTRIBUTION #2 — sizing that compounds without blowing up

The backtest's naive per-trade compounding hit +1352%/yr — a lie produced by fractional
compounding over hundreds of trades. Real money faces two walls the math ignored: **liquidity**
(you can't pour a growing account into Rs.5 0DTE options) and **non-stationarity** (edges decay).
The sizing engine respects both:

```
risk_per_trade = min(
    kelly_fraction(measured_edge) * 0.25,   # QUARTER-Kelly on the MEASURED edge (ruin-safe)
    target_pct * slot_equity,               # hard cap (start 2%, raise only on proven edge)
    strike_liquidity_cap                    # can't exceed what the strike can absorb cleanly
)
drawdown_throttle:  risk *= 0.5  while slot is >10% off its peak   # anti-martingale
compounding:        resize the slot ANNUALLY, not per-trade        # bounded, real
```

- **Quarter-Kelly on the *measured* edge** maximizes long-run growth while keeping risk-of-ruin
  negligible — the opposite of the seller's all-in-on-theta fragility.
- **Drawdown throttle** cuts size *into* a slump and restores it on recovery — we shrink when
  wrong, press when right. (The seller does the reverse and dies.)
- **Compound across years**, not trades: ~50%/yr → 7.6× in 5y per slot. Honest, and enough.

---

## 4. MY CONTRIBUTION #3 — the throughput engine (where "big" actually comes from)

```
  2 instruments (NIFTY-Tue, SENSEX-Thu)  x  ~50 expiry sessions each / yr   = ~100 expiries/yr
  x  up to 4 OI-confirmed re-entries per expiry (only when momentum re-fires)
  x  ~72-79% win on the 2x book (sniper/assassin tier, IF signal real)
  =  a high cadence of high-probability small wins, compounded annually
```

The leverage is **frequency of a repeatable edge**, not the size of a rare one. This is why
"book 2× and re-enter" beats "wait for 20×": ten 2× scalps a month at 75% win compounds far more
reliably than one moonshot a quarter. **Add SENSEX-Thursday and you double the expiry surface for
free** — the same engine, a second weekly trap.

---

## 5. MY CONTRIBUTION #4 — fuel gating (the discipline that funds the win rate)

The win rate only survives if we refuse the bad days. A **pre-market + 10:00 fuel scan** decides
*trade today or sit*:

```
ARM the day only if:  a large naked wall exists (trap fuel)  AND  IV not crush-rich into an event
                      AND VIX in a tradeable band  AND not a dead low-range pin day.
Otherwise: FLAT. Most low-fuel expiries are the seller's paradise — we donate nothing.
```

Selectivity isn't a setting; it's the product. The backtest shows win rate climbing 53%→79% and
drawdown *falling* purely by trading less. Doing nothing, well, is a position.

---

## 6. Risk overlay & circuit breakers (protect the compounding)

```
- Per-trade: defined risk = premium at risk (option IS the stop). No averaging down. Ever.
- Daily: stop after monthly_loss_cap/4 or 3 consecutive losers (revenge-trade lock).
- Weekly/Monthly: loss caps -> flat for the period. Misfire pause -> SHADOW a week, re-assess.
- Regime: VIX>35 or feed/OI down -> FLAT (blind = flat).
- The kill switch is sacred. A live system that can't stop itself is a liability, not an asset.
```

---

## 7. The staged path to live (no real money on an unproven assumption)

```
PHASE 0  OI RECORDER live              -> measure trap-score follow-through        [start here]
PHASE 1  SHADOW (paper) on measured    -> does live match backtest shape?
         signal, 4-8 weeks
PHASE 2  MICRO-LIVE 1 lot (Rs.10k)     -> real fills/slippage on cheap 0DTE legs
PHASE 3  SCALE to the Rs.5L slot ONLY  -> gate: measured 2x-hit-rate >= 65% AND
         if the gates pass                 paper Sharpe ok AND slippage within model
GO/NO-GO at every phase. We earn the right to the next phase; we don't assume it.
```

---

## 8. PASHUPATASTRA's role in the Rs.5Cr portfolio

| Slot | Personality | Win rate | Cadence | Role |
|---|---|---|---|---|
| RAMS_v1 | steady momentum | ~moderate | daily | base hum |
| **PASHUPATASTRA** | **trapped-seller scalper** | **high (~75%)** | **~4/expiry, re-entry** | **the compounding engine** |
| BLACK_SWAN_v1 | rare convex | low | a few/yr | crisis alpha |
| VIX_SELLER_v1 | premium seller | very high | regime | the annuity (the OTHER side) |

PASHUPATASTRA is meant to be the **reliable compounder** — high win rate, high cadence, capped
drawdown — that does the heavy lifting on the 10× while BLACK_SWAN catches the tails and
VIX_SELLER harvests the premium we usually pay. Diversified personalities, one goal.

---

## 9. Build backlog (in priority order — what I'd do next)

1. **OI recorder** (`options_intel.py` → daily chain log). *Unlocks everything; ~1 day.*
2. **Seller-Trap Score** (§2a) computed live + journaled with outcomes.
3. **`pashupatastra_live.py`** engine: fuel gate → trap score → book-2× + re-entry → sizing/throttle.
4. **Measured-edge sizing** (§3) wired to the rolling edge table.
5. **SENSEX-Thursday** added (double the expiry surface).
6. Shadow → micro-live → scale, per §7.

> The honest one-liner: **the strategy's shape is now right and it hits your targets in the model;
> the only thing between here and real money is measuring that the trap actually springs.** Build
> #1 and we replace the last assumption with a fact. Say the word and I'll start on the recorder.
