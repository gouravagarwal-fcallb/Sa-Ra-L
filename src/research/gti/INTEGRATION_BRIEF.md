# Sa-Ra-L Integration Brief — GTI Demand/Supply Zone Engine

Hand this file (plus `gti_zones.py`, `gti_backtest.py`, `kite_data.py`) to Claude
Code inside the Sa-Ra-L project. It describes what the three modules do and
exactly how to merge them natively rather than dropping them in as loose files.

---

## 1. What the three modules are

| File | Role | Depends on |
|---|---|---|
| `gti_zones.py` | Core detector. Turns OHLCV candles into scored demand/supply zones (Seiden-style RBR/DBD + order-block imbalance). Volume optional (spot vs futures). | numpy, pandas |
| `gti_backtest.py` | Event-driven, single-position backtest with a deliberately dumb baseline entry (limit@proximal, SL beyond distal, fixed R:R). Reports expectancy **bucketed by zone strength**. No lookahead. | numpy, pandas, `gti_zones` |
| `kite_data.py` | Chunked Kite historical fetcher: per-interval day-cap chunking, ~3 req/s throttle + 429 backoff, incremental CSV cache. | pandas, `gti_zones` (for the candle→DF converter) |

No live-order code. Nothing here touches funds. `kite_data` only reads history.

## 2. Public API surface (what the rest of Sa-Ra-L calls)

```python
# gti_zones.py
detect_zones(df, ZoneConfig()) -> list[Zone]
update_freshness(zones, df)            # marks tests / mitigation
active_zones(zones, ltp, max_distance_pct=1.0, min_strength=0.0) -> list[Zone]
# Zone fields: side, ztype, proximal, distal, strength, tests, mitigated, ...
# Zone.contains(price), Zone.as_dict()

# gti_backtest.py
run_backtest(df, ZoneConfig(), BacktestConfig()) -> BacktestResult
print_report(result); trades_to_df(result) -> pd.DataFrame

# kite_data.py
fetch_history(kite, token, from_date, to_date, interval="5minute",
              continuous=False, cache_dir="./cache") -> pd.DataFrame
```

DataFrame contract everywhere: DatetimeIndex (IST) + columns
`open, high, low, close, volume` (volume may be all-zero for spot indices).

## 3. Merge checklist for Claude Code

**A. Placement.** Put the files where Sa-Ra-L keeps like code. Suggested if no
strong existing convention:
```
sa_ra_l/data/kite_data.py
sa_ra_l/strategies/gti_zones.py
sa_ra_l/backtest/gti_backtest.py
```
Fix the cross-imports to the chosen package paths (they currently import each
other by bare module name).

**B. De-duplicate utilities — REUSE Sa-Ra-L's, don't ship parallel copies:**
- `gti_zones.atr()` and `gti_backtest._atr_array()` — if Sa-Ra-L already has an
  ATR/indicator util, delete these and import the project's version.
- `gti_zones.candles_to_df()` / `fetch_kite_candles()` — if there's an existing
  Kite session wrapper or candle converter, route through it. `kite_data` already
  falls back gracefully, so point it at the canonical one.
- If Sa-Ra-L owns the authenticated `KiteConnect` instance centrally, have
  `fetch_history` accept that shared client (it already takes `kite` as an arg).

**C. Config reconciliation.** Fold `ZoneConfig` and `BacktestConfig` into
Sa-Ra-L's settings system (central config module / `.env` / YAML — match what
exists). Keep the dataclasses as the typed schema; source their defaults from
project config so they're tunable without code edits.

**D. Wire into execution + kill-switch.** This is the point of the merge:
- Add a thin strategy adapter that, each decision tick, calls
  `active_zones(zones, ltp)` and emits Sa-Ra-L's standard signal object
  (whatever the momentum/VWAP strategies already emit).
- Route that signal through the **existing kill-switch / risk manager** so
  zone entries are vetoed on the same rules as every other strategy.
- IMPORTANT: detect zones on **closed** candles only; the live/forming candle is
  partial. For live use, build candles from the WebSocket feed (Kite's historical
  API is backtest-only) and re-run `detect_zones` on the closed set, evaluating
  entries against the live LTP. Keep the detector out of the hot path — it's pure
  and cheap to re-run on bar close.

**E. Conventions.** Match Sa-Ra-L's logging (replace the `print()` calls in
`kite_data`/`gti_backtest` with the project logger), naming, type-hint style,
and error handling. Add to `requirements`/`pyproject` if `numpy`/`pandas`/
`kiteconnect` aren't already declared.

**F. Tests.** Both `gti_zones.py` and `kite_data.py` have `__main__`
self-tests (synthetic data + a mock Kite client). Port these into Sa-Ra-L's
test suite (`pytest`) — the mock-Kite test validates chunking/cache/gap logic
with no network.

## 4. Ready-to-paste prompt for Claude Code

> I've added `gti_zones.py`, `gti_backtest.py`, and `kite_data.py` to the repo,
> plus `INTEGRATION_BRIEF.md`. Read the brief, then integrate the three modules
> natively into Sa-Ra-L: place them in the right packages and fix imports;
> de-duplicate ATR / candle-conversion / Kite-session helpers against what
> already exists; fold ZoneConfig/BacktestConfig into our config system; add a
> strategy adapter that turns `active_zones()` output into our standard signal
> and routes it through the existing kill-switch; convert prints to our logger;
> and port the self-tests into pytest. Show me a plan and the diff before
> applying, and flag anything in Sa-Ra-L that already does part of this so we
> don't duplicate it.

---

### Two questions the merge will surface (answer for Claude Code)
1. **Spot or futures for backtesting?** Spot = clean price, no volume
   (range-expansion scoring). Futures = real volume but roll/basis quirks
   (`continuous=True` to stitch expired contracts).
2. **Where does the signal hand off?** Name the module/class the momentum & VWAP
   strategies emit into, so the zone adapter targets the same interface.
