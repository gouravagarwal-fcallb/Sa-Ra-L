"""
OI & positioning data logger  (READ-ONLY — no orders, no funds, ever)
─────────────────────────────────────────────────────────────────────
Captures and stores, as an append-only proprietary dataset:
  • option-chain snapshots (per strike/right, per timestamp)   — §3.1
  • derived chain metrics (PCR, Max-Pain, ATM IV, skew)        — §3.2
  • participant-wise OI (FII / DII / Pro / Client)             — §3.3

Deep history of institutional positioning cannot be bought, so we log it
ourselves starting now — every un-logged day is data we can never recover.

Sources
  • Chain + metrics: Kite (`kite.instruments()` + `kite.quote()`), reusing the
    platform's authenticated session and the GTI fetcher's rate-limit throttle.
  • Participant OI: the NSE daily `fao_participant_oi_DDMMYYYY.csv` (a separate
    download; NSE F&O only — BSE/SENSEX participant data is best-effort).

Strictly read-only: this module calls ONLY instruments()/quote()/ltp() and the
NSE CSV. No order/position/margin/fund endpoint appears anywhere.

Run (on a machine with a Kite token):
    python -m src.data.oi_logger --mode all         # self-scheduling daemon
    python -m src.data.oi_logger --mode intraday    # one snapshot (for cron)
    python -m src.data.oi_logger --mode eod         # one close snapshot
    python -m src.data.oi_logger --mode participant # evening participant fetch
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import pandas as pd

try:
    from src.utils.logger import setup_logger
    log = setup_logger("oi_logger")
except Exception:  # keep importable even if the project logger isn't available
    import logging
    log = logging.getLogger("oi_logger")

IST = timezone(timedelta(hours=5, minutes=30))

_SPOT_TOKEN = {"NIFTY": 256265, "SENSEX": 265}
_OPT_EXCHANGE = {"NIFTY": "NFO", "SENSEX": "BFO"}
_STRIKE_STEP = {"NIFTY": 50, "SENSEX": 100}
_RISK_FREE = 0.065

# Exact output schemas (§3) — column order is the contract.
CHAIN_COLS = ["ts", "index", "expiry", "strike", "right", "ltp", "oi", "oi_change",
              "volume", "iv", "bid", "ask", "underlying_ltp"]
METRIC_COLS = ["ts", "index", "expiry", "total_ce_oi", "total_pe_oi", "pcr_oi",
               "pcr_vol", "max_pain", "atm_strike", "atm_iv", "iv_skew", "underlying_ltp"]
PARTICIPANT_COLS = ["date", "client_type", "fut_idx_long", "fut_idx_short",
                    "opt_idx_call_long", "opt_idx_call_short",
                    "opt_idx_put_long", "opt_idx_put_short"]

CHAIN_KEYS = ["ts", "index", "expiry", "strike", "right"]
METRIC_KEYS = ["ts", "index", "expiry"]
PARTICIPANT_KEYS = ["date", "client_type"]

_DEFAULTS = {
    "indices": ["NIFTY", "SENSEX"],
    "strike_window": 10,
    "num_expiries": 2,
    "snapshot_interval_min": 5,
    "data_root": "data/oi",
    "participant_fetch_time": "18:00",
    "holidays": [],           # extra YYYY-MM-DD strings on top of the NSE calendar
}


# --------------------------------------------------------------------------- #
# Config / session / calendar
# --------------------------------------------------------------------------- #
def load_cfg() -> dict:
    """Merge the `oi_logger` section of settings over safe defaults."""
    cfg = dict(_DEFAULTS)
    try:
        from main import load_configs
        settings, _ = load_configs(None)
        cfg.update((settings or {}).get("oi_logger", {}) or {})
    except Exception as e:
        log.warning(f"Using default oi_logger config (settings load failed: {str(e)[:80]})")
    return cfg


def get_kite():
    """Authenticated KiteConnect via the platform's own broker (same ladder as
    the live app / GTI validate). Read-only usage only."""
    from main import load_configs
    from src.broker.kite_broker import create_kite_broker
    settings, _ = load_configs(None)
    return create_kite_broker(settings)._kite


def _now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)   # tz-naive IST (repo convention)


def is_session_day(d: date, extra_holidays: Optional[list] = None) -> bool:
    """Trading day per the NSE calendar (holidays lib) minus any config holidays."""
    if d.weekday() >= 5:
        return False
    if extra_holidays and d.isoformat() in set(extra_holidays):
        return False
    try:
        from src.utils.market_calendar import is_trading_day
        return is_trading_day(d)
    except Exception:
        return True   # degrade to "weekday = trading" if the calendar lib is absent


# --------------------------------------------------------------------------- #
# §3.1  Option universe + chain snapshot (Kite)
# --------------------------------------------------------------------------- #
def _spot_ltp(kite, index: str) -> float:
    token = _SPOT_TOKEN[index]
    try:
        data = kite.ltp([token])
        return float(data[str(token)]["last_price"])
    except Exception:
        data = kite.quote([token])
        return float(data[str(token)]["last_price"])


def build_option_universe(kite, index: str, strike_window: int = 10,
                          num_expiries: int = 2, spot: Optional[float] = None) -> list:
    """Option instruments to snapshot: ATM ± strike_window strikes, both CE & PE,
    for the nearest `num_expiries`. Uses the correct segment (NFO/BFO)."""
    exch = _OPT_EXCHANGE[index]
    step = _STRIKE_STEP[index]
    if spot is None:
        spot = _spot_ltp(kite, index)
    atm = round(spot / step) * step
    lo, hi = atm - strike_window * step, atm + strike_window * step

    instruments = kite.instruments(exch)
    today = _now_ist().date()
    opts = [i for i in instruments
            if str(i.get("name", "")).upper() == index
            and i.get("instrument_type") in ("CE", "PE")
            and i.get("expiry")]

    def _exp_date(v):
        return v if isinstance(v, date) else datetime.strptime(str(v)[:10], "%Y-%m-%d").date()

    expiries = sorted({_exp_date(i["expiry"]) for i in opts if _exp_date(i["expiry"]) >= today})
    keep_exp = set(expiries[:num_expiries])

    universe = [i for i in opts
                if _exp_date(i["expiry"]) in keep_exp
                and lo <= int(i.get("strike", 0)) <= hi]
    log.info(f"{index}: universe = {len(universe)} instruments "
             f"(ATM {atm}, ±{strike_window} strikes, {len(keep_exp)} expiries)")
    return universe


def _implied_vol(price: float, spot: float, strike: float, t_years: float, right: str) -> float:
    """Bisection IV from an option mid price (annualised, %). Best-effort."""
    if price <= 0 or spot <= 0 or t_years <= 0:
        return float("nan")
    try:
        from src.data.option_chain import bs_price
    except Exception:
        return float("nan")
    lo, hi = 0.001, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        val = bs_price(spot, strike, t_years, _RISK_FREE, mid, right)
        if abs(val - price) < 0.01:
            return round(mid * 100, 2)
        if val > price:
            hi = mid
        else:
            lo = mid
    return round(((lo + hi) / 2) * 100, 2)


def snapshot_option_chain(kite, index: str, universe: list, underlying_ltp: float,
                          prev_oi: Optional[dict] = None, throttle_sec: float = 0.34) -> pd.DataFrame:
    """One timestamped chain snapshot as a DataFrame with the §3.1 schema.

    `prev_oi` maps (expiry, strike, right) -> last OI so oi_change is the intraday
    delta since the previous snapshot (0 on the first snapshot of the day)."""
    from src.research.gti.kite_data import _Throttle
    throttle = _Throttle(throttle_sec)
    ts = _now_ist()
    prev_oi = prev_oi or {}

    rows = []
    # batch quote in chunks (Kite allows ~500 instruments/call; stay well under)
    keys = [f"{i['exchange']}:{i['tradingsymbol']}" for i in universe]
    by_key = {f"{i['exchange']}:{i['tradingsymbol']}": i for i in universe}
    for start in range(0, len(keys), 200):
        chunk = keys[start:start + 200]
        throttle.wait()
        try:
            quotes = kite.quote(chunk)
        except Exception as e:
            log.warning(f"{index}: quote batch failed ({str(e)[:80]}) — skipping {len(chunk)}")
            continue
        for k, q in quotes.items():
            inst = by_key.get(k)
            if not inst:
                continue
            strike = int(inst.get("strike", 0))
            right = inst.get("instrument_type")
            exp = inst["expiry"]
            exp_s = exp.isoformat() if isinstance(exp, date) else str(exp)[:10]
            ltp = float(q.get("last_price") or 0.0)
            oi = int(q.get("oi") or 0)
            vol = int(q.get("volume") or 0)
            depth = q.get("depth") or {}
            bid = float((depth.get("buy") or [{}])[0].get("price") or 0.0)
            ask = float((depth.get("sell") or [{}])[0].get("price") or 0.0)
            # time to expiry in years (calendar)
            try:
                exp_d = exp if isinstance(exp, date) else datetime.strptime(exp_s, "%Y-%m-%d").date()
                t_years = max((exp_d - ts.date()).days, 0) / 365.0 + (6.5 / 24 / 365)
            except Exception:
                t_years = 1 / 365
            mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else ltp
            iv = _implied_vol(mid, underlying_ltp, strike, t_years, right)
            pkey = (exp_s, strike, right)
            oi_change = oi - prev_oi.get(pkey, oi)
            rows.append({
                "ts": ts, "index": index, "expiry": exp_s, "strike": strike,
                "right": right, "ltp": ltp, "oi": oi, "oi_change": oi_change,
                "volume": vol, "iv": iv, "bid": bid, "ask": ask,
                "underlying_ltp": round(underlying_ltp, 2),
            })
    df = pd.DataFrame(rows, columns=CHAIN_COLS)
    log.info(f"{index}: snapshot {len(df)} rows @ {ts:%H:%M:%S}")
    return df


# --------------------------------------------------------------------------- #
# §3.2  Derived chain metrics
# --------------------------------------------------------------------------- #
def _max_pain(group: pd.DataFrame) -> int:
    """Strike that minimises total option-writer payout across the chain.
    At settlement S: call writers pay OI_ce·max(0,S−K); put writers pay
    OI_pe·max(0,K−S). Max-pain = argmin_S of the sum over all strikes."""
    strikes = sorted(group["strike"].unique())
    ce = group[group["right"] == "CE"].set_index("strike")["oi"].to_dict()
    pe = group[group["right"] == "PE"].set_index("strike")["oi"].to_dict()
    best_s, best_pay = strikes[0], None
    for s in strikes:
        pay = sum(oi * max(0, s - k) for k, oi in ce.items()) \
            + sum(oi * max(0, k - s) for k, oi in pe.items())
        if best_pay is None or pay < best_pay:
            best_pay, best_s = pay, s
    return int(best_s)


def compute_chain_metrics(chain_df: pd.DataFrame) -> pd.DataFrame:
    """Per (index, expiry): PCR(OI & vol), Max-Pain, ATM strike/IV and IV-skew.

    iv_skew is defined as ATM_put_IV − ATM_call_IV (positive = puts richer =
    downside fear priced in), using the strike nearest the underlying."""
    if chain_df.empty:
        return pd.DataFrame(columns=METRIC_COLS)
    out = []
    for (index, expiry), g in chain_df.groupby(["index", "expiry"]):
        ce, pe = g[g["right"] == "CE"], g[g["right"] == "PE"]
        total_ce_oi, total_pe_oi = int(ce["oi"].sum()), int(pe["oi"].sum())
        total_ce_vol, total_pe_vol = int(ce["volume"].sum()), int(pe["volume"].sum())
        underlying = float(g["underlying_ltp"].iloc[0])
        atm_strike = int(min(g["strike"].unique(), key=lambda k: abs(k - underlying)))
        atm_ce_iv = ce.loc[ce["strike"] == atm_strike, "iv"]
        atm_pe_iv = pe.loc[pe["strike"] == atm_strike, "iv"]
        ce_iv = float(atm_ce_iv.iloc[0]) if len(atm_ce_iv) else float("nan")
        pe_iv = float(atm_pe_iv.iloc[0]) if len(atm_pe_iv) else float("nan")
        # ATM IV = mean of the ATM call/put IVs that are present (NaN if neither).
        both = [v for v in (ce_iv, pe_iv) if v == v]   # v == v drops NaN
        atm_iv = round(sum(both) / len(both), 2) if both else float("nan")
        iv_skew = round(pe_iv - ce_iv, 2) if (ce_iv == ce_iv and pe_iv == pe_iv) else float("nan")
        out.append({
            "ts": g["ts"].iloc[0], "index": index, "expiry": expiry,
            "total_ce_oi": total_ce_oi, "total_pe_oi": total_pe_oi,
            "pcr_oi": round(total_pe_oi / total_ce_oi, 4) if total_ce_oi else float("nan"),
            "pcr_vol": round(total_pe_vol / total_ce_vol, 4) if total_ce_vol else float("nan"),
            "max_pain": _max_pain(g), "atm_strike": atm_strike,
            "atm_iv": atm_iv, "iv_skew": iv_skew, "underlying_ltp": round(underlying, 2),
        })
    return pd.DataFrame(out, columns=METRIC_COLS)


# --------------------------------------------------------------------------- #
# §3.3  Participant-wise OI (NSE daily CSV)
# --------------------------------------------------------------------------- #
_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "text/csv,application/csv,*/*",
    "Referer": "https://www.nseindia.com/all-reports",
}


def _participant_urls(d: date) -> list:
    ddmmyyyy = d.strftime("%d%m%Y")
    return [
        f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv",
        f"https://archives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv",
    ]


def parse_participant_csv(text: str, d: date) -> pd.DataFrame:
    """Parse the NSE participant-OI CSV text into the normalised §3.3 schema
    (keeps the raw columns too). The file has a 1-line title before the header."""
    import io
    lines = text.splitlines()
    hdr_idx = next((i for i, ln in enumerate(lines)
                    if "client type" in ln.lower() or "clienttype" in ln.lower()), 0)
    raw = pd.read_csv(io.StringIO("\n".join(lines[hdr_idx:])))
    raw.columns = [c.strip() for c in raw.columns]

    def col(*names):
        low = {c.lower().replace(" ", ""): c for c in raw.columns}
        for n in names:
            if n in low:
                return low[n]
        return None

    ct = col("clienttype", "participant")
    m = {
        "client_type": ct,
        "fut_idx_long": col("futureindexlong", "futidxlong"),
        "fut_idx_short": col("futureindexshort", "futidxshort"),
        "opt_idx_call_long": col("optionindexcalllong", "optidxcalllong"),
        "opt_idx_call_short": col("optionindexcallshort", "optidxcallshort"),
        "opt_idx_put_long": col("optionindexputlong", "optidxputlong"),
        "opt_idx_put_short": col("optionindexputshort", "optidxputshort"),
    }
    norm = pd.DataFrame()
    norm["date"] = [d.isoformat()] * len(raw)
    for k, src in m.items():
        norm[k] = raw[src] if src in raw.columns else None
    if ct:  # keep only the four standard client types, tidy names
        norm["client_type"] = norm["client_type"].astype(str).str.strip()
        norm = norm[norm["client_type"].str.upper().isin(
            ["FII", "DII", "PRO", "CLIENT", "TOTAL"])]
    # attach raw columns (prefixed) for full fidelity
    for c in raw.columns:
        norm[f"raw_{c.replace(' ', '_')}"] = raw[c].values[:len(norm)] if len(raw) >= len(norm) else None
    return norm.reset_index(drop=True)


def fetch_participant_oi(d: date, max_retries: int = 4) -> Optional[pd.DataFrame]:
    """Download + parse the NSE participant-OI CSV for date d. Returns None if
    unavailable (e.g. not yet published). NSE F&O only."""
    import requests
    sess = requests.Session()
    try:
        sess.get("https://www.nseindia.com/all-reports", headers=_NSE_HEADERS, timeout=10)
    except Exception:
        pass
    for url in _participant_urls(d):
        for attempt in range(1, max_retries + 1):
            try:
                r = sess.get(url, headers=_NSE_HEADERS, timeout=20)
                if r.status_code == 200 and "client" in r.text.lower():
                    return parse_participant_csv(r.text, d)
                log.info(f"participant OI {d}: HTTP {r.status_code} (attempt {attempt})")
            except Exception as e:
                log.info(f"participant OI {d}: {str(e)[:70]} (attempt {attempt})")
            time.sleep(min(2 ** attempt, 20))
    log.warning(f"participant OI for {d} not available yet.")
    return None


# --------------------------------------------------------------------------- #
# Storage — append-only, idempotent, Parquet (CSV fallback)
# --------------------------------------------------------------------------- #
def _partition_base(cfg: dict, kind: str, index: Optional[str], day: date) -> str:
    root = cfg.get("data_root", "data/oi")
    if index:
        return os.path.join(root, kind, index, day.isoformat())
    return os.path.join(root, kind, day.isoformat())


def _read_partition(base: str) -> Optional[pd.DataFrame]:
    for ext in (".parquet", ".csv"):
        p = base + ext
        if os.path.exists(p):
            try:
                return pd.read_parquet(p) if ext == ".parquet" else pd.read_csv(p)
            except Exception:
                continue
    return None


def write_partition(df: pd.DataFrame, base: str, key_cols: list) -> str:
    """Append df to the partition at `base`, dedupe on key_cols (keep last),
    write Parquet (fallback CSV). Idempotent: same rows twice → one set."""
    if df is None or df.empty:
        return ""
    os.makedirs(os.path.dirname(base), exist_ok=True)
    existing = _read_partition(base)
    combined = pd.concat([existing, df], ignore_index=True) if existing is not None else df
    combined = combined.drop_duplicates(subset=key_cols, keep="last").reset_index(drop=True)
    try:
        path = base + ".parquet"
        combined.to_parquet(path, index=False)
    except Exception as e:
        log.info(f"parquet unavailable ({str(e)[:50]}) — writing CSV")
        path = base + ".csv"
        combined.to_csv(path, index=False)
    return path


def _prev_oi_map(cfg: dict, index: str, day: date) -> dict:
    """Last logged OI per (expiry, strike, right) today → for oi_change deltas."""
    existing = _read_partition(_partition_base(cfg, "option_chain", index, day))
    if existing is None or existing.empty:
        return {}
    last = existing.sort_values("ts").drop_duplicates(
        subset=["expiry", "strike", "right"], keep="last")
    return {(r.expiry, int(r.strike), r.right): int(r.oi) for r in last.itertuples()}


# --------------------------------------------------------------------------- #
# Capture runners
# --------------------------------------------------------------------------- #
def run_snapshot(kite, cfg: dict) -> None:
    day = _now_ist().date()
    for index in cfg.get("indices", ["NIFTY"]):
        try:
            spot = _spot_ltp(kite, index)
            universe = build_option_universe(kite, index, cfg["strike_window"],
                                             cfg["num_expiries"], spot=spot)
            if not universe:
                log.warning(f"{index}: empty universe — skipping"); continue
            prev = _prev_oi_map(cfg, index, day)
            chain = snapshot_option_chain(kite, index, universe, spot, prev_oi=prev)
            if chain.empty:
                log.warning(f"{index}: empty snapshot — skipping"); continue
            metrics = compute_chain_metrics(chain)
            cp = write_partition(chain, _partition_base(cfg, "option_chain", index, day), CHAIN_KEYS)
            mp = write_partition(metrics, _partition_base(cfg, "metrics", index, day), METRIC_KEYS)
            log.info(f"{index}: wrote chain→{cp} metrics→{mp}")
        except Exception as e:                     # one index failing must not stop the other
            log.error(f"{index}: snapshot failed — {str(e)[:120]}")


def run_participant(cfg: dict, d: Optional[date] = None) -> None:
    d = d or _now_ist().date()
    df = fetch_participant_oi(d)
    if df is None or df.empty:
        return
    p = write_partition(df, _partition_base(cfg, "participant", None, d), PARTICIPANT_KEYS)
    log.info(f"participant OI {d}: wrote {len(df)} rows → {p}")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def _hm(s: str) -> tuple:
    h, m = s.split(":"); return int(h), int(m)


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only OI & positioning logger.")
    ap.add_argument("--mode", default="all", choices=["intraday", "eod", "participant", "all"])
    args = ap.parse_args()

    cfg = load_cfg()
    today = _now_ist().date()
    if not is_session_day(today, cfg.get("holidays")):
        log.warning(f"{today} is not a trading day — nothing to do.")
        return 0

    # Auth once; fail fast on a bad token (no retry storm).
    try:
        kite = get_kite()
    except Exception as e:
        log.error(f"Kite auth failed — run `python main.py --mode login`. ({str(e)[:100]})")
        return 1

    if args.mode == "participant":
        run_participant(cfg); return 0
    if args.mode in ("intraday", "eod"):
        run_snapshot(kite, cfg); return 0

    # --mode all : self-scheduling daemon (snapshots in-session, participant in evening)
    interval = int(cfg.get("snapshot_interval_min", 5)) * 60
    p_h, p_m = _hm(cfg.get("participant_fetch_time", "18:00"))
    done_participant = False
    log.info("oi_logger daemon started (--mode all). Ctrl-C to stop.")
    while True:
        now = _now_ist()
        if now.date() != today:                   # rolled past midnight
            today = now.date(); done_participant = False
            if not is_session_day(today, cfg.get("holidays")):
                time.sleep(1800); continue
        t = now.time()
        if (t.hour, t.minute) >= (9, 15) and (t.hour, t.minute) <= (15, 30):
            run_snapshot(kite, cfg)
            time.sleep(interval)
        elif (t.hour, t.minute) >= (p_h, p_m) and not done_participant:
            run_participant(cfg); done_participant = True
            time.sleep(interval)
        else:
            time.sleep(300)


if __name__ == "__main__":
    raise SystemExit(main())
