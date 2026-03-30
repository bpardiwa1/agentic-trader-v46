"""
analytics/optimizer.py

Auto-tuning engine for Agentic Trader v4.6 thresholds.
Uses:
  - trade_history (realized P&L)
  - loop_events (conf/trust/RSI/ATR at entry)
"""

import numpy as np
import pandas as pd
from pathlib import Path

from analytics.metrics_trades import (
    get_enriched_trades,
    compute_r_multiples,
    winrate_by_confidence
)
from analytics.metrics import get_skip_reasons


# -------------------------------------------------------
# Helper: safe nanmean
# -------------------------------------------------------

def safe_mean(x):
    x = [v for v in x if v is not None]
    return np.mean(x) if x else None


# -------------------------------------------------------
# 1) Confidence Threshold Tuner
# -------------------------------------------------------

def tune_confidence(df_trades):
    """
    Logic:
      - If high-conf trades (conf >= 0.60) have poor win rate or poor R-multiples → tighten threshold.
      - If medium-conf trades (0.50–0.60) have good performance and many SKIP → loosen threshold.
    """

    if df_trades.empty:
        return None

    # Confidence buckets
    df = df_trades.copy()
    df["bucket"] = pd.cut(df["entry_conf"], bins=[0.4, 0.5, 0.6, 0.7, 1.0])

    summary = (
        df.groupby("bucket")["profit"]
        .agg(["count", "mean"])
        .reset_index()
    )

    # Determine tuning direction
    hi = df[df["entry_conf"] >= 0.60]["profit"].mean()
    mid = df[(df["entry_conf"] >= 0.50) & (df["entry_conf"] < 0.60)]["profit"].mean()

    recommendation = {}

    if hi is not None and hi < 0:
        recommendation["strict"] = "increase (tighten threshold)"
    else:
        recommendation["strict"] = "keep or slightly lower"

    if mid is not None and mid > 0:
        recommendation["flex"] = "lower threshold slightly"
    else:
        recommendation["flex"] = "keep"

    return recommendation, summary


# -------------------------------------------------------
# 2) ATR Floor Tuner
# -------------------------------------------------------

def tune_atr_floor(df_trades):
    """
    High-level logic:
      - If low ATR trades have poor P&L → raise floor.
      - If low ATR skipped signals had good opportunity → lower floor.
    """

    if df_trades.empty:
        return None

    df = df_trades.copy()
    df["atr_zone"] = pd.cut(df["entry_atr_pct"], bins=[0, 0.0004, 0.0007, 0.0012, 1.0])

    zone_stats = df.groupby("atr_zone")["profit"].mean()

    rec = {}

    low_vol_mean = safe_mean(df[df["entry_atr_pct"] < 0.0004]["profit"])
    if low_vol_mean is not None and low_vol_mean < 0:
        rec["atr_floor"] = "raise (filter weak low-vol trades)"
    else:
        rec["atr_floor"] = "lower (allow more low-vol trades)"

    return rec, zone_stats


# -------------------------------------------------------
# 3) RSI Gate Tuner
# -------------------------------------------------------

def tune_rsi(df_trades):
    """
    Evaluate:
      - RSI > 65 (bull)
      - RSI < 35 (bear)
      - Middle zone trades

    Logic:
      - If middle zone trades lose → widen zone (tighten RSIs)
      - If outer zone trades win → keep thresholds
      - If many good trades appear inside the middle zone → loosen thresholds
    """

    if df_trades.empty:
        return None

    df = df_trades.copy()

    df["rsi_zone"] = pd.cut(
        df["rsi"],
        bins=[0, 35, 50, 65, 100],
        labels=["oversold", "mid_low", "mid_high", "overbought"]
    )

    zone_stats = df.groupby("rsi_zone")["profit"].mean()

    rec = {}

    if zone_stats.get("mid_low", 0) < 0 and zone_stats.get("mid_high", 0) < 0:
        rec["rsi"] = "tighten (raise long threshold, lower short threshold)"
    else:
        rec["rsi"] = "loosen slightly"

    return rec, zone_stats


# -------------------------------------------------------
# MAIN ENTRY — Auto-Tune for a given agent
# -------------------------------------------------------

def auto_tune(db_path: Path, agent: str):
    df = get_enriched_trades(db_path, agent=agent)
    df = compute_r_multiples(df)

    results = {}

    if df.empty:
        return {"error": "No trades available"}

    # 1) Confidence Tuning
    results["confidence"], results["confidence_stats"] = tune_confidence(df)

    # 2) ATR Tuning
    results["atr"], results["atr_stats"] = tune_atr_floor(df)

    # 3) RSI Tuning
    results["rsi"], results["rsi_stats"] = tune_rsi(df)

    return results
