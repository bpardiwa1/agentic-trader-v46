"""
Agentic Trader FX v4.6 — MT5 Trade Executor (Enhanced)
------------------------------------------------------
Includes:
 - Guardrails: max trades, cooldown, same-direction blocking
 - Dynamic lot sizing from confidence
 - Trust integration
 - Pure .env-driven configuration
 - Full MT5 diagnostics (retcode + last_error)
 - Broker preflight (lot step/min/max, stop level)
"""

from __future__ import annotations
import time
from typing import Dict, Any, Iterable
import MetaTrader5 as mt5  # type: ignore

from fx_v46.app.fx_env_v46 import ENV
from fx_v46.util.logger import setup_logger
from fx_v46.util.lot_scaler_v46 import compute_lot
from fx_v46.trust.trust_engine_v46 import update_trust
from fx_v46.acmi.acmi_interface_v46 import ACMI

log = setup_logger("fx_executor_v46", level="INFO")

# ==============================================================
# Runtime memory for cooldown and direction tracking
# ==============================================================
_last_trade_time: Dict[str, float] = {}
_last_direction: Dict[str, str] = {}

# ==============================================================
# Helpers
# ==============================================================
def _as_list(val: Any) -> list[str]:
    if isinstance(val, str):
        return [s.strip() for s in val.split(",") if s.strip()]
    if isinstance(val, Iterable):
        return [str(x).strip() for x in val if str(x).strip()]
    return []

def _pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.0001

def _price_round(price: float, digits: int) -> float:
    factor = 10.0 ** digits
    return round(price * factor) / factor

def _deviation() -> int:
    try:
        return int(float(getattr(ENV, "mt5_deviation", 50) or 50))
    except Exception:
        return 50

def _widen_mult() -> float:
    try:
        return float(getattr(ENV, "mt5_stop_widen_mult", 2.0) or 2.0)
    except Exception:
        return 2.0

def _normalize_volume(symbol: str, lots: float) -> float:
    """Snap volume to broker min/step/max."""
    info = mt5.symbol_info(symbol)
    if not info:
        return lots
    vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
    vmax = float(getattr(info, "volume_max", 100.0) or 100.0)
    vstep = float(getattr(info, "volume_step", 0.01) or 0.01)

    lots = max(vmin, min(vmax, lots))
    # snap to step
    steps = round(lots / vstep)
    lots_ok = round(steps * vstep, 8)
    if lots_ok != lots:
        log.debug("[VOLUME] snapped %.4f -> %.4f (min=%.2f step=%.2f max=%.2f)", lots, lots_ok, vmin, vstep, vmax)
    return lots_ok

def _min_stop_distance_ok(symbol: str, side: str, price: float, sl: float, tp: float) -> bool:
    """Check broker min stop level; log warning if too close."""
    info = mt5.symbol_info(symbol)
    if not info:
        return True
    stops_level_points = getattr(info, "trade_stops_level", 0) or 0  # points
    if stops_level_points <= 0:
        return True
    point = getattr(info, "point", 0.0) or (10 ** -getattr(info, "digits", 5))
    min_dist = stops_level_points * point

    sl_ok = abs(price - sl) >= min_dist
    tp_ok = abs(price - tp) >= min_dist
    if not (sl_ok and tp_ok):
        log.warning(
            "[STOPS] %s min stop level=%.1f pts (min_dist=%.6f). SL_OK=%s TP_OK=%s | price=%.6f sl=%.6f tp=%.6f",
            symbol, float(stops_level_points), float(min_dist), sl_ok, tp_ok, price, sl, tp
        )
    return sl_ok and tp_ok

# ==============================================================
# Guardrail checks
# ==============================================================
def _can_open_trade(symbol: str, side: str) -> bool:
    """Full guardrail validation."""
    try:
        positions = mt5.positions_get() or []
    except Exception as e:
        log.warning("[GUARDRAIL] mt5.positions_get() failed: %s", e)
        positions = []

    allowed = set(_as_list(getattr(ENV, "agent_symbols", [])))
    filtered = [p for p in positions if (not allowed) or (p.symbol in allowed)]

    total_active = len(filtered)
    per_symbol = len([p for p in filtered if p.symbol == symbol])

    # Log with safe types
    log.info(
        "[DEBUG] Guardrail check: %s total_active=%d per_symbol=%d limits=(%d,%d)",
        symbol, int(total_active), int(per_symbol), int(getattr(ENV, "agent_max_open", 10)), int(getattr(ENV, "agent_max_per_symbol", 3))
    )

    # --- Max open limits ---
    if total_active >= int(getattr(ENV, "agent_max_open", 10)):
        log.warning("[BLOCKED] Global max open cap reached (%d/%d)", total_active, int(getattr(ENV, "agent_max_open", 10)))
        return False
    if per_symbol >= int(getattr(ENV, "agent_max_per_symbol", 3)):
        log.warning("[BLOCKED] %s cap reached (%d/%d)", symbol, per_symbol, int(getattr(ENV, "agent_max_per_symbol", 3)))
        return False

    # --- Cooldown guardrail ---
    cooldown_sec = int(float(getattr(ENV, "fx_cooldown_sec", getattr(ENV, "cooldown_sec", 180)) or 180))
    last_t = _last_trade_time.get(symbol, 0)
    if time.time() - last_t < cooldown_sec:
        remaining = cooldown_sec - (time.time() - last_t)
        log.info("[BLOCKED] %s cooldown active (%.1fs remaining)", symbol, remaining)
        return False

    # --- Same-direction guardrail ---
    if bool(getattr(ENV, "fx_block_same_direction", getattr(ENV, "block_same_direction", False))):
        last_dir = _last_direction.get(symbol)
        if last_dir == side:
            log.info("[BLOCKED] %s same-direction trade prevented (%s)", symbol, side)
            return False

    return True

# ==============================================================
# Price builder
# ==============================================================
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

# ==============================================================
# Core trade execution
# ==============================================================
def execute_trade(symbol: str, side: str, base_lot: float,
                  sl_pips: float, tp_pips: float,
                  env=ENV, confidence: float = 0.5) -> Dict[str, Any]:
    """
    Executes a market order with SL/TP attached.
    Returns dict with ok/blocked/error, and logs full MT5 diagnostics on failure.
    """

    # ✅ Guardrails
    if not _can_open_trade(symbol, side):
        ACMI.post_status(symbol, {"guardrail_blocked": True})
        return {"ok": False, "blocked": True, "reason": "guardrail"}

    # --- Ensure symbol is tradeable/selected ---
    info = mt5.symbol_info(symbol)
    if not info:
        if not mt5.symbol_select(symbol, True):
            msg = f"symbol_select failed for {symbol}"
            log.error("[MT5] %s", msg)
            return {"ok": False, "reason": msg}
        info = mt5.symbol_info(symbol)

    # --- Compute and normalize lot size to broker constraints ---
    lot_size_raw = compute_lot(symbol, confidence)
    lot_size = _normalize_volume(symbol, lot_size_raw)
    log.info("[RISK] %s dynamic_lots=%.2f (raw=%.2f, conf=%.2f)", symbol, lot_size, lot_size_raw, confidence)

    try:
        # --- Build initial prices ---
        base = _build_prices(symbol, side, sl_pips, tp_pips)
        price, sl, tp = base["price"], base["sl"], base["tp"]

        # --- Check min stop distance; if too close, widen now (before send) ---
        if not _min_stop_distance_ok(symbol, side, price, sl, tp):
            widen = _widen_mult()
            pip = _pip_size(symbol)
            if side == "LONG":
                sl = _price_round(price - (sl_pips * widen) * pip, info.digits)
                tp = _price_round(price + (tp_pips * widen) * pip, info.digits)
            else:
                sl = _price_round(price + (sl_pips * widen) * pip, info.digits)
                tp = _price_round(price - (tp_pips * widen) * pip, info.digits)
            log.info("[STOPS] Widened pre-send using mult=%.2f -> sl=%.6f tp=%.6f", widen, sl, tp)

        log.info("[ORDER] %s %s lots=%.2f SL=%.1f TP=%.1f", symbol, side, lot_size, sl_pips, tp_pips)
        res = _send_order(symbol, side, lot_size, price, sl, tp, _deviation())
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("[EXECUTED] %s %s ok (ticket=%s)", symbol, side, getattr(res, "order", "?"))
            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side
            ACMI.post_status(symbol, {"executed": True, "lots": lot_size, "side": side})
            return {"ok": True, "result": res._asdict(), "attempts": 1}

        # --- Retry: widen SL/TP a bit more and small delay ---
        time.sleep(0.2)
        widen_mult = max(_widen_mult(), 1.2)
        pip = _pip_size(symbol)
        if side == "LONG":
            sl2 = _price_round(price - (sl_pips * widen_mult) * pip, info.digits)
            tp2 = _price_round(price + (tp_pips * widen_mult) * pip, info.digits)
        else:
            sl2 = _price_round(price + (sl_pips * widen_mult) * pip, info.digits)
            tp2 = _price_round(price - (tp_pips * widen_mult) * pip, info.digits)

        log.info("[ORDER] RETRY %s %s widen=%.2f -> sl=%.6f tp=%.6f", symbol, side, widen_mult, sl2, tp2)
        res2 = _send_order(symbol, side, lot_size, price, sl2, tp2, _deviation())
        if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
            log.info("[EXECUTED] %s %s ok after widen (ticket=%s)", symbol, side, getattr(res2, "order", "?"))
            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side
            ACMI.post_status(symbol, {"executed": True, "lots": lot_size, "side": side, "retry": True})
            return {"ok": True, "result": res2._asdict(), "attempts": 2}

        # --- Failure ---
        log.warning("[FAILED] %s %s both attempts failed", symbol, side)
        update_trust(symbol, False)
        ACMI.post_status(symbol, {"executed": False, "error": "order_failed"})
        return {"ok": False, "reason": "execution_failed"}

    except Exception as e:
        log.exception("[EXCEPTION] %s %s failed: %s", symbol, side, e)
        update_trust(symbol, False)
        ACMI.post_status(symbol, {"executed": False, "exception": str(e)})
        return {"ok": False, "error": str(e)}

# ==============================================================
# Low-level MT5 order send with full diagnostics
# ==============================================================
def _send_order(symbol: str, side: str, lots: float, price: float,
                sl: float, tp: float, deviation: int):
    order_type = mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": order_type,
        "price": float(price),
        "sl": float(sl),
        "tp": float(tp),
        "deviation": int(deviation),
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
        "comment": "fx_v46",
    }

    log.debug("[MT5] Sending order: %s | side=%s | lots=%.2f | price=%.6f sl=%.6f tp=%.6f dev=%d",
              symbol, side, lots, price, sl, tp, deviation)

    result = mt5.order_send(request)

    # Case 1: None → API error; show last_error
    if result is None:
        last_error = mt5.last_error()
        log.error("[MT5] order_send() returned None for %s", symbol)
        log.error("[MT5] last_error: %s", last_error)
        return None

    # Case 2: Non-success retcode → show full result
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        try:
            detail = result._asdict()
        except Exception:
            detail = {"retcode": result.retcode}
        log.warning("[MT5] order_send() failed: retcode=%s -> %s", result.retcode, detail)

        # Try a different filling mode once (some brokers require FOK)
        if request["type_filling"] == mt5.ORDER_FILLING_IOC:
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            time.sleep(0.1)
            result2 = mt5.order_send(request)
            if result2 is None:
                last_error = mt5.last_error()
                log.error("[MT5] retry order_send() returned None for %s", symbol)
                log.error("[MT5] last_error: %s", last_error)
                return None
            if result2.retcode != mt5.TRADE_RETCODE_DONE:
                try:
                    detail2 = result2._asdict()
                except Exception:
                    detail2 = {"retcode": result2.retcode}
                log.warning("[MT5] retry failed: retcode=%s -> %s", result2.retcode, detail2)
                return result2
            return result2

        return result

    # Case 3: Success
    log.info("[MT5] order_send() success: %s -> order=%s retcode=%s", symbol, result.order, result.retcode)
    return result
