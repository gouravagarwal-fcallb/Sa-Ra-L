# Sa-Ra-L — Strategy Fidelity Audit

_Last updated 2026-07-27. This is the honest, post-bug-sweep picture of every
strategy. If a number here disagrees with an older note, this file wins._

---

## 1. Why this audit happened

While making each strategy "a gem", we found the backtests were **systematically
optimistic** — the numbers looked better than the strategies really are. The audit
hunted down every modelling bug, fixed it, and re-derived the honest numbers. The
guiding rule throughout: **never present a fantasy number as if it were real.**

## 2. The main bug: intrabar target-overshoot

**In plain terms:** when an option hit its profit target, the backtest booked the
*highest price the option spiked to inside that 1- or 5-minute bar* — not the price
you'd actually get from your target/limit order. On expiry-day options that can be
an 8–15× spike, so winners were inflated massively.

**Where it lived (all fixed — target now books the limit price):**

| Location | Strategies affected |
|---|---|
| `engine.run_expiry_scalper` | EXPIRY_SCALPER |
| `engine.run_bb_expiry_scalper` | BB_EXPIRY |
| `engine.run_gap_fade` | GAP_FADE |
| `engine.run_range_scalper` | RANGE_SCALPER |
| `engine.run_nifty_intraday` | NIFTY_INTRADAY |
| `option_pricer.simulate_trade` (shared) | BLACK_SWAN, TREND_RIDER, ATM_PULSE |

**Effect:** e.g. EXPIRY_SCALPER's headline fell from a fantasy **PF 5.7 → ~2.5**;
BB_EXPIRY from **₹7.4L → ~₹46k** (near break-even); NIFTY_INTRADAY flipped from
apparently-positive to a **−₹4.7L loser**.

## 3. Other fidelity fixes

- **Flat VIX → real VIX.** EXPIRY/BB/RANGE priced every option at a flat volatility
  of 15 for the whole history (through COVID's VIX 80 and calm VIX 10 alike). Now
  each day is priced at its real India-VIX close.
- **Phantom instruments.** Backtests traded weekly options *before they existed*
  (NIFTY weekly launched 11-Feb-2019, SENSEX weekly 15-May-2023). A guard now skips
  pre-launch days so no fictional instrument is traded.
- **VIX_SELLER short side.** Its decay-target booked the overshoot on the *sell*
  side too — clamped.
- **INRUSD look-ahead.** Its indicators were fed *today's close* and then used to
  decide entry at *today's open* — the signal peeked at the future. Fixed to use
  through-yesterday state only.
- **BRAHMASTRA tie-break.** A daily bar that touched both stop and target booked the
  *target*; now books the *stop* (conservative), matching INRUSD.

## 4. The honest board (as of 2026-07-27)

| Strategy | Verdict | Notes |
|---|---|---|
| **EXPIRY_SCALPER** | ✅ **Real edge — the only one** | PF ~2.5 full-period, ~5.3 on the instrument-existent 2023-06+ slice. Concentrated: ~77% SENSEX, ~69% window W3. A per-window `enabled` flag lets us A/B dropping the weak W1 window. |
| BLACK_SWAN | 🟡 Marginal | PF ~1.2, +₹2.7L/7yr on modelled premiums — borderline; not a confident keeper. |
| BB_EXPIRY | ⛔ No edge | PF ~1.2, ~break-even; its old edge was overshoot. |
| GAP_FADE | ⛔ No edge | PF ~1.0; the fade signal is near coin-flip. Parked, paper-only. |
| NIFTY_INTRADAY | ⛔ No edge | −₹4.7L/7yr once honest. |
| RANGE_SCALPER | ⛔ No edge | PF ~0.3; also very low frequency. |
| VIX_SELLER | ⛔ No edge | PF ~0.2; structural loser (confirmed). |
| ATM_PULSE, RAMS, TREND_RIDER | ⚪ Forward-paper only | OI-dependent / too selective → 0 backtest trades. Evidence must come from live paper. |
| **PASHUPATASTRA** | 🟣 **Model only** | PF 1.84 is a *synthetic Monte-Carlo* with an ASSUMED signal skill (`filter_skill=0.50`). Its own summary.json calls it "a feasibility model, not a track record." Confirm by forward paper. |
| BRAHMASTRA | 🟣 Model only | Daily→intraday structural model (yfinance 2008-24). Not a real-data track record. |
| INRUSD | Thin (real data) | PF ~0.6–1.2 futures strategy; no options-overshoot risk, but weak edge. |
| SRAL | Archived | Intentional. |

**Bottom line: after auditing all 14, EXPIRY_SCALPER is the single validated
real-data edge.** Everything else is no-edge, marginal, model-only, or awaiting
forward-paper evidence.

## 5. The tooling that keeps it honest

- **`python main.py --mode audit`** → writes `logs/audit/strategy_audit_<date>.md`:
  a static code scan (overshoot clamp / real-VIX / phantom guard) + metric flags
  (thin edge, low frequency, concentration) + a grade and next action per strategy.
- **`GET /api/strategy-audit`** and the **Audit Desk** dashboard tab surface the same
  data live, and clearly label **MODEL_ONLY** runs so a feasibility PF is never shown
  next to a real edge.

## 6. What still needs YOU (needs Kite / real market — can't run in the sandbox)

1. **EXPIRY_SCALPER W1-off A/B:** set the W1 window `enabled: false` in its config and
   re-backtest; compare PF / P&L / Sharpe to decide whether to drop W1 permanently.
2. **Re-run the fixed no-edge strategies** if you want their exact honest numbers
   refreshed on your machine, then `python main.py --mode audit`.
3. **Forward-paper** ATM_PULSE / RAMS / TREND_RIDER / PASHUPATASTRA to see if their
   assumed/OI-dependent edges materialise live.
