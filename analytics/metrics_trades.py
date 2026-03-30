"""
analytics/metrics_trades.py
Analytics for realized P&L, R-multiples, equity curves.
"""

import sqlite3
from pathlib import Path
from typing import Optional
import pandas as pd


def conn(db): return sqlite3.connect(db, detect_types=sqlite3.PARSE_DECLTYPES)


# ----------------------------------------------------------
# Join trade_history with loop_events to attach entry CONF + TRUST
# ----------------------------------------------------------

def get_enriched_trades(
    db_path: str | Path,
    agent: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """
    Returns trade_history enriched with loop_events entry stats.
    Matches your REAL trade_history schema precisely.
    """

    conn = sqlite3.connect(str(db_path))

    # --- Filters ---
    params: list = []

    agent_sql = ""
    if agent and agent != "ALL":
        agent_sql = " AND le.agent = ?"
        params.append(agent)

    symbol_sql = ""
    if symbol:
        symbol_sql = " AND th.symbol = ?"
        params.append(symbol)

    sql = f"""
        SELECT
            th.id,
            th.deal_id,
            th.order_id,
            th.symbol,
            th.side,
            th.volume,
            th.price_open,
            th.price_close,
            th.time_open,
            th.time_close,
            th.sl,
            th.tp,
            th.profit,
            th.swap,
            th.commission,
            th.magic,
            th.comment,

            -- Enrichment from loop_events
            le.confidence AS entry_confidence,
            le.trust      AS entry_trust,
            le.atr_pct    AS entry_atr_pct,
            le.policy     AS entry_policy,
            le.reasons    AS entry_reasons

        FROM trade_history th
        LEFT JOIN loop_events le
            ON le.symbol = th.symbol
           AND le.event_type = 'EXECUTED'
           AND ABS(strftime('%s', th.time_open) - strftime('%s', le.ts)) < 120

        WHERE 1=1
        {agent_sql}
        {symbol_sql}

        ORDER BY th.time_open DESC
        LIMIT 1000
    """

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


# ----------------------------------------------------------
# Compute R-Multiple (Profit / Risk)
# ----------------------------------------------------------

def compute_r_multiples(df):
    """
    Assumes:
      R = profit / (|entry_price - SL|)
    """
    if df.empty:
        return df

    df = df.copy()

    # Risk per lot
    df["risk_per_lot"] = abs(df["price_open"] - df["sl"])
    df["R"] = df["profit"] / (df["risk_per_lot"] * df["volume"]).replace(0, 1)

    return df


# ----------------------------------------------------------
# Equity Curve
# ----------------------------------------------------------

def get_equity_curve(df_trades):
    """
    Returns cumulative P&L curve sorted by close time.
    """
    if df_trades.empty:
        return pd.DataFrame()

    df = df_trades.copy()
    df["time_close"] = pd.to_datetime(df["time_close"])
    df = df.sort_values("time_close")
    df["cum_pnl"] = df["profit"].cumsum()

    return df[["time_close", "cum_pnl"]]


# ----------------------------------------------------------
# Win Rate by Confidence Bucket
# ----------------------------------------------------------

def winrate_by_confidence(df_trades):
    """
    Compute win rate grouped by entry_conf buckets.
    Expects a DataFrame as returned by get_enriched_trades(...),
    optionally after compute_r_multiples(...).
    """

    if df_trades is None or df_trades.empty:
        return pd.DataFrame()

    df = df_trades.copy()

    # Drop trades where we don't have a confidence value
    # (e.g. MT5 history-only trades with no matching loop_event)
    if "entry_conf" not in df.columns:
        return pd.DataFrame()

    df = df[df["entry_conf"].notnull()]
    if df.empty:
        return pd.DataFrame()

    # Use net_profit if present, otherwise fall back to profit
    if "net_profit" in df.columns:
        profit_col = "net_profit"
    else:
        profit_col = "profit"

    # Define confidence buckets
    bins = [0.4, 0.5, 0.6, 0.7, 1.0]
    labels = ["0.4–0.5", "0.5–0.6", "0.6–0.7", "0.7–1.0"]

    df["conf_bucket"] = pd.cut(
        df["entry_conf"],
        bins=bins,
        labels=labels,
        include_lowest=True,
    )

    # Win flag
    df["win"] = df[profit_col] > 0

    summary = (
        df.groupby("conf_bucket")["win"]
        .mean()
        .reset_index()
        .rename(columns={"win": "win_rate"})
    )

    return summary
