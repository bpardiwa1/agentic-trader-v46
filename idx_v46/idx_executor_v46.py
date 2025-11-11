# ============================================================
# Agentic Trader idx_v46 — Executor (CM07b, Env-Driven)
# ============================================================

from __future__ import annotations
from typing import Dict, Any
import time
import MetaTrader5 as mt5

from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.util.idx_lot_scaler_v46 import compute_lot
from idx_v46.trust.idx_trust_engine_v46 import update_trust

log = setup_logger("idx_executor_v46", level=str(ENV.get("LOG_LEVEL", "INFO")))

_last_trade_time: Dict[str, float] = {}
_last_direction: Dict[str, str] = {}

def _index_point(symbol: str) -> float:
    mode = str(ENV.get("IDX_PIP_MODE", "index_point")).lower()
    if mode == "index_point":
        return 1.0
    info = mt5.symbol_info(symbol)
    return float(getattr(info, "point", 1.0)) if info else 1.0

def _rounded(price: float, symbol: str) -> float:
    info = mt5.symbol_info(symbol)
    digits = int(getattr(info, "digits", 1)) if info else 1
    return round(price, digits)

def _guardrail(symbol: str, side: str) -> bool:
    allowed = [s.strip() for s in str(ENV.get("AGENT_SYMBOLS", "NAS100.s,UK100.s,HK50.s")).split(",") if s.strip()]
    try:
        positions = [p for p in (mt5.positions_get() or []) if p.symbol in allowed]
    except Exception:
        positions = []

    max_open = int(ENV.get("IDX_AGENT_MAX_OPEN", 8))
    max_per = int(ENV.get("IDX_AGENT_MAX_PER_SYMBOL", 1))

    total_active = len(positions)
    per_symbol = len([p for p in positions if p.symbol == symbol])

    if total_active >= max_open:
        log.info("[BLOCK] global cap %d/%d", total_active, max_open)
        return False
    if per_symbol >= max_per:
        log.info("[BLOCK] %s cap %d/%d", symbol, per_symbol, max_per)
        return False

    cooldown = int(ENV.get("IDX_COOLDOWN_SEC", 300))
    last_t = _last_trade_time.get(symbol, 0)
    if time.time() - last_t < cooldown:
        log.info("[BLOCK] %s cooldown", symbol)
        return False

    if str(ENV.get("IDX_BLOCK_SAME_DIRECTION", True)).lower() == "true":
        if _last_direction.get(symbol) == side:
            log.info("[BLOCK] %s same-direction", symbol)
            return False
    return True

def execute_trade(symbol: str, side: str, sl_points: float, tp_points: float,
                  confidence: float = 0.5, atr_pct: float = 0.0) -> Dict[str, Any]:

    if not _guardrail(symbol, side):
        return {"ok": False, "blocked": True}

    lots = compute_lot(symbol=symbol, confidence=confidence, atr_pct=atr_pct)

    pt = _index_point(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return {"ok": False, "error": "no_tick"}

    price = float(tick.ask if side == "LONG" else tick.bid)
    sl = price - sl_points * pt if side == "LONG" else price + sl_points * pt
    tp = price + tp_points * pt if side == "LONG" else price - tp_points * pt
    sl = _rounded(sl, symbol); tp = _rounded(tp, symbol); price = _rounded(price, symbol)

    deviation = int(ENV.get("MT5_DEVIATION", 80))

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": deviation,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
        "comment": "idx_v46",
    }

    res = mt5.order_send(req)
    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        update_trust(symbol, True)
        _last_trade_time[symbol] = time.time(); _last_direction[symbol] = side
        return {"ok": True, "result": res._asdict(), "attempts": 1}
    res = mt5.order_send(req)
    if res:
        log.info("[EXEC] %s %s attempt1 retcode=%s comment=%s", symbol, side, res.retcode, getattr(res, "comment", ""))
    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        update_trust(symbol, True)
        _last_trade_time[symbol] = time.time(); _last_direction[symbol] = side
        return {"ok": True, "result": res._asdict(), "attempts": 1}


    widen = float(ENV.get("IDX_STOP_WIDEN_MULT", 1.5))
    sl2 = price - sl_points * widen * pt if side == "LONG" else price + sl_points * widen * pt
    tp2 = price + tp_points * widen * pt if side == "LONG" else price - tp_points * widen * pt
    req["sl"], req["tp"], req["type_filling"] = _rounded(sl2, symbol), _rounded(tp2, symbol), mt5.ORDER_FILLING_FOK

    res2 = mt5.order_send(req)
    if res2:
        log.info("[EXEC] %s %s attempt2 retcode=%s comment=%s", symbol, side, res2.retcode, getattr(res2, "comment", ""))
    if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
        update_trust(symbol, True)
        _last_trade_time[symbol] = time.time(); _last_direction[symbol] = side
        return {"ok": True, "result": res2._asdict(), "attempts": 2}
    
    update_trust(symbol, False)
    return {
        "ok": False,
        "reason": "execution_failed",
        "first": getattr(res, "comment", "") if res else "",
        "second": getattr(res2, "comment", "") if res2 else "",
    }
