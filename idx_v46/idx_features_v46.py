# ============================================================
# Agentic Trader IDX v4.6 — Feature Computation
# ------------------------------------------------------------
# • KL-time aware trading sessions (symbol-specific)
# • EMA / RSI / ATR computation
# • ATR-scaled SL/TP for index CFDs
# • Log format aligned with FX/XAU modules
# ============================================================

from __future__ import annotations
import datetime as dt
from typing import Any, Dict
import numpy as np
import pytz

from idx_v46.util.idx_indicators import ema, rsi, atr
from idx_v46.util.idx_mt5_bars import get_bars
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.logger import setup_logger

# ------------------------------------------------------------
# Logger
# ------------------------------------------------------------
log = setup_logger("idx_features_v46", level=ENV.get("LOG_LEVEL", "INFO").upper())


# ------------------------------------------------------------
# Session Control — uses KL time and per-symbol overrides
# ------------------------------------------------------------
def _is_session_open(env, now: dt.datetime, symbol: str) -> bool:
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now_kl = tz.localize(now) if now.tzinfo is None else now.astimezone(tz)
    weekday = now_kl.isoweekday()

    sym = symbol.replace(".s", "").replace(".ecn", "").replace("-", "_").upper()
    start_key = f"INDICES_TRADING_WINDOW_START_{sym}"
    end_key = f"INDICES_TRADING_WINDOW_END_{sym}"
    days_key = f"INDICES_TRADING_DAYS_{sym}"

    start_str = env.get(start_key, env.get("INDICES_TRADING_WINDOW_START", "00:00"))
    end_str = env.get(end_key, env.get("INDICES_TRADING_WINDOW_END", "23:59"))
    days_str = env.get(days_key, env.get("INDICES_TRADING_DAYS", "1,2,3,4,5"))

    try:
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        start_t = dt.time(sh, sm)
        end_t = dt.time(eh, em)
    except Exception:
        return True  # fail open if parsing fails

    allowed_days = [int(x.strip()) for x in days_str.split(",") if x.strip().isdigit()]
    if weekday not in allowed_days:
        return False

    t = now_kl.time()
    if start_t <= end_t:
        return start_t <= t <= end_t
    return t >= start_t or t <= end_t


# ------------------------------------------------------------
# Core Feature Computation
# ------------------------------------------------------------
def compute_features(symbol: str, env=ENV) -> Dict[str, Any]:
    tf = env.get("TIMEFRAME", "M15")
    ema_fast = int(env.get("INDICES_EMA_FAST", 20))
    ema_slow = int(env.get("INDICES_EMA_SLOW", 50))
    rsi_period = int(env.get("INDICES_RSI_PERIOD", 14))
    atr_period = int(env.get("INDICES_ATR_PERIOD", 14))

    df = get_bars(symbol, tf, ema_slow + 50)
    if df is None or df.empty or len(df) < ema_slow + 5:
        log.warning("[SKIP] %s insufficient bars (len=%d)", symbol, 0 if df is None else len(df))
        return {"ok": False, "note": "no_data"}

    closes = df["close"].astype(float)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)

    price = float(closes.iloc[-1])
    efast = float(ema(closes, ema_fast).iloc[-1])
    eslow = float(ema(closes, ema_slow).iloc[-1])
    rsi_val = float(rsi(closes, rsi_period))
    atr_val = float(atr(df, atr_period))
    atr_pct = atr_val / price if price else 0.0
    ema_gap = efast - eslow

    atr_sl_mult = float(env.get("INDICES_ATR_SL_MULT", 2.0))
    atr_tp_mult = float(env.get("INDICES_ATR_TP_MULT", 3.0))
    sl_dyn = atr_sl_mult * atr_val
    tp_dyn = atr_tp_mult * atr_val

    eps = float(env.get("INDICES_EPS", 10.0))
    rsi_long_th = float(env.get("INDICES_RSI_LONG_TH", 60))
    rsi_short_th = float(env.get("INDICES_RSI_SHORT_TH", 40))

    # Session
    session_open = _is_session_open(env, dt.datetime.now(), symbol)
    if not session_open:
        log.info("[SKIP] %s outside trading window", symbol)
        return {"ok": False, "note": "outside_session", "symbol": symbol, "session_open": False}

    # Clean summary
    log.info(
        "[FEATURES] %s EMA_FAST=%.2f EMA_SLOW=%.2f GAP=%.2f RSI=%.2f ATR=%.2f pts",
        symbol, efast, eslow, ema_gap, rsi_val, atr_val
    )

    return {
        "ok": True,
        "symbol": symbol,
        "tf": tf,
        "price": price,
        "ema_fast": efast,
        "ema_slow": eslow,
        "ema_gap": ema_gap,
        "rsi": rsi_val,
        "atr_pips": atr_val,
        "atr_pct": atr_pct,
        "sl_pips_atr": sl_dyn,
        "tp_pips_atr": tp_dyn,
        "eps": eps,
        "rsi_long_th": rsi_long_th,
        "rsi_short_th": rsi_short_th,
        "session_open": session_open,
    }
