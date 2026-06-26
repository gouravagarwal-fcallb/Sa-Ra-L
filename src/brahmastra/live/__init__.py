"""
BRAHMASTRA Live Layer
━━━━━━━━━━━━━━━━━━━━
narrator.py   — Market Narrator: plain-language forward commentary every 5m bar
human_gate.py — Execution mode (AUTO / HUMAN_WATCH) + pending signal queue
"""
from src.brahmastra.live.narrator   import MarketNarrator, NarratorInput, NarratorUpdate
from src.brahmastra.live.human_gate import HumanGate, ExecutionMode, PendingSignal

__all__ = [
    "MarketNarrator", "NarratorInput", "NarratorUpdate",
    "HumanGate", "ExecutionMode", "PendingSignal",
]
