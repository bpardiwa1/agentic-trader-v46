"""
Agentic Trader FX v4.2 — Unified Environment Loader
---------------------------------------------------
Single source of truth for all FX runtime configuration.

• Loads fx_v4.env automatically (using python-dotenv)
• Parses all environment variables into a structured FxEnv object
• Exposes a global ENV singleton shared across all modules
"""

from __future__ import annotations
import os
import pathlib
from dataclasses import dataclass
from typing import Dict, List
from dotenv import load_dotenv
import logging

log = logging.getLogger("fx.env")

# =============================================================
# Helper converters
# =============================================================
def _b(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")

def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).split("#", 1)[0].strip())
    except Exception:
        return default

def _i(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).split("#", 1)[0].strip())
    except Exception:
        return default


# =============================================================
# Auto-load fx_v4.env
# =============================================================
ENV_PATH = pathlib.Path(__file__).resolve().parent / "fx_v4.env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
    log.info(f"[FX_ENV] Loaded environment file: {ENV_PATH}")
else:
    log.warning(f"[FX_ENV] ⚠️ No fx_v4.env found at {ENV_PATH}")


# =============================================================
# Dataclasses
# =============================================================
@dataclass
class PerSymbol:
    ema_fast: int
    ema_slow: int
    rsi_period: int
    rsi_long_th: float
    rsi_short_th: float
    sl_pips: float
    tp_pips: float
    lots: float


@dataclass
class FxEnv:
    # Core trading parameters
    symbols: List[str]
    timeframe: str
    min_conf: float
    atr_enabled: bool
    atr_period: int
    atr_sl_mult: float
    atr_tp_mult: float
    per: Dict[str, PerSymbol]

    # Advanced / Risk Controls
    dynamic_lots: bool
    accept_mixed: bool
    min_lots: float
    max_lots: float
    confidence_gate: bool
    conf_lock_n: int
    conf_reset_n: int
    conf_trust_n: int
    trust_decay_bars: int
    trust_decay_step_high: float
    trust_decay_step_low: float
    bar_minutes: int
    max_symbols: int
    batch_delay: float

    # Global trade guardrails
    agent_max_open: int
    agent_max_per_symbol: int


# =============================================================
# Symbol alias handling
# =============================================================
ALIASES: Dict[str, str] = {
    "EURUSD": "EURUSD-ECNc",
    "GBPUSD": "GBPUSD-ECNc",
    "AUDUSD": "AUDUSD-ECNc",
    "USDJPY": "USDJPY-ECNc",
    "XAUUSD": "XAUUSD-ECNc",
}

def _apply_alias_overrides() -> None:
    """Allow alias overrides via FX_ALIAS_<SYM>=<BROKER_SYMBOL>."""
    for key, val in os.environ.items():
        if key.startswith("FX_ALIAS_") and val.strip():
            logical = key.replace("FX_ALIAS_", "").upper()
            ALIASES[logical] = val.strip()

def resolve_symbol(symbol: str) -> str:
    """Return broker-specific alias for logical symbol."""
    sym_upper = symbol.upper()
    if sym_upper in ALIASES:
        return ALIASES[sym_upper]
    for v in ALIASES.values():
        if sym_upper == v.upper():
            return v
    return symbol


# =============================================================
# Environment Loader Function
# =============================================================
def load_env() -> FxEnv:
    """Parse environment variables and return FxEnv dataclass."""
    _apply_alias_overrides()

    # --- Global parameters ---
    symbols = [s.strip() for s in os.getenv("AGENT_SYMBOLS", "EURUSD").split(",") if s.strip()]
    timeframe = os.getenv("TIMEFRAME", "M15")
    min_conf = _f("AGENT_MIN_CONFIDENCE", 0.55)

    # --- ATR controls ---
    atr_enabled = _b("FX_ATR_ENABLED", True)
    atr_period = _i("FX_ATR_PERIOD", 14)
    atr_sl_mult = _f("FX_ATR_SL_MULT", 2.0)
    atr_tp_mult = _f("FX_ATR_TP_MULT", 3.0)

    # --- Per-symbol tuning ---
    per: Dict[str, PerSymbol] = {}
    for sym in symbols:
        base = sym.replace("-", "_").replace(".", "_").upper()
        per[sym] = PerSymbol(
            ema_fast=_i(f"EMA_FAST_{base}", 20),
            ema_slow=_i(f"EMA_SLOW_{base}", 50),
            rsi_period=_i(f"RSI_PERIOD_{base}", 14),
            rsi_long_th=_f(f"RSI_LONG_TH_{base}", 55.0),
            rsi_short_th=_f(f"RSI_SHORT_TH_{base}", 45.0),
            sl_pips=_f(f"SL_{base}", 40.0),
            tp_pips=_f(f"TP_{base}", 90.0),
            lots=_f(f"LOTS_{base}", 0.10),
        )

    # --- Build environment object ---
    return FxEnv(
        symbols=symbols,
        timeframe=timeframe,
        min_conf=min_conf,
        atr_enabled=atr_enabled,
        atr_period=atr_period,
        atr_sl_mult=atr_sl_mult,
        atr_tp_mult=atr_tp_mult,
        per=per,
        dynamic_lots=_b("FX_DYNAMIC_LOTS", True),
        accept_mixed=_b("FX_ACCEPT_MIXED", False),
        min_lots=_f("FX_MIN_LOTS", 0.03),
        max_lots=_f("FX_MAX_LOTS", 0.30),
        confidence_gate=_b("FX_CONFIDENCE_GATE", True),
        conf_lock_n=_i("FX_CONFIDENCE_LOCK_N", 3),
        conf_reset_n=_i("FX_CONFIDENCE_RESET_N", 3),
        conf_trust_n=_i("FX_CONFIDENCE_TRUST_N", 5),
        trust_decay_bars=_i("FX_TRUST_DECAY_BARS", 600),
        trust_decay_step_high=_f("FX_TRUST_DECAY_STEP_HIGH", 0.005),
        trust_decay_step_low=_f("FX_TRUST_DECAY_STEP_LOW", 0.002),
        bar_minutes=_i("FX_BAR_MINUTES", 15),
        max_symbols=_i("FX_MAX_SYMBOLS", 5),
        batch_delay=_f("FX_SYMBOL_BATCH_DELAY", 2.0),
        agent_max_open=_i("AGENT_MAX_OPEN", 10),
        agent_max_per_symbol=_i("AGENT_MAX_PER_SYMBOL", 2),
    )


# =============================================================
# Global Singleton
# =============================================================
ENV = load_env()
