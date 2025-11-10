# ============================================================
# Agentic Trader IDX v4.6 — Execution Engine (CM07b Final)
# ------------------------------------------------------------
# Enhancements:
# • Safe SL/TP recalibration (CM07)
# • Absolute SL/TP conversion (CM07b)
# • Consistent env guardrails (AGENT_MAX_OPEN / AGENT_MAX_PER_SYMBOL)
# • Retains trust/confidence/dynamic lots
# ============================================================

from __future__ import annotations
import MetaTrader5 as mt5  # type: ignore
from typing import Any, Dict

from idx_v46.app.idx_env_v46 import ENV
from core.mt5_connect_v46 import ensure_mt5_initialized
from idx_v46.util.logger import setup_logger
from idx_v46.trust.idx_trust_engine_v46 import get_trust, update_trust
from idx_v46.util.idx_lot_scaler_v46 import compute_lot

log = setup_logger("idx_executor_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())


# ------------------------------------------------------------
# Broker-Safe Helpers
# ------------------------------------------------------------
def _adjust_volume(symbol: str, lots: float) -> float:
    """Clamp volume to broker min/max and step sizes."""
    info = mt5.symbol_info(symbol)
    if not info:
        return lots
    vol_min, vol_max, vol_step = info.volume_min, info.volume_max, info.volume_step
    lots = max(vol_min, min(lots, vol_max))
    steps = round((lots - vol_min) / vol_step)
    adj = vol_min + steps * vol_step
    return round(adj, 2)


def _send_order(req: dict, side: str) -> dict:
    """Wrapper to safely send order to MT5."""
    ensure_mt5_initialized(ENV)
    try:
        result = mt5.order_send(req)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.warning(
                "[FAILED] %s %s failed | retcode=%s | result=%s",
                req["symbol"], side, result.retcode, result,
            )
            return {"ok": False, "reason": "execution_failed", "result": result._asdict()}
        return {"ok": True, "result": result._asdict()}
    except Exception as e:
        log.error("[EXCEPTION] during order_send for %s: %s", req["symbol"], e)
        return {"ok": False, "reason": "exception"}


# ------------------------------------------------------------
# Execution Core
# ------------------------------------------------------------
def execute_trade(symbol: str, decision: Dict[str, Any], feats: Dict[str, Any]) -> Dict[str, Any]:
    """Executes trade with trust/confidence scaling and price-safe SL/TP widening."""
    if not decision.get("accepted"):
        return {"ok": False, "reason": "not_accepted"}

    side = decision.get("side")
    conf_raw = float(decision.get("confidence_raw", 0.0))
    conf_adj_base = float(decision.get("confidence_adj", decision.get("confidence", 0.0)))
    atr_pct = float(feats.get("atr_pct", 0.0))
    base_sl = float(decision.get("sl", 100.0))
    base_tp = float(decision.get("tp", 200.0))

    # --- Trust + Confidence
    trust_prev = get_trust(symbol)
    trust = update_trust(symbol, side, conf_raw, atr_pct)
    final_conf = conf_adj_base * (0.6 + 0.8 * trust)
    final_conf = max(0.0, min(1.0, final_conf))

    log.debug("[TRUST] %s conf_raw=%.2f adj=%.2f trust_prev=%.2f → trust=%.2f final=%.2f",
              symbol, conf_raw, conf_adj_base, trust_prev, trust, final_conf)

    # --- Dynamic SL/TP scaling
    scale_sl = 1.2 - 0.4 * final_conf
    scale_tp = 0.8 + 0.8 * final_conf
    sl_scaled = base_sl * scale_sl
    tp_scaled = base_tp * scale_tp

    # --- Symbol-aware widening base (CM07 logic)
    key_base = symbol.replace(".s", "").replace("-", "_").upper()
    min_stop = float(ENV.get(f"MT5_MIN_STOP_POINTS_{key_base}", ENV.get("MT5_MIN_STOP_POINTS", 100)))
    widen_mult = float(ENV.get(f"MT5_STOP_WIDEN_MULT_{key_base}", ENV.get("MT5_STOP_WIDEN_MULT", 2.0)))

    # --- CM07: Price-aware broker-safe recalibration
    info = mt5.symbol_info(symbol)
    if info:
        min_level_points = getattr(info, "trade_stops_level", 0)
        tick_size = getattr(info, "point", 1.0)
        min_level_price = min_level_points * tick_size

        if min_level_price > 0:
            sl_scaled = max(sl_scaled, min_level_price * widen_mult)
            tp_scaled = max(tp_scaled, min_level_price * widen_mult)

        # Extra cushion for indices like UK100
        if symbol.upper().startswith("UK"):
            sl_scaled = max(sl_scaled, min_stop * widen_mult * 1.5)
            tp_scaled = max(tp_scaled, min_stop * widen_mult * 1.5)

        log.warning(
            "[CM07] %s SL/TP recalibrated: SL=%.2f TP=%.2f | min_level=%.2f pts (%.5f price units)",
            symbol, sl_scaled, tp_scaled, min_level_points, min_level_price,
        )
    else:
        sl_scaled = max(sl_scaled, min_stop * widen_mult)
        tp_scaled = max(tp_scaled, min_stop * widen_mult)
        log.warning(
            "[CM07] %s fallback widening → SL=%.1f TP=%.1f (min_stop=%.1f mult=%.1f)",
            symbol, sl_scaled, tp_scaled, min_stop, widen_mult,
        )

    decision["sl"] = sl_scaled
    decision["tp"] = tp_scaled

    # --- Dynamic lot sizing
    lots = compute_lot(symbol, final_conf, atr_pct)
    lots = _adjust_volume(symbol, lots)
    log.info("[LOT] %s conf=%.2f trust=%.2f atr%%=%.4f → final lots=%.2f",
             symbol, final_conf, trust, atr_pct, lots)

    # --- Guardrails (consistent vars)
    total_orders = len(mt5.positions_get() or [])
    per_symbol = len(mt5.positions_get(symbol=symbol) or [])
    limit_total = int(ENV.get("AGENT_MAX_OPEN", 15))
    limit_symbol = int(ENV.get("AGENT_MAX_PER_SYMBOL", 3))
    log.info("[DEBUG] Guardrail: %s total=%d per_symbol=%d limits=(%d,%d)",
             symbol, total_orders, per_symbol, limit_total, limit_symbol)

    if total_orders >= limit_total or per_symbol >= limit_symbol:
        log.warning("[BLOCKED] %s guardrail limit reached (%d/%d)",
                    symbol, per_symbol, limit_symbol)
        return {"ok": False, "reason": "guardrail_blocked"}

    # --- Tradeable check
    if not info or not info.trade_mode:
        log.warning("[BLOCKED] %s not tradeable (mode=%s)",
                    symbol, getattr(info, "trade_mode", None))
        return {"ok": False, "reason": "not_tradeable"}

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        log.warning("[BLOCKED] %s missing tick data", symbol)
        return {"ok": False, "reason": "no_tick_data"}

    price = tick.ask if side == "LONG" else tick.bid
    point = getattr(info, "point", 1.0)

    # --- CM07b: convert SL/TP distances → absolute prices
    if side == "LONG":
        sl_price = price - sl_scaled * point
        tp_price = price + tp_scaled * point
    else:
        sl_price = price + sl_scaled * point
        tp_price = price - tp_scaled * point

    log.info("[CM07b] %s %s price=%.2f SL(abs)=%.2f TP(abs)=%.2f | ΔSL=%.1f ΔTP=%.1f pts",
             symbol, side, price, sl_price, tp_price, sl_scaled, tp_scaled)

    # --- Construct MT5 order
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lots,
        "type": mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "deviation": int(ENV.get("MT5_DEVIATION", 80)),
        "sl": sl_price,
        "tp": tp_price,
        "type_filling": mt5.ORDER_FILLING_IOC,
        "comment": f"idx_v46 {side} conf={final_conf:.2f}",
    }

    # --- Execute trade
    result = _send_order(req, side)
    if result.get("ok"):
        log.info("[EXECUTED] %s %s lots=%.2f SL=%.1f TP=%.1f conf=%.2f",
                 symbol, side, lots, sl_price, tp_price, final_conf)
    else:
        log.warning("[FAILED] %s %s reason=%s", symbol, side, result.get("reason"))

    return result
