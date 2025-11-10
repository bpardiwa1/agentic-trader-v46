"""
Agentic Trader FX v4 — Momentum Trust Engine
--------------------------------------------
Tracks signal reliability per symbol and adjusts confidence dynamically.
"""

from __future__ import annotations
import json, time
from pathlib import Path
from typing import Dict

TRUST_PATH = Path(__file__).resolve().parent / "trust_state.json"

DEFAULT_STATE = {"EURUSD": 0.5, "GBPUSD": 0.5, "USDJPY": 0.5, "AUDUSD": 0.5}
DECAY_SECONDS = 3600 * 24 * 2  # 2 days
LEARNING_RATE = 0.1  # how quickly trust adjusts


def load_trust() -> Dict[str, Dict[str, float]]:
    if TRUST_PATH.exists():
        with open(TRUST_PATH, "r") as f:
            return json.load(f)
    return {"trust": DEFAULT_STATE, "timestamp": time.time()}


def save_trust(data: Dict[str, Dict[str, float]]):
    with open(TRUST_PATH, "w") as f:
        json.dump(data, f, indent=2)


def decay_trust(state: Dict[str, float]) -> Dict[str, float]:
    """Slowly revert trust toward 0.5 over long inactivity."""
    now = time.time()
    decayed = {}
    for sym, val in state.items():
        drift = (0.5 - val) * 0.01  # 1 % per decay cycle
        decayed[sym] = val + drift
    return decayed


def adjust_confidence(symbol: str, raw_conf: float) -> float:
    """Apply trust multiplier to confidence."""
    data = load_trust()
    trust = data["trust"].get(symbol, 0.5)
    mult = 1 + (trust - 0.5) * 0.3  # ±15 % adjustment
    adjusted = max(0.0, min(1.0, raw_conf * mult))
    return adjusted


def update_trust(symbol: str, outcome: bool):
    """Update trust after a trade result (True=profit, False=loss)."""
    data = load_trust()
    state = data.get("trust", DEFAULT_STATE)
    val = state.get(symbol, 0.5)
    if outcome:
        val += LEARNING_RATE * (1 - val)
    else:
        val -= LEARNING_RATE * (val)
    val = max(0.0, min(1.0, val))
    state[symbol] = val
    data["trust"] = state
    data["timestamp"] = time.time()
    save_trust(data)
