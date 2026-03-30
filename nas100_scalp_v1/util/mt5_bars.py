from __future__ import annotations

from typing import Any
import pandas as pd
import MetaTrader5 as mt5  # type: ignore

def get_bars(symbol: str, timeframe: str, count: int) -> pd.DataFrame | None:
    tf = getattr(mt5, f"TIMEFRAME_{timeframe}", None)
    if tf is None:
        return None

    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df