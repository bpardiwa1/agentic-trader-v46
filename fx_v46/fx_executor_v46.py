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
from datetime import datetime
from typing import Any, Dict, Iterable

import MetaTrader5 as mt5  # type: ignore

from fx_v46.acmi.acmi_interface_v46 import ACMI
from fx_v46.app.fx_env_v46 import ENV
from fx_v46.trust.trust_engine_v46 import update_trust
from fx_v46.util.fx_event_sink import emit_event
from fx_v46.util.logger import setup_logger
from fx_v46.util.lot_scaler_v46 import compute_lot

# Unified FX logging
_FX_LOG_DIR = "logs/fx_v4.6"
_FX_LOG_LEVEL = str(ENV.get("FX_LOG_LEVEL", "INFO")).upper()
_FX_LOG_NAME = f"fx_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_FX_LOG_NAME, log_dir=_FX_LOG_DIR, level=_FX_LOG_LEVEL)

# ------------------------------------------------------------
# EVENT JSONL helper (watcher looks for token 'EVENT' + JSON)
# ------------------------------------------------------------
def _emit_event(event: str, **fields: Any) -> None:
    emit_event(event, fields, log=log, asset="FX")


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
    factor = 10.0**digits
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
    steps = round(lots / vstep)
    lots_ok = round(steps * vstep, 8)
    if lots_ok != lots:
        log.debug(
            "[VOLUME] snapped %.4f -> %.4f (min=%.2f step=%.2f max=%.2f)",
            lots,
            lots_ok,
            vmin,
            vstep,
            vmax,
        )
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
            "[STOPS] %s min stop level=%.1f pts (min_dist=%.6f). SL_OK=%s TP_OK=%s | "
            "price=%.6f sl=%.6f tp=%.6f",
            symbol,
            float(stops_level_points),
            float(min_dist),
            sl_ok,
            tp_ok,
            price,
            sl,
            tp,
        )
        _emit_event(
            "RISK",
            module="executor",
            symbol=symbol,
            side=side,
            reason="min_stop_level_violation",
            stops_level_points=float(stops_level_points),
            min_dist=float(min_dist),
            sl_ok=bool(sl_ok),
            tp_ok=bool(tp_ok),
            price=float(price),
            sl=float(sl),
            tp=float(tp),
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
        _emit_event(
            "ERROR",
            module="executor",
            symbol=symbol,
            side=side,
            where="mt5.positions_get",
            error=type(e).__name__,
            detail=str(e),
        )
        positions = []

    allowed = set(_as_list(getattr(ENV, "agent_symbols", [])))
    filtered = [p for p in positions if (not allowed) or (p.symbol in allowed)]

    total_active = len(filtered)
    per_symbol = len([p for p in filtered if p.symbol == symbol])

    log.info(
        "[DEBUG] Guardrail check: %s total_active=%d per_symbol=%d limits=(%d,%d)",
        symbol,
        int(total_active),
        int(per_symbol),
        int(getattr(ENV, "agent_max_open", 10)),
        int(getattr(ENV, "agent_max_per_symbol", 3)),
    )

    if total_active >= int(getattr(ENV, "agent_max_open", 10)):
        log.warning(
            "[BLOCKED] Global max open cap reached (%d/%d)",
            total_active,
            int(getattr(ENV, "agent_max_open", 10)),
        )
        _emit_event(
            "BLOCKED",
            module="executor",
            symbol=symbol,
            side=side,
            reason="max_open",
            total_active=int(total_active),
            limit=int(getattr(ENV, "agent_max_open", 10)),
        )
        return False

    if per_symbol >= int(getattr(ENV, "agent_max_per_symbol", 3)):
        log.warning(
            "[BLOCKED] %s cap reached (%d/%d)",
            symbol,
            per_symbol,
            int(getattr(ENV, "agent_max_per_symbol", 3)),
        )
        _emit_event(
            "BLOCKED",
            module="executor",
            symbol=symbol,
            side=side,
            reason="max_per_symbol",
            per_symbol=int(per_symbol),
            limit=int(getattr(ENV, "agent_max_per_symbol", 3)),
        )
        return False

    cooldown_sec = int(float(getattr(ENV, "fx_cooldown_sec", getattr(ENV, "cooldown_sec", 180)) or 180))
    last_t = _last_trade_time.get(symbol, 0)
    if time.time() - last_t < cooldown_sec:
        remaining = cooldown_sec - (time.time() - last_t)
        log.info("[BLOCKED] %s cooldown active (%.1fs remaining)", symbol, remaining)
        _emit_event(
            "BLOCKED",
            module="executor",
            symbol=symbol,
            side=side,
            reason="cooldown",
            remaining_sec=float(remaining),
            cooldown_sec=int(cooldown_sec),
        )
        return False

    if bool(getattr(ENV, "fx_block_same_direction", getattr(ENV, "block_same_direction", False))):
        last_dir = _last_direction.get(symbol)
        if last_dir == side:
            log.info("[BLOCKED] %s same-direction trade prevented (%s)", symbol, side)
            _emit_event(
                "BLOCKED",
                module="executor",
                symbol=symbol,
                side=side,
                reason="same_direction",
                last_direction=str(last_dir),
            )
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

    price = _price_round(float(price), int(info.digits))
    sl = _price_round(float(sl), int(info.digits))
    tp = _price_round(float(tp), int(info.digits))
    return {"price": price, "sl": sl, "tp": tp}


# ==============================================================
# Core trade execution
# ==============================================================
def execute_trade(
    symbol: str,
    side: str,
    base_lot: float,
    sl_pips: float,
    tp_pips: float,
    env=ENV,
    confidence: float = 0.5,
    context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Executes a market order with SL/TP attached.
    Returns dict with ok/blocked/error, and logs full MT5 diagnostics on failure.
    """
    ctx = context or {}
    ctx_fields = {
        "cycle": ctx.get("cycle"),
        "policy": ctx.get("policy"),
        "regime": ctx.get("regime"),
        "atr_pct": ctx.get("atr_pct"),
        "decision_reason": ctx.get("decision_reason"),
        "session": ctx.get("session"),
        "why": ctx.get("why"),
    }
    ctx_fields = {k: v for k, v in ctx_fields.items() if v is not None}

    def _emit_ctx(event: str, **fields: Any) -> None:
        merged = dict(ctx_fields)
        merged.update(fields)
        _emit_event(event, **merged)

    # ----------------------------------------------------------
    # OPTION A: Kill-switch — do not execute without decision_reason
    # ----------------------------------------------------------
    if not ctx_fields.get("decision_reason"):
        _emit_ctx(
            "BLOCKED",
            module="executor",
            symbol=symbol,
            side=side,
            reason="missing_decision_reason",
        )
        return {"ok": False, "blocked": True, "reason": "missing_decision_reason"}

    # ✅ Guardrails
    if not _can_open_trade(symbol, side):
        ACMI.post_status(symbol, {"guardrail_blocked": True})
        _emit_ctx(
            "BLOCKED",
            module="executor",
            symbol=symbol,
            side=side,
            reason="guardrail",
        )
        return {"ok": False, "blocked": True, "reason": "guardrail"}

    # --- Ensure symbol is tradeable/selected ---
    info = mt5.symbol_info(symbol)
    if not info:
        if not mt5.symbol_select(symbol, True):
            msg = f"symbol_select failed for {symbol}"
            log.error("[MT5] %s", msg)
            _emit_ctx(
                "FAILED",
                module="executor",
                symbol=symbol,
                side=side,
                reason="symbol_select_failed",
            )
            return {"ok": False, "reason": msg}
        info = mt5.symbol_info(symbol)

    # --- Compute and normalize lot size to broker constraints ---
    # Stabilization: treat base_lot as a CAP (prevents accidental oversizing)
    try:
        lot_from_conf = float(compute_lot(symbol, confidence))
    except Exception:
        lot_from_conf = float(base_lot)

    lot_size_raw = min(float(base_lot), lot_from_conf) if base_lot else lot_from_conf
    lot_size = _normalize_volume(symbol, lot_size_raw)

    log.info("[RISK] %s dynamic_lots=%.2f (raw=%.2f, base=%.2f, conf=%.2f)", symbol, lot_size, lot_size_raw, float(base_lot), confidence)
    _emit_ctx(
        "RISK",
        module="executor",
        symbol=symbol,
        side=side,
        confidence=float(confidence),
        lot_raw=float(lot_size_raw),
        lot=float(lot_size),
    )

    try:
        base = _build_prices(symbol, side, sl_pips, tp_pips)
        price, sl, tp = base["price"], base["sl"], base["tp"]

        if not _min_stop_distance_ok(symbol, side, price, sl, tp):
            widen = _widen_mult()
            pip = _pip_size(symbol)
            if side == "LONG":
                sl = _price_round(price - (sl_pips * widen) * pip, int(info.digits))
                tp = _price_round(price + (tp_pips * widen) * pip, int(info.digits))
            else:
                sl = _price_round(price + (sl_pips * widen) * pip, int(info.digits))
                tp = _price_round(price - (tp_pips * widen) * pip, int(info.digits))
            log.info("[STOPS] Widened pre-send using mult=%.2f -> sl=%.6f tp=%.6f", widen, sl, tp)
            _emit_ctx(
                "RISK",
                module="executor",
                symbol=symbol,
                side=side,
                reason="widen_pre_send",
                widen_mult=float(widen),
                price=float(price),
                sl=float(sl),
                tp=float(tp),
            )

        log.info("[ORDER_SEND] %s %s lots=%.2f SL=%.1f TP=%.1f", symbol, side, lot_size, sl_pips, tp_pips)
        _emit_ctx(
            "ORDER_SEND",
            module="executor",
            symbol=symbol,
            side=side,
            attempt=1,
            lots=float(lot_size),
            sl_pips=float(sl_pips),
            tp_pips=float(tp_pips),
            price=float(price),
            sl=float(sl),
            tp=float(tp),
            deviation=int(_deviation()),
        )

        res = _send_order(symbol, side, lot_size, price, sl, tp, _deviation(), ctx_fields=ctx_fields)

        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = str(getattr(res, "order", getattr(res, "deal", "?")))
            log.info("[EXECUTOR] %s %s ok lots=%.2f ticket=%s", symbol, side, lot_size, ticket)
            _emit_ctx(
                "EXECUTED",
                module="executor",
                symbol=symbol,
                side=side,
                lots=float(lot_size),
                ticket=ticket,
                attempts=1,
                retcode=int(res.retcode),
                result=res._asdict() if hasattr(res, "_asdict") else {"retcode": int(res.retcode)},
            )
            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side
            ACMI.post_status(symbol, {"executed": True, "lots": lot_size, "side": side})
            return {
                "ok": True,
                "result": res._asdict() if hasattr(res, "_asdict") else {"retcode": int(res.retcode)},
                "attempts": 1,
                "lots": float(lot_size),
                "confidence": float(confidence),
                "ticket": ticket,
            }

        # Retry: widen SL/TP slightly
        time.sleep(0.2)
        widen_mult = max(_widen_mult(), 1.2)
        pip = _pip_size(symbol)
        if side == "LONG":
            sl2 = _price_round(price - (sl_pips * widen_mult) * pip, int(info.digits))
            tp2 = _price_round(price + (tp_pips * widen_mult) * pip, int(info.digits))
        else:
            sl2 = _price_round(price + (sl_pips * widen_mult) * pip, int(info.digits))
            tp2 = _price_round(price - (tp_pips * widen_mult) * pip, int(info.digits))

        log.info("[ORDER_SEND] RETRY %s %s widen=%.2f -> sl=%.6f tp=%.6f", symbol, side, widen_mult, sl2, tp2)
        _emit_ctx(
            "ORDER_SEND",
            module="executor",
            symbol=symbol,
            side=side,
            attempt=2,
            lots=float(lot_size),
            widen_mult=float(widen_mult),
            price=float(price),
            sl=float(sl2),
            tp=float(tp2),
            deviation=int(_deviation()),
        )

        res2 = _send_order(symbol, side, lot_size, price, sl2, tp2, _deviation(), ctx_fields=ctx_fields)

        if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
            ticket2 = str(getattr(res2, "order", getattr(res2, "deal", "?")))
            log.info("[EXECUTOR] %s %s ok_after_widen lots=%.2f ticket=%s", symbol, side, lot_size, ticket2)
            _emit_ctx(
                "EXECUTED",
                module="executor",
                symbol=symbol,
                side=side,
                lots=float(lot_size),
                ticket=ticket2,
                attempts=2,
                retcode=int(res2.retcode),
                result=res2._asdict() if hasattr(res2, "_asdict") else {"retcode": int(res2.retcode)},
                retry=True,
            )
            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side
            ACMI.post_status(symbol, {"executed": True, "lots": lot_size, "side": side, "retry": True})
            return {
                "ok": True,
                "result": res2._asdict() if hasattr(res2, "_asdict") else {"retcode": int(res2.retcode)},
                "attempts": 2,
                "lots": float(lot_size),
                "confidence": float(confidence),
                "ticket": ticket2,
            }

        log.warning("[FAILED] %s %s both attempts failed", symbol, side)
        _emit_ctx(
            "FAILED",
            module="executor",
            symbol=symbol,
            side=side,
            reason="both_attempts_failed",
            attempts=2,
        )
        update_trust(symbol, False)
        ACMI.post_status(symbol, {"executed": False, "error": "order_failed"})
        return {"ok": False, "reason": "execution_failed"}

    except Exception as e:
        log.exception("[EXCEPTION] %s %s failed: %s", symbol, side, e)
        _emit_ctx(
            "ERROR",
            module="executor",
            symbol=symbol,
            side=side,
            where="execute_trade",
            error=type(e).__name__,
            detail=str(e),
        )
        update_trust(symbol, False)
        ACMI.post_status(symbol, {"executed": False, "exception": str(e)})
        return {"ok": False, "error": str(e)}


# ==============================================================
# Low-level MT5 order send with full diagnostics
# ==============================================================
def _send_order(
    symbol: str,
    side: str,
    lots: float,
    price: float,
    sl: float,
    tp: float,
    deviation: int,
    ctx_fields: Dict[str, Any] | None = None,
):
    ctx_fields = ctx_fields or {}

    def _emit_ctx(event: str, **fields: Any) -> None:
        merged = dict(ctx_fields)
        merged.update(fields)
        _emit_event(event, **merged)

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

    log.debug(
        "[MT5] Sending order: %s | side=%s | lots=%.2f | price=%.6f sl=%.6f tp=%.6f dev=%d",
        symbol,
        side,
        lots,
        price,
        sl,
        tp,
        deviation,
    )

    result = mt5.order_send(request)

    # Case 1: None → API error; show last_error
    if result is None:
        last_error = mt5.last_error()
        log.error("[ORDER_FAIL] %s order_send() returned None", symbol)
        log.error("[MT5] last_error: %s", last_error)
        _emit_ctx(
            "ORDER_RESULT",
            module="executor",
            symbol=symbol,
            side=side,
            lots=float(lots),
            ok=False,
            retcode=None,
            last_error=str(last_error),
            detail="order_send_none",
        )
        return None

    # Case 2: Non-success retcode → show full result
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        try:
            detail = result._asdict()
        except Exception:
            detail = {"retcode": int(getattr(result, "retcode", 0))}
        log.warning("[ORDER_FAIL] %s retcode=%s detail=%s", symbol, getattr(result, "retcode", None), detail)

        _emit_ctx(
            "ORDER_RESULT",
            module="executor",
            symbol=symbol,
            side=side,
            lots=float(lots),
            ok=False,
            retcode=int(result.retcode),
            detail=detail,
            filling=str(request.get("type_filling")),
        )

        # Try a different filling mode once (some brokers require FOK)
        if request["type_filling"] == mt5.ORDER_FILLING_IOC:
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            time.sleep(0.1)
            result2 = mt5.order_send(request)

            if result2 is None:
                last_error = mt5.last_error()
                log.error("[ORDER_FAIL] %s retry order_send() returned None", symbol)
                log.error("[MT5] last_error: %s", last_error)
                _emit_ctx(
                    "ORDER_RESULT",
                    module="executor",
                    symbol=symbol,
                    side=side,
                    lots=float(lots),
                    ok=False,
                    retcode=None,
                    last_error=str(last_error),
                    detail="retry_order_send_none",
                    filling=str(request.get("type_filling")),
                    retry=True,
                )
                return None

            if result2.retcode != mt5.TRADE_RETCODE_DONE:
                try:
                    detail2 = result2._asdict()
                except Exception:
                    detail2 = {"retcode": int(getattr(result2, "retcode", 0))}
                log.warning("[ORDER_FAIL] %s retry_retcode=%s detail=%s", symbol, getattr(result2, "retcode", None), detail2)
                _emit_ctx(
                    "ORDER_RESULT",
                    module="executor",
                    symbol=symbol,
                    side=side,
                    lots=float(lots),
                    ok=False,
                    retcode=int(result2.retcode),
                    detail=detail2,
                    filling=str(request.get("type_filling")),
                    retry=True,
                )
                return result2

            _emit_ctx(
                "ORDER_RESULT",
                module="executor",
                symbol=symbol,
                side=side,
                lots=float(lots),
                ok=True,
                retcode=int(result2.retcode),
                order=str(getattr(result2, "order", "")),
                deal=str(getattr(result2, "deal", "")),
                detail=result2._asdict() if hasattr(result2, "_asdict") else {"retcode": int(result2.retcode)},
                filling=str(request.get("type_filling")),
                retry=True,
            )
            return result2

        return result

    # Case 3: Success
    log.info("[ORDER_OK] %s order=%s retcode=%s", symbol, getattr(result, "order", ""), getattr(result, "retcode", ""))
    _emit_ctx(
        "ORDER_RESULT",
        module="executor",
        symbol=symbol,
        side=side,
        lots=float(lots),
        ok=True,
        retcode=int(result.retcode),
        order=str(getattr(result, "order", "")),
        deal=str(getattr(result, "deal", "")),
        detail=result._asdict() if hasattr(result, "_asdict") else {"retcode": int(result.retcode)},
    )
    return result