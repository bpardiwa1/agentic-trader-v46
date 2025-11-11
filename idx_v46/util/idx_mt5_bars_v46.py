# ============================================================
# Agentic Trader v4.6 — IDX Bar Fetcher (MT5 Safe Loader)
# ============================================================

from __future__ import annotations
import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime
from idx_v46.app.idx_env_v46 import ENV
from idx_v46.util.idx_logger_v46 import setup_logger

log = setup_logger("idx_mt5_bars_v46", level=str(ENV.get("LOG_LEVEL", "INFO")))

def _ensure_mt5_ready():
    if not mt5.initialize():
        log.warning("[MT5] initialize() failed, retrying...")
        time.sleep(2)
        if not mt5.initialize():
            raise RuntimeError("MetaTrader5 initialization failed.")
    return True

def get_bars(symbol: str, timeframe: str = "M15", limit: int = 240) -> pd.DataFrame:
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M15)
    mt5.symbol_select(symbol, True)

    bars = None
    for i in range(5):
        bars = mt5.copy_rates_from_pos(symbol, tf, 0, limit)
        if bars is not None and len(bars) >= min(limit, 10):
            break
        log.warning("[WARN] get_bars(%s): %s bars (attempt %d/5)",
                    symbol, 0 if bars is None else len(bars), i + 1)
        mt5.history_select(0, time.time())
        time.sleep(2)

    if bars is None or len(bars) < 10:
        raise RuntimeError(f"[ERROR] get_bars: insufficient bars for {symbol}")

    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

if __name__ == "__main__":
    sym = "NAS100.s"
    try:
        bars = get_bars(sym, "M15", 120)
        print(bars.tail())
        print(f"✅ Retrieved {len(bars)} bars successfully at {datetime.now()}")
    except Exception as e:
        print(f"❌ Fetch failed: {e}")
