# ============================================================
# Agentic Trader IDX v4.6a — Adaptive Trust Engine (Session-Aware)
# ------------------------------------------------------------
# • Session-reset trust memory (intraday focus for indices)
# • Confidence-weighted trust growth
# • ATR-aware decay (faster in volatile regimes)
# • Utility helpers for lot scaling and snapshots
# ============================================================

from __future__ import annotations
from typing import Dict, Any
import datetime as dt
import pytz

from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.logger import setup_logger

log = setup_logger("idx_trust_engine_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())

# In-proc memory
_TRUST: Dict[str, Dict[str, Any]] = {}

# --- KL timezone
_TZ = pytz.timezone("Asia/Kuala_Lumpur")

def _now_kl() -> dt.datetime:
    return dt.datetime.now(tz=_TZ)

def _session_key(now_kl: dt.datetime) -> str:
    # Session identity (daily): YYYY-MM-DD in KL time
    return now_kl.strftime("%Y-%m-%d")

def _rec(symbol: str) -> Dict[str, Any]:
    r = _TRUST.get(symbol)
    if not r:
        r = {
            "trust": 0.0,              # 0..1
            "last_side": "",
            "last_update": _now_kl().isoformat(),
            "session_key": _session_key(_now_kl()),
            "streak": 0,
        }
        _TRUST[symbol] = r
    return r

def get_trust(symbol: str) -> float:
    return float(_rec(symbol)["trust"])

def trust_snapshot() -> Dict[str, Any]:
    # Read-only debug view
    return {k: v.copy() for k, v in _TRUST.items()}

def update_trust(symbol: str, side: str, conf_base: float, atr_pct: float) -> float:
    """
    Adaptive session-aware trust update:
      • Session reset factor (overnight) → reduces stale memory
      • Confidence-weighted growth
      • ATR-weighted decay
    Returns: updated trust in [0..1]
    """
    r = _rec(symbol)
    now = _now_kl()
    sk = _session_key(now)

    # --- Session reset (if the KL date changed)
    reset_en = str(ENV.get("IDX_TRUST_SESSION_RESET", "true")).lower() in ("1","true","yes","on")
    reset_factor = float(ENV.get("IDX_TRUST_SESSION_RESET_FACTOR", 0.2))  # keep 20% carryover by default
    if reset_en and r["session_key"] != sk:
        old = r["trust"]
        r["trust"] = max(0.0, min(1.0, old * reset_factor))
        r["session_key"] = sk
        log.debug("[TRUST] %s session reset: %.3f -> %.3f", symbol, old, r["trust"])

    # --- Side streak (optional small reinforcement on same side)
    if side and r["last_side"] == side:
        r["streak"] += 1
    else:
        r["streak"] = 1
        r["last_side"] = side

    # --- Confidence-weighted growth
    # base_growth + confidence_gain * conf
    base_growth = float(ENV.get("IDX_TRUST_BASE_GROWTH", 0.02))
    conf_gain = float(ENV.get("IDX_TRUST_CONF_GAIN", 0.03))
    growth = base_growth + conf_gain * max(0.0, min(1.0, conf_base))

    # --- ATR-aware decay (stronger in high vol)
    # decay = base * (1 + atr_pct * multiplier)
    decay_base = float(ENV.get("IDX_TRUST_DECAY_BASE", 0.005))
    decay_vol_mult = float(ENV.get("IDX_TRUST_DECAY_VOL_MULT", 50.0))
    decay = decay_base * (1.0 + max(0.0, atr_pct) * decay_vol_mult)

    # Net update
    new_trust = r["trust"] + growth - decay
    new_trust = max(0.0, min(1.0, new_trust))

    r["trust"] = new_trust
    r["last_update"] = now.isoformat()

    log.debug(
        "[TRUST] %s side=%s conf=%.2f atr%%=%.4f | +%.3f -%.3f -> %.3f (streak=%d)",
        symbol, side, conf_base, atr_pct, growth, decay, new_trust, r["streak"]
    )
    return new_trust

def dynamic_lot_scale(symbol: str, conf_adj_base: float, trust: float, atr_pct: float) -> float:
    """
    Lot sizing with confidence × trust blend and ATR moderation.
    lots = clamp( lots_min + (lots_max - lots_min) * blend(conf, trust) * vol_dampen )
    """
    lots_min = float(ENV.get("IDX_MIN_LOTS", 0.10))
    lots_max = float(ENV.get("IDX_MAX_LOTS", 0.30))

    # Confidence + trust blend (smooth, capped)
    conf = max(0.0, min(1.0, conf_adj_base))
    tru  = max(0.0, min(1.0, trust))
    blend = 0.5 * conf + 0.3 * tru + 0.2 * (conf * tru)  # 50/30/20 mix
    blend = max(0.0, min(1.0, blend))

    # Volatility dampener (protect lot size during high ATR regimes)
    vol_ref = float(ENV.get("IDX_VOL_REF", 0.004))  # ~0.40% reference ATR
    vol_ratio = 0.0 if vol_ref <= 0 else min(1.0, max(0.0, atr_pct / vol_ref))
    vol_dampen = max(0.5, 1.0 - vol_ratio)  # never less than 0.5

    lots = lots_min + (lots_max - lots_min) * blend * vol_dampen
    lots = max(lots_min, min(lots, lots_max))
    log.debug(
        "[RISK] %s lots=%.2f (conf=%.2f trust=%.2f atr%%=%.4f damp=%.2f)",
        symbol, lots, conf, tru, atr_pct, vol_dampen
    )
    return lots
