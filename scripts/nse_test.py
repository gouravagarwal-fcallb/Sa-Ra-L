"""
NSE option-chain fetch diagnostic — PASHUPATASTRA
=================================================
Tells you EXACTLY why the option-chain fetch failed (the recorder only logs a
generic 'NSE fetch failed'). Run it and read the verdict at the bottom:

    python scripts/nse_test.py
    python scripts/nse_test.py --symbol NIFTY
"""
import argparse
import sys
import time

URL_HOME = "https://www.nseindia.com"
URL_PAGE = "https://www.nseindia.com/option-chain"
URL_CONTRACT = "https://www.nseindia.com/api/option-chain-contract-info?symbol={sym}"
URL_V3 = "https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol={sym}"
URL_LEGACY = "https://www.nseindia.com/api/option-chain-indices?symbol={sym}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    args = ap.parse_args()
    verdict = []

    # truststore?
    try:
        import truststore
        truststore.inject_into_ssl()
        print("✓ truststore: installed and active (OS trust store in use)")
    except Exception:
        print("• truststore: NOT installed — if you see an SSL error below, run: pip install truststore")

    # requests?
    try:
        import requests
        print(f"✓ requests: installed (v{requests.__version__})")
    except Exception as e:
        print(f"✗ requests: NOT installed -> THIS is the cause. Fix:  pip install requests")
        print(f"    ({e})")
        return 1

    s = requests.Session()
    s.headers.update(HEADERS)

    # warmup
    for label, url in [("homepage", URL_HOME), ("option-chain page", URL_PAGE)]:
        try:
            r = s.get(url, timeout=10)
            print(f"✓ warmup {label}: HTTP {r.status_code}, cookies now={len(s.cookies)}")
        except Exception as e:
            kind = type(e).__name__
            print(f"✗ warmup {label} FAILED: {kind}: {e}")
            if "SSL" in kind or "Certificate" in str(e):
                verdict.append("SSL interception (antivirus/proxy). Fix: pip install truststore "
                               "(then re-run). The recorder already injects it.")
            time.sleep(1)

    sym = args.symbol.upper()

    def getj(label, url):
        try:
            r = s.get(url, timeout=12)
            head = r.text[:120].replace("\n", " ")
            ok_json = r.status_code == 200 and r.text.strip()[:1] in "{["
            print(f"→ {label}: HTTP {r.status_code}, {len(r.text)} bytes"
                  + ("" if ok_json else f"  body: {head!r}"))
            return (r.json() if ok_json else None), r.status_code
        except Exception as e:
            print(f"✗ {label} FAILED: {type(e).__name__}: {e}")
            if "SSL" in type(e).__name__ or "Certificate" in str(e):
                verdict.append("SSL interception (antivirus/proxy). Fix: pip install truststore.")
            return None, None

    time.sleep(1)
    # 1) expiry list  2) v3 (with nearest expiry)  3) v3 (no expiry)  4) legacy
    info, _ = getj("contract-info (expiries)", URL_CONTRACT.format(sym=sym))
    expiries = []
    if isinstance(info, dict):
        expiries = info.get("expiryDates") or (info.get("records", {}) or {}).get("expiryDates") or []
    elif isinstance(info, list):
        expiries = [x if isinstance(x, str) else x.get("expiryDate", "") for x in info]
    print(f"   expiries found: {expiries[:3]}{' ...' if len(expiries) > 3 else ''}")

    data, status = (None, None)
    if expiries:
        data, status = getj("option-chain-v3 (nearest expiry)",
                            URL_V3.format(sym=sym) + f"&expiry={expiries[0]}")
    if data is None:
        data, status = getj("option-chain-v3 (no expiry)", URL_V3.format(sym=sym))
    if data is None:
        data, status = getj("option-chain-indices (legacy)", URL_LEGACY.format(sym=sym))

    rows = []
    if isinstance(data, dict):
        rows = (data.get("records", {}) or {}).get("data") or data.get("data") or []
    if rows:
        spot = (data.get("records", {}) or {}).get("underlyingValue") or data.get("underlyingValue")
        print(f"✓ PARSED OK: {len(rows)} strikes, spot={spot}")
        verdict.append("WORKS — the v3 endpoint returns data. Re-run: python record_oi.py --once --all-hours")
    elif status in (401, 403):
        verdict.append("NSE bot-block (401/403). Usually transient; most reliable on a trading day "
                       "during market hours (9:15–15:30).")
    elif status == 404:
        verdict.append("404 on all endpoints — NSE may have changed the path again; paste this output.")
    else:
        verdict.append("No strikes parsed. On a weekend NSE often serves no option data — retry "
                       "on a trading day (Tue NIFTY / Thu SENSEX) during market hours.")

    print("\n" + "=" * 60)
    print("VERDICT:")
    for v in (verdict or ["No clear verdict — paste this whole output back."]):
        print("  • " + v)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
