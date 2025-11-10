# fx_v46/app/fx_env_v46.py
# ============================================================
# Agentic Trader v4.6 – Unified Environment Loader (FX)
# Loads fx_v46.env and exposes it globally as ENV
# ============================================================

import os
from dotenv import load_dotenv

from fx_v46.app.fx_env import ALIASES

class DotDict(dict):
    """Dictionary with dot-style access (attr-style)."""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


class EnvNamespace:
    """
    Dynamic, self-healing environment loader for Agentic Trader.

    ✅ Automatically loads fx_v46.env
    ✅ Exposes all variables in both upper & lower case
    ✅ Provides type-safe get() with bool/int/float inference
    ✅ Keeps backward compatibility (e.g., env.min_conf)
    """

    def __init__(self, env_file: str | None = None):
        # --- Determine .env file ---
        env_path = env_file or os.path.join(os.path.dirname(__file__), "fx_v46.env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
            print(f"[INFO] Environment loaded from {env_path}")
        else:
            print(f"[WARN] Env file not found: {env_path}")

        # --- Snapshot environment into dict ---
        self._env = dict(os.environ)

        # --- Expose lowercase variants for convenience ---
        for k, v in self._env.items():
            setattr(self, k.lower(), v)

        # --- Backward-compatible short aliases ---
        try:
            self.min_conf = float(self.get("AGENT_MIN_CONFIDENCE", 0.55))
        except Exception:
            self.min_conf = 0.55
        self.AGENT_MIN_CONFIDENCE = self.min_conf

   
    # --------------------------------------------------------
    # Smart accessors
    # --------------------------------------------------------
    def get(self, key: str, default=None):
        """
        Smart getter with automatic type inference from `default`.

        Example:
            ENV.get("FX_DYNAMIC_LOTS", True)
            ENV.get("AGENT_MIN_CONFIDENCE", 0.55)
            ENV.get("MT5_DEVIATION", 50)
        """
        for variant in (key, key.upper(), key.lower()):
            if variant in self._env:
                raw = self._env[variant]
                return self._cast(raw, default)
        return default

    def _cast(self, raw: str, default):
        """Infer type from the default value."""
        if default is None:
            return raw
        if isinstance(default, bool):
            return str(raw).lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            try:
                return int(float(raw))
            except Exception:
                return default
        if isinstance(default, float):
            try:
                return float(raw)
            except Exception:
                return default
        return str(raw)

    # --------------------------------------------------------
    # Fallback attribute access
    # --------------------------------------------------------
    def __getattr__(self, key):
        """Allow both uppercase/lowercase access; never raises AttributeError."""
        for variant in (key, key.upper(), key.lower()):
            if variant in self._env:
                return self._env[variant]
        return None

    def __contains__(self, key):
        return key in self._env or key.lower() in self._env or key.upper() in self._env

    def __repr__(self):
        return f"<EnvNamespace keys={len(self._env)}>"


# ------------------------------------------------------------
# Create global environment instance
# ------------------------------------------------------------
ENV = EnvNamespace()

# ------------------------------------------------------------
# Normalize key numeric / boolean values at startup
# ------------------------------------------------------------
def _normalize_env_types():
    """Force proper numeric and boolean types for critical fields."""
    try:
        ENV.agent_min_confidence    = float(ENV.get("AGENT_MIN_CONFIDENCE", 0.55))
        ENV.agent_max_open          = int(float(ENV.get("AGENT_MAX_OPEN", 10)))
        ENV.agent_max_per_symbol    = int(float(ENV.get("AGENT_MAX_PER_SYMBOL", 3)))
        ENV.fx_symbol_batch_delay   = float(ENV.get("FX_SYMBOL_BATCH_DELAY", 2.0))
        ENV.fx_cooldown_sec         = int(float(ENV.get("FX_COOLDOWN_SEC", 180)))
        ENV.fx_min_lots             = float(ENV.get("FX_MIN_LOTS", 0.03))
        ENV.fx_max_lots             = float(ENV.get("FX_MAX_LOTS", 0.30))
        ENV.fx_dynamic_lots         = str(ENV.get("FX_DYNAMIC_LOTS", "true")).lower() in ("1", "true", "yes", "on")
        ENV.fx_block_same_direction = str(ENV.get("FX_BLOCK_SAME_DIRECTION", "false")).lower() in ("1", "true", "yes", "on")
    except Exception as e:
        print(f"[WARN] fx_env_v46::_normalize_env_types failed: {e}")

_normalize_env_types()

# ------------------------------------------------------------
# Build per-symbol parameter map dynamically from .env
# ------------------------------------------------------------
def _build_per_symbol_map():
    """Creates ENV.per = { 'AUDUSD': DotDict({...}), 'EURUSD': DotDict({...}), ... }"""
    ENV.per = {}
    try:
        symbols = ENV.get("AGENT_SYMBOLS", "")
        if not symbols:
            print("[WARN] No AGENT_SYMBOLS found in env.")
            return

        for sym in [s.strip().replace("-ECNc", "").upper() for s in symbols.split(",") if s.strip()]:
            ENV.per[sym] = DotDict({
                "ema_fast":      int(float(ENV.get(f"EMA_FAST_{sym}", 20))),
                "ema_slow":      int(float(ENV.get(f"EMA_SLOW_{sym}", 50))),
                "rsi_period":    int(float(ENV.get(f"RSI_PERIOD_{sym}", 14))),
                "rsi_long_th":   float(ENV.get(f"RSI_LONG_TH_{sym}", 53)),
                "rsi_short_th":  float(ENV.get(f"RSI_SHORT_TH_{sym}", 47)),
                "sl_pips":       float(ENV.get(f"SL_{sym}", 40)),
                "tp_pips":       float(ENV.get(f"TP_{sym}", 90)),
                "lots":          float(ENV.get(f"LOTS_{sym}", 0.10))
            })
        print(f"[INFO] Loaded per-symbol configs: {list(ENV.per.keys())}")
    except Exception as e:
        print(f"[WARN] _build_per_symbol_map failed: {e}")
        ENV.per = {}

_build_per_symbol_map()

def resolve_symbol(symbol: str) -> str:
        """Return broker-specific alias for logical symbol."""
        sym_upper = symbol.upper()
        if sym_upper in ALIASES:
            return ALIASES[sym_upper]
        for v in ALIASES.values():
            if sym_upper == v.upper():
                return v
        return symbol