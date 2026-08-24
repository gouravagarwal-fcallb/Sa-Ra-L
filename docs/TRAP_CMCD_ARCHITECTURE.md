# TRAP_CMCD — Architecture & IT-Audit Compliance (structure-first)

**Scope:** Nifty 50 intraday option-buying tool implementing the CMCD trap
framework, built to the platform's real-money safety standards. This document
establishes the **security** and **non-repainting** protocols and the **Logic
Engine + Broker Interface** structure. The **execution layer is intentionally
built LAST**, after everything below is in place and signed off.

> **Auditor's note:** most controls already exist in Sa-Ra-L. This design *maps to*
> them rather than duplicating them; only genuine gaps were scaffolded new.

---

## 1. Layered architecture (dependencies point downward; execution is last)

```
┌──────────────────────────────────────────────────────────────────┐
│  L5  EXECUTION LAYER            ← BUILT LAST (not yet wired)        │
│      order routing · fill/slippage capture · square-off            │
├──────────────────────────────────────────────────────────────────┤
│  L4  BROKER INTERFACE          src/broker/base.py (BaseBroker)     │
│      + src/security/safe_broker.py (SafeBrokerGuard: kill/arm/size)│
├──────────────────────────────────────────────────────────────────┤
│  L3  LOGIC ENGINE              src/live/trap_cmcd_live.py          │
│      CMCD state machine · trap detection · zone/VWAP/squeeze       │
├──────────────────────────────────────────────────────────────────┤
│  L2  DATA & INTEGRITY          non-repainting zones · VWAP         │
│      src/research/gti/gti_zones.py · tests/test_non_repainting.py  │
├──────────────────────────────────────────────────────────────────┤
│  L1  SECURITY                  src/security/vault.py (no hardcoded │
│      secrets) · audit_log.py (SYSTEM/DATA/EXECUTION channels)      │
└──────────────────────────────────────────────────────────────────┘
```

A strategy in L3 may only reach the market through L4's `SafeBrokerGuard`, which
in turn only obtains credentials through L1's vault. There is no path from L3 to a
raw broker or a raw secret.

---

## 2. Audit compliance map

| Control | Requirement | Status | Where |
|---|---|---|---|
| **CRED-1** | No hardcoded API keys; encrypted `.env`/secrets manager | **NEW** | `src/security/vault.py` (env→keyring→Fernet-enc→dotenv ladder); existing gitignored `settings.local.yaml`, `config/.kite_token` |
| **INTEGRITY-1** | Prove non-repainting (zones/VWAP static after close) | **NEW ✓ passing** | `tests/test_non_repainting.py` (tests real `detect_zones`) |
| **LOG-1** | Structured logs split System / Data / Execution | **NEW** | `src/security/audit_log.py` |
| **KILL-1** | Heartbeat → Emergency Kill Switch + Telegram | **PARTIAL** | STOP-ALL exists (`runner`, frontend); heartbeat→auto-kill+notify **deferred** (see §7) |
| **EXEC-1** | Human-in-the-loop arm/confirm before live | **EXISTS** | `POST /api/strategy/{name}/arm-live`→`confirm-live`, phrase `GO LIVE {name}`, 56s single-use token (`src/api/server.py`) |
| **RISK-1** | Hard 10%-capital cap per trade | **EXISTS + enforced at gate** | `trap_cmcd` config + `SafeBrokerGuard` re-asserts the cap |
| **RISK-2** | Ratcheting trail: BE at +25, ride to opposite zone | **EXISTS** | `trap_cmcd_live._monitor` (35 SL → 25 min → 20-pt ratchet → zone-to-zone) |
| **RISK-3** | Hard square-off 15:10 IST; daily max-loss lock | **EXISTS** | `trap_cmcd_live` (`hard_close_time`, `daily_loss_lock_rs`) |
| **COMPLIANCE-1** | SEBI-style educational/paper disclaimer in UI | **TEXT READY** | see §8 — embed on dashboard footer |
| **MODEL-1** | Label Volume Footprint a "Proxy" (needs L2 tick data) | **DONE** | RULEBOOK + engine comments + UI label task |
| **BT-1** | High-fidelity backtest: slippage 0.5–1% + brokerage + STT, real 3m data | **DEFERRED to L5** | needs Nifty **futures** 3m (spot has no volume) — see §7 |
| **VETO-1** | Multi-TF veto: kill 3m signal into fresh Daily/Weekly opposing zone | **DEFERRED** | extend `confluence_ab` to D/W (see §7) |

---

## 3. Logic Engine structure (L3)

The engine is a **pull-based state machine** (mirrors the platform's live-engine
contract: `__init__(strategy_config, broker, mode, status_callback)` + a `run()`
loop that checks `self._stop_event`). Concrete impl: `src/live/trap_cmcd_live.py`.

```python
class Phase(Enum): COMPRESSION; ACCUMULATION; MANIPULATION; CORRECTION; DISTRIBUTION

class TrapLogicEngine(Protocol):
    # ── L2 inputs (all non-repainting: closed bars only) ──
    def zones(self, df) -> list[Zone]:        ...  # Rank-1 Volume Profile (detect_zones, fresh-gated)
    def golden_line(self, df) -> float:       ...  # VWAP magnet + stretch(ATR)
    def compression(self, df) -> bool:        ...  # Bollinger-in-Keltner squeeze
    def candle(self, df) -> str:              ...  # Blue/Black/Yellow/Neutral

    # ── state + signal ──
    def advance_phase(self, state) -> Phase:  ...  # C→A→M→C→D transitions
    def detect_trap(self, df, zones, vwap) -> Signal | None:
        # Whale 'W' (CE): >=2 Black candles fail-low in a FRESH demand zone + reversal
        # Whale 'M' (PE): mirror in a FRESH supply zone; reject clean breakouts
        ...
```

**Indicator hierarchy (as specified):** Rank-1 Volume-Profile zones gate location;
VWAP gates mean-reversion direction; Bollinger squeeze gates the Compression phase.
Freshness (`zone.tests==0`), **not** strength, is the quality cue (platform-verified).

---

## 4. Broker Interface structure (L4)

**Contract (transport):** `src/broker/base.py::BaseBroker` —
`place_order(Order)->id`, `get_ltp`, `get_positions`, `get_order_status`,
`cancel_order`. Implementations: `PaperBroker`, `KiteBroker`.

**Audited gate (the only object a strategy is handed):**
`src/security/safe_broker.py::SafeBrokerGuard.place(order, *, mode, strategy,
intended_price, arm_token)` enforces, in order:

1. **KILL-1** — kill-switch blocks new **BUY** entries; **SELL** exits always allowed (must be able to flatten).
2. **EXEC-1** — a *live* entry needs a valid single-use arm token (human-in-the-loop).
3. **RISK-1** — asserts `cost ≤ 10% × capital` at the gate (defence in depth).
4. **LOG-1** — writes INTENT → order_id → FILLED + **slippage** to the EXECUTION channel.

The guard consults a `RiskGate` protocol (`kill_switch_active`, `is_armed`,
`capital_for`) wired by the runner — it owns no state and contains no strategy logic.

---

## 5. Security protocol (L1) — CRED-1

`vault.get_secret(name)` resolves through **env → OS keyring → Fernet-encrypted
`config/secrets.env.enc` → plaintext `.env` (dev, warns)**. Secret *values* are
never logged (only name + source + `****last4`). `vault.audit_report([...])`
produces a values-free snapshot of where each secret resolves — hand this to the
auditor. `encrypt_env()` converts a dev `.env` into the encrypted vault file; the
Fernet key lives only in env/keyring, never in git.

---

## 6. Non-repainting protocol (L2) — INTEGRITY-1  ✓

`tests/test_non_repainting.py` proves, against the **real** `detect_zones`:
- a zone computed at its `confirmed_index` is **bit-identical** when recomputed
  with 70+ future bars added;
- session VWAP for every bar ≤ k is **prefix-invariant**;
- a closed candle's colour never changes.

`3 passed`. This is the gate that must stay green before any execution work.

---

## 7. Deferred to the execution layer (L5 — built last, on sign-off)

1. **BT-1 high-fidelity backtest** — run the trap logic on **real Nifty *futures*
   3-minute** bars (spot index carries no volume, which the candle model needs),
   BS-priced options via `OptionPricer`, with **slippage 0.5–1% + brokerage + STT**.
   Writes a correct `TRAP_CMCD_v1/results/summary.json` (the current one is a
   default-engine artifact — see the diagnosis note).
2. **KILL-1 heartbeat** — a watchdog that trips the kill switch + Telegrams the
   operator when the Kite feed/heartbeat goes stale.
3. **VETO-1 multi-TF veto** — extend `src/research/gti/confluence_ab.py` to veto a
   3m signal that fires into a fresh **Daily/Weekly** opposing zone.

---

## 8. SEBI-style disclaimer (embed in dashboard footer) — COMPLIANCE-1

> **Educational / paper-trading use only.** This tool is a research and
> decision-support system. It is **not** investment advice and its operators are
> **not** SEBI-registered investment advisers or research analysts. Nothing here
> is a recommendation to buy or sell any security. Derivatives trading carries a
> high risk of loss, including loss exceeding the option premium. Backtested and
> paper results are hypothetical, use modelled prices and costs, and do not
> guarantee future results. Trade only your own capital, on your own decision.

---

## 9. Pine vs Python boundary (why the structure splits)

Pine Script (`pine/nifty_trap_bot.pine`) implements **L3 signal + visualization +
webhook alerts only**. It **cannot** hold secrets (CRED-1) or place broker orders
(L4/L5) — TradingView has no secure vault and no Indian-broker order path. So Pine
is the *analytics/alert* surface; the **Python** stack owns security, the broker
interface, and execution. Keeping this boundary explicit is itself an audit control.
