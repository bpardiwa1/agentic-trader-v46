# ============================================================
# Agentic Trader idx_v46 — Feature Computation (SCCR Phase-2)
# ============================================================

from __future__ import annotations
import pandas as pd
from datetime import datetime

from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_mt5_bars_v46 import get_bars
from idx_v46.util.idx_logger_v46 import setup_logger
from idx_v46.util.idx_indicators_v46 import ema, rsi, atr
from idx_v46.trust.idx_trust_engine_v46 import adjusted_confidence

# Unified IDX logging
_IDX_LOG_DIR = "logs/idx_v4.6"
_IDX_LOG_LEVEL = str(ENV.get("IDX_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
_IDX_LOG_NAME = f"idx_v46_{datetime.now():%Y-%m-%d}"
log = setup_logger(_IDX_LOG_NAME, log_dir=_IDX_LOG_DIR, level=_IDX_LOG_LEVEL)


# ------------------------------------------------------------
# Helper: find bars_since_swing (local high/low detection)
# ------------------------------------------------------------
def _compute_bars_since_swing(df: pd.DataFrame, lookback: int = 12) -> int:
    """
    Determine distance (bars) from last swing high/low.
    Swing high = high[i] > high[i-1] and high[i] > high[i+1]
    Swing low  = low[i] < low[i-1] and low[i] < low[i+1]
    """
    if len(df) < lookback + 3:
        return 999

    highs = df["high"].values
    lows = df["low"].values

    last_swing = None
    for i in range(len(df) - 2, 1, -1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            last_swing = i
            break
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            last_swing = i
            break

    if last_swing is None:
        return 999

    bars_since = len(df) - 1 - last_swing
    return bars_since


def compute_features(symbol: str) -> dict | None:
    """
    Compute EMA/RSI/ATR/Confidence + bars_since_swing + H1 trend + SPX bias
    """
    try:
        # ------------- ENV -----------------------
        tf = str(ENV.get("IDX_TIMEFRAME", "M15"))
        n_bars = int(ENV.get("IDX_HISTORY_BARS", 240))
        ema_fast_p = int(ENV.get("IDX_EMA_FAST", 20))
        ema_slow_p = int(ENV.get("IDX_EMA_SLOW", 50))
        rsi_period = int(ENV.get("IDX_RSI_PERIOD", 14))
        atr_period = int(ENV.get("IDX_ATR_PERIOD", 14))

        # ------------- Fetch M15 bars ------------
        df = get_bars(symbol, timeframe=tf, limit=n_bars)
        if df is None or len(df) < max(ema_fast_p, ema_slow_p, rsi_period, atr_period) + 2:
            log.warning("[DATA] %s insufficient bars", symbol)
            return None

        closes = df["close"].astype(float)
        price = float(closes.iloc[-1])

        # ------------- Primary Indicators --------
        ema_fast_val = ema(closes, ema_fast_p)
        ema_slow_val = ema(closes, ema_slow_p)
        rsi_val = rsi(closes, rsi_period)
        atr_val = atr(df, atr_period)

        atr_pct = float(atr_val / price) if price else 0.0
        ema_gap = float(ema_fast_val - ema_slow_val)

        # ------------- bars_since_swing ----------
        swing_lookback = int(ENV.get("IDX_SWING_LOOKBACK", 12))
        bars_since_swing = _compute_bars_since_swing(df, lookback=swing_lookback)

        # ------------- ATR Regime Tagging --------
        quiet_th = float(ENV.get("IDX_ATR_QUIET_PCT", 0.0009))
        hot_th = float(ENV.get("IDX_ATR_HOT_PCT", 0.0030))

        if atr_pct < quiet_th:
            atr_level = "quiet"
        elif atr_pct > hot_th:
            atr_level = "hot"
        else:
            atr_level = "normal"

        # ------------- Confidence ----------------
        ema_weight = float(ENV.get("IDX_CONF_BASE_WEIGHT_EMA", 0.5))
        rsi_weight = float(ENV.get("IDX_CONF_BASE_WEIGHT_RSI", 0.5))

        # EMA confidence relative to ATR-normalised gap
        ema_score = min(1.0, abs(ema_gap) / (max(1e-9, atr_pct) * 2.0))
        rsi_score = abs(rsi_val - 50.0) / 50.0

        raw_conf = max(0.0, min(1.0, ema_weight * ema_score + rsi_weight * rsi_score))
        adj_conf = adjusted_confidence(
            raw_conf, symbol, trust_weight=float(ENV.get("IDX_TRUST_WEIGHT", 0.4))
        )

        # ------------------------------------------------------------
        # H1 Trend Confluence (EMA fast/slow)
        # ------------------------------------------------------------
        try:
            df_h1 = get_bars(symbol, timeframe="H1", limit=120)
            if df_h1 is not None and len(df_h1) > ema_slow_p:
                closes_h1 = df_h1["close"].astype(float)
                ema_fast_h1 = ema(closes_h1, ema_fast_p)
                ema_slow_h1 = ema(closes_h1, ema_slow_p)
                trend_h1 = "BULL" if ema_fast_h1 > ema_slow_h1 else "BEAR"
            else:
                trend_h1 = "UNKNOWN"
        except Exception:
            trend_h1 = "UNKNOWN"

        # ------------------------------------------------------------
        # SPX Macro Bias (applies to NAS100 only)
        # ------------------------------------------------------------
        symbol_up = symbol.upper()
        if symbol_up.startswith("NAS"):
            spx_symbol = str(ENV.get("IDX_SPX_SYMBOL", "SP500.s"))
            spx_tf = str(ENV.get("IDX_SPX_TF", "H1"))
            spx_bias = "UNKNOWN"  # until computed
            try:
                df_spx = get_bars(spx_symbol, timeframe=spx_tf, limit=120)
                if df_spx is not None and len(df_spx) > ema_slow_p:
                    closes_spx = df_spx["close"].astype(float)
                    ema_fast_spx = ema(closes_spx, ema_fast_p)
                    ema_slow_spx = ema(closes_spx, ema_slow_p)
                    spx_bias = "BULL" if ema_fast_spx > ema_slow_spx else "BEAR"
            except Exception:
                spx_bias = "UNKNOWN"
        else:
            # Not NAS100 → macro bias does not apply
            spx_bias = "NA"



        # ------------------------------------------------------------
        # Build final feature map
        # ------------------------------------------------------------
        out = {
            "symbol": symbol,
            "timeframe": tf,
            "price": price,

            "ema_fast": float(ema_fast_val),
            "ema_slow": float(ema_slow_val),
            "ema_gap": float(ema_gap),

            "rsi": float(rsi_val),
            "atr_pct": float(atr_pct),
            "atr_level": atr_level,

            "bars_since_swing": int(bars_since_swing),

            "raw_conf": round(raw_conf, 4),
            "adj_conf": round(adj_conf, 4),

            "trend_h1": trend_h1,
            "spx_bias": spx_bias,
        }

        log.info(
            "[FEAT] %s TF=%s EMA_FAST=%.2f EMA_SLOW=%.2f GAP=%.2f RSI=%.2f "
            "ATR%%=%.4f ATR_LVL=%s RAW=%.2f ADJ=%.2f H1=%s SPX=%s swing=%d",
            symbol,
            tf,
            out["ema_fast"],
            out["ema_slow"],
            out["ema_gap"],
            out["rsi"],
            out["atr_pct"],
            out["atr_level"],
            out["raw_conf"],
            out["adj_conf"],
            out["trend_h1"],
            out["spx_bias"],
            out["bars_since_swing"],
        )

        return out

    except Exception as e:
        log.exception("[ERROR] compute_features failed for %s: %s", symbol, e)
        return None
