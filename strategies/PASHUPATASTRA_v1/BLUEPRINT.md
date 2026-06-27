# PASHUPATASTRA_v1 — The Seller-Hunter Blueprint

> *"The Pashupatastra is never to be used against a lesser enemy or with a lesser cause,
> for it does not merely defeat — it annihilates. It must be earned through penance,
> released but once, and never wasted."*

**A deep study of the index option SELLER — our structural counterparty — and a patient,
rare-release plan to convert the seller's own breaking points into asymmetric 10×–20× buyer trades.**

- **Instruments:** NIFTY 50 (primary), SENSEX (secondary). Options **BUYING ONLY**. We never write premium here.
- **Capital slot:** Rs.5,00,000 (one of the ten Sa-Ra-L portfolio strategies).
- **Philosophy:** *Study time ≫ trade time.* Most weeks: zero trades. The few we take are decisive.
- **Status:** `planned` — blueprint + config; live module to be built (`pashupatastra_live.py`).

---

## 0. HOW TO READ THIS DOCUMENT

This is both a **study** (Sections 2–6) and an **executable plan** (Sections 7–11). The study exists
because the user's instruction is exact: *"trade time will be less and study/analysis time will be more."*
We do not earn the 10×–20× by being clever on entry. We earn it by **understanding the seller so well that
we can sit on our hands for days, recognise the rare moment his structure breaks, and already be positioned
when his forced covering does the work for us.**

Two honesty rules govern every line below, taken from an adversarial review of options folklore:

1. **Most days the seller wins.** Selling premium has a high win-rate (~75–90% of days) funded by theta.
   We will lose small, repeatedly, while we wait. If you cannot stomach a long string of small losses,
   do not run this strategy.
2. **Max Pain, PCR, and "OI walls hold" are folklore, not signals.** They describe a *state*, never an
   *event*. We trigger on **events** (ΔOI flips, wall breaks on volume, IV expansion), and use the static
   metrics only as background context. See Section 4.5 and the Appendix.

---

## 1. THE PASHUPATASTRA DOCTRINE

### 1.1 The asymmetry we harvest

We are option **buyers**. Our payoff is convex: **loss is capped at premium paid, gain is large/uncapped.**
The seller owns the mirror: **gain capped at premium collected, loss large/■unbounded.** On a normal day this
asymmetry is *priced against us* — we pay theta, the seller collects it, and the seller wins. The entire
edge of this strategy is to **refuse to play on normal days** and to be long premium only on the minority of
days when the seller's structure — unhedged, herded, margin-thin, psychologically unable to cut — turns him
into a **forced buyer in our direction.**

We are not smarter than the seller on any given tick. We are simply positioned for the day his own machinery
detonates.

### 1.2 What this is / what this is NOT

```
┌────────────────────────────────────────────────────────────────────────────┐
│  PASHUPATASTRA IS…                     │  PASHUPATASTRA IS NOT…              │
├────────────────────────────────────────┼─────────────────────────────────────┤
│  A rare-release, high-conviction        │  A daily scalper (that's RAMS,      │
│  asymmetric BUY on seller capitulation  │  NIFTY_INTRADAY, ATM_PULSE)         │
│                                         │                                     │
│  Defined-risk: premium fully at risk,   │  A martingale / averaging-down      │
│  sized so a total loss is survivable    │  book — we NEVER add to a loser     │
│                                         │                                     │
│  Built on EVENT tells: ΔOI flips,       │  A Max-Pain / PCR "magnet" system   │
│  wall breaks on volume, IV expansion    │  (those are demoted to context)     │
│                                         │                                     │
│  A patience engine — most weeks flat,   │  An "always in the market" engine.  │
│  3–8 armed trades a MONTH at most        │  No fuel → no trade. Full stop.     │
│                                         │                                     │
│  A theta-AVOIDER (short time-in-trade)   │  A premium-seller. We buy only.     │
│                                         │                                     │
│  Honest about an ~80% zero-rate on the   │  A "guaranteed 10×" lottery. Most   │
│  cheap legs; the squeezes pay the year   │  cheap legs expire worthless.       │
└────────────────────────────────────────┴─────────────────────────────────────┘
```

### 1.3 Where it sits among the ten Sa-Ra-L strategies

| Strategy | Cadence | Per-trade payoff | Edge source |
|---|---|---|---|
| RAMS_v1 | Many/day | 1 : 2.3 | Intraday momentum confluence |
| NIFTY_INTRADAY_v1 | Several/day | 1 : 2.3 | Regime ORB / S-R |
| EXPIRY_SCALPER_v1 | 1–3 / expiry | 2.5–5× | Expiry-day momentum windows |
| BLACK_SWAN_v1 | Rare (event) | 4× | Extreme-move momentum |
| **PASHUPATASTRA_v1** | **3–8 / month** | **10–20× (tail)** | **Seller capitulation / forced covering** |

PASHUPATASTRA is the **rarest, highest-payoff, most-patient** slot. It overlaps BLACK_SWAN (both rare,
both asymmetric) but is **distinct**: BLACK_SWAN fires *reactively* on an already-extreme move (gap ≥1.5% /
intraday ≥2%); PASHUPATASTRA fires *anticipatorily* on the **option-chain signature of trapped sellers about
to be forced to cover**, often before the extreme move is obvious, and explicitly hunts the gamma/vega
mechanics rather than raw price momentum.

---

## 2. THE CURRENT BATTLEFIELD — VERIFIED MICROSTRUCTURE (mid-2026)

Indian F&O rules churned heavily in 2024–2025. The facts below were **web-verified on 2026-06-27**; items
tagged ⚠ must be **re-confirmed on the live NSE/BSE/SEBI page before going live** (lot sizes and expiry
weekdays are reviewed periodically). This section matters because **it changed the seller's economics and
crowding**, which is exactly our hunting ground.

| Fact | Current value (2026-06-27) | Note |
|---|---|---|
| NIFTY weekly expiry | **TUESDAY** (NSE) | Moved from Thursday on **1 Sep 2025** ⚠ |
| SENSEX weekly expiry | **THURSDAY** (BSE) | NSE=Tue / BSE=Thu split |
| One weekly expiry / exchange | **In force** (20 Nov 2024) | NSE→NIFTY only, BSE→SENSEX only |
| BANKNIFTY / FINNIFTY / MIDCPNIFTY weeklies | **DISCONTINUED** (Nov 2024) | Monthly contracts only now |
| NIFTY lot size | **65** | Cut 75→65 on **30 Dec 2025** ⚠ (repo still uses 75 — see §10.4) |
| SENSEX lot size | **20** | Up from 10 in 2025 |
| Min contract notional | **Rs.15–20 lakh** | SEBI hike, 20 Nov 2024 |
| STT — sell side of premium | **0.10%** | Raised from 0.0625% on 1 Oct 2024 |
| STT — option buyer, expires worthless | **NIL** | Buyer pays no STT on a worthless long |
| Expiry-day short options | **+2% ELM**, no calendar-spread margin benefit | 20 Nov 2024 / 10 Feb 2025 |
| Upfront premium collection | **Mandatory** | From buyers, since Oct 2024 |
| India VIX, 2025–26 range | **~9 (record low Dec-25) … ~29** | Median ~15.8; "calm 10–15, stress 20+" |
| Retail F&O outcome | **~91% of individuals LOST money in FY25** | SEBI study 2025; aggregate net loss ≈ Rs.1.05 lakh cr |

**What this did to the seller (our read):** higher expiry-day margin (+2% ELM, lost calendar offset),
bigger ticket size, and **fewer products to sell** (only NIFTY-Tue and SENSEX-Thu weeklies remain liquid).
The crowded short-premium trade is now **concentrated into two weekly expiries**, so seller crowding — and
therefore the OI "fuel" we hunt — is **denser and more readable** than ever at those two strikes-and-days.
The 91%-lose-money figure is the macro confirmation of our thesis: **the average counterparty to our buy is
a losing, unhedged retail seller.**

---

## 3. KNOW THY ENEMY — THE OPTION SELLER

### 3.1 Taxonomy: who is on the other side of our buy

The OI chart is a **map of who is trapped where.** Not all short OI is equal — some squeezes violently,
some never moves. Learn to tell them apart.

| # | Seller type | Margin/capital | Hedged? | Strikes / DTE | OI readability | Squeezable? |
|---|---|---|---|---|---|---|
| a | Market makers / HFT delta-hedgers | Portfolio margin, huge | **Yes** (continuous) | All, ATM±10 on expiry | **Low (noise)** | Their *hedging* is the transmission belt, not the prey |
| b | Institutional short-vol / short-gamma desks | Crores, portfolio margin | Dynamically | Short-dated ATM straddles | Moderate | On **IV expansion** (short vega) |
| c | **HNI / retail "income" weekly OTM sellers** | **SPAN+exp ≈ Rs.1–2L/lot, thin buffers** | **No (naked)** | **OTM weeklies, 0–2 DTE** | **HIGH — primary intel** | **YES — the prey** |
| d | Condor / strangle / credit-spread sellers | Lower (defined risk) | Partly (long wings) | Symmetric OTM wings | Moderate | Bounded — smaller, slower squeeze |
| e | Covered-call / overwriters | Backed by longs | "Covered" | OTM calls, monthly | Visible but **sticky** | **NO — won't run** |

**The one distinction that matters:** separate **naked, margin-thin, herded** short OI (type c → squeezes)
from **covered / defined-risk** short OI (types d, e → bounded or sticky). A rally that blows through
overwriter call OI produces *no fireworks*; one that blows through naked retail call OI produces a melt-up.
Type **c** is the structural heart of the Indian market and our designated prey.

### 3.2 Economics & the Greeks — the kill mechanics

The seller's P&L is **our P&L inverted.** Everything that pays him costs us; everything that ruins him pays us.

**How he MAKES money — positive THETA, accelerating non-linearly into expiry.** ATM extrinsic value decays
≈ √(time): slow far out, a cliff at the end. Representative NIFTY ATM weekly (per-share, ×65/lot):

| Time to expiry | ATM premium (≈/sh) | 1-day theta (≈/sh) | Per lot (×65) |
|---|---|---|---|
| 5 DTE | ~120 | ~14 | ~Rs.910/day |
| 1 DTE | ~55 | ~38 | ~Rs.2,470/day |
| Expiry 13:30→15:00 (flat tape) | 30 → 5–8 | collapses | **~75% gone in 90 min** |

That expiry-afternoon collapse on a *quiet* tape is the seller's payday — and the naked buyer's slaughterhouse.
**It is our designated NO-TRADE zone** (§7.1).

**How he DIES:**
- **Negative VEGA (IV expansion).** ATM weekly vega ≈ 8/sh. A **5-vol-point** IV spike (12→17) adds ≈ 40/sh =
  **~Rs.2,600/lot** to what he's short — **with spot flat.**
- **Negative GAMMA — the real killer.** His delta moves *against* him as spot moves, so losses **accelerate**,
  and gamma is **largest for ATM near expiry** — exactly where retail crowds on 0DTE. Worked example, naked
  short 25,000 CE sold at Rs.30 on expiry day:

  ```
  Spot 25,000 → 25,100 (+100):  CE 30 → ~85   loss ~55/sh  = Rs.3,575/lot
  Spot 25,100 → 25,200 (+100):  CE ~85 → ~165  loss ~80/sh  = Rs.5,200/lot  ← same move, BIGGER loss
  Spot 25,200 → 25,350 (+150):  CE ~165 → ~330 loss ~165/sh = Rs.10,725/lot
  Net on a ~350-pt expiry trend: Rs.30 credit → ~Rs.300 debit ≈ a 10× LOSS vs premium.
  The buyer of that same call made the mirror ~10×.
  ```

- **Margin expansion = the forced-flow engine.** SPAN is **vol-sensitive**: when IV rises, required margin
  **expands** at the exact moment MTM is bleeding. Thin-buffer type-c sellers breach maintenance → **margin
  call** → broker **auto-square-off** (non-discretionary, simultaneous, one-directional buy-to-cover). When
  many sellers at one strike are auto-squared at once, **that buy-to-cover IS the squeeze.** This is the
  cleanest fuel for a long-premium trade.

**The mirror — when seller mechanics are our enemy vs our engine:**

| Seller mechanic | Our ENEMY (be flat) | Our 10–20× ENGINE (be long) |
|---|---|---|
| Theta (his wage) | Calm / pinned / expiry-afternoon | never a friend — only survivable in short bursts around a catalyst |
| Vega (he's short) | Falling IV / post-event crush | **IV expansion off a cheap base** (~Rs.2,600/lot per 5 vols) |
| Gamma (he's short) | Low realized vol / chop | **Fast directional move near expiry (0–1 DTE)** |
| Margin / auto-square | Calm tape | **Vol spike + trend → margin-call covering cascade** |

**One-line doctrine:** *Avoid his theta. Hunt his short gamma + short vega.* Lose small on theta days, win
large on gamma days. The asymmetry — **not the win-rate** — is the entire edge.

### 3.3 Psychology — why the seller is *predictable*

The naked type-c seller is a behavioural machine. His economics are mirror-able; his **psychology is what
makes him exploitable.**

- **"Picking up pennies in front of a steamroller."** Small frequent wins train the behaviour right up until
  the steamroller.
- **High-win-rate dopamine + overconfidence after streaks** → he **upsizes and sells closer to the money**
  exactly when he is most exposed. Crowding peaks after calm stretches.
- **Loss-aversion → HE HOLDS AND ADJUSTS instead of cutting.** *This is the defining flaw.* A losing short is
  rolled, hedged, averaged-down, or prayed over on theta-hope. He converts what *should* be many small
  stop-losses into a few **synchronised, forced covering cascades.** (Note: this is the disease we must never
  catch — §9 forbids averaging down.)
- **Fear of overnight gap risk** → he **covers into the close**, especially expiry/pre-event → predictable
  end-of-day buy pressure on threatened strikes.
- **"Sell the spike" reflex** → on an IV pop he sells *more* premium; if the move sustains, he has added
  shorts into a trend — maximally wrong.
- **Anchoring to round strikes / max-pain; herd crowding into identical OTM strikes** → concentrated,
  identical, unhedged positions → **synchronised capitulation** when breached. *This is the structural reason
  Indian squeezes are violent.*

### 3.4 The exploit table — "When the seller does X → we do Y"

We do not predict direction. We **wait for the seller to commit, then position for his forced unwind.**

| Seller does X (the tell) | We do Y (the exploit) |
|---|---|
| Builds a huge OI wall at one OTM strike | Mark the battle line. Pre-stage a cheap long *beyond* it. Do **not** fade it. |
| Defends the strike intraday (price stalls, OI not yet falling) | **Wait.** This is the theta trap. No entry while he's still adding. |
| **Strike breaks + its OI starts FALLING fast (covering)** | **THE TRADE.** Go long in the break direction; covering + MM hedging extend the move = our window. |
| Holds losers on theta-hope as spot trends against him | Be positioned long *ahead* of the panic — he is unlit fuel; his margin-call/EOD cover is leg two. |
| Rolls / averages down the tested side | Larger short-gamma now stacked → bigger eventual unwind → raise conviction in continuation. |
| Sells into an IV spike on a *real* catalyst | Buy — we short-vega him; his short adds to the squeeze. (Beware false alarms / IV crush.) |
| **Caught short-gamma on a trend day, 0–1 DTE** | **Prime ground.** Long ATM/near-OTM in trend direction; auto-square cascade = cleanest 10–20×. |
| Covers into the close on a threatened expiry strike | Anticipate EOD buy pressure; position before ~14:00–14:30. |
| Pins price to max-pain on a calm day | **Stand aside.** His win condition. Long premium bleeds. Conserve the astra. |

---

## 4. READING THE SELLER IN THE OPTION CHAIN

This is the operational heart of the user's instruction — *"the seller's momentum can be watched via Option
Chain and big OI."* Every rule below maps to fields our `options_intel.py` already computes
(`call_oi`, `put_oi`, `change-in-OI` = ΔOI, `IV`, `LTP`, `PCR`, `max_pain`, `iv_percentile`, OI-buildup tag,
top-5 call/put OI strikes).

### 4.1 The OI 4-quadrant grid — and which quadrants are seller stress

OI counts *open* contracts (every one has a buyer AND a writer). **ΔOI** says whether positions are being
*created* or *closed*; **price** says who is winning. Combine:

| Price | ΔOI | Quadrant | Meaning |
|---|---|---|---|
| ↑ | ↑ | **Long buildup** | New money long; sustainable up-move (sellers calmly writing into strength, may become wrong) |
| ↑ | ↓ | **Short covering** | The move *is* shorts buying back to exit — fuel-limited but **violent** |
| ↓ | ↑ | **Short buildup** | Fresh shorts pressing; sustainable down-move |
| ↓ | ↓ | **Long unwinding** | Longs exiting; weak, drifting decline |

**Translated to the chain (the operational layer):**
- `call_oi` **rising** while spot pushes toward/through a strike = **call writers defending** (manufacturing
  resistance). Do **not** buy into this.
- `call_oi` **falling** while spot rises through it = **call writers covering = capitulation** → the single
  most bullish micro-tell. Their buy-to-close *is* the up-move.
- Symmetric on the downside: `put_oi` **falling** as spot drops through a put wall = put writers covering =
  downside accelerant.

**Seller-stress quadrants that precede explosive moves:** *Short covering* in the index combined with `call_oi`
falling above spot (call sellers squeezed → long CE), and the **flip** — a prior heavy *short buildup* whose
ΔOI turns negative as price grinds back into the strike (the pre-loaded short interest becomes a covering bid;
**the bigger the prior buildup, the bigger the squeeze**). *Trapped fuel must exist before it can ignite.*

> **Rule of thumb:** Buildup tells you **where** the fuel is stacked. Covering (ΔOI turning negative at that
> strike while price runs at it) tells you the fuel **just lit.** We enter on the second, never the first.

### 4.2 Walls, and what a BREAK means

- High `call_oi` strike = overhead-supply **ceiling**; high `put_oi` strike = **support floor**. The top-5
  OI strikes are the resistance / support ladders.
- **But — honesty check:** a "wall" is *not* a hard barrier (every contract has a long too). It is a
  **reference level + a battery of short gamma**, and it **breaks routinely** on trend/news days. Its value
  to us is not "price will respect it" — it is *"if price breaks it on volume and its writers cover, their
  forced hedging becomes the accelerant."* A broken wall flips from resistance into an **air pocket**, because
  the defenders just became forced buyers.
- A wall only matters if it's **large relative to the chain** (≳1.5–2× the median nearby strike OI). The
  highest-conviction break is through the **largest** wall when the **next** strike up/down has **thin OI**
  (minimal next line of defence → fastest extension).

### 4.3 Intraday ΔOI — adding (defending) vs covering (capitulating)

ΔOI vs prior session is the daily skeleton; **intraday ΔOI** is the live nervous system. Decode by strike
location vs spot:

| Location | OI rising = writers ADDING | OI falling = writers COVERING |
|---|---|---|
| CE above spot | Defending resistance (caps rally) | **Capitulating → up-squeeze fuel ✅** |
| PE below spot | Defending support (builds floor) | **Capitulating → down-squeeze fuel ✅** |

The tell we wait for: spot pressing a wall, and the wall's writers switch from **adding → covering** (ΔOI
flips negative). **Defending writers cap moves; covering writers create them.** Never buy *into* a wall still
being reinforced.

### 4.4 IV / IV-percentile / skew — the seller's fear gauge, and the crush trap

- **ATM IV** = the price of fear the seller is charging. **IV percentile (52-wk)** = is it cheap vs its own
  history? **<20–30th pct = cheap, buy-vega regime; >70–80th = rich** (expensive lottery tickets with a vol
  headwind).
- **Skew** (OTM-put IV − OTM-call IV): steep put skew = crash fear already paid for; flat/low skew + low IV
  percentile = sellers asleep = the asymmetric setup (a pop expands vega *and* moves delta).
- **The IV-CRUSH trap — the #1 retail-buyer killer.** Before a **scheduled event** sellers jack ATM IV;
  you buy a "cheap"-looking OTM; the event resolves; IV collapses 30–50% in minutes and **you lose even if
  spot moves your way.** **Never buy rich IV into a scheduled event.** Our edge is the inverse: buy **cheap
  vega off a crushed base before an *unscheduled* expansion.**

### 4.5 HONEST reliability ratings — Max Pain & PCR are CONTEXT, never triggers

Adversarially verified (see Appendix). Most "option-chain analysis" content over-trusts these:

| Metric | Reliability | How to use |
|---|---|---|
| **Max Pain** ("price gravitates to it") | **LOW / folklore** | A deep arbitraged index is **not pinned** by OI. The "magnet" is a statistical artifact of OI clustering at round strikes; it has **no consistent predictive edge** and **fails entirely on trend/event days — exactly the days we trade.** Use only to know the "comfort zone," so you recognise when price *leaves* it. **Never a price target.** |
| **PCR** (put OI / call OI, contrarian) | **LOW–MODERATE / context** | Thresholds (1.3/0.7) are **non-stationary**; index PCR is distorted by hedging flow; empirical results mixed-to-weak and horizon-dependent. **Never an entry trigger.** Slow background read only. |
| **OI "walls hold"** | **LOW** | Walls break routinely; they are reference levels + short-gamma batteries, not barriers. The **break** is the signal, not the wall. |
| **"Smart money from aggregate OI"** | **REFUTED** | OI nets longs and shorts and is lagged — it cannot reveal net direction. Narrative overfitting. |

**Demotion rule:** static/aggregate metrics describe a **state**; *changes* (ΔOI at specific strikes, IV
expansion, wall breaks on volume) describe **events.** **We trigger on events, contextualise with state.**

**Our trigger reliability ladder:** (1) strike-level ΔOI flip to covering — strongest; (2) wall break on
rising volume — strong; (3) IV-percentile + skew — strong *filter*; (4) OI-buildup tags — locate fuel;
(5) PCR / Max Pain — wallpaper only.

### 4.6 Worked example — spotting a trapped short-call wall about to break

*Representative NIFTY weekly-expiry (Tuesday) session; numbers illustrative.*

```
09:30  Spot 22,180. Top call wall = 22,200 CE, call_oi 48 lakh (next strike 22,250 only 19 lakh).
       ATM IV 9.5%, IV-percentile ~15th (CHEAP). 22,300 CE = Rs.5.
Morning Spot chops 22,150–22,200. 22,200 CE ΔOI = +9 lakh → writers ADDING (defending). NO TRADE.
13:10  Catalyst lifts spot to 22,205, THROUGH the wall, on a VOLUME SURGE.
       22,200 CE ΔOI rolls +9 lakh → −6 lakh in 20 min → writers COVERING. ← THE FLARE.
       Buy 22,300 CE @ Rs.7 (cheap vega, thin OI above = air pocket).
13:10–14:30  Short-gamma writers buy futures + buy back calls → spot 22,290 → 22,380.
       ATM IV 9.5% → 11% (vega tailwind, no crush — unscheduled). 22,300 CE ≈ Rs.95.
PAYOFF  Entry Rs.7 → ~Rs.95 ≈ 13.5×.
```

**Why it worked (the checklist):** (1) a *genuinely large* wall = real trapped fuel; (2) cheap IV-percentile =
no vol headwind; (3) thin OI above = air pocket; (4) we **waited for ΔOI to flip add→cover**; (5) expiry day =
gamma amplifier. **Failure mirror:** buying at 09:30 while ΔOI was +9 lakh and spot stayed pinned → the same
22,300 CE decays Rs.5 → Rs.1 by close. *Same chain, opposite outcome — the difference is the ΔOI flip and the
volume-confirmed break.*

---

## 5. SWOT — THE OPTION SELLER (our competitor)

Built strictly from the buyer's exploit lens. **Honest framing:** the seller's Strengths dominate on *most*
days — which is precisely why we wait.

```
┌─────────────────────────── STRENGTHS (why he wins most days) ───────────────────────────┐
│ S1 Positive theta — paid every calm day; decay accelerates into expiry in his favour      │
│ S2 High base win-rate (~75–90% of days); realized vol < implied vol most of the time      │
│ S3 Time is his ally — he profits from nothing happening (the modal outcome)               │
│ S4 Pin/range days: max-pain "comfort zone" + delta-hedging toward it work FOR him          │
│ S5 Post-2024 rules concentrated flow into 2 weekly expiries → deep, liquid, efficient      │
└────────────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────────────── WEAKNESSES (structural soft spots) ─────────────────────────┐
│ W1 Negative-skew payoff: capped gain, large/unbounded loss ("pennies / steamroller")      │
│ W2 Short GAMMA — losses ACCELERATE on a move; worst near-ATM, near-expiry (0DTE)          │
│ W3 Short VEGA — an IV pop hurts him with spot flat                                         │
│ W4 Margin is vol-sensitive — it EXPANDS exactly when he's bleeding → forced de-risk        │
│ W5 Type-c retail is UNHEDGED, margin-thin, and HERDED into identical OTM strikes           │
│ W6 Psychology: holds & adjusts losers instead of cutting → synchronized covering cascades  │
│ W7 Naked short OI is VISIBLE in the chain — he announces where he is trapped               │
└────────────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────────── OPPORTUNITIES (forces that help him) ───────────────────────────┐
│ O1 Structurally low-vol regimes (2025–26 saw record-low VIX ~9) → long calm stretches      │
│ O2 ~91% of retail BUYERS lose → his average counterparty is weak (theta/IV-crush victims)  │
│ O3 IV-crush around scheduled events → he sells rich IV and lets it collapse                │
│ O4 Higher buyer transaction drag (flat fees on tiny premium) taxes our side                │
└────────────────────────────────────────────────────────────────────────────────────────┘
┌──────────────────────── THREATS (what destroys him = OUR EDGE) ─────────────────────────┐
│ T1 Trend day near expiry → short-gamma cascade up/down the OI ladder                       │
│ T2 IV expansion off a crushed base → short-vega bleed + forced buy-back                    │
│ T3 Wall break on volume → forced covering becomes the accelerant (self-reinforcing)        │
│ T4 Event/gap past clustered OI → trapped at the open, covers into the move                 │
│ T5 Vol-squeeze release → crowded, complacent shorts all offside at once → violent break     │
│ T6 Margin-call / RMS auto-square cascade → non-discretionary one-directional buy flow       │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 5.1 The mirror — Buyer Counter-SWOT (monetising each Threat)

| Seller Weakness / Threat | Our action that monetises it | Setup |
|---|---|---|
| W2 / T1 short gamma near expiry | Buy near-OTM 0DTE in the break direction | **A — Gamma Squeeze** |
| W3 / T2 short vega off cheap base | Buy long-vega (slightly-OTM, >0DTE) at low IV-percentile | **B — Coiled Vega** |
| W5–W7 / T3 herded naked walls break | Enter only on **ΔOI add→cover flip + volume break** | **A & C** |
| T5 vol-squeeze release | Buy the *expansion*, not the coil (BB bandwidth turns up) | **B/C** |
| T4 event/gap | Cheap vega *before* IV ramp, or post-crush trend (NOT rich IV into event) | **D — Event/Gap** |
| T6 margin cascade | Hold the runner through the forced-cover leg | exit logic §7.5 |

### 5.2 Competitive thesis (one paragraph)

*We harvest a single asymmetry: the naked Indian index seller is **right most of the time and ruined some of
the time, and structurally cannot cut.** His refusal to cut converts many small would-be stop-losses into a
few synchronized, forced, one-directional covering cascades — and his short gamma/vega makes those cascades
self-reinforcing. We cannot beat his theta on a normal day and we will not try. We stay flat through his
win-condition days, read the option chain to find where his naked, herded, margin-thin shorts are stacked,
wait for the ΔOI-flip + volume break that proves he has stopped defending and started covering, and are
already long premium — cheap, defined-risk, near his gamma — when his own forced hedging delivers our 10–20×.
We are not smarter than him daily; we are positioned for the day his structure detonates.*

---

## 6. SELLER BREAKING POINTS — THE 10–20× WINDOWS

> **Base-rate warning, stated bluntly:** on a *typical* session **none of these windows is present.** The
> seller wins ~75–85% of days on theta. A clean 10–20× on a cheap OTM is a **few-times-a-month** event; the
> 20×+ tail is monthly-to-quarterly. **No fuel → no trade.** Forcing trades on no-signal days is how you
> donate your wall-break winnings back via theta.

| Window | Seller behaviour that creates it | Chain + indicator signature | Realistic multiple | Frequency | Failure mode |
|---|---|---|---|---|---|
| **A — Gamma squeeze (0DTE)** | Near-ATM short options have huge gamma; writers hedge in the move's direction | Expiry day; spot pinned just below a large wall, thin OI beyond; **ΔOI flips +→− at the wall**; volume surge; IV not rich | Rs.3–8 → **Rs.60–120 (10–20×)**, rarely more | Setups most expiry days; **clean 10×+ a few/month** | **Pin/theta cliff** — wall holds, OTM → 0 by 15:30 (~80–90% of cheap legs die) |
| **B — Coiled vega (IV expansion off cheap base)** | Sellers over-sold vol; complacency maxed; a pop forces buy-back | **IV-percentile <20th**, ATM IV at multi-week low, low skew, BB/IV compressed; long-vega slightly-OTM, **>0DTE** | 3–8× (vega leg); **10×+** if it stacks with C/D | Cheap-vol regimes monthly-ish; expansion may take days | **Vol stays crushed** and theta grinds you out; cut on time-stop |
| **C — Trend-trap (ladder cascade)** | Range writers stack the top-5 ladder; a break makes each wall's writers cover into the next | Strong drive on expanding volume; **sequential ΔOI flips −** up successive strikes; price **leaves Max-Pain zone & doesn't return**; ADX rising, one side of VWAP | ATM/near-OTM **5–15×** climbing the ladder | True trend days ~**3–6/month**; clean ladders fewer | **Failed breakout** — wall absorbs, snaps back through VWAP; require the ΔOI flip, not a price poke |
| **D — Event / gap** | Shock gaps the index past clustered OI; writers trapped at the open cover into the gap | *Scheduled* → IV already rich = **crush trap, don't buy the obvious lotto**; *unscheduled gap-and-go* off cheap prior IV → delta+vega both pay | Gap-and-go **10–30×**; once-a-year shock **50×+** | Big tradable gaps a **handful/year** | Buying **rich IV into a scheduled event** and getting crushed though right on direction |
| **E — Vol-squeeze release** | Tight range = sellers feast on theta, compress vol → crowded, complacent, all offside one way | BB bandwidth at multi-day low, OI concentrating into a tight band, low IV-percentile; trigger = **bandwidth expands + range break on volume + ΔOI flip** | 5–15×; higher if it fires into A/D | Multi-day squeezes a **few/quarter** | **Premature entry into the coil** (theta bleed) + **false breaks**; wait for bandwidth to actually expand |

**Stacked windows pay the most.** The biggest tails share one DNA — *crowded, complacent sellers (cheap IV,
big OI) + a catalyst forcing them to hedge/cover in the move's direction.* A **vol-squeeze firing on expiry
into an event (E+A+D)** is the jackpot configuration.

---

## 7. THE PLAN — PASHUPATASTRA SETUPS

Four named, mutually-exclusive setups, each a window from §6 made executable on our computed signals. **At
most one armed position at a time.** Everything is **defined-risk** (premium fully at risk) and built around a
**partial-scale + runner** exit so the rare win can ride to the 10–20× tail.

### 7.1 Preconditions — the gate (most days we never pass it)

```
GLOBAL ARM GATE (all must hold before any setup can fire):
  ✓ FUEL present:   a wall ≥1.5× median nearby strike OI  (options_intel top-5 OI strikes)
                    OR IV-percentile < 25th (cheap vega)
                    OR BB bandwidth in a multi-day squeeze (coiled)
  ✓ NOT a known IV-crush trap: no SCHEDULED event in the next 60 min with rich ATM IV (IV-pctl > 70)
  ✓ NOT the theta deathzone: not flat-tape expiry after 14:45 with spot pinned to max-pain comfort zone
  ✓ Day is not pure chop: ScoutWatchman VWAP-crossings < 3 by 10:00  (reuse existing gate)
  ✓ Budget: ≥1 astra charge (Rs.10,000) available and monthly loss cap not breached
  IF GATE FAILS → FLAT. No trade. Log the reason. This is the expected outcome most days.
```

### 7.2 Setup A — OI-Wall-Break Gamma Squeeze (the core trade)

| | |
|---|---|
| **When** | Expiry day (NIFTY-Tue / SENSEX-Thu) or 1 DTE; a large call/put wall near spot |
| **Trigger** | (1) spot breaks the wall strike by ≥ buffer (0.05% NIFTY) on **volume ≥1.5× avg**; (2) **wall ΔOI flips +→−** (writers covering) on the breaking side; (3) thin OI on the next strike beyond (air pocket); (4) ATM IV-percentile not rich |
| **Direction** | Break direction (up break of call wall → CE; down break of put wall → PE) |
| **Strike** | The **broken wall strike or one strike beyond** toward the move (cheap, high-gamma) |
| **Expiry** | 0DTE / current weekly |
| **Time stop** | **No follow-through within ~15 min (3×5-min bars) → exit.** Hard exit by 15:10 (NIFTY) — never hold a 0DTE cheapie into the final pin. |

### 7.3 Setup B — Coiled-Vega Squeeze-Break

| | |
|---|---|
| **When** | IV-percentile <20–25th AND BB bandwidth in a multi-day squeeze; **no** scheduled event (we want *unscheduled* expansion) |
| **Trigger** | BB **bandwidth turns up** (expansion begins) **+** range break on volume **+** (if near a wall) ΔOI flip |
| **Direction** | Break direction |
| **Strike** | Slightly OTM (1 strike) |
| **Expiry** | **>0DTE** — current or next weekly, so vega has time to work and theta isn't fatal |
| **Time stop** | Wider — up to ~1 session; cut if bandwidth re-compresses or IV fails to expand |

### 7.4 Setup C — Trend-Trap Runner (ladder cascade)

| | |
|---|---|
| **When** | ScoutWatchman classifies the day **ALERT/TREND** at 10:00 (ADX≥22, range≥0.35× ATR-proxy, chop-free) AND a stacked top-5 OI ladder exists on one side |
| **Trigger** | Price clears wall #1 on volume with **ΔOI flipping − sequentially** up the ladder; price leaves the Max-Pain comfort zone and holds one side of VWAP; momentum-score ≥65 (existing ScoutWatchman scorer) |
| **Direction** | Trend direction |
| **Strike** | ATM / near-OTM (delta to ride the trend) |
| **Expiry** | Current weekly |
| **Time stop** | ATR trailing stop (reuse ScoutWatchman trail) + reversal-score exit; hard close 15:10 |

### 7.5 Setup D — Event / Gap

| | |
|---|---|
| **When** | A real gap/shock past clustered OI |
| **Two regimes** | *Scheduled event:* **do NOT buy rich IV beforehand.** Either own cheap vega bought **well before** the IV ramp, or trade the **post-crush** confirmed trend. *Unscheduled gap-and-go* off cheap prior-close IV → enter on the open drive (delta+vega both pay). |
| **Strike / expiry** | 1 strike OTM, current weekly |
| **Relationship to BLACK_SWAN_v1** | BLACK_SWAN fires on raw price extremity (gap ≥1.5% / intraday ≥2%) with a 4× target & 90-min stop. PASHUPATASTRA-D fires on the **option-chain trapped-seller signature** and rides a partial+runner for the larger tail. If both arm, run **one** (prefer the one whose gate is cleaner) to avoid double risk on the same move. |
| **Time stop** | 90 min if no extension (cf. BLACK_SWAN), else trail; hard close per instrument |

### 7.6 Exit & booking — the runner that captures the tail

The single most important mechanic for converting a *move* into a *10–20×*: **bank enough early to make the
bullet free, then let the rest run.**

```
SCALE-OUT LADDER (per armed bullet):
  +3×  : SELL 40%  → recovers 0.40 × 3 = 1.2× of TOTAL cost  → the whole bullet is now risk-free
  +6–8×: SELL 30%  → locks a strong profit
  Runner 30% ("the astra"): trail at 40% below peak premium, OR ride to the next OI wall / structural
                            target / time-stop / hard EOD close — whichever comes first.
  Reversal override: if ΔOI flips back to ADDING at the new strike + volume dies → exit runner immediately.
```

Because 40% sold at 3× already returns 1.2× of the entire premium, **a bullet that reaches 3× and then dies
is still a net win**, and a bullet that reaches 15× pays ~ (1.2 + 0.30×6 + 0.30×15) ≈ **7.5× on capital**
after the ladder. That is the engine.

---

## 8. POSITION SIZING — THE ASTRA CHARGE

**Objective:** every bullet is defined-risk; a total loss is a small, survivable fraction of the Rs.5L slot,
and we can fire several bullets a month while we wait for the tail.

```
ASTRA CHARGE (max premium at risk per bullet) = Rs.10,000  (2% of the Rs.5L slot)
  qty_units = floor( 10,000 / (entry_premium × lot_size) ) × lot_size      # lot_size: NIFTY 65 ⚠ / SENSEX 20
  capital_at_risk = qty_units × entry_premium        (≤ Rs.10,000 ; this IS the max loss)
  There is NO premium stop-loss on the cheap legs (A/B/D 0DTE/OTM) — the option itself is the stop.
  Setup C (ATM/trend) MAY use the ScoutWatchman ATR trailing stop instead of full-premium risk.

PORTFOLIO LIMITS:
  Max concurrent bullets ......... 1 (one armed position at a time)
  Max bullets per week ........... 3
  Monthly loss cap ............... Rs.40,000  (8% of slot)  → strategy goes FLAT for the month if hit
  Consecutive-misfire pause ...... after 4 straight total-loss bullets → SHADOW for 1 week, re-assess regime
```

### 8.1 Worked example — the win (Setup A, ~13.5×)

```
NIFTY expiry, lot 65. Entry 22,300 CE @ Rs.7 (post wall-break + ΔOI flip, IV-pctl 15th).
  qty = floor(10,000 / (7 × 65)) × 65 = floor(21.9) × 65 = 21 × 65 = 1,365 units
  cost = 1,365 × 7 = Rs.9,555   (max loss)
  +3× (Rs.21): sell 40% (546 u) → +546×14 = Rs.7,644 banked  (bullet now ~risk-free)
  +6× (Rs.42): sell 30% (410 u) → +410×35 = Rs.14,350 banked
  Runner 30% (409 u) rides to Rs.95 (13.5×): +409×88 = Rs.36,000
  ── Total ≈ Rs.7,644 + 14,350 + 36,000 = Rs.58,000 profit on Rs.9,555 risk ≈ 6× on capital ──
```

### 8.2 Worked example — the (more common) loss

```
Same Rs.7 entry, but the wall HOLDS (ΔOI never flips, spot pinned to comfort zone).
  22,300 CE decays Rs.7 → Rs.1 → 0 by 15:10 close.
  Loss = full Rs.9,555.  This happens to ~70–85% of cheap A-bullets. It is EXPECTED and BUDGETED.
  Expectancy holds because one 6×-on-capital win pays for ~6 of these losses, and disciplined
  selectivity (fuel + flare required) lifts the armed-trade hit-rate well above a blind 0DTE buy.
```

**Expectancy sanity check (conservative):** if 1-in-4 armed bullets reaches the scale-ladder for ~6× capital
and 3-in-4 lose ~1×: `0.25×6 − 0.75×1 = +0.75×` per bullet, *before* refinement from the runner tail and
*after* which costs/slippage must be subtracted. The math only works with **strict selectivity** — overtrade
and it inverts to negative.

---

## 9. THE DISCIPLINE — "NEVER WASTE THE ASTRA"

These rules are non-negotiable; they are what make §8's expectancy real rather than aspirational.

```
1. NO FUEL, NO TRADE. If the §7.1 gate fails, stay flat. Most days/weeks this is the answer.
2. NEVER buy into a wall still being DEFENDED (ΔOI still +). Wait for the add→cover FLIP.
3. NEVER buy rich IV (IV-pctl > 70) into a SCHEDULED event. That premium is engineered to crush you.
4. NEVER average down a losing bullet. Adding to a loser is the SELLER's disease (§3.3 W6) — not ours.
5. ONE armed bullet at a time. The astra is released once, not sprayed.
6. RESPECT the clock. 0DTE cheap legs are exited by 15:10 and on a 15-min no-follow-through time-stop.
   The last 30–45 min of a flat expiry is pure theta/pin — the seller's home turf.
7. RESPECT the caps. Monthly loss cap Rs.40,000 → flat for the month. 4 misfires → SHADOW a week.
8. The runner is for the TAIL, not the hope. Trail it mechanically (40% off peak / ΔOI re-add) — do not
   "give it room" emotionally.
9. LOG every no-trade with its gate-fail reason. The discipline of NOT trading is the strategy's main work.
```

**Kill switches (manual, pre-session):** skip the day if (a) yesterday breached the monthly cap trajectory,
(b) a major scheduled event sits inside trading hours with already-rich IV and no cheap-vega pre-position,
(c) VIX > 35 (premiums distorted, sizing breaks), (d) NSE/BSE data feed or `options_intel` NSE fetch is down
(we are blind to ΔOI — and blind = flat).

---

## 10. INTEGRATION WITH Sa-Ra-L

### 10.1 Reuse (already built)
- **`options_intel.py`** — `call_oi`, `put_oi`, `change-in-OI`, `IV`, `iv_percentile`, `max_pain`, PCR,
  top-5 call/put OI strikes. This is the chain intel layer the whole plan rides on.
- **`scout_watchman.py`** — the SCOUT→ALERT→TRADE→DONE machine, the 10:00 day-classification gate (ADX /
  range / VWAP-chop), the momentum scorer, and the ATR-trailing-stop + reversal-score exit. Setup C is
  essentially ScoutWatchman ALERT escalated into an asymmetric buy; reuse it wholesale.
- **`confluence_scorer.py`** — the weighted vote matrix; raise the **Options** weight for this strategy.
- **Pre-market BIAS** + **economic_calendar.yaml** — for the §7.1 scheduled-event gate (IV-crush guard).

### 10.2 Build (new, modelled on existing live modules)
- **`SellerStress` signal in `options_intel.py`** — per-strike intraday **ΔOI flip detector** (add→cover),
  wall-size ratio (strike OI ÷ median nearby OI), air-pocket flag (thin OI beyond the wall), IV-percentile
  gate, and BB-bandwidth squeeze tie-in. Emits `WALL_BREAK_UP / WALL_BREAK_DOWN / DEFENDING / NEUTRAL` plus a
  0–100 stress score. This is the heart of the new logic.
- **`pashupatastra_live.py`** (new live engine, sibling to `scout_watchman.py` / `black_swan_live.py`) —
  the §7.1 gate, the four setups, the §8 astra-charge sizing, the §7.6 scale-out-ladder + runner, the §9
  discipline rails. Plug into `portfolio_runner.py` like the other strategies.
- **`run_pashupatastra()`** handler in `src/backtest/engine.py` for the §11 backtest.

### 10.3 Config
See `strategies/PASHUPATASTRA_v1/config.yaml` (companion file) — all thresholds, the astra charge, caps, and
per-setup parameters, in the same schema as the sibling strategies.

### 10.4 ⚠ Platform corrections this study surfaced (action items)
The microstructure verification (Section 2, web-checked 2026-06-27) found the repo's constants are **stale**:
- **NIFTY lot size is 65, not 75** (changed 30 Dec 2025). Every sibling config (`NIFTY_INTRADAY_v1`,
  `BLACK_SWAN_v1`, `EXPIRY_SCALPER_v1`, etc.) and the `lot_size: 75` constants still say 75. **Sizing math
  across the platform is off by ~15%.** Recommend a platform-wide audit.
- **NIFTY weekly expiry is TUESDAY, not Thursday** (since 1 Sep 2025); **SENSEX is Thursday**. Any
  expiry-day logic keyed to Thursday for NIFTY (e.g. `EXPIRY_SCALPER_v1`, `BB_EXPIRY_SCALPER_v1`, the
  `market_calendar`) needs updating.
- BANKNIFTY/FINNIFTY/MIDCPNIFTY **weeklies no longer exist** — any weekly logic on those indices is dead.
- These are flagged ⚠ and should be re-confirmed on the live NSE/BSE contract pages before live trading.

---

## 11. BACKTEST & VALIDATION PLAN

Because the edge lives in a **fat right tail**, standard win-rate metrics mislead. Validate on the *shape*.

```
PERIOD: 2020-01 → 2026-06 (covers COVID, 2021 bull, 2022 correction, low-vol 2025, and the
        post-SEBI-2024/25 microstructure — segment results BEFORE vs AFTER 20-Nov-2024).

DATA: NSE/BSE option chain with OI + ΔOI + IV per strike (needed to replay the ΔOI-flip trigger).
      Spot/futures 1-min + 5-min bars. India VIX. Economic-event calendar (for the IV-crush gate).

METRICS (tail-aware — do NOT optimise win-rate):
  • Expectancy per bullet (R, net of slippage + true-to-label charges + STT-on-runner-sells)
  • Right-tail: count and size of ≥5× / ≥10× / ≥20× outcomes; % of total P&L from the top 5 trades
  • Hit-rate on ARMED bullets vs a blind-0DTE-OTM control (must beat it materially — else no edge)
  • Profit factor, max drawdown (in astra-charges), longest losing streak (in bullets and in weeks)
  • Per-setup attribution (A/B/C/D) and per-window-stack (does E+A+D really pay most?)
  • Slippage/Iv-crush stress: re-run with 2× slippage and a forced 20% IV-crush on event days

ACCEPTANCE (paper → live):
  • Positive expectancy AFTER costs, driven by the tail (not by win-rate)
  • Armed-bullet hit-rate beats the blind-0DTE control by a clear margin
  • Max drawdown ≤ ~Rs.80,000 (≤16% of slot) across the worst stretch
  • Discipline check: ≥80% of days are correctly NO-TRADE; no averaging-down ever fires
Deploy ladder: SHADOW (analysis only) → paper (≥2 months, ≥15 armed bullets) → 25% size → full.
```

---

## 12. HONESTY APPENDIX — VERIFIED CLAIMS & FOLKLORE DEMOTED

Adversarially fact-checked (skeptic pass + web verification, 2026-06-27).

| Claim | Verdict | Use in this doc |
|---|---|---|
| Max Pain predicts the expiry close (price is "pulled" to it) | **REFUTED** | Demoted to comfort-zone context only (§4.5); never a target |
| High/low PCR is a tradable contrarian signal | **PARTIALLY TRUE (weak)** | Context only; never a trigger (§4.5) |
| Heavy-OI strikes are hard floors/ceilings | **REFUTED** | Walls break routinely; the **break** is the signal (§4.2) |
| Breaking a written wall on volume can force covering → squeeze → 5–20× | **PARTIALLY TRUE** | Real but **conditional** on net dealer short-gamma; multiples are rare tails (§6) |
| Theta decay is non-linear, brutal on near-ATM 0DTE | **CONFIRMED** | Drives the §7.1 theta-deathzone gate & §9 clock rule |
| Cheap 0DTE OTM can do 10–20× but blind buying is negative-EV | **CONFIRMED** | The reason for strict selectivity + the ~80% zero-rate budgeting (§8.2) |
| Buyers have low win-rate; edge is the fat tail + strict risk + selectivity, not frequency | **CONFIRMED** | The core doctrine (§1, §8) |
| Negative gamma → seller losses accelerate, worst near-ATM near expiry | **CONFIRMED** | The §6-A mechanism |
| IV mean-reverts/clusters → buy vega at LOW IV-percentile, not after a spike | **PARTIALLY TRUE** | Setup B gate; with the "low IV can persist" caveat |
| "Smart money direction" can be read from aggregate OI | **REFUTED** | Not used; OI nets longs/shorts and is lagged (§4.5) |

**Verified market facts (web, 2026-06-27):** NIFTY weekly = Tue (since 1 Sep 2025); SENSEX weekly = Thu;
one-weekly-per-exchange + BankNifty/FinNifty/MidcpNifty weeklies discontinued (Nov 2024); NIFTY lot 65
(Dec 2025), SENSEX lot 20; STT sell-side 0.10% (Oct 2024), buyer pays nil on worthless expiry; +2% expiry ELM
& no expiry-day calendar-spread benefit; India VIX ~9–29 in 2025–26; ~91% of retail F&O individuals lost
money in FY25 (SEBI 2025).

**Could NOT be fully verified (re-confirm before live):** exact current BANKNIFTY lot; precise 0DTE share of
volume; SENSEX lot effective date; intraday position-limit go-live date; NIFTY=65 & Tuesday expiry should be
re-checked on the live NSE page at deployment (lot sizes/expiry weekdays are reviewed periodically).

---

*Sources: SEBI circulars (Oct-2024 derivatives framework, Jul-2024 true-to-label, 2025 expiry
standardisation, Sep-2024 & 2025 retail-loss studies); NSE/BSE circulars & contract specs; Zerodha Z-Connect
& charges; Business Standard, Moneylife/NISM, Bloomberg/FIA. Multiples and frequencies are representative
orders of magnitude for NIFTY/SENSEX weeklies, not guarantees. This document is a trading-strategy design and
study, not investment advice.*
