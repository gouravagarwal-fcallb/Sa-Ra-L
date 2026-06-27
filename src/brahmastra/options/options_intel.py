"""
BRAHMASTRA Options Intelligence Engine — Layer 5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Provides four options-derived intelligence signals:

  1. PCR (Put-Call Ratio) — by open interest
       PCR < 0.8  → bearish (too many calls = market complacent)
       PCR 0.8–1.3 → neutral
       PCR > 1.3  → bullish (put hedging = smart money expects rally)

  2. Max Pain — strike where option writers lose least
       Market tends to expire near max pain at expiry.
       Price > max pain → bearish pressure; price < max pain → bullish support.

  3. IV Percentile — where current IV sits in the 52-week range
       < 25th pct  → LOW IV  — options cheap, good for buying
       25–75th pct → NORMAL IV
       > 75th pct  → HIGH IV — options expensive, prefer selling
       > 90th pct  → EXTREME IV — event-driven; avoid buying premium

  4. OI Buildup / Unwinding
       Significant call OI at a strike = resistance ceiling.
       Significant put OI at a strike = support floor.
       OI rising with price rising = bullish confirmation (PUT_BUILDUP).
       OI rising with price falling = bearish (CALL_BUILDUP).

Data source: NSE public JSON endpoints (no API key required).
All fetches are non-blocking; errors return graceful defaults.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))

# NSE option chain endpoint (unofficial but publicly accessible)
_NSE_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
_NSE_HEADERS   = {
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept":      "application/json, text/plain, */*",
    "Referer":     "https://www.nseindia.com/option-chain",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class StrikeOI:
    strike:    int
    call_oi:   float
    put_oi:    float
    call_iv:   float   # implied volatility %
    put_iv:    float
    call_ltp:  float
    put_ltp:   float
    call_chg_oi: float  # change in OI from previous session
    put_chg_oi:  float


@dataclass
class OptionsSnapshot:
    instrument:    str
    expiry:        str               # "DDMMMYYYY" format
    spot_price:    float
    pcr:           float             # Put-Call Ratio by OI
    pcr_label:     str               # BULLISH | NEUTRAL | BEARISH
    max_pain:      Optional[int]
    iv_current:    Optional[float]   # ATM IV %
    iv_percentile: Optional[float]   # 0–100
    iv_label:      str               # LOW | NORMAL | HIGH | EXTREME
    oi_buildup:    str               # CALL_BUILDUP | PUT_BUILDUP | NEUTRAL
    oi_trend:      str               # OI trend description
    top_call_oi_strikes: list[int]   # Top 5 strikes by call OI (resistance)
    top_put_oi_strikes:  list[int]   # Top 5 strikes by put OI (support)
    strike_data:   list[StrikeOI]    = field(default_factory=list)
    fetched_at:    datetime          = field(default_factory=lambda: datetime.now(IST))
    error:         Optional[str]     = None

    def summary_line(self) -> str:
        pcr_arrow = "↑" if self.pcr_label == "BULLISH" else ("↓" if self.pcr_label == "BEARISH" else "→")
        return (
            f"PCR={self.pcr:.2f} {pcr_arrow} | "
            f"MaxPain={self.max_pain or '?'} | "
            f"IV={self.iv_current:.1f}% ({self.iv_label})" if self.iv_current else
            f"PCR={self.pcr:.2f} {pcr_arrow} | MaxPain={self.max_pain or '?'} | IV=?"
        )


class OptionsIntelEngine:
    """
    Fetches NSE option chain data and computes PCR, Max Pain, IV, OI signals.

    Usage:
        engine = OptionsIntelEngine()
        snap   = engine.get_snapshot("NIFTY", expiry="27JUN2025", spot=24500)
        if snap:
            scorer.set_options_intel(snap)
    """

    def __init__(self, iv_history_window: int = 52):
        self._iv_history: list[float] = []         # rolling 52-week weekly IV samples
        self._iv_window   = iv_history_window
        self._session     = None                    # requests.Session, lazy init
        self._last_fetch: Optional[float] = None
        self._fetch_interval = 300                  # re-fetch at most every 5 min
        self.last_error: Optional[str] = None       # surfaced for diagnostics

    def _warmup(self, session) -> None:
        """Prime NSE cookies: hit the homepage, then the option-chain page (NSE sets
        the cookies it later checks on the API only after you've visited the page)."""
        for url in ("https://www.nseindia.com", "https://www.nseindia.com/option-chain"):
            try:
                session.get(url, timeout=6)
            except Exception:
                pass

    def _get_session(self):
        if self._session is None:
            try:
                import requests
                self._session = requests.Session()
                self._session.headers.update(_NSE_HEADERS)
                self._warmup(self._session)
            except ImportError:
                self.last_error = "requests not installed (pip install requests)"
        return self._session

    def fetch_nse_chain(self, symbol: str) -> Optional[dict]:
        """Fetch raw NSE option chain JSON. Returns None on failure (sets last_error).
        Resilient to NSE bot-blocks (re-warms + retries) and antivirus/proxy TLS
        interception (retries with verify=False as a last resort)."""
        session = self._get_session()
        if session is None:
            return None
        url = _NSE_CHAIN_URL.format(symbol=symbol.upper())
        for attempt in range(2):
            try:
                resp = session.get(url, timeout=10)
                if resp.status_code in (401, 403):
                    self.last_error = f"NSE blocked (HTTP {resp.status_code})"
                    self._warmup(session)            # re-prime cookies and retry once
                    continue
                resp.raise_for_status()
                self.last_error = None
                return resp.json()
            except Exception as e:
                name = type(e).__name__
                if ("SSL" in name or "CERTIFICATE" in str(e).upper()) and attempt == 0:
                    # antivirus/proxy TLS interception -> retry without verification
                    try:
                        import urllib3
                        urllib3.disable_warnings()
                    except Exception:
                        pass
                    try:
                        resp = session.get(url, timeout=10, verify=False)
                        resp.raise_for_status()
                        self.last_error = ("SSL bypassed (AV/proxy interception) — "
                                           "for a clean fix: pip install truststore")
                        return resp.json()
                    except Exception as e2:
                        self.last_error = f"{type(e2).__name__}: {e2}"
                        return None
                self.last_error = f"{name}: {e}"
        return None

    def _parse_chain(self, data: dict, spot: float) -> list[StrikeOI]:
        """Parse NSE chain JSON → list of StrikeOI ordered by strike."""
        records: dict[int, StrikeOI] = {}
        try:
            for row in data.get("records", {}).get("data", []):
                strike = int(row.get("strikePrice", 0))
                if strike == 0:
                    continue
                ce = row.get("CE", {}) or {}
                pe = row.get("PE", {}) or {}
                records[strike] = StrikeOI(
                    strike      = strike,
                    call_oi     = float(ce.get("openInterest",    0)),
                    put_oi      = float(pe.get("openInterest",    0)),
                    call_iv     = float(ce.get("impliedVolatility", 0)),
                    put_iv      = float(pe.get("impliedVolatility", 0)),
                    call_ltp    = float(ce.get("lastPrice", 0)),
                    put_ltp     = float(pe.get("lastPrice", 0)),
                    call_chg_oi = float(ce.get("changeinOpenInterest", 0)),
                    put_chg_oi  = float(pe.get("changeinOpenInterest", 0)),
                )
        except Exception:
            pass
        return sorted(records.values(), key=lambda x: x.strike)

    def compute_pcr(self, strikes: list[StrikeOI]) -> float:
        """PCR = total put OI / total call OI."""
        total_call = sum(s.call_oi for s in strikes)
        total_put  = sum(s.put_oi  for s in strikes)
        if total_call <= 0:
            return 1.0
        return round(total_put / total_call, 3)

    def compute_max_pain(self, strikes: list[StrikeOI]) -> Optional[int]:
        """
        Max Pain: expiry price where total option writer loss is minimised.
        For each candidate strike price, compute total payout to buyers if
        expiry is exactly at that strike.
        """
        if not strikes:
            return None
        min_pain_loss = float("inf")
        max_pain_strike = None
        for candidate in strikes:
            s = candidate.strike
            loss = 0.0
            for sk in strikes:
                # Call writers pay max(S - sk.strike, 0) per call OI unit
                loss += sk.call_oi * max(s - sk.strike, 0)
                # Put writers pay max(sk.strike - S, 0) per put OI unit
                loss += sk.put_oi  * max(sk.strike - s, 0)
            if loss < min_pain_loss:
                min_pain_loss      = loss
                max_pain_strike = s
        return max_pain_strike

    def compute_atm_iv(self, strikes: list[StrikeOI], spot: float) -> Optional[float]:
        """Average IV of the ATM call and put (nearest strike to spot)."""
        if not strikes:
            return None
        nearest = min(strikes, key=lambda x: abs(x.strike - spot))
        ivs = [v for v in (nearest.call_iv, nearest.put_iv) if v > 0]
        if not ivs:
            return None
        return round(sum(ivs) / len(ivs), 2)

    def compute_iv_percentile(self, current_iv: float) -> float:
        """
        Returns 0–100 indicating where current_iv sits in the historical range.
        Updates rolling IV history.
        """
        self._iv_history.append(current_iv)
        if len(self._iv_history) > self._iv_window * 4:  # weekly → 4 obs/month * 52w
            self._iv_history.pop(0)
        if len(self._iv_history) < 5:
            return 50.0  # not enough history
        lo = min(self._iv_history)
        hi = max(self._iv_history)
        if hi <= lo:
            return 50.0
        return round((current_iv - lo) / (hi - lo) * 100, 1)

    def _iv_label(self, pct: float) -> str:
        if pct >= 90:
            return "EXTREME"
        if pct >= 75:
            return "HIGH"
        if pct >= 25:
            return "NORMAL"
        return "LOW"

    def compute_oi_buildup(self, strikes: list[StrikeOI], spot: float) -> str:
        """
        Detect dominant OI buildup around the current spot ± 2 strikes.
        PUT_BUILDUP  → bulls defending support
        CALL_BUILDUP → bears defending resistance
        NEUTRAL      → no clear signal
        """
        if not strikes:
            return "NEUTRAL"
        # Focus on ±3 ATM strikes
        atm = min(strikes, key=lambda x: abs(x.strike - spot)).strike
        band = [s for s in strikes if abs(s.strike - atm) <= 200]  # ±200 pt NIFTY

        call_chg = sum(s.call_chg_oi for s in band)
        put_chg  = sum(s.put_chg_oi  for s in band)

        if abs(call_chg) < 1000 and abs(put_chg) < 1000:
            return "NEUTRAL"

        ratio = (put_chg - call_chg) / (abs(put_chg) + abs(call_chg) + 1)
        if ratio > 0.3:
            return "PUT_BUILDUP"    # more puts being added → support
        if ratio < -0.3:
            return "CALL_BUILDUP"   # more calls being added → resistance
        return "NEUTRAL"

    def get_snapshot(
        self,
        symbol:  str,
        expiry:  str = "",
        spot:    float = 0.0,
        force:   bool = False,
    ) -> Optional[OptionsSnapshot]:
        """
        Fetch and compute a full OptionsSnapshot.
        Returns None if NSE is unreachable or parsing fails.
        Caches for _fetch_interval seconds to avoid hammering NSE.
        """
        now_mono = time.monotonic()
        if (not force and self._last_fetch is not None and
                (now_mono - self._last_fetch) < self._fetch_interval):
            return None  # Rate-limited — caller keeps last snapshot

        self._last_fetch = now_mono
        data = self.fetch_nse_chain(symbol)
        if data is None:
            return OptionsSnapshot(
                instrument=symbol, expiry=expiry, spot_price=spot,
                pcr=1.0, pcr_label="NEUTRAL", max_pain=None,
                iv_current=None, iv_percentile=None,
                iv_label="NORMAL", oi_buildup="NEUTRAL", oi_trend="UNKNOWN",
                top_call_oi_strikes=[], top_put_oi_strikes=[],
                error=f"NSE fetch failed: {self.last_error}",
            )

        try:
            if spot <= 0:
                spot = float(data.get("records", {}).get("underlyingValue", 0) or 0)
        except Exception:
            pass

        strikes = self._parse_chain(data, spot)
        if not strikes:
            return None

        pcr        = self.compute_pcr(strikes)
        max_pain   = self.compute_max_pain(strikes)
        atm_iv     = self.compute_atm_iv(strikes, spot)
        iv_pct     = self.compute_iv_percentile(atm_iv) if atm_iv else None
        oi_buildup = self.compute_oi_buildup(strikes, spot)

        pcr_label = ("BULLISH" if pcr > 1.3 else
                     "BEARISH" if pcr < 0.8 else "NEUTRAL")

        # Top-5 OI strikes
        top_calls = sorted(strikes, key=lambda x: x.call_oi, reverse=True)[:5]
        top_puts  = sorted(strikes, key=lambda x: x.put_oi,  reverse=True)[:5]

        oi_trend = (
            "CALL OI dominant" if oi_buildup == "CALL_BUILDUP" else
            "PUT OI dominant"  if oi_buildup == "PUT_BUILDUP"  else
            "Balanced OI"
        )

        return OptionsSnapshot(
            instrument           = symbol,
            expiry               = expiry,
            spot_price           = spot,
            pcr                  = pcr,
            pcr_label            = pcr_label,
            max_pain             = max_pain,
            iv_current           = atm_iv,
            iv_percentile        = iv_pct,
            iv_label             = self._iv_label(iv_pct) if iv_pct is not None else "NORMAL",
            oi_buildup           = oi_buildup,
            oi_trend             = oi_trend,
            top_call_oi_strikes  = [s.strike for s in top_calls],
            top_put_oi_strikes   = [s.strike for s in top_puts],
            strike_data          = strikes,
        )
