"""
Kite Auto-Login
───────────────
Fully automated Zerodha Kite Connect login using direct HTTP requests.
No browser required — runs headlessly at 8:00 AM IST before market open.

How it works:
  1. POST to kite.zerodha.com/api/login with user_id + password
  2. Generate TOTP from stored secret using pyotp
  3. POST to kite.zerodha.com/api/twofa to complete 2FA
  4. Follow OAuth redirect to capture request_token
  5. Exchange request_token for access_token via kiteconnect SDK
  6. Save access_token to config/.kite_token (read on next broker start)

Credentials required (store in config/settings.local.yaml, never commit):
  broker:
    kite:
      user_id:     your_zerodha_client_id    # e.g. AB1234
      password:    your_zerodha_password
      totp_secret: your_totp_base32_secret   # from Zerodha 2FA setup page
      api_key:     your_kite_api_key
      api_secret:  your_kite_api_secret

How to get totp_secret:
  1. Go to kite.zerodha.com → My Profile → Security → Enable TOTP
  2. When the QR code appears, click "show secret"
  3. Copy the Base32 string — that is your totp_secret
  4. Also scan the QR in Google Authenticator as a backup

Token is saved to config/.kite_token and valid for the trading day.
"""

from __future__ import annotations

import os
import re
import time
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

TOKEN_FILE   = Path("config/.kite_token")
SETTINGS_LOCAL = Path("config/settings.local.yaml")

IST = timezone(timedelta(hours=5, minutes=30))

LOGIN_URL  = "https://kite.zerodha.com/api/login"
TWOFA_URL  = "https://kite.zerodha.com/api/twofa"


def _load_local_settings() -> dict:
    """Load credentials from settings.local.yaml (gitignored secrets file)."""
    if not SETTINGS_LOCAL.exists():
        raise FileNotFoundError(
            f"{SETTINGS_LOCAL} not found.\n"
            "Create it with your Zerodha credentials (it is gitignored — safe to store secrets):\n\n"
            "  broker:\n"
            "    kite:\n"
            "      user_id:     AB1234\n"
            "      password:    your_password\n"
            "      totp_secret: BASE32SECRETFROM2FASETUP\n"
            "      api_key:     your_api_key\n"
            "      api_secret:  your_api_secret\n"
        )
    import yaml
    with open(SETTINGS_LOCAL, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_kite_creds(settings_local: dict) -> dict:
    kite = settings_local.get("broker", {}).get("kite", {})
    required = ["user_id", "password", "totp_secret", "api_key", "api_secret"]
    missing = [k for k in required if not kite.get(k)]
    if missing:
        raise ValueError(
            f"Missing in config/settings.local.yaml → broker.kite: {missing}\n"
            "See comments in src/broker/kite_auto_login.py for setup instructions."
        )
    return kite


def _generate_totp(secret: str) -> str:
    try:
        import pyotp
    except ImportError:
        raise RuntimeError("pyotp not installed. Run: pip install pyotp")
    return pyotp.TOTP(secret).now()


def _save_token(access_token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": access_token,
        "date":         date.today().isoformat(),
        "saved_at":     datetime.now(IST).strftime("%H:%M:%S IST"),
    }
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def load_cached_token() -> Optional[str]:
    """
    Return today's cached access_token if it exists, else None.
    Called automatically by create_kite_broker() so you never need
    to set KITE_ACCESS_TOKEN manually after running autologin.
    """
    if not TOKEN_FILE.exists():
        return None
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == date.today().isoformat():
            return data.get("access_token")
    except Exception:
        pass
    return None


def auto_login(verbose: bool = True) -> str:
    """
    Perform headless Kite Connect login.
    Returns the access_token and caches it to config/.kite_token.
    """
    import requests

    local_cfg = _load_local_settings()
    creds     = _get_kite_creds(local_cfg)

    user_id     = creds["user_id"]
    password    = creds["password"]
    totp_secret = creds["totp_secret"]
    api_key     = creds["api_key"]
    api_secret  = creds["api_secret"]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "X-Kite-Version": "3",
    })

    if verbose:
        print(f"\n  Kite auto-login  |  {datetime.now(IST).strftime('%H:%M:%S IST')}")
        print(f"  User: {user_id}")

    # ── Step 1: Password login ────────────────────────────────────────────────
    resp1 = session.post(LOGIN_URL, data={
        "user_id":  user_id,
        "password": password,
    }, timeout=15)

    if resp1.status_code != 200:
        raise RuntimeError(f"Login step 1 failed: HTTP {resp1.status_code} — {resp1.text[:200]}")

    body1 = resp1.json()
    if body1.get("status") != "success":
        msg = body1.get("message", "unknown error")
        raise RuntimeError(f"Login step 1 error: {msg}")

    request_id = body1["data"]["request_id"]
    if verbose:
        print("  Step 1/3 — password OK")

    # ── Step 2: TOTP 2FA ──────────────────────────────────────────────────────
    totp_code = _generate_totp(totp_secret)

    resp2 = session.post(TWOFA_URL, data={
        "user_id":      user_id,
        "request_id":   request_id,
        "twofa_value":  totp_code,
        "twofa_type":   "totp",
        "skip_session": "",
    }, timeout=15)

    if resp2.status_code != 200:
        raise RuntimeError(f"Login step 2 (2FA) failed: HTTP {resp2.status_code} — {resp2.text[:200]}")

    body2 = resp2.json()
    if body2.get("status") != "success":
        msg = body2.get("message", "unknown error")
        raise RuntimeError(f"2FA error: {msg}")

    if verbose:
        print("  Step 2/3 — TOTP OK")

    # ── Step 3: OAuth redirect → request_token ───────────────────────────────
    oauth_url = (
        f"https://kite.trade/connect/login"
        f"?api_key={api_key}&v=3"
    )
    resp3 = session.get(oauth_url, allow_redirects=False, timeout=15)

    # Kite redirects to your app's redirect_url with ?request_token=...
    location = resp3.headers.get("Location", "")
    match = re.search(r"request_token=([^&]+)", location)
    if not match:
        # Some setups require following the redirect chain
        resp3b = session.get(oauth_url, allow_redirects=True, timeout=15)
        location = resp3b.url
        match = re.search(r"request_token=([^&]+)", location)
    if not match:
        raise RuntimeError(
            f"Could not extract request_token from redirect URL.\n"
            f"Location header: {location}\n"
            "Check that your Kite Connect app's redirect URL is correctly configured."
        )

    request_token = match.group(1)
    if verbose:
        print(f"  Step 3/3 — OAuth request_token captured")

    # ── Step 4: Exchange request_token for access_token ──────────────────────
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    _save_token(access_token)

    if verbose:
        print(f"\n  Access token saved to {TOKEN_FILE}")
        print(f"  Valid for today: {date.today().isoformat()}")
        print(f"  Token: {access_token[:8]}...{access_token[-4:]}\n")
        print("  Ready to trade. Run:")
        print("    python main.py --mode portfolio")

    return access_token


def wait_and_login(target_hour: int = 8, target_minute: int = 0,
                   verbose: bool = True) -> str:
    """
    Wait until target_hour:target_minute IST, then run auto_login.
    Use this if you start the process the night before.
    """
    now = datetime.now(IST)
    target = now.replace(hour=target_hour, minute=target_minute,
                         second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)

    wait_sec = int((target - now).total_seconds())
    if verbose and wait_sec > 0:
        hh, rem = divmod(wait_sec, 3600)
        mm, ss  = divmod(rem, 60)
        print(
            f"\n  Kite auto-login scheduled for "
            f"{target.strftime('%Y-%m-%d %H:%M IST')}"
            f"  (waiting {hh:02d}:{mm:02d}:{ss:02d})"
        )
    if wait_sec > 0:
        time.sleep(wait_sec)

    return auto_login(verbose=verbose)
