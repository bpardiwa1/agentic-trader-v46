"""
Agentic Trader FX v4 — MT5 Trade Executor (Dynamic v4)
------------------------------------------------------
Handles order submission, adaptive lot sizing, broker guardrails,
and trust updates.
"""

from __future__ import annotations
from typing import Dict, Any
import MetaTrader5 as mt5  # type: ignore

from fx_v4.app.fx_env import resolve_symbol, ENV
from fx_v4.util.logger import setup_logger
from fx_v4.util.lot_scaler import compute_lot
from fx_v4.trust.trust_engine import update_trust

log = setup_logger("fx_executor", level="INFO")

# ---------------------------------------------------
# Helper functions
# ---------------------------------------------------
def _pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.0001

def _price_round(price: float, digits: int) -> float:
    factor = 10.0 ** digits
    return round(price * factor) / factor

def _min_stop_points(info) -> int:
    lvl = getattr(info, "trade_stops_level", 0) or 0
    dist = getattr(info, "trade_stops_distance", 0) or 0
    return max(int(lvl), int(dist), 0)

def _deviation() -> int:
    return int(getattr(ENV, "mt5_deviation", 50) or 50)

def _widen_mult() -> float:
    return float(getattr(ENV, "mt5_stop_widen_mult", 2.0) or 2.0)

# ---------------------------------------------------
# Guardrail: trade limits
# ---------------------------------------------------
def _can_open_trade(symbol: str) -> bool:
    positions = mt5.positions_get() or []
    fx_symbols = [p for p in positions if any(x in p.symbol.upper() for x in ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CHF", "CAD"])]
    total_fx = len(fx_symbols)
    per_symbol = len([p for p in fx_symbols if p.symbol == symbol])

    # 🧩 Diagnostic line
    log.info("[DEBUG] Guardrail check: %s total_fx=%d per_symbol=%d limits=(%d,%d)",
             symbol, total_fx, per_symbol, ENV.agent_max_open, ENV.agent_max_per_symbol)

    if total_fx >= ENV.agent_max_open:
        log.warning("[BLOCKED] FX trade cap reached (%d/%d).", total_fx, ENV.agent_max_open)
        return False
    if per_symbol >= ENV.agent_max_per_symbol:
        log.warning("[BLOCKED] %s cap reached (%d/%d).", symbol, per_symbol, ENV.agent_max_per_symbol)
        return False

    return True


# ---------------------------------------------------
# Price building
# ---------------------------------------------------
def _build_prices(symbol: str, side: str, sl_pips: float, tp_pips: float) -> Dict[str, float]:
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        raise RuntimeError(f"No tick for {symbol}")
    price = tick.ask if side == "LONG" else tick.bid
    pip = _pip_size(symbol)
    sl = price - sl_pips * pip if side == "LONG" else price + sl_pips * pip
    tp = price + tp_pips * pip if side == "LONG" else price - tp_pips * pip

    info = mt5.symbol_info(symbol)
    if not info:
        raise RuntimeError(f"No symbol_info for {symbol}")

    price = _price_round(price, info.digits)
    sl = _price_round(sl, info.digits)
    tp = _price_round(tp, info.digits)
    return {"price": price, "sl": sl, "tp": tp}

def _respect_min_stops(symbol: str, side: str, price: float, sl: float, tp: float) -> Dict[str, float]:
    info = mt5.symbol_info(symbol)
    if not info:
        return {"price": price, "sl": sl, "tp": tp}
    min_points = _min_stop_points(info)
    if min_points <= 0:
        return {"price": price, "sl": sl, "tp": tp}

    def _points(a, b): return abs(a - b) / info.point
    adj_sl, adj_tp = sl, tp
    if side == "LONG":
        if _points(price, sl) < min_points:
            adj_sl = price - min_points * info.point
        if _points(price, tp) < min_points:
            adj_tp = price + min_points * info.point
    else:
        if _points(price, sl) < min_points:
            adj_sl = price + min_points * info.point
        if _points(price, tp) < min_points:
            adj_tp = price - min_points * info.point

    adj_sl = _price_round(adj_sl, info.digits)
    adj_tp = _price_round(adj_tp, info.digits)
    return {"price": price, "sl": adj_sl, "tp": adj_tp}

# ---------------------------------------------------
# Core trade executor
# ---------------------------------------------------
def execute_trade(symbol: str, side: str, base_lot: float, sl_pips: float, tp_pips: float,
                  env=ENV, confidence: float = 0.5) -> Dict[str, Any]:
    symbol = resolve_symbol(symbol)

    # --- Guardrails ---
    if not _can_open_trade(symbol):
        return {"ok": False, "reason": "guardrail_blocked"}

    if not mt5.symbol_info(symbol):
        if not mt5.symbol_select(symbol, True):
            return {"ok": False, "reason": f"symbol_select failed for {symbol}"}

    # --- Dynamic Lot Scaling ---
    lot_size = compute_lot(symbol, confidence)
    log.info("[RISK] %s dynamic_lots=%.2f (conf=%.2f)", symbol, lot_size, confidence)

    try:
        base = _build_prices(symbol, side, sl_pips, tp_pips)
        base = _respect_min_stops(symbol, side, base["price"], base["sl"], base["tp"])

        log.info("[ORDER] %s %s lots=%.2f SL=%.1f TP=%.1f",
                 symbol, side, lot_size, sl_pips, tp_pips)
        res = _send_order(symbol, side, lot_size, base["price"], base["sl"], base["tp"], _deviation())

        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("[EXECUTED] %s %s ok (ticket=%s)", symbol, side, getattr(res, "order", "?"))
            update_trust(symbol, True)  # mild positive reinforcement
            return {"ok": True, "result": res._asdict(), "attempts": 1}

        # Retry widened
        widen_mult = _widen_mult()
        info = mt5.symbol_info(symbol)
        pip = _pip_size(symbol)
        price = base["price"]

        if side == "LONG":
            sl = price - (sl_pips * widen_mult) * pip
            tp = price + (tp_pips * widen_mult) * pip
        else:
            sl = price + (sl_pips * widen_mult) * pip
            tp = price - (tp_pips * widen_mult) * pip

        sl = _price_round(sl, info.digits)
        tp = _price_round(tp, info.digits)
        adj = _respect_min_stops(symbol, side, price, sl, tp)

        res2 = _send_order(symbol, side, lot_size, adj["price"], adj["sl"], adj["tp"], _deviation())
        if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("[EXECUTED] %s %s ok after widen (ticket=%s)", symbol, side, getattr(res2, "order", "?"))
            update_trust(symbol, True)
            return {"ok": True, "result": res2._asdict(), "attempts": 2}

        log.warning("[FAILED] %s %s both attempts failed", symbol, side)
        update_trust(symbol, False)
        return {"ok": False, "result": {"first": res._asdict() if res else {}, "second": res2._asdict() if res2 else {}}, "attempts": 2}

    except Exception as e:
        log.exception("[EXCEPTION] %s %s failed: %s", symbol, side, e)
        update_trust(symbol, False)
        return {"ok": False, "error": str(e), "attempts": 0}

# ---------------------------------------------------
def _send_order(symbol: str, side: str, lots: float, price: float, sl: float, tp: float, deviation: int):
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
        "comment": "fx_v4",
    }
    res = mt5.order_send(request)
    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        return res

    request["type_filling"] = mt5.ORDER_FILLING_FOK
    return mt5.order_send(request)
