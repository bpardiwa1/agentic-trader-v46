"""
Agentic Trader FX v4.6 — Environment Loader
--------------------------------------------
Reads configuration from fx_v46.env and builds
a namespace object usable across the framework.

Now supports both:
 - env.per["AUDUSD"]["rsi_long_th"]
 - env.per["AUDUSD"].rsi_long_th
"""

from __future__ import annotations
import os
from dotenv import load_dotenv
import pathlib
import logging

log = logging.getLogger("fx_env_v46")

# --------------------------------------------------------------------
# Helper
# --------------------------------------------------------------------
def _get(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val is None:
        return default
    return val.split("#", 1)[0].strip()  # strip inline comments

def _to_bool(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")

def _to_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val)
    except Exception:
        return default

def _to_int(val: str, default: int = 0) -> int:
    try:
        return int(val)
    except Exception:
        return default


# --------------------------------------------------------------------
# SymbolConfig class
# --------------------------------------------------------------------
class SymbolConfig:
    """Allows attribute access for symbol-level parameters."""
    def __init__(self, cfg_dict):
        self.__dict__.update(cfg_dict)

    def __getitem__(self, key):
        return self.__dict__[key]

    def __repr__(self):
        return f"<SymbolConfig {self.__dict__}>"


# --------------------------------------------------------------------
# Load .env into memory
# --------------------------------------------------------------------
def load_env() -> "EnvNamespace":
    env_path = pathlib.Path(__file__).resolve().parent / "fx_v46.env"
    if env_path.exists():
        load_dotenv(env_path)
        log.info("[ENV] Loaded configuration from: %s", env_path)
    else:
        log.warning("[ENV] No environment file found at %s", env_path)

    # Global parameters
    env_data = {
        "symbols": [s.strip() for s in _get("AGENT_SYMBOLS", "").split(",") if s.strip()],
        "timeframe": _get("TIMEFRAME", "M15"),
        "agent_min_confidence": _to_float(_get("AGENT_MIN_CONFIDENCE", "0.5")),
        "atr_enabled": _to_bool(_get("FX_ATR_ENABLED", "false")),
        "atr_period": _to_int(_get("FX_ATR_PERIOD", "14")),
        "atr_sl_mult": _to_float(_get("FX_ATR_SL_MULT", "2.0")),
        "atr_tp_mult": _to_float(_get("FX_ATR_TP_MULT", "3.0")),
        "dynamic_lots": _to_bool(_get("FX_DYNAMIC_LOTS", "true")),
        "min_lots": _to_float(_get("FX_MIN_LOTS", "0.03")),
        "max_lots": _to_float(_get("FX_MAX_LOTS", "0.30")),
        "confidence_gate": _to_bool(_get("FX_CONFIDENCE_GATE", "true")),
        "agent_max_open": _to_int(_get("AGENT_MAX_OPEN", "10")),
        "agent_max_per_symbol": _to_int(_get("AGENT_MAX_PER_SYMBOL", "2")),
        "batch_delay": _to_float(_get("FX_SYMBOL_BATCH_DELAY", "2.0")),
        "max_symbols": _to_int(_get("FX_MAX_SYMBOLS", "5")),
        "cooldown_sec": _to_int(_get("FX_COOLDOWN_SEC", "180")),
        "block_same_direction": _to_bool(_get("FX_BLOCK_SAME_DIRECTION", "false")),
        "fx_symbol_batch_delay": _to_float(_get("FX_SYMBOL_BATCH_DELAY", "2.0")),
    }

    # Per-symbol section
    per = {}
    for sym in env_data["symbols"]:
        s = sym.strip().upper()
        per[s] = {
            "ema_fast": _to_int(_get(f"EMA_FAST_{s}", "20")),
            "ema_slow": _to_int(_get(f"EMA_SLOW_{s}", "50")),
            "rsi_period": _to_int(_get(f"RSI_PERIOD_{s}", "14")),
            "rsi_long_th": _to_float(_get(f"RSI_LONG_TH_{s}", "55")),
            "rsi_short_th": _to_float(_get(f"RSI_SHORT_TH_{s}", "45")),
            "sl_pips": _to_float(_get(f"SL_{s}", "40")),
            "tp_pips": _to_float(_get(f"TP_{s}", "90")),
            "lots": _to_float(_get(f"LOTS_{s}", "0.10")),
        }
        # 👇 Wrap each symbol dict into a SymbolConfig
        per[s] = SymbolConfig(per[s])

    env_data["per"] = per

    log.info(
        "[ENV SUMMARY] symbols=%s | min_conf=%.2f | cooldown=%ss | block_same_direction=%s",
        env_data["symbols"], env_data["agent_min_confidence"],
        env_data["cooldown_sec"], env_data["block_same_direction"]
    )

    return EnvNamespace(env_data)


# --------------------------------------------------------------------
# Namespace wrapper
# --------------------------------------------------------------------
class EnvNamespace:
    def __init__(self, data: dict):
        self.__dict__.update(data)

    def __getitem__(self, key):
        return self.__dict__[key]

    def __repr__(self):
        return f"<EnvNamespace {self.__dict__}>"


# --------------------------------------------------------------------
# Global singleton (auto-loaded)
# --------------------------------------------------------------------
ENV = load_env()
