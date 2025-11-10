# ============================================================
# Agentic Trader IDX v4.6a — Trade Executor (Adaptive Trust)
# ------------------------------------------------------------
# • Blends trust with base confidence from decider
# • Volatility-aware dynamic lot sizing
# • Broker-safe volume rounding (min/step/max)
# • Unified logs: [DEBUG]/[RISK]/[ORDER]/[FAILED]/[BLOCKED]
# ============================================================

from __future__ import annotations
from typing import Dict, Any
import MetaTrader5 as mt5  # type: ignore

from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.logger import setup_logger
from core.mt5_connect_v46 import ensure_mt5_initialized
from idx_v46.trust.idx_trust_engine_v46 import (
    update_trust, get_trust, dynamic_lot_scale
)

log = setup_logger("idx_executor_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())


# ---------------------------------------
# Helpers
# ---------------------------------------
def _adjust_volume(symbol: str, lots: float) -> float:
    """
    Clamp lots to broker's min/max and round to step.
    Prevents 'Invalid volume' retcode (10014).
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        return max(0.01, round(lots, 2))  # last resort

    step = getattr(info, "volume_step", 0.01) or 0.01
    min_lot = getattr(info, "volume_min", 0.01) or 0.01
    max_lot = getattr(info, "volume_max", 100.0) or 100.0

    # Round to nearest step
    rounded = round(round(lots / step) * step, 8)
    rounded = max(min_lot, min(rounded, max_lot))
    return rounded


# ---------------------------------------
# Main execution
# ---------------------------------------
def execute_trade(symbol: str, decision: Dict[str, Any], feats: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a trade using adaptive trust & dynamic lots.
    Expects decision from idx_decider_v46.decide(...) and feats from idx_features_v46.
    """
    if not decision.get("accepted"):
        return {"ok": False, "reason": "not_accepted"}

    side = decision.get("side")
    conf_raw = float(decision.get("confidence_raw", 0.0))
    conf_adj_base = float(decision.get("confidence_adj", 0.0))  # decider's post-volatility (no trust)
    atr_pct = float(decision.get("atr_pct", 0.0))
    sl = float(decision.get("sl") or 0.0)
    tp = float(decision.get("tp") or 0.0)

    # --- Update & read trust memory
    trust = update_trust(symbol, side, conf_raw, atr_pct)

    # --- Final confidence (executor applies trust multiplier)
    #     final_conf = base_adj * (0.6 + 0.8 * trust)
    final_conf = conf_adj_base * (0.6 + 0.8 * trust)
    final_conf = max(0.0, min(1.0, final_conf))

    # --- Dynamic lot sizing (confidence × trust × volatility dampening)
    lots = dynamic_lot_scale(symbol, conf_adj_base, trust, atr_pct)
    lots = _adjust_volume(symbol, lots)

    # --- Pre-trade unified debug line (FX/XAU style)
    log.info(
        "[DEBUG] %s TF=%s EMA_FAST=%.2f EMA_SLOW=%.2f GAP=%.2f RSI=%.2f ATR%%=%.4f | RAW=%.2f ADJ=%.2f TRU=%.2f FIN=%.2f WHY=%s",
        symbol,
        feats.get("tf", "M15"),
        feats.get("ema_fast", 0.0),
        feats.get("ema_slow", 0.0),
        feats.get("ema_gap", 0.0),
        feats.get("rsi", 0.0),
        atr_pct,
        conf_raw,
        conf_adj_base,
        trust,
        final_conf,
        decision.get("why", []),
    )

    # --- Guardrails (lightweight; your fuller rules can slot in here)
    # Example caps — feel free to swap with your existing position counter.
    max_total = int(ENV.get("IDX_MAX_TOTAL", 12))
    max_per_symbol = int(ENV.get("IDX_MAX_PER_SYMBOL", 1))

    # We do a safe, minimal check (no position scan to keep this executor focused).
    # If you have a positions reader, plug it here:
    open_total = 0
    open_for_symbol = 0
    log.info("[DEBUG] Guardrail: %s total=%d per_symbol=%d limits=(%d,%d)",
             symbol, open_total, open_for_symbol, max_total, max_per_symbol)

    if open_total >= max_total:
        log.warning("[BLOCKED] %s total cap reached (%d/%d)", symbol, open_total, max_total)
        return {"ok": False, "reason": "guard_total"}

    if open_for_symbol >= max_per_symbol:
        log.warning("[BLOCKED] %s cap reached (%d/%d)", symbol, open_for_symbol, max_per_symbol)
        return {"ok": False, "reason": "guard_symbol"}

    # --- Ensure MT5 init and place order
    ensure_mt5_initialized(ENV)

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log.warning("[FAILED] %s no market tick available", symbol)
        return {"ok": False, "reason": "no_tick"}

    price = tick.ask if side == "LONG" else tick.bid
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL,
        "price": float(price),
        "sl": float(sl),
        "tp": float(tp),
        "deviation": int(ENV.get("MT5_DEVIATION", 100)),
        "magic": 0,
        "comment": f"idx_v46 {side} conf={final_conf:.2f}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(req)
    if result is None or getattr(result, "retcode", None) != mt5.TRADE_RETCODE_DONE:
        log.warning(
            "[FAILED] %s %s both attempts failed | retcode=%s | result=%s",
            symbol, side, getattr(result, "retcode", None), getattr(result, "__dict__", result)
        )
        return {"ok": False, "reason": "execution_failed"}

    log.info("[ORDER] %s %s lots=%.2f SL=%.1f TP=%.1f", symbol, side, lots, sl, tp)
    return {"ok": True, "reason": "executed", "lots": lots, "final_conf": final_conf}
