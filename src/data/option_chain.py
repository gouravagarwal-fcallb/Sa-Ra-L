"""
Option chain analysis module.

In paper/live mode: fetches from Kite Connect or NSE API.
In backtest mode: uses Black-Scholes synthetic data.
"""

from __future__ import annotations
import math
import requests
from dataclasses import dataclass, field
from typing import Optional
from scipy.stats import norm
from src.utils.logger import setup_logger
from src.utils.helpers import round_to_strike

log = setup_logger("option_chain")

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


@dataclass
class Greeks:
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0   # per day
    vega: float = 0.0    # per 1% IV move


@dataclass
class OptionData:
    strike: int = 0
    option_type: str = "CE"   # CE | PE
    ltp: float = 0.0
    iv: float = 0.0
    oi: int = 0
    oi_change: int = 0
    oi_change_pct: float = 0.0
    greeks: Greeks = field(default_factory=Greeks)


@dataclass
class OptionChainSnapshot:
    spot: float = 0.0
    atm_strike: int = 0
    iv_percentile: float = 0.0
    atm_call: OptionData = field(default_factory=OptionData)
    atm_put: OptionData = field(default_factory=OptionData)
    calls: list[OptionData] = field(default_factory=list)
    puts: list[OptionData] = field(default_factory=list)
    pcr: float = 0.0          # Put-Call Ratio (OI based)
    oi_resistance: int = 0    # Strike with max Call OI (resistance)
    oi_support: int = 0       # Strike with max Put OI (support)


# ─────────────────────────────────────────────────────
#  Black-Scholes Greeks (used in backtest + paper mode)
# ─────────────────────────────────────────────────────

def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def bs_price(S: float, K: float, T: float, r: float, sigma: float, opt: str) -> float:
    """Black-Scholes option price. T in years, sigma annualised."""
    if T <= 0:
        return max(0.0, S - K) if opt == "CE" else max(0.0, K - S)
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    if opt == "CE":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, opt: str) -> Greeks:
    if T <= 0 or sigma <= 0:
        return Greeks()
    d1 = _d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    sqrt_T = math.sqrt(T)
    nd1 = norm.pdf(d1)

    delta = norm.cdf(d1) if opt == "CE" else norm.cdf(d1) - 1
    gamma = nd1 / (S * sigma * sqrt_T)
    theta = (
        -(S * nd1 * sigma) / (2 * sqrt_T)
        - r * K * math.exp(-r * T) * (norm.cdf(d2) if opt == "CE" else norm.cdf(-d2))
    ) / 365
    vega = S * nd1 * sqrt_T / 100   # per 1% change in IV

    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega)


def synthetic_option_data(
    spot: float,
    strike: int,
    opt: str,
    T_hours: float,
    vix: float,
    r: float = 0.065,
) -> OptionData:
    """Synthesise option data using Black-Scholes when live data is unavailable."""
    sigma = vix / 100
    T = T_hours / (365 * 24)
    price = bs_price(spot, strike, T, r, sigma, opt)
    greeks = bs_greeks(spot, strike, T, r, sigma, opt)
    return OptionData(
        strike=strike,
        option_type=opt,
        ltp=round(price, 2),
        iv=round(vix, 2),
        greeks=greeks,
    )


# ─────────────────────────────────────────────────────
#  Live option chain from NSE (paper/live trading)
# ─────────────────────────────────────────────────────

def fetch_nse_option_chain(symbol: str = "NIFTY") -> dict:
    """Fetch raw NSE option chain JSON."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"NSE option chain fetch failed: {e}")
        return {}


def parse_option_chain(
    raw: dict,
    spot: float,
    oi_range_pts: int = 100,
    strike_step: int = 50,
) -> OptionChainSnapshot:
    """Parse NSE API response into OptionChainSnapshot."""
    atm = round_to_strike(spot, strike_step)
    snap = OptionChainSnapshot(spot=spot, atm_strike=atm)

    records = raw.get("records", {}).get("data", [])
    if not records:
        return snap

    call_oi_by_strike = {}
    put_oi_by_strike = {}

    for rec in records:
        strike = rec.get("strikePrice", 0)
        if abs(strike - atm) > oi_range_pts:
            continue

        ce = rec.get("CE", {})
        pe = rec.get("PE", {})

        if ce:
            iv = ce.get("impliedVolatility", 0)
            oi = ce.get("openInterest", 0)
            oi_chg = ce.get("changeinOpenInterest", 0)
            call_oi_by_strike[strike] = oi
            cd = OptionData(
                strike=strike,
                option_type="CE",
                ltp=ce.get("lastPrice", 0),
                iv=iv,
                oi=oi,
                oi_change=oi_chg,
                oi_change_pct=(oi_chg / oi * 100) if oi else 0,
            )
            snap.calls.append(cd)
            if strike == atm:
                snap.atm_call = cd

        if pe:
            iv = pe.get("impliedVolatility", 0)
            oi = pe.get("openInterest", 0)
            oi_chg = pe.get("changeinOpenInterest", 0)
            put_oi_by_strike[strike] = oi
            pd_ = OptionData(
                strike=strike,
                option_type="PE",
                ltp=pe.get("lastPrice", 0),
                iv=iv,
                oi=oi,
                oi_change=oi_chg,
                oi_change_pct=(oi_chg / oi * 100) if oi else 0,
            )
            snap.puts.append(pd_)
            if strike == atm:
                snap.atm_put = pd_

    total_call_oi = sum(call_oi_by_strike.values())
    total_put_oi = sum(put_oi_by_strike.values())
    snap.pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi else 0.0

    if call_oi_by_strike:
        snap.oi_resistance = max(call_oi_by_strike, key=call_oi_by_strike.get)
    if put_oi_by_strike:
        snap.oi_support = max(put_oi_by_strike, key=put_oi_by_strike.get)

    return snap


def get_iv_percentile(symbol: str = "NIFTY", lookback_days: int = 252) -> float:
    """
    Approximate IV Percentile using VIX history.
    IV Percentile = % of days in past year where VIX was BELOW current VIX.
    """
    import yfinance as yf
    try:
        hist = yf.download("^INDIAVIX", period="1y", interval="1d", progress=False)
        if hist.empty or len(hist) < 10:
            return 50.0
        current_vix = float(hist["Close"].iloc[-1])
        pct = float((hist["Close"] < current_vix).mean() * 100)
        return round(pct, 1)
    except Exception:
        return 50.0
