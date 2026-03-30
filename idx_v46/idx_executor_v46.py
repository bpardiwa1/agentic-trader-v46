# ============================================================
# Agentic Trader idx_v46 — Executor (CM07b, Env-Driven, ATR Protect)
# ============================================================

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict

import MetaTrader5 as mt5

from idx_v46.app.idx_env_v46 import ENV
from idx_v46.trust.idx_trust_engine_v46 import update_trust
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.util.idx_lot_scaler_v46 import compute_lot
from idx_v46.util.idx_event_sink_v46 import emit_event  # ✅ EVENTS

# Unified IDX logging
_IDX_LOG_DIR = "logs/idx_v4.6"
_IDX_LOG_LEVEL = str(ENV.get("IDX_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_IDX_LOG_NAME = f"idx_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_IDX_LOG_NAME, log_dir=_IDX_LOG_DIR, level=_IDX_LOG_LEVEL)

# ------------------------------------------------------------
# EVENT JSONL helper (watcher looks for token 'EVENT' + JSON)
# ------------------------------------------------------------
def _emit_event(event: str, **fields):  # ✅ EVENTS (FX/XAU style)
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
    """
    Preserve a short 'reason tag' into MT5 order comment for downstream attribution.
    MT5 comments are short; keep compact. Examples:
      idx46|ema_rsi_bull
      idx46|swing_lock
      idx46|no_reason
    """
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

        return f"idx46|{tag}"
    except Exception:
        return "idx46|no_reason"


def _is_busy_retcode(retcode: int | None) -> bool:
    """
    MT5 can return TRADE_CONTEXT_BUSY=10016. We treat that as retryable.
    We keep it numeric to avoid dependency on missing constants across builds.
    """
    try:
        return int(retcode or 0) == 10016
    except Exception:
        return False


def _guardrail(symbol: str, side: str) -> bool:
    """
    Centralised guardrail for index trades.

    Enforces:
    - Global max open positions (IDX_AGENT_MAX_OPEN)
    - Per-symbol max positions:
        * default IDX_AGENT_MAX_PER_SYMBOL
        * optional override: <BASE>_MAX_POSITIONS (e.g. HK50_MAX_POSITIONS)
    - Per-symbol cooldown (IDX_COOLDOWN_SEC)
    - Optional same-direction block (IDX_BLOCK_SAME_DIRECTION)
      with optional pyramiding override:
        * global  IDX_PYRAMID_MODE
        * per-sym <BASE>_PYRAMID_MODE
    """
    allowed = [
        s.strip()
        for s in str(ENV.get("AGENT_SYMBOLS", "NAS100.s,UK100.s,HK50.s")).split(",")
        if s.strip()
    ]
    try:
        positions = [p for p in (mt5.positions_get() or []) if p.symbol in allowed]
    except Exception:
        positions = []

    base = symbol.upper().split(".")[0]  # e.g. NAS100 from NAS100.s

    max_open = int(ENV.get("IDX_AGENT_MAX_OPEN", 8))
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

    log.info(
        "[GUARD] %s total=%d/%d symbol=%d/%d same_dir=%d",
        symbol,
        total_active,
        max_open,
        per_symbol,
        max_per,
        same_dir,
    )

    if total_active >= max_open:
        log.info("[BLOCK] global cap %d/%d", total_active, max_open)
        _emit_event(
            "BLOCKED",
            module="executor",
            symbol=symbol,
            side=side_up,
            reason="global_cap",
            total_active=int(total_active),
            max_open=int(max_open),
        )
        return False

    if per_symbol >= max_per:
        log.info("[BLOCK] %s cap %d/%d", symbol, per_symbol, max_per)
        _emit_event(
            "BLOCKED",
            module="executor",
            symbol=symbol,
            side=side_up,
            reason="per_symbol_cap",
            per_symbol=int(per_symbol),
            max_per=int(max_per),
        )
        return False

    cooldown = int(ENV.get("IDX_COOLDOWN_SEC", 300))
    last_t = float(_last_trade_time.get(symbol, 0.0))
    if cooldown > 0 and time.time() - last_t < cooldown:
        log.info("[BLOCK] %s cooldown", symbol)
        _emit_event(
            "BLOCKED",
            module="executor",
            symbol=symbol,
            side=side_up,
            reason="cooldown",
            cooldown_sec=int(cooldown),
            elapsed_sec=float(time.time() - last_t),
        )
        return False

    block_same_raw = ENV.get("IDX_BLOCK_SAME_DIRECTION", True)
    block_same = str(block_same_raw).lower() in ("1", "true", "yes", "on")

    pyr_flag = ENV.get(f"{base}_PYRAMID_MODE", ENV.get("IDX_PYRAMID_MODE", "false"))
    pyramiding = str(pyr_flag).lower() in ("1", "true", "yes", "on") and max_per > 1

    if block_same and _last_direction.get(symbol) == side:
        if not pyramiding:
            log.info("[BLOCK] %s same-direction", symbol)
            _emit_event(
                "BLOCKED",
                module="executor",
                symbol=symbol,
                side=side_up,
                reason="same_direction",
            )
            return False
        log.info("[PYRAMID] %s same-direction allowed (%d/%d)", symbol, per_symbol, max_per)

    return True


def _apply_atr_protection(
    symbol: str,
    side: str,
    atr_pct: float,
    pt: float,
    deviation: int,
    order_ticket: int | None,
) -> None:
    enable = str(ENV.get("IDX_PROTECT_ENABLE", "false")).lower() in ("1", "true", "yes", "on")
    if not enable:
        return
    if order_ticket is None:
        return

    try:
        atr_pts_mult = float(ENV.get("IDX_ATR_POINTS_MULT", 10000.0))
        atr_points = max(1.0, atr_pct * atr_pts_mult)

        base = symbol.upper().split(".")[0]
        trig_r = float(ENV.get("IDX_PROTECT_ATR_TRIGGER_R", 1.0))
        off_r = float(ENV.get("IDX_PROTECT_ATR_OFFSET_R", 0.3))

        sym_trig_key = f"{base}_PROTECT_ATR_TRIGGER_R"
        sym_off_key = f"{base}_PROTECT_ATR_OFFSET_R"

        if ENV.get(sym_trig_key) is not None:
            trig_r = float(ENV.get(sym_trig_key))
        if ENV.get(sym_off_key) is not None:
            off_r = float(ENV.get(sym_off_key))

        trigger_pts = atr_points * trig_r
        offset_pts = atr_points * off_r

        positions = mt5.positions_get(symbol=symbol) or []
        pos = next((p for p in positions if getattr(p, "ticket", None) == order_ticket), None)
        if not pos:
            log.debug("[PROTECT] %s no position found for ticket=%s", symbol, order_ticket)
            return

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return

        if pos.type == mt5.POSITION_TYPE_BUY:
            current_price = float(tick.bid)
            entry = float(pos.price_open)
            profit_pts = (current_price - entry) / pt
        else:
            current_price = float(tick.ask)
            entry = float(pos.price_open)
            profit_pts = (entry - current_price) / pt

        if profit_pts < trigger_pts:
            log.debug(
                "[PROTECT] %s profit_pts=%.1f < trigger=%.1f (no adjustment)",
                symbol,
                profit_pts,
                trigger_pts,
            )
            return

        if pos.type == mt5.POSITION_TYPE_BUY:
            new_sl = entry + offset_pts * pt
            new_tp = float(pos.tp)
        else:
            new_sl = entry - offset_pts * pt
            new_tp = float(pos.tp)

        new_sl = _rounded(new_sl, symbol)

        req_mod = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "symbol": symbol,
            "sl": new_sl,
            "tp": new_tp,
            "deviation": deviation,
        }

        res_mod = mt5.order_send(req_mod)
        if res_mod and res_mod.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(
                "[PROTECT] %s %s BE+ATR SL adjusted (ticket=%s, sl=%.2f, profit_pts=%.1f, atr_pts=%.1f)",
                symbol,
                side,
                pos.ticket,
                new_sl,
                profit_pts,
                atr_points,
            )
            _emit_event(
                "PROTECT_APPLIED",
                module="executor",
                symbol=symbol,
                side=str(side),
                ticket=int(pos.ticket),
                sl=float(new_sl),
                profit_pts=float(profit_pts),
                trigger_pts=float(trigger_pts),
                atr_pts=float(atr_points),
            )
        else:
            if res_mod:
                log.info(
                    "[PROTECT] %s %s SLTP adjust retcode=%s comment=%s",
                    symbol,
                    side,
                    res_mod.retcode,
                    getattr(res_mod, "comment", ""),
                )
                _emit_event(
                    "PROTECT_FAILED",
                    module="executor",
                    symbol=symbol,
                    side=str(side),
                    ticket=int(pos.ticket),
                    retcode=int(res_mod.retcode),
                    comment=str(getattr(res_mod, "comment", "")),
                )

    except Exception as e:
        log.warning("[PROTECT] %s failed to apply ATR protection: %s", symbol, e)
        _emit_event(
            "PROTECT_FAILED",
            module="executor",
            symbol=symbol,
            side=str(side),
            error=type(e).__name__,
            detail=str(e),
        )


def execute_trade(
    symbol: str,
    side: str,
    sl_points: float,
    tp_points: float,
    confidence: float = 0.5,
    atr_pct: float = 0.0,
    *,
    align: str | None = None,
    bars_since_swing: int | None = None,
    trend_h1: str | None = None,
    spx_bias: str | None = None,
    override_tag: bool = False,
    reason: Any | None = None,
) -> Dict[str, Any]:

    if not _guardrail(symbol, side):
        return {"ok": False, "blocked": True}

    lots = compute_lot(
        symbol=symbol,
        confidence=confidence,
        atr_pct=atr_pct,
        align=align,
        override_tag=override_tag,
        bars_since_swing=bars_since_swing,
        trend_h1=trend_h1,
        spx_bias=spx_bias,
    )

    log.info(
        "[RISK] %s dynamic_lots=%.2f (conf=%.2f, atr%%=%.4f align=%s swing=%s H1=%s SPX=%s override=%s)",
        symbol,
        float(lots),
        float(confidence),
        float(atr_pct),
        align or "NA",
        str(bars_since_swing),
        trend_h1 or "NA",
        spx_bias or "NA",
        override_tag,
    )

    # ✅ EVENTS: RISK (added)
    _emit_event(
        "RISK",
        module="executor",
        symbol=symbol,
        side=str((side or "").upper()),
        lots=float(lots),
        confidence=float(confidence),
        atr_pct=float(atr_pct),
        align=str(align or "NA"),
        bars_since_swing=bars_since_swing,
        trend_h1=str(trend_h1 or "NA"),
        spx_bias=str(spx_bias or "NA"),
        override=bool(override_tag),
    )

    # ---------------------------------------------------------
    # HK50 lunch break protection
    # ---------------------------------------------------------
    now_kl = datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))
    if symbol.startswith("HK50"):
        if now_kl.hour == 12:
            log.info("[EXEC_SKIP] %s lunch break (HKEX 12:00–13:00)", symbol)
            _emit_event(
                "SKIP",
                module="executor",
                symbol=symbol,
                side=str((side or "").upper()),
                reason="hk50_lunch_break",
                accepted=False,
                confidence=float(confidence),
                atr_pct=float(atr_pct),
            )
            return {"ok": False, "reason": "hk50_lunch_break"}

    # --- Execution semantics proof line (IDX normalization) -------
    pt = _index_point(symbol)
    log.info(
        "[IDX_EXEC_PATH] %s backend=direct_mt5 pip_mode=%s pt=%.5f sl_pts=%.1f tp_pts=%.1f",
        symbol,
        str(ENV.get("IDX_PIP_MODE", "index_point")),
        float(pt),
        float(sl_points),
        float(tp_points),
    )

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        _emit_event(
            "ERROR",
            module="executor",
            symbol=symbol,
            side=str((side or "").upper()),
            where="symbol_info_tick",
            error="no_tick",
        )
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

    # --- Attempt 1 ------------------------------------------------
    _emit_event(
        "ORDER_SEND",
        module="executor",
        symbol=symbol,
        side=side_up,
        attempt="1",
        volume=float(lots),
        price=float(price),
        sl=float(sl),
        tp=float(tp),
        deviation=int(deviation),
        filling="IOC",
        comment=str(req.get("comment", "")),
    )
    res = mt5.order_send(req)

    _emit_event(
        "ORDER_RESULT",
        module="executor",
        symbol=symbol,
        side=side_up,
        attempt="1",
        retcode=int(getattr(res, "retcode", -1)) if res else -1,
        comment=str(getattr(res, "comment", "")) if res else "no_response",
        order=getattr(res, "order", None) if res else None,
        deal=getattr(res, "deal", None) if res else None,
    )

    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            "[EXECUTOR] %s %s ok (%.2f lots, attempt=1, ticket=%s)",
            symbol,
            side_up,
            float(lots),
            getattr(res, "order", "?"),
        )
        update_trust(symbol, True)
        _last_trade_time[symbol] = time.time()
        _last_direction[symbol] = side_up

        _emit_event(
            "EXECUTED",
            module="executor",
            symbol=symbol,
            side=side_up,
            ticket=getattr(res, "order", None),
            lots=float(lots),
            confidence=float(confidence),
            atr_pct=float(atr_pct),
            attempts=1,
            result=res._asdict(),
        )

        _apply_atr_protection(
            symbol=symbol,
            side=side_up,
            atr_pct=atr_pct,
            pt=pt,
            deviation=deviation,
            order_ticket=getattr(res, "order", None),
        )

        return {
            "ok": True,
            "result": res._asdict(),
            "attempts": 1,
            "lots": float(lots),
            "confidence": float(confidence),
        }

    if res:
        log.info(
            "[EXEC] %s %s attempt1 retcode=%s comment=%s",
            symbol,
            side_up,
            res.retcode,
            getattr(res, "comment", ""),
        )
    else:
        log.info("[EXEC] %s %s attempt1 no_response", symbol, side_up)

    # --- Attempt 1b: REAL retry if MT5 busy / no response ----------
    retry_delay = float(ENV.get("IDX_RETRY_BUSY_DELAY_SEC", 0.6))
    if (res is None) or _is_busy_retcode(getattr(res, "retcode", None)):
        log.info("[EXEC] %s %s attempt1b retrying (busy/no_response)", symbol, side_up)
        time.sleep(retry_delay)

        _emit_event(
            "ORDER_SEND",
            module="executor",
            symbol=symbol,
            side=side_up,
            attempt="1b",
            volume=float(lots),
            price=float(price),
            sl=float(sl),
            tp=float(tp),
            deviation=int(deviation),
            filling="IOC",
            comment=str(req.get("comment", "")),
        )
        res1b = mt5.order_send(req)

        _emit_event(
            "ORDER_RESULT",
            module="executor",
            symbol=symbol,
            side=side_up,
            attempt="1b",
            retcode=int(getattr(res1b, "retcode", -1)) if res1b else -1,
            comment=str(getattr(res1b, "comment", "")) if res1b else "no_response",
            order=getattr(res1b, "order", None) if res1b else None,
            deal=getattr(res1b, "deal", None) if res1b else None,
        )

        if res1b and res1b.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(
                "[EXECUTOR] %s %s ok (%.2f lots, attempt=1b, ticket=%s)",
                symbol,
                side_up,
                float(lots),
                getattr(res1b, "order", "?"),
            )
            update_trust(symbol, True)
            _last_trade_time[symbol] = time.time()
            _last_direction[symbol] = side_up

            _emit_event(
                "EXECUTED",
                module="executor",
                symbol=symbol,
                side=side_up,
                ticket=getattr(res1b, "order", None),
                lots=float(lots),
                confidence=float(confidence),
                atr_pct=float(atr_pct),
                attempts=2,
                result=res1b._asdict(),
            )

            _apply_atr_protection(
                symbol=symbol,
                side=side_up,
                atr_pct=atr_pct,
                pt=pt,
                deviation=deviation,
                order_ticket=getattr(res1b, "order", None),
            )

            return {
                "ok": True,
                "result": res1b._asdict(),
                "attempts": 2,
                "lots": float(lots),
                "confidence": float(confidence),
            }

        if res1b:
            log.info(
                "[EXEC] %s %s attempt1b retcode=%s comment=%s",
                symbol,
                side_up,
                res1b.retcode,
                getattr(res1b, "comment", ""),
            )
        else:
            log.info("[EXEC] %s %s attempt1b no_response", symbol, side_up)

    # --- Attempt 2: widen stops & change filling ------------------
    widen = float(ENV.get("IDX_STOP_WIDEN_MULT", 1.5))
    sl2 = price - sl_points * widen * pt if side_up == "LONG" else price + sl_points * widen * pt
    tp2 = price + tp_points * widen * pt if side_up == "LONG" else price - tp_points * widen * pt
    req["sl"], req["tp"], req["type_filling"] = (
        _rounded(sl2, symbol),
        _rounded(tp2, symbol),
        mt5.ORDER_FILLING_FOK,
    )

    _emit_event(
        "ORDER_SEND",
        module="executor",
        symbol=symbol,
        side=side_up,
        attempt="2",
        volume=float(lots),
        price=float(price),
        sl=float(req["sl"]),
        tp=float(req["tp"]),
        deviation=int(deviation),
        filling="FOK",
        comment=str(req.get("comment", "")),
        widen_mult=float(widen),
    )
    res2 = mt5.order_send(req)

    _emit_event(
        "ORDER_RESULT",
        module="executor",
        symbol=symbol,
        side=side_up,
        attempt="2",
        retcode=int(getattr(res2, "retcode", -1)) if res2 else -1,
        comment=str(getattr(res2, "comment", "")) if res2 else "no_response",
        order=getattr(res2, "order", None) if res2 else None,
        deal=getattr(res2, "deal", None) if res2 else None,
    )

    if res2:
        log.info(
            "[EXEC] %s %s attempt2 retcode=%s comment=%s",
            symbol,
            side_up,
            res2.retcode,
            getattr(res2, "comment", ""),
        )
    else:
        log.info("[EXEC] %s %s attempt2 no_response", symbol, side_up)

    if res2 and res2.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(
            "[EXECUTOR] %s %s ok (%.2f lots, attempt=2, ticket=%s)",
            symbol,
            side_up,
            float(lots),
            getattr(res2, "order", "?"),
        )
        update_trust(symbol, True)
        _last_trade_time[symbol] = time.time()
        _last_direction[symbol] = side_up

        _emit_event(
            "EXECUTED",
            module="executor",
            symbol=symbol,
            side=side_up,
            ticket=getattr(res2, "order", None),
            lots=float(lots),
            confidence=float(confidence),
            atr_pct=float(atr_pct),
            attempts=3,
            result=res2._asdict(),
        )

        _apply_atr_protection(
            symbol=symbol,
            side=side_up,
            atr_pct=atr_pct,
            pt=pt,
            deviation=deviation,
            order_ticket=getattr(res2, "order", None),
        )

        return {
            "ok": True,
            "result": res2._asdict(),
            "attempts": 3,
            "lots": float(lots),
            "confidence": float(confidence),
        }

    # --- Failure --------------------------------------------------
    update_trust(symbol, False)

    _emit_event(
        "FAILED",
        module="executor",
        symbol=symbol,
        side=side_up,
        reason="execution_failed",
        lots=float(lots),
        confidence=float(confidence),
        atr_pct=float(atr_pct),
        first=str(getattr(res, "comment", "")) if res else "",
        second=str(getattr(res2, "comment", "")) if res2 else "",
        retcode1=int(getattr(res, "retcode", -1)) if res else -1,
        retcode2=int(getattr(res2, "retcode", -1)) if res2 else -1,
    )

    return {
        "ok": False,
        "reason": "execution_failed",
        "first": getattr(res, "comment", "") if res else "",
        "second": getattr(res2, "comment", "") if res2 else "",
    }