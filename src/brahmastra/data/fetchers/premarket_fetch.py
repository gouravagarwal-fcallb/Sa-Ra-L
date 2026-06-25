"""
BRAHMASTRA Pre-Market Intelligence Fetcher
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs at 8:00 AM IST. Fetches all global and domestic data,
computes a BIAS SCORE (-100 to +100), and emits a pre-market briefing.

All sources are FREE — no paid subscriptions required.
Primary data: yfinance (Yahoo Finance) + NSE website.

Data fetched:
  Global: SGX Nifty proxy, US markets close, Asian markets,
          Crude Oil, Gold, USD/INR
  India:  VIX, NSE option chain (PCR), FII/DII provisional
"""

from __future__ import annotations

import json
import time
import datetime as _dt
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class GlobalSnapshot:
    """One data point from a global/local source."""
    name:        str
    symbol:      str
    price:       Optional[float]
    prev_close:  Optional[float]
    change_pct:  Optional[float]   # positive = up, negative = down
    source:      str
    fetched_at:  datetime = field(default_factory=lambda: datetime.now(IST))
    error:       Optional[str] = None

    @property
    def direction(self) -> str:
        if self.change_pct is None:
            return "UNKNOWN"
        if self.change_pct > 0.3:
            return "UP"
        if self.change_pct < -0.3:
            return "DOWN"
        return "FLAT"


@dataclass
class PreMarketBriefing:
    """Complete pre-market intelligence snapshot."""
    date:             _dt.date
    bias_score:       int          # -100 to +100
    bias_label:       str          # STRONGLY_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONGLY_BEARISH
    snapshots:        dict[str, GlobalSnapshot]
    india_vix:        Optional[float]
    vix_trend:        str          # RISING | FALLING | FLAT
    pcr:              Optional[float]
    pcr_label:        str          # BULLISH | NEUTRAL | BEARISH
    max_pain:         Optional[int]
    fii_net_cr:       Optional[float]   # FII net in crores (+ve = buying)
    high_risk_events: list[str]         # today's risk events
    score_breakdown:  dict[str, int]    # each factor's contribution
    computed_at:      datetime = field(default_factory=lambda: datetime.now(IST))

    def format_message(self) -> str:
        """Format for Telegram / Email notification."""
        sign  = "+" if self.bias_score >= 0 else ""
        arrow = "↑" if self.bias_score > 20 else "↓" if self.bias_score < -20 else "→"
        lines = [
            f"📊 BRAHMASTRA Pre-Market | {self.date.strftime('%d-%b-%Y')}",
            f"",
            f"BIAS SCORE: {sign}{self.bias_score} ({self.bias_label}) {arrow}",
            f"",
            f"Global Context:",
        ]
        for key, snap in self.snapshots.items():
            if snap.change_pct is not None:
                s = "+" if snap.change_pct >= 0 else ""
                lines.append(f"  {snap.name:<18}: {s}{snap.change_pct:.2f}%  {snap.direction}")
            else:
                lines.append(f"  {snap.name:<18}: data unavailable")
        lines += [
            f"",
            f"India Context:",
            f"  VIX          : {self.india_vix:.1f} ({self.vix_trend})" if self.india_vix else "  VIX          : unavailable",
            f"  PCR          : {self.pcr:.2f} → {self.pcr_label}" if self.pcr else "  PCR          : unavailable",
            f"  Max Pain     : {self.max_pain}" if self.max_pain else "  Max Pain     : unavailable",
            f"  FII Net      : +₹{self.fii_net_cr:.0f} Cr (buying)" if (self.fii_net_cr and self.fii_net_cr >= 0)
                               else f"  FII Net      : -₹{abs(self.fii_net_cr):.0f} Cr (selling)" if self.fii_net_cr
                               else "  FII Net      : unavailable",
        ]
        if self.high_risk_events:
            lines += ["", f"  ⚠️  RISK EVENTS TODAY: {', '.join(self.high_risk_events)}"]
        lines += [
            f"",
            f"Score Breakdown:",
        ]
        for factor, pts in self.score_breakdown.items():
            s = "+" if pts >= 0 else ""
            lines.append(f"  {factor:<20}: {s}{pts}")
        return "\n".join(lines)


def _fetch_yfinance(symbol: str, name: str) -> GlobalSnapshot:
    """Fetch latest close and previous close via yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="5d", interval="1d")
        if hist.empty or len(hist) < 2:
            return GlobalSnapshot(name=name, symbol=symbol,
                                  price=None, prev_close=None, change_pct=None,
                                  source="yfinance", error="insufficient data")
        price      = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        change_pct = (price - prev_close) / prev_close * 100
        return GlobalSnapshot(name=name, symbol=symbol, price=price,
                              prev_close=prev_close, change_pct=round(change_pct, 3),
                              source="yfinance")
    except Exception as e:
        return GlobalSnapshot(name=name, symbol=symbol,
                              price=None, prev_close=None, change_pct=None,
                              source="yfinance", error=str(e))


def _fetch_india_vix() -> tuple[Optional[float], str]:
    """Fetch India VIX from NSE. Returns (vix_value, trend_description)."""
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer":    "https://www.nseindia.com",
        }
        session = requests.Session()
        # First request to set cookies
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        time.sleep(0.5)
        r = session.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            vix = data.get("filtered", {}).get("vixClose", None)
            if vix:
                return float(vix), "UNKNOWN"
    except Exception:
        pass

    # Fallback: yfinance for VIX proxy
    try:
        import yfinance as yf
        vix_data = yf.Ticker("^INDIAVIX")
        hist = vix_data.history(period="2d", interval="1d")
        if not hist.empty:
            current = float(hist["Close"].iloc[-1])
            prev    = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current
            trend   = "RISING" if current > prev + 0.3 else "FALLING" if current < prev - 0.3 else "FLAT"
            return current, trend
    except Exception:
        pass

    return None, "UNKNOWN"


def _fetch_pcr_and_maxpain(instrument: str = "NIFTY") -> tuple[Optional[float], Optional[int]]:
    """Fetch PCR and Max Pain from NSE option chain."""
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer":    "https://www.nseindia.com",
            "Accept":     "application/json",
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        time.sleep(0.5)
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={instrument}"
        r = session.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            return None, None

        data = r.json()
        records = data.get("records", {}).get("data", [])
        if not records:
            return None, None

        total_ce_oi = 0
        total_pe_oi = 0
        pain_map: dict[int, float] = {}

        spot = data.get("records", {}).get("underlyingValue", 0)

        for rec in records:
            strike = rec.get("strikePrice", 0)
            ce_oi  = rec.get("CE", {}).get("openInterest", 0)
            pe_oi  = rec.get("PE", {}).get("openInterest", 0)
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi

            # Max Pain: for each strike, compute total ITM loss for all buyers
            # Simplified: pain at this strike = sum of (OI × intrinsic loss) across all strikes
            pain_map[strike] = 0  # will compute below

        # Max Pain computation
        strikes = sorted(pain_map.keys())
        min_pain = float("inf")
        max_pain_strike = None

        for target_strike in strikes:
            total_pain = 0.0
            for rec in records:
                s      = rec.get("strikePrice", 0)
                ce_oi  = rec.get("CE", {}).get("openInterest", 0) or 0
                pe_oi  = rec.get("PE", {}).get("openInterest", 0) or 0
                # CE buyer pain: if target > strike, CE buyers lose (target - strike) × OI
                if target_strike > s:
                    total_pain += (target_strike - s) * ce_oi
                # PE buyer pain: if target < strike, PE buyers lose (strike - target) × OI
                if target_strike < s:
                    total_pain += (s - target_strike) * pe_oi
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = target_strike

        pcr = (total_pe_oi / total_ce_oi) if total_ce_oi > 0 else None
        return (round(pcr, 2) if pcr else None), max_pain_strike

    except Exception:
        return None, None


def _fetch_fii_net() -> Optional[float]:
    """
    Attempt to fetch FII net from NSE.
    Returns FII net in crores (positive = buying, negative = selling).
    Falls back to None if unavailable (provisional data has limited availability).
    """
    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"}
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        time.sleep(0.3)
        r = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            # FII equity net value
            for entry in data:
                category = str(entry.get("category", "")).upper()
                if "FII" in category or "FPI" in category:
                    net = entry.get("netVal", None)
                    if net is not None:
                        return float(net) / 1e7   # convert to crores
    except Exception:
        pass
    return None


def _compute_bias(
    snapshots: dict[str, GlobalSnapshot],
    vix: Optional[float],
    vix_trend: str,
    pcr: Optional[float],
    fii_net: Optional[float],
    config: dict,
) -> tuple[int, dict[str, int]]:
    """
    Compute BIAS score from -100 to +100.
    Returns (total_score, breakdown_dict).
    """
    pm_cfg = config.get("premarket", {})
    score:     dict[str, int] = {}

    # ── SGX Nifty proxy (25 pts) ───────────────────────────────────
    sgx = snapshots.get("SGX_NIFTY") or snapshots.get("NIFTY_FUTURES")
    if sgx and sgx.change_pct is not None:
        w   = pm_cfg.get("sgx_nifty_weight", 25)
        pct = sgx.change_pct
        if   pct >  0.8: pts = w
        elif pct >  0.4: pts = int(w * 0.7)
        elif pct >  0.1: pts = int(w * 0.3)
        elif pct > -0.1: pts = 0
        elif pct > -0.4: pts = -int(w * 0.3)
        elif pct > -0.8: pts = -int(w * 0.7)
        else:            pts = -w
        score["SGX_Nifty"] = pts
    else:
        score["SGX_Nifty"] = 0

    # ── US markets close (20 pts) ─────────────────────────────────
    us_snap = snapshots.get("US_SPX") or snapshots.get("US_DOW")
    if us_snap and us_snap.change_pct is not None:
        w   = pm_cfg.get("us_markets_weight", 20)
        pct = us_snap.change_pct
        if   pct >  0.8: pts = w
        elif pct >  0.3: pts = int(w * 0.6)
        elif pct > -0.3: pts = 0
        elif pct > -0.8: pts = -int(w * 0.6)
        else:            pts = -w
        score["US_Markets"] = pts
    else:
        score["US_Markets"] = 0

    # ── India VIX (15 pts) — LOW VIX = BULLISH ───────────────────
    if vix is not None:
        w = pm_cfg.get("india_vix_weight", 15)
        if   vix < 12:    pts = w        # very low fear = bullish
        elif vix < 15:    pts = int(w * 0.6)
        elif vix < 18:    pts = 0        # neutral
        elif vix < 22:    pts = -int(w * 0.6)
        else:             pts = -w       # high fear = bearish
        # Trend adjustment: falling VIX is additionally bullish
        if vix_trend == "FALLING":
            pts = min(w, pts + int(w * 0.2))
        elif vix_trend == "RISING":
            pts = max(-w, pts - int(w * 0.2))
        score["India_VIX"] = pts
    else:
        score["India_VIX"] = 0

    # ── Asian markets (10 pts) ────────────────────────────────────
    asian = [snapshots.get(k) for k in ("NIKKEI", "HANG_SENG") if k in snapshots]
    asian = [s for s in asian if s and s.change_pct is not None]
    if asian:
        avg_pct = sum(s.change_pct for s in asian) / len(asian)
        w       = pm_cfg.get("asian_markets_weight", 10)
        if   avg_pct >  0.5: pts = w
        elif avg_pct >  0.1: pts = int(w * 0.5)
        elif avg_pct > -0.1: pts = 0
        elif avg_pct > -0.5: pts = -int(w * 0.5)
        else:                pts = -w
        score["Asian_Markets"] = pts
    else:
        score["Asian_Markets"] = 0

    # ── PCR (10 pts) — contrarian ─────────────────────────────────
    if pcr is not None:
        w = pm_cfg.get("pcr_weight", 10)
        if   pcr > 1.4: pts = w          # excessive put buying = bulls will win
        elif pcr > 1.2: pts = int(w * 0.6)
        elif pcr > 0.9: pts = 0
        elif pcr > 0.7: pts = -int(w * 0.6)
        else:           pts = -w         # excessive call buying = bears will win
        score["PCR"] = pts
    else:
        score["PCR"] = 0

    # ── FII net (10 pts) ──────────────────────────────────────────
    if fii_net is not None:
        w = pm_cfg.get("fii_net_weight", 10)
        if   fii_net >  2000: pts = w
        elif fii_net >   500: pts = int(w * 0.6)
        elif fii_net >  -500: pts = 0
        elif fii_net > -2000: pts = -int(w * 0.6)
        else:                 pts = -w
        score["FII_Net"] = pts
    else:
        score["FII_Net"] = 0

    # ── Crude Oil (5 pts) — high crude = bearish for India ───────
    crude = snapshots.get("CRUDE_OIL")
    if crude and crude.change_pct is not None:
        w   = pm_cfg.get("crude_oil_weight", 5)
        pct = crude.change_pct
        if   pct >  2.0: pts = -w            # crude up big = bad for India
        elif pct >  0.5: pts = -int(w * 0.5)
        elif pct > -0.5: pts = 0
        elif pct > -2.0: pts = int(w * 0.5)
        else:            pts = w             # crude down big = good for India
        score["Crude_Oil"] = pts
    else:
        score["Crude_Oil"] = 0

    # ── USD/INR (5 pts) — rupee strength = bullish ────────────────
    usdinr = snapshots.get("USD_INR")
    if usdinr and usdinr.change_pct is not None:
        w   = pm_cfg.get("usdinr_weight", 5)
        pct = usdinr.change_pct   # positive = rupee WEAKENING
        if   pct >  0.5: pts = -w             # rupee weakening = bearish
        elif pct >  0.2: pts = -int(w * 0.5)
        elif pct > -0.2: pts = 0
        elif pct > -0.5: pts = int(w * 0.5)
        else:            pts = w              # rupee strengthening = bullish
        score["USD_INR"] = pts
    else:
        score["USD_INR"] = 0

    total = sum(score.values())
    total = max(-100, min(100, total))
    return total, score


def _bias_label(score: int) -> str:
    if   score >=  60: return "STRONGLY_BULLISH"
    elif score >=  30: return "BULLISH"
    elif score >= -29: return "NEUTRAL"
    elif score >= -59: return "BEARISH"
    else:              return "STRONGLY_BEARISH"


def fetch_premarket_briefing(config: dict) -> PreMarketBriefing:
    """
    Main entry point. Fetches all pre-market data and returns a complete briefing.
    Designed to be called at 8:00–8:15 AM IST.
    Takes ~30–60 seconds (network I/O).
    """
    today = datetime.now(IST).date()

    # ── Global data via yfinance ──────────────────────────────────
    fetch_map = {
        "SGX_NIFTY":    ("^SGXNIFTY",  "SGX Nifty"),         # may not exist
        "NIFTY_FUTURES":("^CNX100",    "Nifty 100 proxy"),   # fallback
        "US_SPX":       ("^GSPC",      "S&P 500"),
        "US_DOW":       ("^DJI",       "Dow Jones"),
        "NIKKEI":       ("^N225",      "Nikkei 225"),
        "HANG_SENG":    ("^HSI",       "Hang Seng"),
        "CRUDE_OIL":    ("CL=F",       "Crude Oil (WTI)"),
        "GOLD":         ("GC=F",       "Gold"),
        "USD_INR":      ("USDINR=X",   "USD/INR"),
    }

    snapshots: dict[str, GlobalSnapshot] = {}
    for key, (symbol, name) in fetch_map.items():
        snapshots[key] = _fetch_yfinance(symbol, name)
        time.sleep(0.2)   # gentle rate limiting

    # ── India-specific data ───────────────────────────────────────
    vix, vix_trend = _fetch_india_vix()
    pcr, max_pain  = _fetch_pcr_and_maxpain("NIFTY")
    fii_net        = _fetch_fii_net()

    # ── Bias score ────────────────────────────────────────────────
    bias_score, breakdown = _compute_bias(
        snapshots, vix, vix_trend, pcr, fii_net, config
    )
    label = _bias_label(bias_score)

    # ── PCR label ─────────────────────────────────────────────────
    if pcr is None:
        pcr_label = "UNAVAILABLE"
    elif pcr > config.get("options", {}).get("pcr_bullish_above", 1.3):
        pcr_label = "BULLISH"
    elif pcr < config.get("options", {}).get("pcr_bearish_below", 0.7):
        pcr_label = "BEARISH"
    else:
        pcr_label = "NEUTRAL"

    # ── High risk events (placeholder — can integrate economic calendar) ──
    high_risk = _check_known_events(today)

    return PreMarketBriefing(
        date             = today,
        bias_score       = bias_score,
        bias_label       = label,
        snapshots        = snapshots,
        india_vix        = vix,
        vix_trend        = vix_trend,
        pcr              = pcr,
        pcr_label        = pcr_label,
        max_pain         = max_pain,
        fii_net_cr       = fii_net,
        high_risk_events = high_risk,
        score_breakdown  = breakdown,
    )


def _check_known_events(today: _dt.date) -> list[str]:
    """
    Returns list of known high-risk events for today.
    Currently a stub — can be extended with a calendar API or manual config.
    """
    # TODO: integrate with investing.com economic calendar API
    # For now: placeholder that checks no events
    return []
