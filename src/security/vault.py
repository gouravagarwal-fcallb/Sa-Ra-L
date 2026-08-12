"""
Credential Vault — audit control CRED-1: no hardcoded secrets.
──────────────────────────────────────────────────────────────
Resolves a named secret (Kite api_key / api_secret / access_token, Telegram bot
tokens, …) from a priority ladder, and NEVER returns it through a log line.

Resolution order (first hit wins):
    1. Process environment variable            (12-factor; best for servers/CI)
    2. OS keyring / Credential Manager         (Windows Credential Manager, macOS
                                                 Keychain) — via `keyring` if present
    3. Encrypted file  secrets.env.enc         (Fernet/AES; key from KEY env or keyring)
    4. Plain  .env / settings.local.yaml       (dev fallback — WARNS, must be gitignored)

Design rules enforced here:
  * Secret VALUES are never logged, printed, or put in exceptions — only the
    secret NAME and the source tier are ever surfaced.
  * A masked accessor (`masked`) exists for audit trails (shows only last 4).
  * `cryptography` and `keyring` are OPTIONAL imports — the module loads without
    them and simply reports those tiers as unavailable.

This module does NOT read broker credentials into any strategy directly; the
runner asks the vault and injects the client. Strategies never see raw secrets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.utils.logger import get_strategy_logger

log = get_strategy_logger("security_vault", "SECURITY")

# ── optional dependencies (module still imports if absent) ──────────────────────
try:
    from cryptography.fernet import Fernet  # type: ignore
    _HAS_FERNET = True
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore
    _HAS_FERNET = False

try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except Exception:  # pragma: no cover
    keyring = None  # type: ignore
    _HAS_KEYRING = False


_KEYRING_SERVICE = "sa-ra-l"
_ENC_FILE = Path("config/secrets.env.enc")
_PLAIN_ENV = Path(".env")
_FERNET_KEY_ENV = "SARAL_VAULT_KEY"      # base64 Fernet key, itself from env/keyring


class VaultError(RuntimeError):
    """Raised when a required secret cannot be resolved. Never carries the value."""


@dataclass(frozen=True)
class SecretRef:
    name: str
    source: str          # "env" | "keyring" | "encrypted" | "dotenv"

    def __repr__(self) -> str:            # audit-safe repr — no value
        return f"SecretRef(name={self.name!r}, source={self.source!r})"


def _mask(value: str) -> str:
    if not value:
        return "<empty>"
    return f"****{value[-4:]}" if len(value) >= 4 else "****"


# ── individual source tiers ────────────────────────────────────────────────────
def _from_env(name: str) -> Optional[str]:
    return os.environ.get(name)


def _from_keyring(name: str) -> Optional[str]:
    if not _HAS_KEYRING:
        return None
    try:
        return keyring.get_password(_KEYRING_SERVICE, name)
    except Exception as e:                # never leak the value; log only the name
        log.warning(f"keyring lookup failed for {name}: {type(e).__name__}")
        return None


def _fernet_key() -> Optional[bytes]:
    key = os.environ.get(_FERNET_KEY_ENV) or _from_keyring(_FERNET_KEY_ENV)
    return key.encode() if key else None


def _from_encrypted(name: str) -> Optional[str]:
    if not (_HAS_FERNET and _ENC_FILE.exists()):
        return None
    key = _fernet_key()
    if not key:
        log.warning(f"{_ENC_FILE} present but no {_FERNET_KEY_ENV} — cannot decrypt")
        return None
    try:
        blob = _ENC_FILE.read_bytes()
        plaintext = Fernet(key).decrypt(blob).decode()
        return _parse_env(plaintext).get(name)
    except Exception as e:
        log.error(f"decrypt of {_ENC_FILE} failed: {type(e).__name__}")
        return None


def _from_dotenv(name: str) -> Optional[str]:
    if not _PLAIN_ENV.exists():
        return None
    log.warning(f"resolving {name} from PLAINTEXT {_PLAIN_ENV} — dev only; "
                f"ensure it is gitignored and move to keyring/encrypted for prod")
    return _parse_env(_PLAIN_ENV.read_text()).get(name)


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# ── public API ──────────────────────────────────────────────────────────────────
_TIERS = (("env", _from_env), ("keyring", _from_keyring),
          ("encrypted", _from_encrypted), ("dotenv", _from_dotenv))


def get_secret(name: str, *, required: bool = True) -> Optional[str]:
    """Resolve a secret by name. Logs only name+source, never the value."""
    for source, fn in _TIERS:
        val = fn(name)
        if val:
            log.info(f"secret {name} resolved from {source} ({_mask(val)})")
            return val
    if required:
        raise VaultError(f"secret {name!r} not found in any vault tier "
                         f"(env/keyring/encrypted/dotenv)")
    log.warning(f"optional secret {name!r} not found")
    return None


def get_ref(name: str) -> Optional[SecretRef]:
    """Return WHERE a secret lives (for audit) without exposing the value."""
    for source, fn in _TIERS:
        if fn(name):
            return SecretRef(name=name, source=source)
    return None


def masked(name: str) -> str:
    val = get_secret(name, required=False)
    return _mask(val) if val else "<missing>"


def audit_report(names: list[str]) -> list[dict]:
    """IT-audit snapshot: for each required secret, where it resolves (no values)."""
    rows = []
    for n in names:
        ref = get_ref(n)
        rows.append({"secret": n, "resolved": ref is not None,
                     "source": ref.source if ref else None,
                     "masked": masked(n)})
    return rows


# ── admin helper: encrypt a plaintext .env into config/secrets.env.enc ──────────
def encrypt_env(plain_path: str = ".env", out_path: str = str(_ENC_FILE)) -> None:
    """One-off: turn a dev .env into the encrypted vault file.
    Generate a key with `Fernet.generate_key()` and store it in keyring/env as
    SARAL_VAULT_KEY — NEVER commit it."""
    if not _HAS_FERNET:
        raise VaultError("cryptography not installed — `pip install cryptography`")
    key = _fernet_key()
    if not key:
        raise VaultError(f"set {_FERNET_KEY_ENV} (a Fernet key) before encrypting")
    data = Path(plain_path).read_bytes()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_bytes(Fernet(key).encrypt(data))
    log.info(f"encrypted {plain_path} -> {out_path} (delete the plaintext now)")
