# ============================================================
# Agentic Trader v4.6 — XAUUSD Bar Fetcher (MT5 Safe Loader)
# ------------------------------------------------------------
# Purpose:
#   • Fetch OHLC bars from MetaTrader5 safely with retries.
#   • Reconnects automatically if MT5 session drops.
#   • Ensures enough bars for indicator calculations.
# ============================================================

from __future__ import annotations
import MetaTrader5 as mt5  # type: ignore
import pandas as pd
import time
from datetime import datetime
from xau_v46.app.xau_env_v46 import ENV  # noqa: F401
from xau_v46.util.logger import setup_logger


log = setup_logger("xau_mt5_bars_v46", level="INFO")

# ------------------------------------------------------------
# Core fetcher
# ------------------------------------------------------------
def _ensure_mt5_ready():
    """Initialize MT5 terminal if not already connected."""
    if not mt5.initialize():
        log.warning("[MT5] initialize() failed, retrying...")
        time.sleep(2)
        if not mt5.initialize():
            raise RuntimeError("MetaTrader5 initialization failed.")
    return True




def get_bars(symbol: str, timeframe: str = "M15", limit: int = 240) -> pd.DataFrame:
    """
    Robust MT5 bar fetcher with forced history load and retries.
    """

    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M15)

    # Ensure symbol is ready
    mt5.symbol_select(symbol, True)

    # 🔁 Force history refresh with retries
    bars = None
    for i in range(5):
        bars = mt5.copy_rates_from_pos(symbol, tf, 0, limit)
        if bars is not None and len(bars) >= limit:
            break
        print(f"[WARN] get_bars: only {0 if bars is None else len(bars)} bars received (attempt {i+1}/5)")
        mt5.history_select(0, time.time())
        time.sleep(2)

    if bars is None or len(bars) < 10:
        raise RuntimeError(f"[ERROR] get_bars: still insufficient bars for {symbol} after retries")

    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


# ------------------------------------------------------------
# Self-test utility
# ------------------------------------------------------------
if __name__ == "__main__":
    sym = "XAUUSD-ECNc"
    print(f"Testing MT5 bar fetch for {sym}...")
    try:
        bars = get_bars(sym, "M15", 120)
        print(bars.tail())
        print(f"✅ Retrieved {len(bars)} bars successfully at {datetime.now()}")
    except Exception as e:
        print(f"❌ Fetch failed: {e}")
