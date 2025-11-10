# ============================================================
# Agentic Trader v4.6 — XAUUSD Trust Engine
# ------------------------------------------------------------
# Purpose:
#   • Track and adjust "trust" scores for XAU symbols.
#   • Trust rises on successful executions, decays slowly with inactivity.
#   • Feeds adjusted_confidence() used in xau_decider_v46.
# ============================================================

from __future__ import annotations
import time
from typing import Dict

# Internal runtime state
_trust: Dict[str, float] = {}          # symbol -> trust (0..1)
_last_update: Dict[str, float] = {}    # symbol -> timestamp of last trust update


# ------------------------------------------------------------
# Parameters (tunable via env or defaults)
# ------------------------------------------------------------
TRUST_DECAY_BARS = 1200      # decay after ~1200 bars (M15 → ~12.5 days)
TRUST_DECAY_STEP = 0.01      # per decay interval
TRUST_INC_SUCCESS = 0.05     # on success
TRUST_DECAY_FAIL = 0.10      # on failure
TRUST_MIN = 0.40
TRUST_MAX = 0.95


# ------------------------------------------------------------
# Update trust after trade
# ------------------------------------------------------------
def update_trust(symbol: str, success: bool):
    """Adjust trust after trade result."""
    now = time.time()
    current = _trust.get(symbol, 0.5)

    if success:
        current += TRUST_INC_SUCCESS
    else:
        current -= TRUST_DECAY_FAIL

    # Clamp within bounds
    current = max(TRUST_MIN, min(current, TRUST_MAX))
    _trust[symbol] = current
    _last_update[symbol] = now

    print(f"[TRUST] {symbol}: {'UP' if success else 'DOWN'} → {current:.2f}")


# ------------------------------------------------------------
# Periodic decay (called lazily in adjusted_confidence)
# ------------------------------------------------------------
def _decay(symbol: str):
    """Decay trust score after inactivity."""
    last_t = _last_update.get(symbol, 0)
    now = time.time()
    # Roughly 15 min per bar × TRUST_DECAY_BARS = decay horizon
    horizon_sec = TRUST_DECAY_BARS * 15 * 60
    if now - last_t > horizon_sec:
        old = _trust.get(symbol, 0.5)
        decayed = max(TRUST_MIN, old - TRUST_DECAY_STEP)
        _trust[symbol] = decayed
        _last_update[symbol] = now
        print(f"[TRUST] {symbol}: DECAY → {decayed:.2f}")


# ------------------------------------------------------------
# Adjusted confidence blending
# ------------------------------------------------------------
def adjusted_confidence(raw_conf: float, symbol: str, trust_weight: float = 0.4) -> float:
    """
    Blend raw confidence with persistent trust level.

    Parameters
    ----------
    raw_conf : float
        Momentum-based confidence (0..1)
    symbol : str
        XAU symbol (e.g., XAUUSD-ECNc)
    trust_weight : float
        Blend weight for trust (default 0.4)

    Returns
    -------
    float
        Adjusted confidence ∈ [0, 1]
    """
    _decay(symbol)
    t = _trust.get(symbol, 0.5)
    adj = (1 - trust_weight) * raw_conf + trust_weight * t
    adj_final = max(0.0, min(1.0, adj))
    return round(adj_final, 3)

# ------------------------------------------------------------
# Trust score accessor
# ------------------------------------------------------------
def get_trust_score(symbol: str) -> float:
    """
    Safely return the current trust score for a symbol.

    • Triggers lazy decay before returning.
    • Defaults to 0.5 if never seen before.
    """
    _decay(symbol)
    return _trust.get(symbol, 0.5)

# ------------------------------------------------------------
# Debug snapshot
# ------------------------------------------------------------
def dump_trust_state() -> Dict[str, float]:
    """Return a copy of current trust memory for diagnostics."""
    return dict(_trust)


if __name__ == "__main__":
    # Quick local test
    sym = "XAUUSD-ECNc"
    for i in range(3):
        update_trust(sym, True)
    print(adjusted_confidence(0.6, sym))
    for i in range(2):
        update_trust(sym, False)
    print(adjusted_confidence(0.6, sym))
    print(dump_trust_state())
