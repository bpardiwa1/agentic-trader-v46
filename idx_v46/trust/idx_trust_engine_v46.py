# ============================================================
# Agentic Trader idx_v46 — Trust Engine (Environment-Driven)
# ============================================================

from __future__ import annotations
import time
from typing import Dict
from idx_v46.app.idx_env_v46 import ENV

_trust: Dict[str, float] = {}
_last_ts: Dict[str, float] = {}

def _env_f(key: str, default: float) -> float:
    try:
        return float(ENV.get(key, default))
    except Exception:
        return default

def _env_i(key: str, default: int) -> int:
    try:
        return int(float(ENV.get(key, default)))
    except Exception:
        return default

def _params() -> dict:
    return {
        "TRUST_MIN": _env_f("IDX_TRUST_MIN", 0.40),
        "TRUST_MAX": _env_f("IDX_TRUST_MAX", 0.95),
        "TRUST_DEFAULT": _env_f("IDX_TRUST_DEFAULT", 0.50),
        "TRUST_INC_SUCCESS": _env_f("IDX_TRUST_INC_SUCCESS", 0.05),
        "TRUST_DEC_FAIL": _env_f("IDX_TRUST_DEC_FAIL", 0.10),
        "TRUST_DECAY_SEC": _env_i("IDX_TRUST_DECAY_SEC", 15 * 60 * 600),
    }

def _decay(symbol: str):
    p = _params()
    last = _last_ts.get(symbol, 0.0)
    if last and (time.time() - last) > p["TRUST_DECAY_SEC"]:
        _trust[symbol] = max(p["TRUST_MIN"], _trust.get(symbol, p["TRUST_DEFAULT"]) - 0.01)
        _last_ts[symbol] = time.time()

def get_trust_score(symbol: str) -> float:
    p = _params()
    _decay(symbol)
    return max(p["TRUST_MIN"], min(p["TRUST_MAX"], _trust.get(symbol, p["TRUST_DEFAULT"])))

def update_trust(symbol: str, success: bool):
    p = _params()
    cur = get_trust_score(symbol)
    cur = cur + p["TRUST_INC_SUCCESS"] if success else cur - p["TRUST_DEC_FAIL"]
    _trust[symbol] = max(p["TRUST_MIN"], min(p["TRUST_MAX"], cur))
    _last_ts[symbol] = time.time()

def adjusted_confidence(raw_conf: float, symbol: str, trust_weight: float | None = None) -> float:
    p = _params()
    tw = float(trust_weight if trust_weight is not None else ENV.get("IDX_TRUST_WEIGHT", 0.4))
    t = get_trust_score(symbol)
    adj = (1.0 - tw) * raw_conf + tw * t
    return max(0.0, min(1.0, round(adj, 4)))
