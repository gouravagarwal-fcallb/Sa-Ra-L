# TRAP_CMCD_v1 — Rulebook

**Archetype:** intraday Nifty option **buyer**, **reversal-only** (fades traps at
zones). Timeframe **3-minute**. No overnight (theta) holding.

> ⚠️ **Evidence status:** UNVALIDATED. Ships `paper` + `live_blocked`. The GTI
> research already found standalone demand/supply-zone edge is *thin and dies at
> costs below 15m* (see `CLAUDE.md` → "GTI demand/supply zones"). A 3-minute
> reversal-at-zones strategy is therefore a **show-me**: it must earn a real
> backtest + a paper-forward sample before anyone discusses live.

## The idea (CMCD)
1. **Compression** — Bollinger-in-Keltner squeeze → a move is loading.
2. **Accumulation** — price parked in a **fresh** demand/supply zone.
3. **Manipulation** — a false push out of the zone (stop-hunt) that fails:
   multiple **Black** (sell) candles trapped in a **buying** zone that *fail to
   push lower*, or **Blue** (buy) candles in a **selling** zone that *fail to
   sustain*.
4. **Correction** — reversal back toward the **Golden Line** (VWAP).
5. **Distribution** — the leg to the opposite zone (where we book, zone-to-zone).

## Entry
- **Long / BUY Call (Whale 'W'):** in a fresh **demand** zone, ≥`min_trap_candles`
  Black candles holding (no new low) → **Yellow** reversal or **Blue** close above
  the trap high. Only if price is **below** VWAP (room to revert up).
- **Short / BUY Put (Whale 'M'):** mirror in a fresh **supply** zone.
- **Reject clean breakouts** — two decisive closes beyond the zone on rising
  volume is a breakout, not a trap → skip.

## Zones — reused, freshness-gated
`src/research/gti/gti_zones.detect_zones` + `active_zones`, filtered to
`tests == 0` (**freshness**, never strength — strength does not rank edge).

## Risk (on option premium, points)
- Hard SL **35** (pre-target safety net).
- At **+25** (minimum benchmark): lock breakeven+, switch to **zone-to-zone**
  trailing with a **20-pt** ratchet under the premium peak → rides the 54/90/155-pt
  moves the source cites while protecting gains.
- Sizing: **10% of capital** per trade (Brain-Freeze guard), capped at
  `max_trade_rs`.
- Day guards: `daily_loss_lock_rs`, `max_trades_per_day`, cooldown, 15:10 square-off.

## "Whale Bubble" caveat
True volume footprint needs bid/ask (L2/tick) order-flow, unavailable from
candles — the same reason ATM_PULSE/RAMS aren't backtestable. Here it is a
**proxy** (volume z-score spike) and the primary structural read is the zone.
