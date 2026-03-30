# xau_executor_v46.py
# ============================================================
# Agentic Trader XAU v4.6 — MT5 Trade Executor (Enhanced)
# ------------------------------------------------------------
# Changes:
#  • Step size now env-driven (XAU_LOT_STEP)
#  • Filling mode fixed to IOC (per broker spec)
#  • Retcode + comment logging preserved
#  • PATCH: expose ticket/order/deal at top-level (for attribution mapping)
# ============================================================

from __future__ import annotations

import json
import time
from typing import Dict, Any
import MetaTrader5 as mt5  # type: ignore
from datetime import datetime, timedelta

from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.logger import setup_logger
from xau_v46.trust.xau_trust_engine_v46 import update_trust
from xau_v46.util.xau_lot_scaler_v46 import compute_lot
from xau_v46.util.xau_event_sink import emit_event as _emit_event


# Unified XAU logging
_XAU_LOG_DIR = "logs/xau_v4.6"
_XAU_LOG_LEVEL = str(ENV.get("XAU_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_XAU_LOG_NAME = f"xau_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_XAU_LOG_NAME, log_dir=_XAU_LOG_DIR, level=_XAU_LOG_LEVEL)



_last_trade_time: Dict[str, float] = {}
_last_direction: Dict[str, str] = {}


def _pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.1  # XAUUSD pip ~0.1 USD


def _price_round(price: float, digits: int) -> float:
    factor = 10.0 ** digits
    return round(price * factor) / factor


def _get_env_value(key: str, default: float | int) -> float:
    try:
        return float(ENV.get(key, default))
    except Exception:
        return float(default)


def _seed_last_trade_time(symbol: str, lookback_sec: int) -> float:
    """
    Seed last trade timestamp from MT5 so cooldown survives process restarts.

    Priority:
      1) Open positions for the symbol (position open time)
      2) Recent deals history within lookback window (last deal time)

    Returns epoch seconds (float) or 0.0 if unavailable.
    """
    # 1) Open positions (if any)
    try:
        pos = mt5.positions_get(symbol=symbol) or []
        if pos:
            try:
                return float(max(p.time for p in pos))
            except Exception:
                return float(pos[0].time)
    except Exception:
        pass

    # 2) Deal history (recent closed deals)
    try:
        to_dt = datetime.now()
        frm_dt = to_dt - timedelta(seconds=max(60, int(lookback_sec)))
        deals = mt5.history_deals_get(frm_dt, to_dt, group=symbol) or []
        if deals:
            try:
                return float(max(d.time for d in deals))
            except Exception:
                return float(deals[-1].time)
    except Exception:
        pass

    return 0.0


def _guardrail_check(symbol: str, side: str) -> bool:
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

    log.info(
        "[DEBUG] Guardrail: %s total=%d per_symbol=%d limits=(%d,%d)",
        symbol,
        total_active,
        per_symbol,
        max_open,
        max_per_symbol,
    )

    if total_active >= max_open:
        log.warning("[BLOCKED] Global cap reached (%d/%d)", total_active, max_open)
        return False
    if per_symbol >= max_per_symbol:
        log.warning("[BLOCKED] %s cap reached (%d/%d)", symbol, per_symbol, max_per_symbol)
        return False

    cooldown_sec = int(_get_env_value("XAU_COOLDOWN_SEC", 300))
    last_t = float(_last_trade_time.get(symbol, 0.0) or 0.0)

    # SCCR PATCH: On process restart, _last_trade_time is empty. Seed it from MT5
    # so cooldown does not get bypassed on startup.
    if last_t <= 0.0 and cooldown_sec > 0:
        seeded = _seed_last_trade_time(symbol, lookback_sec=max(3600, cooldown_sec * 6))
        if seeded > 0.0:
            _last_trade_time[symbol] = seeded
            last_t = seeded
            log.info("[GUARDRAIL] %s seeded last_trade_time=%.0f (cooldown=%ds)", symbol, last_t, cooldown_sec)

    now = time.time()
    elapsed = now - last_t

    if last_t > 0.0 and elapsed < cooldown_sec:
        remaining = cooldown_sec - elapsed
        log.info(
            "[BLOCKED] %s cooldown active (%.1fs remaining, last_t=%.3f now=%.3f elapsed=%.1fs)",
            symbol,
            remaining,
            last_t,
            now,
            elapsed,
        )
        return False

    # Same-direction guardrail
    # IMPORTANT: Do NOT block indefinitely.
    # Only block same-direction re-entry within a time window (defaults to cooldown).
    if str(ENV.get("XAU_BLOCK_SAME_DIRECTION", "True")).strip().lower() in ("1", "true", "yes", "on"):
        last_dir = _last_direction.get(symbol)

        # Window (seconds) in which we prevent same-direction re-entry.
        # Default = cooldown. If cooldown is 0, default window is 0 (disabled).
        same_dir_window_sec = int(_get_env_value("XAU_SAME_DIR_BLOCK_SEC", cooldown_sec))

        # If we have a last direction and we're still within the window, block.
        # If last_t is unknown (0), do not block (avoid "forever" lock).
        if last_dir == side and last_t > 0.0 and elapsed < max(0, same_dir_window_sec):
            remaining = max(0.0, float(same_dir_window_sec) - float(elapsed))
            log.info(
                "[BLOCKED] %s same-direction re-entry prevented (%s) (%.1fs remaining)",
                symbol,
                side,
                remaining,
            )
            return False

    return True


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


def execute_trade(
    symbol: str,
    side: str,
    base_lot: float,
    sl_points: float,
    tp_points: float,
    confidence: float = 0.5,
    atr_pct: float = 0.0,
) -> Dict[str, Any]:

    if not _guardrail_check(symbol, side):
        return {"ok": False, "blocked": True, "reason": "guardrail_limit"}

    if not mt5.symbol_info(symbol):
        if not mt5.symbol_select(symbol, True):
            return {"ok": False, "reason": f"symbol_select failed for {symbol}"}

    info = mt5.symbol_info(symbol)

    # --- Dynamic lot sizing via scaler + broker rounding ---------------
    env_step = _get_env_value("XAU_LOT_STEP", 0.01)
    step = info.volume_step if (info and info.volume_step > 0) else env_step

    # raw lot from confidence × trust × ATR% (see xau_lot_scaler_v46)
    lot_size_raw = compute_lot(symbol, confidence, atr_pct=atr_pct)

    # broker-safe snapping
    lot_size = round(lot_size_raw / step) * step
    if info and getattr(info, "volume_min", 0) > 0:
        lot_size = max(info.volume_min, lot_size)
    lot_size = round(lot_size, 2)

    log.info(
        "[RISK] %s dynamic_lots=%.2f (raw=%.2f, conf=%.2f, atr=%.4f, step=%.2f)",
        symbol,
        lot_size,
        lot_size_raw,
        confidence,
        atr_pct,
        step,
    )
    _emit_event(
        "RISK",
        {
            "symbol": symbol,
            "lots": round(float(lot_size), 4),
            "lots_raw": round(float(lot_size_raw), 4),
            "confidence": round(float(confidence), 4),
            "atr_pct": round(float(atr_pct), 6),
            "step": round(float(step), 6),
        },
    )

    try:
        base = _build_prices(symbol, side, sl_points, tp_points)
        deviation = int(_get_env_value("MT5_DEVIATION", 50))

        log.info("[ORDER] %s %s lots=%.2f SL=%.1f TP=%.1f", symbol, side, lot_size, sl_points, tp_points)

        _emit_event(
            "ORDER_SEND",
            {
                "symbol": symbol,
                "side": side,
                "attempt": 1,
                "lots": round(float(lot_size), 4),
                "price": round(float(base.get("price", 0.0)), 6),
                "sl": round(float(base.get("sl", 0.0)), 6),
                "tp": round(float(base.get("tp", 0.0)), 6),
                "deviation": int(deviation),
            },
        )

        res = _send_order(symbol, side, lot_size, base["price"], base["sl"], base["tp"], deviation)

        if res:
            log.info("[EXEC] %s %s retcode=%s comment=%s", symbol, side, res.retcode, getattr(res, "comment", ""))

        try:
            _payload = res._asdict() if res else {}
        except Exception:
            _payload = {}

        _emit_event(
            "ORDER_RESULT",
            {
                "symbol": symbol,
                "side": side,
                "attempt": 1,
                "retcode": int(getattr(res, "retcode", -1)) if res else -1,
                "comment": str(getattr(res, "comment", "")) if res else "",
                "order": _payload.get("order"),
                "deal": _payload.get("deal"),
                "request_id": _payload.get("request_id"),
            },
        )

        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            payload = res._asdict()
            order = payload.get("order")
            deal = payload.get("deal")
            ticket = deal or order

            log.info("[EXECUTOR] %s %s ok (%.2f lots, ticket=%s)", symbol, side, lot_size, ticket)

            _emit_event(
                "EXECUTED",
                {
                    "symbol": symbol,
                    "side": side,
                    "attempt": 1,
                    "ticket": str(ticket),
                    "order": order,
                    "deal": deal,
                    "lots": round(float(lot_size), 4),
                },
            )

            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side

            return {
                "ok": True,
                "ticket": ticket,
                "order": order,
                "deal": deal,
                "result": payload,
                "attempts": 1,
                "lots": float(lot_size),
                "confidence": float(confidence),
            }

        # attempt #2 widen
        widen_mult = _get_env_value("XAU_STOP_WIDEN_MULT", 1.5)
        pip = _pip_size(symbol)
        price = base["price"]

        sl = price - sl_points * widen_mult * pip if side == "LONG" else price + sl_points * widen_mult * pip
        tp = price + tp_points * widen_mult * pip if side == "LONG" else price - tp_points * widen_mult * pip

        sl = _price_round(sl, info.digits)
        tp = _price_round(tp, info.digits)

        res2 = _send_order(symbol, side, lot_size, price, sl, tp, deviation)

        if res2:
            log.info(
                "[EXEC] %s %s attempt2 retcode=%s comment=%s",
                symbol,
                side,
                res2.retcode,
                getattr(res2, "comment", ""),
            )

        try:
            _payload2 = res2._asdict() if res2 else {}
        except Exception:
            _payload2 = {}

        _emit_event(
            "ORDER_RESULT",
            {
                "symbol": symbol,
                "side": side,
                "attempt": 2,
                "retcode": int(getattr(res2, "retcode", -1)) if res2 else -1,
                "comment": str(getattr(res2, "comment", "")) if res2 else "",
                "order": _payload2.get("order"),
                "deal": _payload2.get("deal"),
                "request_id": _payload2.get("request_id"),
            },
        )

        if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
            payload2 = res2._asdict()
            order2 = payload2.get("order")
            deal2 = payload2.get("deal")
            ticket2 = deal2 or order2

            log.info("[EXECUTOR] %s %s ok after widen (%.2f lots, ticket=%s)", symbol, side, lot_size, ticket2)

            _emit_event(
                "EXECUTED",
                {
                    "symbol": symbol,
                    "side": side,
                    "attempt": 2,
                    "ticket": str(ticket2),
                    "order": order2,
                    "deal": deal2,
                    "lots": round(float(lot_size), 4),
                },
            )

            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side

            return {
                "ok": True,
                "ticket": ticket2,
                "order": order2,
                "deal": deal2,
                "result": payload2,
                "attempts": 2,
                "lots": float(lot_size),
                "confidence": float(confidence),
            }

        log.warning("[FAILED] %s %s both attempts failed", symbol, side)
        _emit_event(
            "FAILED",
            {
                "symbol": symbol,
                "side": side,
                "reason": "execution_failed",
            },
        )
        update_trust(symbol, False)
        return {"ok": False, "reason": "execution_failed"}

    except Exception as e:
        log.exception("[EXCEPTION] %s %s failed: %s", symbol, side, e)
        _emit_event(
            "ERROR",
            {
                "symbol": symbol,
                "side": side,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        update_trust(symbol, False)
        return {"ok": False, "error": str(e)}


def _send_order(symbol: str, side: str, lots: float, price: float, sl: float, tp: float, deviation: int):
    order_type = mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL

    # Broker requires IOC (Immediate or Cancel)
    fill_mode = mt5.ORDER_FILLING_IOC

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
        "type_filling": fill_mode,
        "comment": "xau_v46",
    }
    return mt5.order_send(request)