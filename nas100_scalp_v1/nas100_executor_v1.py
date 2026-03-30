# ============================================================
# NAS100 Scalper v1 — Executor (fork of IDX v4.6, minimal-diff)
# ============================================================

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict

import MetaTrader5 as mt5

from nas100_scalp_v1.app.nas100_env_v1 import ENV
from nas100_scalp_v1.trust.nas100_trust_engine_v1 import update_trust
from nas100_scalp_v1.util.nas100_logger_v1 import setup_logger
from nas100_scalp_v1.util.nas100_lot_scaler_v1 import compute_lot
from nas100_scalp_v1.util.nas100_event_sink_v1 import emit_event

# Unified scalper logging
_LOG_DIR = "logs/nas100_scalp_v1"
_LOG_LEVEL = str(ENV.get("IDX_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_LOG_NAME = f"nas100_scalp_v1_{datetime.now():%Y-%m-%d}"
log = setup_logger(_LOG_NAME, log_dir=_LOG_DIR, level=_LOG_LEVEL)

def _emit_event(event: str, **fields):
    emit_event(event, fields, log=log, asset="INDEX")


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


def _build_order_comment(reason: Any | None) -> str:
    try:
        tag = "no_reason"
        if isinstance(reason, str) and reason.strip():
            tag = reason.strip()
        elif isinstance(reason, (list, tuple)) and len(reason) > 0:
            first = reason[0]
            if isinstance(first, str) and first.strip():
                tag = first.strip()
        tag = tag.replace(" ", "_")
        if len(tag) > 18:
            tag = tag[:18]
        return f"scalp|{tag}"
    except Exception:
        return "scalp|no_reason"


def _is_busy_retcode(retcode: int | None) -> bool:
    try:
        return int(retcode or 0) == 10016
    except Exception:
        return False


def _guardrail(symbol: str, side: str) -> bool:
    allowed = [
        s.strip()
        for s in str(ENV.get("AGENT_SYMBOLS", "NAS100.s")).split(",")
        if s.strip()
    ]
    try:
        positions = [p for p in (mt5.positions_get() or []) if p.symbol in allowed]
    except Exception:
        positions = []

    base = symbol.upper().split(".")[0]

    max_open = int(ENV.get("IDX_AGENT_MAX_OPEN", 2))
    default_max_per = int(ENV.get("IDX_AGENT_MAX_PER_SYMBOL", 1))

    per_key = f"{base}_MAX_POSITIONS"
    max_per = default_max_per
    override_val = ENV.get(per_key)
    if override_val is not None:
        try:
            max_per = int(override_val)
        except Exception:
            max_per = default_max_per

    total_active = len(positions)
    sym_positions = [p for p in positions if p.symbol == symbol]
    per_symbol = len(sym_positions)

    same_dir = 0
    side_up = (side or "").upper()
    try:
        for p in sym_positions:
            p_type = getattr(p, "type", None)
            if side_up == "LONG" and p_type == mt5.POSITION_TYPE_BUY:
                same_dir += 1
            elif side_up == "SHORT" and p_type == mt5.POSITION_TYPE_SELL:
                same_dir += 1
    except Exception:
        same_dir = per_symbol

    log.info("[GUARD] %s total=%d/%d symbol=%d/%d same_dir=%d", symbol, total_active, max_open, per_symbol, max_per, same_dir)

    if total_active >= max_open:
        _emit_event("BLOCKED", module="executor", symbol=symbol, side=side_up, reason="global_cap", total_active=int(total_active), max_open=int(max_open))
        return False

    if per_symbol >= max_per:
        _emit_event("BLOCKED", module="executor", symbol=symbol, side=side_up, reason="per_symbol_cap", per_symbol=int(per_symbol), max_per=int(max_per))
        return False

    cooldown = int(ENV.get("IDX_COOLDOWN_SEC", 120))
    last_t = float(_last_trade_time.get(symbol, 0.0))
    if cooldown > 0 and time.time() - last_t < cooldown:
        _emit_event("BLOCKED", module="executor", symbol=symbol, side=side_up, reason="cooldown", cooldown_sec=int(cooldown), elapsed_sec=float(time.time() - last_t))
        return False

    block_same_raw = ENV.get("IDX_BLOCK_SAME_DIRECTION", True)
    block_same = str(block_same_raw).lower() in ("1", "true", "yes", "on")

    pyr_flag = ENV.get(f"{base}_PYRAMID_MODE", ENV.get("IDX_PYRAMID_MODE", "false"))
    pyramiding = str(pyr_flag).lower() in ("1", "true", "yes", "on") and max_per > 1

    if block_same and _last_direction.get(symbol) == side:
        if not pyramiding:
            _emit_event("BLOCKED", module="executor", symbol=symbol, side=side_up, reason="same_direction")
            return False

    return True


def execute_trade(
    symbol: str,
    side: str,
    sl_points: float,
    tp_points: float,
    confidence: float = 0.5,
    atr_pct: float = 0.0,
    *,
    reason: Any | None = None,
) -> Dict[str, Any]:

    if not _guardrail(symbol, side):
        return {"ok": False, "blocked": True}

    lots = compute_lot(symbol=symbol, confidence=confidence, atr_pct=atr_pct)

    _emit_event("RISK", module="executor", symbol=symbol, side=str((side or "").upper()), lots=float(lots), confidence=float(confidence), atr_pct=float(atr_pct))

    pt = _index_point(symbol)
    log.info("[EXEC_PATH] %s pip_mode=%s pt=%.5f sl_pts=%.1f tp_pts=%.1f", symbol, str(ENV.get("IDX_PIP_MODE", "index_point")), float(pt), float(sl_points), float(tp_points))

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        _emit_event("ERROR", module="executor", symbol=symbol, side=str((side or "").upper()), where="symbol_info_tick", error="no_tick")
        return {"ok": False, "error": "no_tick"}

    side_up = (side or "").upper()
    price = float(tick.ask if side_up == "LONG" else tick.bid)

    sl = price - sl_points * pt if side_up == "LONG" else price + sl_points * pt
    tp = price + tp_points * pt if side_up == "LONG" else price - tp_points * pt
    sl = _rounded(sl, symbol)
    tp = _rounded(tp, symbol)
    price = _rounded(price, symbol)

    deviation = int(ENV.get("MT5_DEVIATION", 80))

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": mt5.ORDER_TYPE_BUY if side_up == "LONG" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": deviation,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
        "comment": _build_order_comment(reason),
    }

    _emit_event("ORDER_SEND", module="executor", symbol=symbol, side=side_up, attempt="1", volume=float(lots), price=float(price), sl=float(sl), tp=float(tp), deviation=int(deviation), filling="IOC", comment=str(req.get("comment", "")))
    res = mt5.order_send(req)

    _emit_event("ORDER_RESULT", module="executor", symbol=symbol, side=side_up, attempt="1", retcode=int(getattr(res, "retcode", -1)) if res else -1, comment=str(getattr(res, "comment", "")) if res else "no_response", order=getattr(res, "order", None) if res else None, deal=getattr(res, "deal", None) if res else None)

    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        update_trust(symbol, True)
        _last_trade_time[symbol] = time.time()
        _last_direction[symbol] = side_up
        _emit_event("EXECUTED", module="executor", symbol=symbol, side=side_up, ticket=getattr(res, "order", None), lots=float(lots), confidence=float(confidence), atr_pct=float(atr_pct), attempts=1, result=res._asdict())
        return {"ok": True, "result": res._asdict(), "attempts": 1, "lots": float(lots), "confidence": float(confidence)}

    retry_delay = float(ENV.get("IDX_RETRY_BUSY_DELAY_SEC", 0.6))
    if (res is None) or _is_busy_retcode(getattr(res, "retcode", None)):
        time.sleep(retry_delay)
        _emit_event("ORDER_SEND", module="executor", symbol=symbol, side=side_up, attempt="1b", volume=float(lots), price=float(price), sl=float(sl), tp=float(tp), deviation=int(deviation), filling="IOC", comment=str(req.get("comment", "")))
        res1b = mt5.order_send(req)
        _emit_event("ORDER_RESULT", module="executor", symbol=symbol, side=side_up, attempt="1b", retcode=int(getattr(res1b, "retcode", -1)) if res1b else -1, comment=str(getattr(res1b, "comment", "")) if res1b else "no_response", order=getattr(res1b, "order", None) if res1b else None, deal=getattr(res1b, "deal", None) if res1b else None)
        if res1b and res1b.retcode == mt5.TRADE_RETCODE_DONE:
            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side_up
            _emit_event("EXECUTED", module="executor", symbol=symbol, side=side_up, ticket=getattr(res1b, "order", None), lots=float(lots), confidence=float(confidence), atr_pct=float(atr_pct), attempts=2, result=res1b._asdict())
            return {"ok": True, "result": res1b._asdict(), "attempts": 2, "lots": float(lots), "confidence": float(confidence)}

    widen = float(ENV.get("IDX_STOP_WIDEN_MULT", 1.5))
    sl2 = price - sl_points * widen * pt if side_up == "LONG" else price + sl_points * widen * pt
    tp2 = price + tp_points * widen * pt if side_up == "LONG" else price - tp_points * widen * pt
    req["sl"], req["tp"], req["type_filling"] = (_rounded(sl2, symbol), _rounded(tp2, symbol), mt5.ORDER_FILLING_FOK)

    _emit_event("ORDER_SEND", module="executor", symbol=symbol, side=side_up, attempt="2", volume=float(lots), price=float(price), sl=float(req["sl"]), tp=float(req["tp"]), deviation=int(deviation), filling="FOK", comment=str(req.get("comment", "")), widen_mult=float(widen))
    res2 = mt5.order_send(req)

    _emit_event("ORDER_RESULT", module="executor", symbol=symbol, side=side_up, attempt="2", retcode=int(getattr(res2, "retcode", -1)) if res2 else -1, comment=str(getattr(res2, "comment", "")) if res2 else "no_response", order=getattr(res2, "order", None) if res2 else None, deal=getattr(res2, "deal", None) if res2 else None)

    if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
        update_trust(symbol, True)
        _last_trade_time[symbol] = time.time()
        _last_direction[symbol] = side_up
        _emit_event("EXECUTED", module="executor", symbol=symbol, side=side_up, ticket=getattr(res2, "order", None), lots=float(lots), confidence=float(confidence), atr_pct=float(atr_pct), attempts=3, result=res2._asdict())
        return {"ok": True, "result": res2._asdict(), "attempts": 3, "lots": float(lots), "confidence": float(confidence)}

    update_trust(symbol, False)
    _emit_event("FAILED", module="executor", symbol=symbol, side=side_up, reason="execution_failed", lots=float(lots), confidence=float(confidence), atr_pct=float(atr_pct), first=str(getattr(res, "comment", "")) if res else "", second=str(getattr(res2, "comment", "")) if res2 else "", retcode1=int(getattr(res, "retcode", -1)) if res else -1, retcode2=int(getattr(res2, "retcode", -1)) if res2 else -1)

    return {"ok": False, "reason": "execution_failed", "first": getattr(res, "comment", "") if res else "", "second": getattr(res2, "comment", "") if res2 else ""}