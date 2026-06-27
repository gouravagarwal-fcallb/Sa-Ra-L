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
URL_API = "https://www.nseindia.com/api/option-chain-indices?symbol={sym}"
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

    time.sleep(1)
    # the actual API call
    url = URL_API.format(sym=args.symbol.upper())
    try:
        r = s.get(url, timeout=12)
        print(f"→ chain API: HTTP {r.status_code}, body length={len(r.text)}")
        if r.status_code == 200 and r.text.strip().startswith("{"):
            data = r.json()
            n = len(data.get("records", {}).get("data", []))
            spot = data.get("records", {}).get("underlyingValue")
            print(f"✓ PARSED OK: {n} strikes, spot={spot}")
            verdict.append("WORKS. If the recorder still fails, ensure you pulled the latest "
                           "branch and (optionally) pip install truststore.")
        elif r.status_code in (401, 403):
            print(f"✗ NSE BLOCKED the request (HTTP {r.status_code}).")
            verdict.append("NSE bot-block. Usually transient: retry, ensure the warmup hit the "
                           "option-chain page first, or try from a residential IP. Weekends/holidays "
                           "also serve stale or no data.")
        else:
            print(f"✗ Unexpected response. First 160 chars: {r.text[:160]!r}")
            verdict.append("Non-JSON / unexpected — likely a block page or maintenance. Retry on a "
                           "trading day during market hours.")
    except Exception as e:
        kind = type(e).__name__
        print(f"✗ chain API FAILED: {kind}: {e}")
        if "SSL" in kind or "Certificate" in str(e):
            verdict.append("SSL interception (antivirus/proxy). Fix: pip install truststore.")
        else:
            verdict.append(f"{kind} — network/timeout. Check connectivity / VPN / firewall.")

    print("\n" + "=" * 60)
    print("VERDICT:")
    for v in (verdict or ["No clear verdict — paste this whole output back."]):
        print("  • " + v)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
