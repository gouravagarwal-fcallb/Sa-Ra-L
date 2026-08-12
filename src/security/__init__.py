"""Security / IT-audit layer for the trap-trading tool.

Structure-first modules that MUST be established before any execution layer:
  - vault.py       : credential vaulting (no hardcoded keys)
  - safe_broker.py : audited broker-interface guard (kill-switch + arm + trail)

Nothing here places an order on its own. The execution layer is built LAST and
only ever talks to the market through SafeBrokerGuard.
"""
