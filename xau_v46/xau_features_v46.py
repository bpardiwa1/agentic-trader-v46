# xau_v46/xau_features_v46.py
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

# import numpy as np
# import pandas as pd

from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.xau_mt5_bars import get_bars
from xau_v46.util.xau_indicators import ema, rsi, atr
from xau_v46.trust.xau_trust_engine_v46 import adjusted_confidence
from xau_v46.util.logger import setup_logger

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
_XAU_LOG_DIR = "logs/xau_v4.6"
_XAU_LOG_LEVEL = str(ENV.get("XAU_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_XAU_LOG_NAME = f"xau_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_XAU_LOG_NAME, log_dir=_XAU_LOG_DIR, level=_XAU_LOG_LEVEL)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _conf_from_indicators(rsi_val: float, ema_gap: float, atr_pct: float) -> tuple[float, list[str]]:
    """
    Same spirit as your previous feature-level confidence, but we keep it
    self-contained and friendly to ATR regimes.
    """
    why: list[str] = []

    # --- RSI contribution ------------------------------------
    rsi_dist = (rsi_val - 50.0) / 25.0
    rsi_conf = _sigmoid(2.5 * rsi_dist)
    if rsi_val >= 55:
        why.append("rsi_confirms_bull")
    elif rsi_val <= 45:
        why.append("rsi_confirms_bear")
    else:
        why.append("rsi_neutral")

    # --- EMA alignment contribution (normalized by ATR%) -----
    sign = 1.0 if ema_gap > 0 else (-1.0 if ema_gap < 0 else 0.0)
    base = float(abs(ema_gap)) / float(atr_pct * 2.0 + 1e-9)
    mag = min(1.0, base)
    ema_conf = _sigmoid(2.0 * sign * mag)

    # --- Low-volatility penalty ------------------------------
    # Baseline ~0.10% ATR; if quieter than that, reduce impact.
    vol_pen = max(0.6, min(1.0, atr_pct / 0.0010))

    raw = max(0.0, min(1.0, 0.5 * rsi_conf + 0.5 * ema_conf)) * vol_pen
    return raw, why


def _classify_atr_regime(atr_pct: float, env: dict[str, Any]) -> str:
    """
    Map ATR% -> "QUIET", "NORMAL", "HOT" (XAU tuned).

    Defaults (in ATR%) if env not set:
      QUIET  <= 0.12%
      NORMAL <= 0.30%
      HOT    >  0.30%
    Env expects *fractions* (0.0012 = 0.12%).
    """
    if atr_pct <= 0.0 or not math.isfinite(atr_pct):
        return "UNKNOWN"

    quiet_max = float(env.get("XAU_ATR_QUIET_MAX", 0.0012))   # 0.12%
    normal_max = float(env.get("XAU_ATR_NORMAL_MAX", 0.0030))  # 0.30%

    if atr_pct <= quiet_max:
        return "QUIET"
    if atr_pct <= normal_max:
        return "NORMAL"
    return "HOT"


def _parse_hhmm(s: str) -> tuple[int, int]:
    try:
        hh, mm = s.strip().split(":")
        return int(hh), int(mm)
    except Exception:
        return 0, 0


def _in_session(now: datetime, env: dict[str, Any]) -> bool:
    """
    IDX-style trading window control for XAU.
    Uses local/server time; env keys:
      - XAU_TRADING_DAYS (e.g. "1,2,3,4,5")
      - XAU_TRADING_WINDOW_START (e.g. "15:00")
      - XAU_TRADING_WINDOW_END   (e.g. "23:59")
    """
    # Days: 1=Mon ... 7=Sun
    days_raw = str(env.get("XAU_TRADING_DAYS", "1,2,3,4,5"))
    try:
        allowed_days = {int(x.strip()) for x in days_raw.split(",") if x.strip()}
    except Exception:
        allowed_days = {1, 2, 3, 4, 5}

    today = now.isoweekday()
    if today not in allowed_days:
        return False

    start_str = str(env.get("XAU_TRADING_WINDOW_START", "00:00"))
    end_str = str(env.get("XAU_TRADING_WINDOW_END", "23:59"))
    sh, sm = _parse_hhmm(start_str)
    eh, em = _parse_hhmm(end_str)

    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)

    # Same-day window
    return start <= now <= end


def _compute_h1_context(symbol: str, env: dict[str, Any]) -> dict[str, Any]:
    """
    Lightweight H1 context similar in spirit to app.market.data.compute_context.
    Uses your existing xau_mt5_bars + xau_indicators.
    """
    try:
        if str(env.get("XAU_H1_CONTEXT_ENABLED", "true")).lower() not in ("1", "true", "yes", "on"):
            return {}

        h1_bars = get_bars(
            symbol,
            timeframe=str(env.get("XAU_H1_TIMEFRAME", "H1")),
            limit=int(env.get("XAU_H1_HISTORY_BARS", 300)),
        )
        if h1_bars is None or len(h1_bars) < 60:
            return {}

        closes = h1_bars["close"].astype(float)
        highs = h1_bars["high"].astype(float)
        lows = h1_bars["low"].astype(float)

        ema50 = ema(closes, 50)
        ema200 = ema(closes, 200)
        rsi14 = rsi(closes, 14)
        atr14 = float(atr(h1_bars, 14))

        price = float(closes.iloc[-1])
        e50 = float(ema50.iloc[-1]) if hasattr(ema50, "iloc") else float(ema50)
        e200 = float(ema200.iloc[-1]) if hasattr(ema200, "iloc") else float(ema200)
        rlast = float(rsi14.iloc[-1]) if hasattr(rsi14, "iloc") else float(rsi14)

        atr_pct = atr14 / price if price > 0 else 0.0
        # Simple regime classification for H1 trend
        h1_rsi_up = float(env.get("XAU_H1_RSI_TREND_UP", 55))
        h1_rsi_down = float(env.get("XAU_H1_RSI_TREND_DOWN", 45))

        if e50 > e200 and rlast > h1_rsi_up:
            regime, notes = "TRENDING_UP", f"ema50>ema200 & rsi>{h1_rsi_up:g}"
        elif e50 < e200 and rlast < h1_rsi_down:
            regime, notes = "TRENDING_DOWN", f"ema50<ema200 & rsi<{h1_rsi_down:g}"
        else:
            regime, notes = "RANGE/MIXED", "mixed ema/rsi"

        # --------------------------------------------------------
        # Debug: H1 context sanity log (optional)
        # --------------------------------------------------------
        debug_h1 = str(env.get("XAU_DEBUG_H1_CONTEXT", "false")).lower() in ("1", "true", "yes", "on")
        if debug_h1:
            log.info(
                "[H1_CONTEXT] %s tf=%s price=%.2f ema50=%.2f ema200=%.2f rsi14=%.2f atr%%=%.4f regime=%s",
                symbol,
                str(env.get("XAU_H1_TIMEFRAME", "H1")),
                price,
                e50,
                e200,
                rlast,
                atr_pct,
                regime,
            )


        return {
            "price": price,
            "ema50": e50,
            "ema200": e200,
            "rsi14": rlast,
            "atr_pct": atr_pct,
            "regime": regime,
            "notes": notes,
        }
    except Exception as e:
        log.exception("[H1_CONTEXT] %s failed: %s", symbol, e)
        return {}


# ------------------------------------------------------------
# Public: core feature extractor
# ------------------------------------------------------------
def compute_features(symbol: str) -> dict | None:
    """
    Compute XAU feature set for v4.6:

      - M15 indicators:
          rsi, ema_fast, ema_slow, ema_gap, atr_pct
      - Feature-level raw_conf + adjusted_conf (trust-aware)
      - ATR regime: QUIET / NORMAL / HOT
      - H1 context: simple trend regime & ATR
      - Session flag: in_session (IDX-style)
      - Swing flag: swing_lock_allowed (only in QUIET regime)

    Returns:
        dict or None on fatal error.
    """

    try:
        # --- Core TF / history --------------------------------
        timeframe = str(ENV.get("XAU_TIMEFRAME", "M15"))
        n_bars = int(ENV.get("XAU_HISTORY_BARS", 240))

        ema_fast_p = int(ENV.get("XAU_EMA_FAST", 20))
        ema_slow_p = int(ENV.get("XAU_EMA_SLOW", 50))
        rsi_period = int(ENV.get("XAU_RSI_PERIOD", 14))
        atr_period = int(ENV.get("XAU_ATR_PERIOD", ENV.get("ATR_PERIOD", 14)))

        bars = get_bars(symbol, timeframe=timeframe, limit=n_bars)
        need_min = max(ema_fast_p, ema_slow_p, rsi_period, atr_period) + 1
        if bars is None or len(bars) < need_min:
            log.error(
                "[DATA] compute_features failed for %s: not enough bars (have=%s need>=%s)",
                symbol,
                len(bars) if bars is not None else None,
                need_min,
            )
            return None

        closes_series = bars["close"].astype(float)
        price = float(closes_series.iloc[-1])

        # --- Indicators (M15) ---------------------------------
        ema_fast_s = ema(closes_series, ema_fast_p)
        ema_slow_s = ema(closes_series, ema_slow_p)
        rsi_s = rsi(closes_series, rsi_period)
        atr_val = float(atr(bars, atr_period))
        atr_pct = atr_val / price if price > 0 else 0.0

        ema_fast_v = float(ema_fast_s.iloc[-1] if hasattr(ema_fast_s, "iloc") else ema_fast_s)
        ema_slow_v = float(ema_slow_s.iloc[-1] if hasattr(ema_slow_s, "iloc") else ema_slow_s)
        rsi_v = float(rsi_s.iloc[-1] if hasattr(rsi_s, "iloc") else rsi_s)
        ema_gap = float(ema_fast_v - ema_slow_v)

        # --- ATR regime & confidence --------------------------
        atr_regime = _classify_atr_regime(atr_pct, ENV)
        raw_conf, why = _conf_from_indicators(rsi_v, ema_gap, atr_pct)

        # Adjust with trust engine
        trust_weight = float(ENV.get("XAU_TRUST_WEIGHT", 0.4))
        adj_conf = adjusted_confidence(raw_conf, symbol, trust_weight=trust_weight)

        # --- H1 context ---------------------------------------
        h1_ctx = _compute_h1_context(symbol, ENV)
        h1_regime = h1_ctx.get("regime")

        # --- Session window flag ------------------------------
        now = datetime.now()
        in_sess = _in_session(now, ENV)

        # --- Swing quiet-only flag ----------------------------
        swing_quiet_only = str(ENV.get("XAU_SWING_QUIET_ONLY", "true")).lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        swing_lock_allowed = swing_quiet_only and (atr_regime == "QUIET")

        # --- Enrich 'why' for diagnostics ---------------------
        why.append(f"atr_regime_{atr_regime.lower()}")
        if not in_sess:
            why.append("out_of_session")
        if h1_regime:
            why.append(f"h1_{h1_regime.lower()}")

        features: dict[str, Any] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "rsi": rsi_v,
            "ema_fast": ema_fast_v,
            "ema_slow": ema_slow_v,
            "ema_gap": ema_gap,
            "atr_pct": atr_pct,
            "atr_regime": atr_regime,          # QUIET / NORMAL / HOT
            "price": price,
            "raw_conf": round(raw_conf, 4),
            "adj_conf": round(adj_conf, 4),
            "why": why,
            "in_session": in_sess,
            "swing_lock_allowed": swing_lock_allowed,
            "context": {
                "h1": h1_ctx,
            },
        }

        log.info(
            "[FEATURES] %s TF=%s price=%.2f EMA_FAST=%.2f EMA_SLOW=%.2f GAP=%.2f "
            "RSI=%.2f ATR%%=%.4f regime=%s H1=%s in_session=%s RAW=%.3f ADJ=%.3f WHY=%s",
            symbol,
            timeframe,
            price,
            ema_fast_v,
            ema_slow_v,
            ema_gap,
            rsi_v,
            atr_pct,
            atr_regime,
            h1_regime,
            in_sess,
            features["raw_conf"],
            features["adj_conf"],
            why,
        )

        return features

    except Exception as e:
        log.exception("[ERROR] compute_features failed for %s: %s", symbol, e)
        return None
