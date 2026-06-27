# PASHUPATASTRA — OI Recorder & Measurement Loop

The recorder is **step 1 of making the strategy real** (V2_REENGINEERED.md §2). The backtest
could only *assume* how often a trapped-seller break follows through (`filter_skill`). The
recorder + analyzer **measure it** from the live chain, so the edge becomes a number we earn,
not a knob we set.

```
  record_oi.py ──> oi_recorder.py ──> JSONL on disk ──> oi_analyze.py ──> EDGE TABLE
  (live NSE chain    (fetch + Seller-   (per-strike OI/     (did the candidate   (2x-hit-rate by
   every N sec)       Trap Score)        ΔOI/IV/LTP +        option hit 2x         trap-score bucket
                                         trap score)         before EOD?)          = measured edge)
```

## Components (all built, all tested)

| File | Role |
|---|---|
| `src/brahmastra/options/trap_score.py` | **Seller-Trap Score (0–100)** from a chain snapshot: naked wall size, ΔOI flip velocity (covering), activity, air pocket, IV cheapness, gamma/time, naked-vs-sticky. Returns side (CE/PE), the wall, and the **candidate option** to buy (just beyond the wall). |
| `src/brahmastra/options/oi_recorder.py` | `OIRecorder` — fetches the chain via the existing `OptionsIntelEngine`, scores it, and appends a full snapshot (aggregate + per-strike OI/ΔOI/IV/LTP) to JSONL. Robust to NSE fetch failures; market-hours loop. |
| `src/brahmastra/options/oi_analyze.py` | Reads the JSONL, finds trap fires, looks **forward in the same session** to measure whether the candidate option reached 2×, and prints the **edge table** (2×-hit-rate by score bucket). |
| `record_oi.py` | Runner. |
| `tests/test_oi_recorder.py` | End-to-end self-test on a synthetic squeeze (passes offline). |

## Run it (live)

```bash
python main.py --mode login          # fresh Kite/NSE session for the day (existing flow)
python record_oi.py                  # NIFTY, every 120s, during market hours
python record_oi.py --instruments NIFTY SENSEX --interval 60
python record_oi.py --once           # one snapshot (smoke test)
```

Storage (append-only, git-ignored): `data/oi_recordings/{INSTRUMENT}/{YYYY-MM-DD}.jsonl`
— one JSON object per fetch, with the ±20-strike window of OI/ΔOI/IV/LTP and the trap score.

## Real-time trap alerts (phone ping the moment a seller is trapped)

Both `record_oi.py` and `pashupatastra_shadow.py` send a **trap alert** the instant the
Seller-Trap Score crosses the threshold — so you can eyeball the live chain against the signal:

```
🟢⬆️ PASHUPATASTRA — 🎯 TRAP DETECTED
NIFTY  UP-squeeze (buy CALL)   [13:10]
Trap score : 82 / 100
Spot       : 25130
Wall       : 25100 CE  (x3.1 median OI, covering ✅)
Candidate  : BUY 25150 CE
```

- **Anti-spam:** one ping per (instrument, wall, side); it re-pings only after a cooldown
  (default 15 min) **or** when the score *escalates* by ≥8 (the squeeze intensifying).
- **Channels:** uses your existing `BrahmastraNotifier` — enable Telegram (or WhatsApp) in
  `config/settings.local.yaml` under `notifications:`; with none enabled it just logs.
- **Disable:** add `--no-alerts`. (`src/brahmastra/options/trap_alert.py` is the engine.)

```yaml
# config/settings.local.yaml
notifications:
  telegram:
    enabled: true
    bot_token: "<your bot token>"
    chat_id:   "<your chat id>"
```

## Measure the edge (after a few recorded expiries)

```bash
python -m src.brahmastra.options.oi_analyze data/oi_recordings/NIFTY --target 2.0
```

Output — the number the whole project hinges on:

```
EDGE TABLE — N day(s), target 2x, min_score 75
  score bucket | fires | 2x-hit-rate | avg peak mult
  ----------------------------------------------------
   70-79       |  ...  |    ...%     |   ...x
   80-100      |  ...  |    ...%     |   ...x
  => MEASURED 2x-hit-rate = the real 'filter_skill' for sizing.
```

## Calibration note (important)

The trap-score weights and the production threshold (default 75) are a **transparent v1, not
tuned.** The whole point is to set them *from the recorded data*: record → read the edge table →
keep the score buckets whose 2×-hit-rate is high → that becomes the live ARM threshold, and the
measured hit-rate feeds the quarter-Kelly sizing (V2 §3). Do **not** size up on the backtest's
assumed numbers; size up on *these* measured ones.

## Why this is the highest-leverage build

Every result in `RESULTS.md` carries one asterisk: *we assume the cleanest traps follow through
more.* This loop removes the asterisk. A few weeks of recordings tells us — cheaply, with zero
capital at risk — whether the edge is real, and exactly how selective to be. Only then do we
point `pashupatastra_live.py` at real money.
