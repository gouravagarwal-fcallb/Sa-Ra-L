# OI & Positioning Data Logger

**What it is (plain words):** a small program that, every few minutes during
market hours, takes a photograph of the option chain — how much open interest
(OI) sits at each strike, the traded volume, an implied-vol estimate — plus the
end-of-day *participant* picture (how FIIs / DIIs / Pro / Client are positioned).
It saves each photograph to disk as your own private dataset.

**Why we bother:** nobody sells you *historical* institutional positioning. If we
don't record it ourselves, that day is gone forever. Start logging now and in a
few months you own a dataset you could never have bought — the raw material for a
regime classifier later (Task 2, not built yet).

**The one rule:** this module is **strictly read-only**. It calls only Kite's
`instruments()`, `quote()`, and `ltp()`, plus a public NSE CSV. It never places,
modifies, or cancels an order, and never touches funds, margins, positions, or
holdings. (Proven by `grep` in `tests/` and in the code review.)

---

## What it captures (§3 schema)

Three datasets, one folder each under `data/oi/`:

### 1. `option_chain/{INDEX}/{YYYY-MM-DD}` — one row per strike/right/snapshot
`ts, index, expiry, strike, right, ltp, oi, oi_change, volume, iv, bid, ask, underlying_ltp`

- `oi_change` = OI now minus OI at the previous snapshot **today** (0 on the first
  snapshot of the day).
- `iv` = implied volatility (%), inverted from the option's mid price by bisection
  on the platform's Black-Scholes (`src/data/option_chain.bs_price`). Best-effort.

### 2. `metrics/{INDEX}/{YYYY-MM-DD}` — one row per (index, expiry, snapshot)
`ts, index, expiry, total_ce_oi, total_pe_oi, pcr_oi, pcr_vol, max_pain, atm_strike, atm_iv, iv_skew, underlying_ltp`

- `pcr_oi` = total PE OI ÷ total CE OI (put-call ratio; >1 = more puts written).
- `max_pain` = the strike that minimises total option-writer payout at settlement
  (where the most option buyers "lose" — a classic pin magnet).
- `iv_skew` = ATM put IV − ATM call IV (positive = downside fear priced in).
- `atm_iv` = mean of the ATM call/put IVs present.

### 3. `participant/{YYYY-MM-DD}` — one row per client type (EOD)
`date, client_type, fut_idx_long, fut_idx_short, opt_idx_call_long, opt_idx_call_short, opt_idx_put_long, opt_idx_put_short`
(plus the raw NSE columns, prefixed `raw_`, for full fidelity).

- Source: NSE's daily `fao_participant_oi_DDMMYYYY.csv`. **NSE F&O only** — there is
  no equivalent BSE/SENSEX participant file, so SENSEX participant data is
  best-effort / absent.

**Storage is append-only and idempotent.** Re-running a snapshot writes to the same
day's partition and de-duplicates on the key columns (`ts,index,expiry,strike,right`
for the chain; `date,client_type` for participants) — so a crash-and-retry or an
overlapping cron never doubles your rows. Parquet is used when available, with an
automatic CSV fallback.

---

## Configuration

The logger reads an optional `oi_logger:` block from your **gitignored**
`settings.local.yaml` (same file the rest of the app uses; never commit it). If the
block is absent, the built-in defaults below apply.

```yaml
# --- paste into settings.local.yaml (NOT tracked in git) ---
oi_logger:
  indices: [NIFTY, SENSEX]      # SENSEX chain works; SENSEX participant data does not exist
  strike_window: 10             # ATM ± this many strikes per side
  num_expiries: 2               # nearest N expiries
  snapshot_interval_min: 5      # minutes between intraday snapshots
  data_root: data/oi            # where the partitions land
  participant_fetch_time: "18:00"   # when --mode all fetches the NSE CSV (IST)
  holidays: []                  # extra "YYYY-MM-DD" holidays on top of the NSE calendar
```

The Kite session is the **same** authenticated broker the live app uses — no extra
credentials. If the token is stale you'll get a clear "run `python main.py --mode
login`" message and the logger exits without a retry storm.

---

## Running it

All commands run on a machine that has a valid Kite token (the sandbox has no
network — run these on your laptop).

```bash
# Self-scheduling daemon: snapshots 09:15–15:30, participant CSV at 18:00.
python -m src.data.oi_logger --mode all

# One-shot snapshot (for an external scheduler — cron / Task Scheduler):
python -m src.data.oi_logger --mode intraday

# One close-of-day snapshot:
python -m src.data.oi_logger --mode eod

# Evening participant-OI fetch only:
python -m src.data.oi_logger --mode participant
```

On a non-trading day (weekend / NSE holiday / a `holidays:` entry) every mode
logs "not a trading day" and exits 0.

### Scheduling without the daemon

If you'd rather use the OS scheduler than leave `--mode all` running:

**Linux / macOS cron** (snapshot every 5 min in-session, participant at 18:10):
```cron
*/5 9-15 * * 1-5   cd /path/to/Sa-Ra-L && python -m src.data.oi_logger --mode intraday >> logs/oi.log 2>&1
10  18   * * 1-5   cd /path/to/Sa-Ra-L && python -m src.data.oi_logger --mode participant >> logs/oi.log 2>&1
```

**Windows Task Scheduler** (two tasks):
```
# Intraday — trigger: daily, repeat every 5 min for 6.5 h starting 09:15, weekdays
schtasks /Create /TN "SaRaL OI intraday" /SC MINUTE /MO 5 /ST 09:15 /DU 0006:30 ^
  /TR "cmd /c cd /d C:\path\to\Sa-Ra-L && python -m src.data.oi_logger --mode intraday"

# Participant — trigger: daily 18:10, weekdays
schtasks /Create /TN "SaRaL OI participant" /SC DAILY /ST 18:10 ^
  /TR "cmd /c cd /d C:\path\to\Sa-Ra-L && python -m src.data.oi_logger --mode participant"
```
(The logger's own calendar check makes the weekend runs harmless no-ops, so a plain
weekday trigger is fine even if you don't restrict days.)

---

## Tests

```bash
python -m pytest tests/test_oi_logger.py -q
```

The suite uses a fake Kite (no network). It asserts **PCR** and **Max-Pain**
against a hand-computed 3-strike chain (the expected values are worked out in the
test's comments), plus the exact §3 schemas, idempotent de-duped writes, the
participant-CSV parser, and the trading-calendar skip.

---

## Not built yet (Task 2 — held)

A **regime classifier** (`src/strategies/regime.py`) that reads this dataset and
labels the day (e.g. risk-on / risk-off / short-covering / put-writing) is the
intended next step. It is deliberately **not** started until this logger has run
and its tests pass, so the classifier is built on real captured data, not guesses.
