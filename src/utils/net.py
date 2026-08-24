"""
Network helper — prefer IPv4 for outbound connections.
──────────────────────────────────────────────────────
Zerodha's Kite Connect app has an IP whitelist. On dual-stack home connections
(e.g. Jio), the OS often reaches Kite over IPv6, and Windows rotates its
*temporary* IPv6 address every day or two — so a whitelisted IPv6 stops matching
and orders get rejected ("IP … is not allowed to place orders"). The whitelist
can only be changed once a week, so a rotation can lock the operator out.

The IPv4 address is far steadier. Forcing all outbound sockets to resolve to IPv4
means the connection egresses over the whitelisted IPv4 and keeps working across
IPv6 rotations. This affects only *outbound* name resolution; it does not change
how the local server binds. IPv4 works for every endpoint we talk to (Kite,
yfinance, NSE, Telegram), so there is no downside here.

Disable by setting the env var PREFER_IPV4=0 (or network.prefer_ipv4: false in
settings), in which case the OS default (usually IPv6-first) is used.
"""
from __future__ import annotations

import os
import socket

_ORIG_GETADDRINFO = socket.getaddrinfo
_PATCHED = False


def prefer_ipv4(enable: bool = True) -> bool:
    """Monkeypatch socket.getaddrinfo to return IPv4 results first (falling back
    to the original list if a host has no IPv4). Idempotent. Returns True if the
    IPv4 preference is active after the call."""
    global _PATCHED
    if enable and not _PATCHED:
        def _ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
            try:
                results = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
            except socket.gaierror:
                raise
            v4 = [r for r in results if r[0] == socket.AF_INET]
            return v4 or results        # keep IPv6 only if there is no IPv4 at all
        socket.getaddrinfo = _ipv4_first
        _PATCHED = True
    elif not enable and _PATCHED:
        socket.getaddrinfo = _ORIG_GETADDRINFO
        _PATCHED = False
    return _PATCHED


def prefer_ipv4_from_env(default: bool = True) -> bool:
    """Enable IPv4 preference unless explicitly disabled via PREFER_IPV4=0/false/no."""
    val = os.environ.get("PREFER_IPV4")
    if val is not None:
        enable = str(val).strip().lower() not in ("0", "false", "no", "off")
    else:
        enable = default
    return prefer_ipv4(enable)
