"""
Agentic Trader FX v4.6 — Trust Engine
-------------------------------------
Maintains per-symbol trust scores with time-based decay,
and provides confidence adjustment blending.
"""

from __future__ import annotations
import time
from collections import defaultdict

# ------------------------------------------------------------------
# Simple in-memory trust store with decay
# ------------------------------------------------------------------
_TRUST = defaultdict(lambda: 0.50)   # neutral start
_LAST  = defaultdict(lambda: 0.0)    # last update timestamp

def _decay(trust: float, seconds: float, half_life_s: float) -> float:
    """Exponential decay of trust toward 0.5 (neutral baseline)."""
    if half_life_s <= 0 or seconds <= 0:
        return trust
    k = 0.5 ** (seconds / half_life_s)
    return 0.5 + (trust - 0.5) * k


def get_trust_level(symbol: str, half_life_minutes: int = 180) -> float:
    """Return trust level for a symbol, applying exponential decay."""
    now = time.time()
    delta = now - _LAST[symbol]
    _LAST[symbol] = now
    _TRUST[symbol] = _decay(_TRUST[symbol], delta, half_life_minutes * 60)
    return max(0.0, min(1.0, _TRUST[symbol]))


def update_trust(symbol: str, won: bool, step_up: float = 0.05, step_dn: float = 0.08):
    """Update trust level after a trade outcome."""
    t = get_trust_level(symbol)
    t = t + (step_up if won else -step_dn)
    _TRUST[symbol] = max(0.0, min(1.0, t))


def adjusted_confidence(raw_conf: float, symbol: str, trust_weight: float = 0.4) -> float:
    """Blend raw confidence with trust memory."""
    t = get_trust_level(symbol)
    adj = (1.0 - trust_weight) * raw_conf + trust_weight * t
    return max(0.0, min(1.0, adj))
