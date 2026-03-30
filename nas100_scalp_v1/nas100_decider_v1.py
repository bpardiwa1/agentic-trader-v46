# ============================================================
# NAS100 Scalper v1 — Decider
# ============================================================

from __future__ import annotations

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from typing import Any, Dict, Tuple

from nas100_scalp_v1.app.nas100_env_v1 import ENV

MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _parse_hhmm(s: str) -> dtime:
    hh, mm = (s or "00:00").split(":")
    return dtime(int(hh), int(mm))


def _in_window(now: dtime, start: dtime, end: dtime) -> bool:
    """
    Supports both normal windows (start <= end) and overnight windows (start > end),
    e.g. 22:30 -> 00:30.
    """
    if start <= end:
        return (now >= start) and (now <= end)
    # Overnight window crossing midnight
    return (now >= start) or (now <= end)


def decide(features: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(MY_TZ).time()

    w_start = _parse_hhmm(str(ENV.get("SCALP_TRADING_WINDOW_START", "21:30")))
    w_end = _parse_hhmm(str(ENV.get("SCALP_TRADING_WINDOW_END", "23:30")))
    if not _in_window(now, w_start, w_end):
        return {"side": "", "why": ["outside_trading_window"]}

    hot_only = bool(ENV.get("SCALP_HOT_ONLY", True))
    if hot_only:
        h_start = _parse_hhmm(str(ENV.get("SCALP_HOT_START", "21:30")))
        h_end = _parse_hhmm(str(ENV.get("SCALP_HOT_END", "22:30")))
        if not _in_window(now, h_start, h_end):
            return {"side": "", "why": ["outside_hot_window"]}

    bias = str(features.get("bias_side", "") or "").upper()
    if bias not in ("LONG", "SHORT"):
        return {"side": "", "why": ["no_m5_bias"]}

    # Guardrails
    max_trades = int(ENV.get("SCALP_MAX_TRADES_PER_SESSION", 3))
    if int(state.get("trades_today", 0)) >= max_trades:
        return {"side": "", "why": ["session_trade_cap"]}

    min_bars = int(ENV.get("SCALP_MIN_BARS_BETWEEN_TRADES", 5))
    last_entry = int(state.get("last_entry_bar_idx", -10_000))
    cur_bar = int(state.get("bar_idx", 0))
    if (cur_bar - last_entry) < min_bars:
        return {"side": "", "why": [f"bar_spacing<{min_bars}"]}

    # ATR band (M1)
    atr_pct = float(features.get("atr_pct_m1", 0.0) or 0.0)
    atr_min = float(ENV.get("SCALP_ATR_PCT_MIN", 0.00025))
    atr_max = float(ENV.get("SCALP_ATR_PCT_MAX", 0.00120))
    if not (atr_min <= atr_pct <= atr_max):
        return {"side": "", "why": [f"atr_band({atr_pct:.6f})"]}

    # EMA gap (points)
    gap = float(features.get("ema_gap_m1", 0.0) or 0.0)
    gap_min = float(ENV.get("SCALP_EMA_GAP_MIN_POINTS", 5.0))
    if abs(gap) < gap_min:
        return {"side": "", "why": [f"ema_gap<{gap_min}"]}

    # Trigger
    ema_f1 = float(features.get("ema_fast_m1", 0.0) or 0.0)
    ema_s1 = float(features.get("ema_slow_m1", 0.0) or 0.0)
    rsi1 = float(features.get("rsi_m1", 50.0) or 50.0)

    rsi_long = float(ENV.get("SCALP_RSI_LONG_TH", 52.0))
    rsi_short = float(ENV.get("SCALP_RSI_SHORT_TH", 48.0))

    side = ""
    why: list[str] = []

    if bias == "LONG":
        if (ema_f1 > ema_s1) and (rsi1 >= rsi_long):
            side = "LONG"
            why.append("bias_long+m1_trigger")
        else:
            why.append("no_long_trigger")
    else:
        if (ema_f1 < ema_s1) and (rsi1 <= rsi_short):
            side = "SHORT"
            why.append("bias_short+m1_trigger")
        else:
            why.append("no_short_trigger")

    if not side:
        return {"side": "", "why": why}

    sl_points = float(ENV.get("SCALP_SL_POINTS", 80.0))
    tp_points = float(ENV.get("SCALP_TP_POINTS", 120.0))

    return {"side": side, "sl_points": sl_points, "tp_points": tp_points, "why": why}