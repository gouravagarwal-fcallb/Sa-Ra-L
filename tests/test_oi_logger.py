"""
Unit tests for the read-only OI & positioning logger (src/data/oi_logger.py).

The two numbers that MUST be right (they drive every downstream read of the
dataset) are PCR and Max-Pain — both are asserted here against a small chain
whose expected values are computed by hand in the comments, not by re-running
the same code.  Also covered: the exact §3 schemas, idempotent (dedupe) writes,
the participant-CSV parser, and the trading-calendar skip.

No network and no Kite token are needed: Kite is a tiny fake object.

Run:  python -m pytest tests/test_oi_logger.py -q
"""
from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from src.data import oi_logger as OL


# --------------------------------------------------------------------------- #
# A hand-built 3-strike chain with values chosen so PCR / Max-Pain / skew are
# unambiguous and easy to verify by eye.
#
#   underlying_ltp = 110  → ATM strike = 110
#
#   strike   CE_oi  PE_oi   CE_vol PE_vol   CE_iv PE_iv
#   100       100    400      10     50       -     -
#   110       200    200      20     40      15.0  17.0   (ATM)
#   120       400    100      30     30       -     -
#
#   total_ce_oi = 700 ,  total_pe_oi = 700  → PCR_oi = 700/700 = 1.0
#   total_ce_vol = 60 ,  total_pe_vol = 120 → PCR_vol = 120/60 = 2.0
#
#   Max-pain (writer payout at settlement S, summed over strikes):
#     S=100 → CE 0            + PE (200*10 + 100*20)      = 4000
#     S=110 → CE (100*10)     + PE (100*10)               = 2000   ← minimum
#     S=120 → CE (100*20+200*10)+ PE 0                     = 4000
#   → Max-Pain = 110
#
#   atm_iv  = mean(15.0, 17.0) = 16.0
#   iv_skew = PE_iv - CE_iv    = 17.0 - 15.0 = 2.0
# --------------------------------------------------------------------------- #
EXPIRY = "2026-07-09"


def _sample_chain() -> pd.DataFrame:
    rows = [
        # strike, right, oi, vol, iv
        (100, "CE", 100, 10, float("nan")),
        (110, "CE", 200, 20, 15.0),
        (120, "CE", 400, 30, float("nan")),
        (100, "PE", 400, 50, float("nan")),
        (110, "PE", 200, 40, 17.0),
        (120, "PE", 100, 30, float("nan")),
    ]
    recs = []
    for strike, right, oi, vol, iv in rows:
        recs.append({
            "ts": "2026-07-02 10:15:00", "index": "NIFTY", "expiry": EXPIRY,
            "strike": strike, "right": right, "ltp": 5.0, "oi": oi,
            "oi_change": 0, "volume": vol, "iv": iv, "bid": 4.9, "ask": 5.1,
            "underlying_ltp": 110.0,
        })
    return pd.DataFrame(recs, columns=OL.CHAIN_COLS)


# --------------------------------------------------------------------------- #
# Max-Pain — the core institutional metric
# --------------------------------------------------------------------------- #
def test_max_pain_hand_computed():
    assert OL._max_pain(_sample_chain()) == 110


def test_max_pain_shifts_with_oi():
    """If put OI piles onto the 120 strike, writers hurt more as price falls,
    so max-pain (min-pain-for-writers) must move UP toward 120."""
    chain = _sample_chain()
    chain.loc[(chain.strike == 120) & (chain.right == "PE"), "oi"] = 100000
    assert OL._max_pain(chain) == 120


# --------------------------------------------------------------------------- #
# Derived metrics — PCR, ATM IV, skew, schema
# --------------------------------------------------------------------------- #
def test_chain_metrics_values():
    m = OL.compute_chain_metrics(_sample_chain())
    assert list(m.columns) == OL.METRIC_COLS          # exact §3.2 schema
    assert len(m) == 1
    r = m.iloc[0]
    assert r["total_ce_oi"] == 700
    assert r["total_pe_oi"] == 700
    assert r["pcr_oi"] == pytest.approx(1.0)
    assert r["pcr_vol"] == pytest.approx(2.0)
    assert r["max_pain"] == 110
    assert r["atm_strike"] == 110
    assert r["atm_iv"] == pytest.approx(16.0)
    assert r["iv_skew"] == pytest.approx(2.0)


def test_chain_metrics_empty():
    m = OL.compute_chain_metrics(pd.DataFrame(columns=OL.CHAIN_COLS))
    assert m.empty
    assert list(m.columns) == OL.METRIC_COLS


def test_pcr_zero_ce_is_nan_not_crash():
    chain = _sample_chain()
    chain.loc[chain.right == "CE", "oi"] = 0
    r = OL.compute_chain_metrics(chain).iloc[0]
    assert math.isnan(r["pcr_oi"])          # divide-by-zero → NaN, no exception


# --------------------------------------------------------------------------- #
# Idempotent storage — writing the same snapshot twice must not double rows
# --------------------------------------------------------------------------- #
def test_write_partition_idempotent(tmp_path):
    base = str(tmp_path / "part")
    chain = _sample_chain()
    p1 = OL.write_partition(chain, base, OL.CHAIN_KEYS)
    p2 = OL.write_partition(chain, base, OL.CHAIN_KEYS)   # same rows again
    assert p1 == p2
    back = OL._read_partition(base)
    assert len(back) == len(chain)                        # deduped, not 2x


def test_write_partition_updates_on_key(tmp_path):
    base = str(tmp_path / "part")
    chain = _sample_chain()
    OL.write_partition(chain, base, OL.CHAIN_KEYS)
    bumped = chain.copy()
    bumped["oi"] = bumped["oi"] + 5          # same keys, new OI → keep=last
    OL.write_partition(bumped, base, OL.CHAIN_KEYS)
    back = OL._read_partition(base).sort_values(["strike", "right"]).reset_index(drop=True)
    assert len(back) == len(chain)
    assert int(back.loc[(back.strike == 100) & (back.right == "CE"), "oi"].iloc[0]) == 105


def test_write_partition_empty_is_noop(tmp_path):
    base = str(tmp_path / "part")
    assert OL.write_partition(pd.DataFrame(columns=OL.CHAIN_COLS), base, OL.CHAIN_KEYS) == ""
    assert OL._read_partition(base) is None


# --------------------------------------------------------------------------- #
# Trading-calendar skip
# --------------------------------------------------------------------------- #
def test_weekend_is_not_a_session_day():
    assert OL.is_session_day(date(2026, 7, 4)) is False   # Saturday
    assert OL.is_session_day(date(2026, 7, 5)) is False   # Sunday


def test_config_holiday_skips():
    # a Thursday, explicitly listed as a config holiday (checked before the
    # calendar lib, so this holds regardless of the NSE calendar's opinion)
    assert OL.is_session_day(date(2026, 7, 2), extra_holidays=["2026-07-02"]) is False


# --------------------------------------------------------------------------- #
# Participant-OI CSV parser
# --------------------------------------------------------------------------- #
_PARTICIPANT_CSV = (
    "Open Interest as on 02-Jul-2026\n"
    "Client Type,Future Index Long,Future Index Short,"
    "Option Index Call Long,Option Index Call Short,"
    "Option Index Put Long,Option Index Put Short\n"
    "Client,1000,1200,500,300,400,600\n"
    "DII,50,20,0,0,0,0\n"
    "FII,800,900,300,400,600,300\n"
    "Pro,200,150,100,120,90,80\n"
    "TOTAL,2050,2270,900,820,1090,980\n"
)


def test_parse_participant_csv():
    df = OL.parse_participant_csv(_PARTICIPANT_CSV, date(2026, 7, 2))
    # normalised columns present, one row per standard client type
    for c in OL.PARTICIPANT_COLS:
        assert c in df.columns
    types = set(df["client_type"].str.upper())
    assert {"FII", "DII", "PRO", "CLIENT", "TOTAL"} <= types
    fii = df[df["client_type"].str.upper() == "FII"].iloc[0]
    assert int(fii["fut_idx_long"]) == 800
    assert int(fii["opt_idx_put_short"]) == 300
    assert (df["date"] == "2026-07-02").all()


# --------------------------------------------------------------------------- #
# Chain snapshot against a fake Kite (schema + oi_change delta), no network
# --------------------------------------------------------------------------- #
class _FakeKite:
    """Minimal read-only Kite stand-in: instruments() + quote() + ltp() only."""
    def __init__(self):
        self._instr = []
        for strike in (24000, 24050, 24100):
            for right in ("CE", "PE"):
                self._instr.append({
                    "name": "NIFTY", "instrument_type": right, "strike": strike,
                    "expiry": date(2026, 7, 9), "exchange": "NFO",
                    "tradingsymbol": f"NIFTY26709{strike}{right}",
                })

    def instruments(self, exch):
        return list(self._instr)

    def ltp(self, tokens):
        return {str(tokens[0]): {"last_price": 24050.0}}

    def quote(self, keys):
        out = {}
        for k in keys:
            inst = next(i for i in self._instr
                        if f"{i['exchange']}:{i['tradingsymbol']}" == k)
            out[k] = {
                "last_price": 100.0, "oi": 1000 + inst["strike"] % 100,
                "volume": 500,
                "depth": {"buy": [{"price": 99.0}], "sell": [{"price": 101.0}]},
            }
        return out


def test_snapshot_schema_and_oi_change():
    kite = _FakeKite()
    universe = OL.build_option_universe(kite, "NIFTY", strike_window=1,
                                        num_expiries=1, spot=24050.0)
    assert len(universe) == 6      # ATM ±1 strike × CE/PE = 3 strikes × 2

    df = OL.snapshot_option_chain(kite, "NIFTY", universe, 24050.0, throttle_sec=0.0)
    assert list(df.columns) == OL.CHAIN_COLS
    assert len(df) == 6
    assert (df["oi_change"] == 0).all()             # first snapshot → delta 0

    # a later snapshot with a prev_oi map → non-zero deltas
    prev = {(EXPIRY, int(r.strike), r.right): int(r.oi) - 7 for r in df.itertuples()}
    df2 = OL.snapshot_option_chain(kite, "NIFTY", universe, 24050.0,
                                   prev_oi=prev, throttle_sec=0.0)
    assert (df2["oi_change"] == 7).all()
