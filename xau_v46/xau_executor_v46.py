# ============================================================
# Agentic Trader XAU v4.6 — MT5 Trade Executor (Environment-Driven)
# ------------------------------------------------------------
# Features:
#  • Reads all operational parameters from xau_v46.env
#  • Guardrails: max trades, cooldowns, same-direction blocking
#  • Dynamic lot scaling from confidence
#  • ATR & volatility-aware SL/TP adjustment
#  • Trust updates on success/failure
# ============================================================

from __future__ import annotations
import time
from typing import Dict, Any
import MetaTrader5 as mt5  # type: ignore

from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.logger import setup_logger
from xau_v46.trust.xau_trust_engine_v46 import update_trust

log = setup_logger("xau_executor_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())

# ------------------------------------------------------------
# Runtime memory
# ------------------------------------------------------------
_last_trade_time: Dict[str, float] = {}
_last_direction: Dict[str, str] = {}

# ------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------
def _pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.1  # XAUUSD pip ~0.1 USD

def _price_round(price: float, digits: int) -> float:
    factor = 10.0 ** digits
    return round(price * factor) / factor

def _get_env_value(key: str, default: float | int) -> float:
    try:
        return float(ENV.get(key, default))
    except Exception:
        return default

def _guardrail_check(symbol: str, side: str) -> bool:
    """Comprehensive guardrail validation."""
    try:
        positions = mt5.positions_get() or []
    except Exception as e:
        log.warning("[GUARDRAIL] mt5.positions_get() failed: %s", e)
        positions = []

    allowed = set([s.strip() for s in str(ENV.get("XAU_AGENT_SYMBOLS", "XAUUSD-ECNc")).split(",")])
    filtered = [p for p in positions if p.symbol in allowed]

    total_active = len(filtered)
    per_symbol = len([p for p in filtered if p.symbol == symbol])

    max_open = int(_get_env_value("XAU_AGENT_MAX_OPEN", 2))
    max_per_symbol = int(_get_env_value("XAU_AGENT_MAX_PER_SYMBOL", 1))

    log.info("[DEBUG] Guardrail: %s total=%d per_symbol=%d limits=(%d,%d)",
             symbol, total_active, per_symbol, max_open, max_per_symbol)

    if total_active >= max_open:
        log.warning("[BLOCKED] Global cap reached (%d/%d)", total_active, max_open)
        return False
    if per_symbol >= max_per_symbol:
        log.warning("[BLOCKED] %s cap reached (%d/%d)", symbol, per_symbol, max_per_symbol)
        return False

    # Cooldown
    cooldown_sec = int(_get_env_value("XAU_COOLDOWN_SEC", 300))
    last_t = _last_trade_time.get(symbol, 0)
    if time.time() - last_t < cooldown_sec:
        log.info("[BLOCKED] %s cooldown active (%.1fs remaining)",
                 symbol, cooldown_sec - (time.time() - last_t))
        return False

    # Same direction
    if ENV.get("XAU_BLOCK_SAME_DIRECTION", "True").lower() == "true":
        last_dir = _last_direction.get(symbol)
        if last_dir == side:
            log.info("[BLOCKED] %s same-direction trade prevented (%s)", symbol, side)
            return False

    return True

# ------------------------------------------------------------
# Order price computation
# ------------------------------------------------------------
def _build_prices(symbol: str, side: str, sl_points: float, tp_points: float) -> Dict[str, float]:
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        raise RuntimeError(f"No tick for {symbol}")
    price = tick.ask if side == "LONG" else tick.bid
    pip = _pip_size(symbol)
    sl = price - sl_points * pip if side == "LONG" else price + sl_points * pip
    tp = price + tp_points * pip if side == "LONG" else price - tp_points * pip

    info = mt5.symbol_info(symbol)
    if not info:
        raise RuntimeError(f"No symbol_info for {symbol}")

    price = _price_round(price, info.digits)
    sl = _price_round(sl, info.digits)
    tp = _price_round(tp, info.digits)
    return {"price": price, "sl": sl, "tp": tp}

# ------------------------------------------------------------
# Core trade execution
# ------------------------------------------------------------
def execute_trade(symbol: str, side: str, base_lot: float,
                  sl_points: float, tp_points: float,
                  confidence: float = 0.5,
                  atr_pct: float = 0.0) -> Dict[str, Any]:

    # Guardrail check
    if not _guardrail_check(symbol, side):
        return {"ok": False, "blocked": True, "reason": "guardrail_limit"}

    # Prepare symbol
    if not mt5.symbol_info(symbol):
        if not mt5.symbol_select(symbol, True):
            return {"ok": False, "reason": f"symbol_select failed for {symbol}"}

    # Dynamic lot scaling from confidence
    min_lots = _get_env_value("XAU_MIN_LOTS", 0.05)
    max_lots = _get_env_value("XAU_MAX_LOTS", 0.10)
    lot_size = min_lots + (max_lots - min_lots) * confidence
    lot_size = round(lot_size, 2)

    log.info("[RISK] %s dynamic_lots=%.2f (conf=%.2f, atr=%.4f)", symbol, lot_size, confidence, atr_pct)

    try:
        base = _build_prices(symbol, side, sl_points, tp_points)
        deviation = int(_get_env_value("MT5_DEVIATION", 50))

        log.info("[ORDER] %s %s lots=%.2f SL=%.1f TP=%.1f",
                 symbol, side, lot_size, sl_points, tp_points)

        res = _send_order(symbol, side, lot_size,
                          base["price"], base["sl"], base["tp"], deviation)

        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("[EXECUTED] %s %s ok (ticket=%s)", symbol, side, getattr(res, "order", "?"))
            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side
            return {"ok": True, "result": res._asdict(), "attempts": 1}

        # Retry with widened stops
        widen_mult = _get_env_value("XAU_STOP_WIDEN_MULT", 1.5)
        pip = _pip_size(symbol)
        price = base["price"]

        sl = price - sl_points * widen_mult * pip if side == "LONG" else price + sl_points * widen_mult * pip
        tp = price + tp_points * widen_mult * pip if side == "LONG" else price - tp_points * widen_mult * pip

        info = mt5.symbol_info(symbol)
        sl = _price_round(sl, info.digits)
        tp = _price_round(tp, info.digits)

        res2 = _send_order(symbol, side, lot_size, price, sl, tp, deviation)
        if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("[EXECUTED] %s %s ok after widen (ticket=%s)", symbol, side, getattr(res2, "order", "?"))
            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side
            return {"ok": True, "result": res2._asdict(), "attempts": 2}

        log.warning("[FAILED] %s %s both attempts failed", symbol, side)
        update_trust(symbol, False)
        return {"ok": False, "reason": "execution_failed"}

    except Exception as e:
        log.exception("[EXCEPTION] %s %s failed: %s", symbol, side, e)
        update_trust(symbol, False)
        return {"ok": False, "error": str(e)}

# ------------------------------------------------------------
# Low-level MT5 order send
# ------------------------------------------------------------
def _send_order(symbol: str, side: str, lots: float, price: float,
                sl: float, tp: float, deviation: int):
    order_type = mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": deviation,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
        "comment": "xau_v46",
    }
    res = mt5.order_send(request)
    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        return res
    request["type_filling"] = mt5.ORDER_FILLING_FOK
    return mt5.order_send(request)
