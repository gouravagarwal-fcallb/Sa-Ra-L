# Trap Trading + CMCD — Strategy Roadmap & Project Gist (v2)

*Nifty (Tue expiry) **and** Sensex (Thu expiry) — one instrument-agnostic engine.
Now enhanced with the four GTI institutional-context filters: Weekly POC macro-bias
veto, High-Timeframe zone confluence, Yellow-candle volatility trailing, and a
zone-freshness cap.*

> **Status: PAPER · LIVE-BLOCKED · UNVALIDATED.** Expiry-only. The core edge is
> real but modelled-magnitude-soft (BS pricing on expiry) and thin; the four new
> filters ship **config-gated and default-OFF** because each *reduces* an already
> small trade count. This document is the roadmap for turning them on, one at a
> time, behind a backtest.

---

## 1. What the system is

A reversal-only intraday **option-buying** bot that fades a failed institutional
"trap" at a demand/supply zone and rides it **zone-to-zone**, on the 3-minute
chart. It runs on **two instruments** through the same engine:

| Strategy | Underlying | Weekly expiry | Exchange | Lot | Strike step |
|---|---|---|---|---|---|
| `TRAP_CMCD_v1` | NIFTY | **Tuesday** | NFO | 65 | 50 |
| `TRAP_CMCD_SENSEX_v1` | SENSEX | **Thursday** | BFO | 20 | 100 |

Together they cover **two expiry days per week** (Nifty Tue + Sensex Thu) — the
days when cheap, gamma-rich ATM options make the reversal ride pay.

---

## 2. Revised Logic Hierarchy

Location and macro-bias now gate the signal *before* the 3-minute pattern is even
considered — the strategy moved from **frequency-based** to **location-based**.

| Rank | Layer | Role | Source | Non-repainting basis |
|---|---|---|---|---|
| **0** | **Weekly POC** (macro bias) | **VETO** — don't fight the week's dominant volume | TPO Point-of-Control of the **prior** week | prior completed week only |
| **1** | **HTF Zones** (Daily/Weekly) | **VETO** — trap must sit *inside* a higher-TF zone | `detect_zones` on daily bars | zones formed *before* the signal day |
| **2** | **3-min Volume-Profile zones** | Location of the trap (demand/supply) | `detect_zones` (freshness-gated) | closed 3m bars; `confirmed_index` |
| **3** | **Golden Line** (session VWAP) | Mean-reversion magnet / direction | session VWAP (avg-price proxy on spot) | cumulative over closed bars |
| **4** | **Bollinger squeeze** | Compression phase trigger | BB-in-Keltner | closed bars |
| **5** | **Institutional candles** | Blue/Black/Yellow flow read | body · range-expansion · close-position | fixed once the bar closes |
| **6** | **Yellow candle** | *Volatility warning* → tighten the trail | high-range rejection bar | fixed once the bar closes |

The bottom four *find* the trade; the top two (**Weekly POC + HTF Zones**) are the
**"Double Veto"** that decides whether the trade is allowed to risk capital.

---

## 3. The four GTI enhancements (all non-repainting, all opt-in)

### 3.1 Weekly POC — the "Structural Magnet" macro-bias veto
- **Feature:** the Weekly **Point of Control** (the price the most bars traded
  through) — a heavier, slower magnet than the session VWAP "Golden Line."
- **Volume honesty:** spot indices carry **no volume**, so the POC is computed as
  a **TPO (time-at-price)** POC — standard Market Profile when volume is absent —
  not a volume-at-price POC. On a volume-bearing (futures) feed it becomes a true
  volume POC unchanged.
- **Logic (veto):** skip a **long** if price is trading **> `poc_veto_atr` ATRs
  below** the Weekly POC (and a **short** if that far **above** it). You don't buy
  a 3-minute demand trap while the week's institutional volume sits far overhead.
- **Non-repainting:** uses the **prior completed week's** POC — fixed, never
  revised intra-week. Config: `weekly_poc_veto`, `poc_veto_atr` (default 2.0).

### 3.2 Multi-Timeframe Zone Confluence
- **Feature:** GTI's principle that zones are *objective reference levels across
  all timeframes.*
- **Logic (veto):** a 3-minute Whale **W** is valid **only if** its price sits
  **inside a Daily/Weekly demand zone**; a Whale **M** only inside a Daily/Weekly
  **supply** zone. This is the shift from "trade every trap" to "trade only traps
  at levels HTF institutions actually defended."
- **Non-repainting:** Daily zones are detected from daily bars and filtered to
  those **confirmed before the signal's day** (point-in-time, no look-ahead — the
  same discipline the platform's `confluence_ab` research used). Config:
  `htf_confluence`.

### 3.3 Yellow Candle → volatility trailing trigger
- **Feature:** a Yellow candle marks *unusual activity that may precede a
  volatility spike* — treat it as a **warning**, not just another reversal color.
- **Logic:** if a Yellow candle prints **while the trade is in profit**, immediately
  ratchet a **spot-level trailing stop to that candle's extreme** (its low for a
  call, its high for a put). This locks gains **before** the spike that often
  follows can reach the 35-point premium stop — directly addressing the inverted
  R:R. Exit reason: `YELLOW_TRAIL`. Config: `yellow_trail`.
- **Non-repainting:** acts only on a **closed** Yellow candle's own high/low.

### 3.4 Freshness Filter (strengthened veto)
- **Feature:** zones are best used as **filters, not standalone systems**
  (the platform's own GTI research verdict).
- **Logic (veto):** skip a signal firing into a **stale** zone — one **tested more
  than `max_zone_tests` times** (GTI value: 3) — because institutional liquidity
  there is likely exhausted. Complements (does not replace) the existing
  `require_fresh_zone`. Config: `max_zone_tests` (GTI: 3; 0 = off).

---

## 4. CMCD framework (unchanged core)

**C**ompression (squeeze) → **A**ccumulation (price parks in a zone) →
**M**anipulation (the trap: Black candles fail in a buying zone / Blue in a selling
zone) → **C**orrection (reversal back toward the Golden Line) → **D**istribution
(the ride to the opposite zone, where profit is booked).

**Entry — Whale 'W' (BUY Call) / 'M' (BUY Put):** ≥ *N* trapped candles that fail
to extend, then a Yellow reversal or a decisive close reclaiming the trap — **and**
the Double Veto (§3.1–3.2) clears, **and** the zone is fresh (§3.4). Clean breakouts
are rejected: this only fades traps.

---

## 5. Risk Management (with the new vetoes)

**Position sizing**
- **10% of capital per trade** (the "Brain-Freeze" guard), hard-capped at
  `max_trade_rs`. Enforced twice — in the engine and again at the broker gate
  (`SafeBrokerGuard`).

**Exits (on the option premium, in points)**
- **35-pt hard stop** (pre-target safety net).
- **+25-pt minimum** → lock breakeven, then **zone-to-zone** ride behind a
  ratcheting trail (the matured-winner mechanism).
- **`YELLOW_TRAIL`** (new): a Yellow candle in profit tightens a **spot-level**
  stop to that candle's extreme — protects gains ahead of a volatility spike.
- **`NO_PROGRESS`**: bail if +25 isn't reached within *N* bars.
- **EOD square-off 15:10 IST** — intraday only, no overnight theta.

**The veto stack (before any capital is risked)**
1. **Weekly-POC macro veto** — not fighting the week's dominant volume.
2. **HTF-zone confluence veto** — only at levels HTF institutions defended.
3. **Freshness veto** — not into liquidity-exhausted (stale) zones.
4. **Breakout veto** — reversals only, never chase.

**Day-level circuit breakers:** `daily_loss_lock_rs`, `max_trades_per_day`,
post-trade cooldown, and a daily loss halt.

> **Why default-OFF:** each veto removes trades. Stacked on the current thin sample
> (39 Nifty / 13 Sensex expiry trades) they can drive the count toward zero — the
> same over-gating that killed the (rejected) non-expiry thesis. Enable **one at a
> time**, re-backtest, keep it only if it lifts expectancy *after* costs.

---

## 6. System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  MACRO CONTEXT (new)   Weekly POC (prior week, TPO)  +  Daily/Weekly   │
│                        zones (point-in-time)  →  HTFContext            │
├──────────────────────────────────────────────────────────────────────┤
│  L1 SECURITY      vault (no hardcoded keys) · audit_log (Sys/Data/Exec)│
│  L2 DATA/INTEGRITY 3m zones · VWAP · non-repainting proof suite        │
│  L3 LOGIC ENGINE  trap_cmcd_live  (instrument-agnostic: Nifty | Sensex)│
│                   CMCD state-machine · Double-Veto · Yellow-trail      │
│  L4 BROKER GUARD  SafeBrokerGuard (kill-switch · arm · 10% cap)        │
│  L5 EXECUTION     paper (real quotes) · live = BLOCKED                 │
└──────────────────────────────────────────────────────────────────────┘
      Telegram: pre-entry narrative alerts (Bot 2, per instrument)
```

**Weekly-POC & HTF integration.** The engine receives an `HTFContext`
(`{weekly_poc, daily/weekly zones}`) computed **once per day** and consulted inside
`_detect` *before* a signal is emitted. In the **backtester** it's precomputed
point-in-time (prior-week POC; daily zones confirmed before the signal day). In the
**live engine** it's built best-effort from the daily chart and **cached per day**,
degrading to `None` (filters skip) on any error — so live never breaks.

**Instrument-agnostic engine.** `underlying: NIFTY|SENSEX` in config selects the
symbol, exchange (NFO/BFO), weekly-expiry function (Tue/Thu, historically accurate
across the 2025-09 Nifty Thu→Tue shift), lot and strike step. One codebase, one
dispatch (`strategy_type: trap_cmcd`), two instruments.

---

## 7. Non-repainting guarantee (every new parameter)

| Parameter | How it stays non-repainting |
|---|---|
| `weekly_poc_veto` / Weekly POC | **prior completed week** only — fixed, never revised intra-week |
| `htf_confluence` / Daily zones | zones **confirmed before the signal's day** (point-in-time) |
| `max_zone_tests` / freshness | `zone.tests` counted on closed bars up to the signal |
| `yellow_trail` | acts on a **closed** Yellow candle's own high/low |
| 3m zones · VWAP · candles | closed bars only — proven by `tests/test_non_repainting.py` (passing) |

A value printed for bar *t* never changes on bar *t+1*, on any timeframe.

---

## 8. Evidence & honest status

- **Nifty · Tue (2024–26):** 39 trades, 59% win, PF 4.43, **survives jackknife** —
  but BS-modelled expiry pricing makes the *magnitude* optimistic.
- **Sensex · Thu (Sep25–Aug26):** 13 trades, PF 1.24 — positive but **jackknife-
  fragile** (one trade carries it). Forward-test, don't bank on it.
- **Non-expiry:** backtested and **rejected** (flat, jackknife-negative) → both
  strategies stay **expiry-only**.
- **The four new filters:** implemented, non-repainting, unit-smoke-tested — **not
  yet backtested for impact.** Expectation: they raise precision but cut frequency;
  the open question is whether precision gains beat the frequency loss on this thin
  sample. That's the next backtest.

---

## 9. Guardrails (unchanged, non-negotiable)

Paper by default + `live_blocked`; live needs a human **arm → typed-phrase →
single-use token**. No hardcoded secrets (vault). Kill-switch blocks new entries,
never exits. Structured System/Data/Execution audit log. Backtests labelled an
*optimistic upper bound* (modelled premiums). "Whale footprint" and "Weekly POC"
are honestly labelled proxies on volume-less spot. Nothing here is investment
advice; intraday option buying can lose 100% of premium.

---

## 10. How to enable & tune the filters

Edit the strategy `config.yaml` (`trap_cmcd:` block), one filter at a time:

```yaml
max_zone_tests: 3       # freshness cap (GTI value)
htf_confluence: true    # require the trap inside a Daily/Weekly zone
weekly_poc_veto: true   # macro-bias veto
poc_veto_atr: 2.0
yellow_trail: true      # volatility trailing on Yellow candles
```

Then re-backtest that instrument (`python main.py --mode backtest --strategy
TRAP_CMCD_v1 --source kite`, or the UI "Run backtest") and compare trade count,
profit factor, and the **jackknife (ex-top-1 / ex-top-3)** figures before and
after. Keep a filter only if it lifts expectancy after costs without collapsing
the sample.

---

## 11. Glossary
- **POC / TPO POC:** Point of Control — the most-traded price; TPO = time-at-price
  (used when volume is unavailable, as on spot indices).
- **HTF:** Higher timeframe (Daily / Weekly).
- **Whale W / M:** the long / short trap-reversal patterns.
- **Golden Line:** session VWAP magnet.
- **Zone-to-zone:** riding from one volume-profile zone to the opposite one.
- **Jackknife:** re-checking P&L with the biggest winner(s) removed — a fragility test.
- **Repainting:** an indicator silently changing past values; this system avoids it.
