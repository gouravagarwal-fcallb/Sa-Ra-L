"""
Live capital overrides
──────────────────────
Lets the user adjust, from the dashboard, the capital each strategy is allowed to
trade with — without editing the (comment-rich, version-controlled) registry.yaml.

The registry stays the documented baseline; user adjustments are layered on top and
persisted to a small local JSON file (config/capital_overrides.json), which is
git-ignored like settings.local.yaml so each machine keeps its own live figures.

`apply_overrides()` is called inside the server's _load_registry() so EVERY place
that reads capital_allocated_rs (the strategy list AND the live-arming cap) sees the
adjusted value consistently.
"""
from __future__ import annotations

import os
import json
import threading

OVERRIDES_PATH = os.path.join("config", "capital_overrides.json")
_lock = threading.Lock()

# Guard-rails: a single strategy can't be set negative or absurdly large from the UI.
MAX_CAPITAL_RS = 50_000_000   # Rs.5 Cr hard ceiling per strategy (sanity, not policy)


def load_overrides() -> dict:
    try:
        with open(OVERRIDES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {k: float(v) for k, v in data.get("capital_allocated_rs", {}).items()}
    except Exception:
        return {}


def set_override(name: str, amount) -> dict:
    """Validate + persist one strategy's capital. Returns {name, capital_allocated_rs}."""
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        raise ValueError("capital must be a number")
    if amt < 0:
        raise ValueError("capital cannot be negative")
    if amt > MAX_CAPITAL_RS:
        raise ValueError(f"capital exceeds the Rs.{MAX_CAPITAL_RS:,} per-strategy ceiling")
    amt = round(amt, 2)
    with _lock:
        try:
            with open(OVERRIDES_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data.setdefault("capital_allocated_rs", {})[name] = amt
        os.makedirs(os.path.dirname(OVERRIDES_PATH) or ".", exist_ok=True)
        with open(OVERRIDES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return {"name": name, "capital_allocated_rs": amt}


def apply_overrides(registry: dict) -> dict:
    """Layer saved overrides onto a freshly-loaded registry dict (in place)."""
    ov = load_overrides()
    for name, amt in ov.items():
        if name in registry and isinstance(registry[name], dict):
            registry[name]["capital_allocated_rs"] = amt
            registry[name]["capital_overridden"] = True
    return registry
