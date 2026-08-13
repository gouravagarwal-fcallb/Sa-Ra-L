# Trap Trading + CMCD — Strategy Roadmap & Project Gist (v3)

*One instrument-agnostic engine on **Nifty (Tue expiry)** and **Sensex (Thu
expiry)**. **v3 shift: Macro zones + Weekly POC moved from VETOES to a SCORING
module** — the 3-minute GTI zone + trap is now the mandatory trigger, and the
higher-timeframe context **sizes the target** (scalp +25 vs ride zone-to-zone)
instead of blocking the trade.*

> **Status: PAPER · LIVE-BLOCKED · UNVALIDATED.** Expiry-only. Real backtest edge
> is robust in direction but modelled-magnitude-soft; forward-test on real quotes
> before trusting the rupee figure.

---

## 1. Why v3 (Vetoes → Scoring)

**v2 used the GTI context as vetoes** (block a trade unless Weekly POC + HTF zone +
freshness all agreed). On a thin sample this **over-gated** — the strict variant
fired **0 trades**. **v3 keeps the trigger mandatory and turns the context into a
score that sizes the target.** Frequency is preserved; ambition scales with
confluence. The old hard vetoes remain available (config, default OFF).

| | v2 (vetoes) | **v3 (scoring)** |
|---|---|---|
| 3m zone + trap | trigger | trigger (**mandatory**) |
| Weekly POC / HTF / freshness | **block the trade** | **+points → target size** |
| Effect | frequency collapses | frequency preserved, robustness up |

---

## 2. Revised Logic Hierarchy

| Rank | Layer | Role | Non-repainting basis |
|---|---|---|---|
| **1 — TRIGGER** | 3-min GTI zone + trap (Whale W/M) | **mandatory** entry gate | closed 3m bars · `confirmed_index` |
| **2 — SCORE** | Weekly POC · HTF zones · freshness · whale | **sizes the target** (0–4) | prior week / pre-signal-day / closed bars |
| **3 — DIRECTION** | Golden Line (session VWAP) | mean-reversion context | cumulative closed bars |
| **4 — PHASE** | Bollinger squeeze | Compression trigger | closed bars |
| **5 — FLOW** | Blue/Black/Yellow candles | institutional read | fixed once bar closes |
| **6 — VOLATILITY** | Yellow candle | trailing-stop trigger | fixed once bar closes |

Rank 1 *finds* the trade; Rank 2 *decides how hard to press it.*

---

## 3. The Scoring Module (0–4) — identical on Nifty & Sensex

Computed at entry (`_trade_score`), non-repainting, from the higher-timeframe
context handed in as an `HTFContext`:

| +1 if… | Meaning |
|---|---|
| **Weekly-POC bias** | price is positioned to run *toward* the prior week's POC (long at/below POC; short at/above) — riding *with* weekly volume |
| **HTF zone** | the 3m trap sits **inside a Daily/Weekly** demand (long) / supply (short) zone — a level HTF institutions defended |
| **Whale bubble** | a volume/range z-score spike at the trap (institutional-size cluster proxy) |
| **Fresh zone** | the 3m zone is untested (`tests == 0`) |

**Weekly POC** is a **TPO (time-at-price)** Point of Control — spot indices have
no volume, so it is time-based Market-Profile POC, not volume-at-price (a true
volume POC appears automatically on a futures feed). All inputs use closed bars /
the prior completed week / zones confirmed before the signal day → **no look-ahead**.

---

## 4. Exit: Scalp vs Ride (+ Yellow trailing)

```
                 entry (3m trap trigger)
                          │
             score = Σ(POC, HTF, whale, fresh)
                          │
        ┌── score < ride_score_threshold (2) ──┐
        │                                        │
     SCALP                                     RIDE
  bank +25 pts                        zone-to-zone + ratchet
 (TARGET exit)                       (captures 50–150+ pt moves)
        │                                        │
        └──────── Yellow candle in profit ───────┘
                 → tighten stop to candle extreme (YELLOW_TRAIL)
```

- **`ride_score_threshold` (default 2)** is the **upside ↔ robustness knob**:
  lower → more trades ride (higher total, more concentrated); higher → more scalps
  (lower total, more consistent).
- **Win-rate protection:** the +25 scalp is applied to **both** Nifty and Sensex
  expiry trades — banking a defined profit keeps the win rate high in the volatile
  expiry environment, and only high-confluence setups are allowed to ride.
- **`YELLOW_TRAIL`:** a Yellow (reversal/volatility) candle while in profit ratchets
  a **spot-level** stop to that candle's low (call) / high (put) — locks gains
  before the spike that often follows can reach the 35-pt premium stop.

---

## 5. Risk Management (consistent across both instruments)

- **10% capital per trade** ("Brain-Freeze" guard), capped at `max_trade_rs`,
  enforced twice (engine + `SafeBrokerGuard`).
- **35-pt hard system stop** on the option premium (protects against freak trades),
  pre-target.
- **Intraday only** — hard **square-off ~15:10 IST**, no overnight theta.
- **Circuit breakers:** daily loss lock, max trades/day, post-trade cooldown.
- **NO_PROGRESS** time-stop if +25 isn't reached within N bars.

| Instrument | Underlying | Expiry | Exch | Lot | Step |
|---|---|---|---|---|---|
| `TRAP_CMCD_v1` | NIFTY | Tuesday | NFO | 65 | 50 |
| `TRAP_CMCD_SENSEX_v1` | SENSEX | Thursday | BFO | 20 | 100 |

---

## 6. Evidence (real 3-min data, BS-priced options, full costs)

| Cut | Trades | Win% | PF | Sharpe | ex-top-3 | Read |
|---|---|---|---|---|---|---|
| **Nifty · scoring (2024–26)** | 41 | 61% | 3.47 | **7.72** | **+₹75k** | most **robust** |
| Nifty · always-ride | 39 | 59% | 4.43 | 6.19 | +₹63k | higher **total** (+₹181k) |
| Sensex · Thu (Sep25–Aug26) | 13 | 38% | 1.24 | — | *neg* | positive but **fragile** |
| Non-expiry (any gate) | — | — | ~1.1 | — | *neg* | **rejected** → expiry-only |

**Scoring lifts win-rate, Sharpe and the jackknife** (robustness) versus always-ride,
at the cost of some peak upside — because on expiry, gamma rewards riding. Magnitude
is an **optimistic upper bound** (BS pricing is weakest exactly on expiry day).

---

## 7. Non-repainting (every parameter)

| Parameter | Basis |
|---|---|
| Weekly POC (score) | **prior completed week** only |
| HTF zones (score) | daily zones **confirmed before the signal day** |
| freshness (score) | `zone.tests` on closed bars up to the signal |
| whale (score) | volume/range z-score of the closed bar |
| yellow_trail | acts on a **closed** Yellow candle's own high/low |
| 3m zones · VWAP · candles | closed bars — proven by `tests/test_non_repainting.py` (passing) |

**Dtype fix (v3):** `_classify`/`_vwap` now use `.replace(0, np.nan)` (not `pd.NA`),
which previously upcast to object dtype on a zero-range bar and broke `.rolling().mean()`.

---

## 8. Way ahead
1. **Forward paper-test** both on live expiry days (Sensex Thu, Nifty Tue) — the
   paper broker prices fills off **real option quotes**, the true test of the
   BS-soft magnitude.
2. **A/B `ride_score_threshold`** (1 / 2 / 3) on both instruments — pick total-return
   vs consistency.
3. **Refine the whale/POC proxies** with a futures (real-volume) feed where history allows.
4. **Go-live gate:** define the forward-test evidence bar (trade count, PF, max
   drawdown, win-rate floor) before any 1-lot live test behind arm+confirm.

*Educational / paper research. Not investment advice. Intraday option buying can
lose 100% of premium.*
