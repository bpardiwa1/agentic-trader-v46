"""
analytics/metrics.py
Analytics layer for Agentic Trader v4.6
"""

import sqlite3
from pathlib import Path
import pandas as pd


def conn(db_path: Path):
    return sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)

# ---------------------------------------------------------------
# Trades per day
# ---------------------------------------------------------------

def get_trades_per_day(db_path, agent=None, symbol=None):
    conn = sqlite3.connect(str(db_path))

    params = []
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
            date(th.time_close) AS trade_date,
            COUNT(*) AS n_trades,
            SUM(th.profit) AS net_profit
        FROM trade_history th
        LEFT JOIN loop_events le
            ON le.symbol = th.symbol
           AND le.event_type='EXECUTED'
           AND ABS(strftime('%s', th.time_open) - strftime('%s', le.ts)) < 120
        WHERE 1=1
        {agent_sql}
        {symbol_sql}
        GROUP BY trade_date
        ORDER BY trade_date;
    """

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df

# ---------------------------------------------------------------
# Skip reasons
# ---------------------------------------------------------------



def get_skip_reasons(db_path, agent=None, symbol=None, limit: int = 1000) -> pd.DataFrame:
    """
    Return recent SKIP events with reasons from loop_events.

    Uses the new schema:
      confidence, trust, atr_pct, policy, reasons, raw
    """

    conn = sqlite3.connect(db_path)

    sql = (
        "SELECT "
        "  date(ts) AS date, "
        "  ts, "
        "  agent, "
        "  symbol, "
        "  policy, "
        "  confidence, "
        "  atr_pct, "
        "  reasons "
        "FROM loop_events "
        "WHERE event_type='SKIP' "
    )
    params = []

    if agent and agent != "ALL":
        sql += "AND agent = ? "
        params.append(agent)

    if symbol:
        sql += "AND symbol = ? "
        params.append(symbol)

    sql += "ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


# ---------------------------------------------------------------
# Policy stats
# ---------------------------------------------------------------

def get_policy_stats(db_path, agent=None):
    """
    Summaries for SKIP events grouped by agent, symbol, policy.
    Uses the new schema:
      confidence, trust, atr_pct, policy, reasons
    """

    conn = sqlite3.connect(db_path)

    sql = (
        "SELECT "
        "  agent, "
        "  symbol, "
        "  policy, "
        "  COUNT(*) AS total_skips, "
        "  SUM(CASE WHEN reasons LIKE '%conf%' THEN 1 ELSE 0 END) AS skipped_conf_gate, "
        "  SUM(CASE WHEN reasons LIKE '%policy%' THEN 1 ELSE 0 END) AS skipped_policy_rules, "
        "  SUM(CASE WHEN reasons LIKE '%atr%' THEN 1 ELSE 0 END) AS skipped_atr_floor "
        "FROM loop_events "
        "WHERE event_type='SKIP' "
    )

    params = []
    if agent and agent != "ALL":
        sql += "AND agent = ? "
        params.append(agent)

    sql += "GROUP BY agent, symbol, policy ORDER BY agent, symbol"

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


# ---------------------------------------------------------------
# Trust × Confidence heatmap
# ---------------------------------------------------------------

def get_trust_conf_heatmap(db_path, agent=None, symbol=None):
    """
    Return EXECUTED events with confidence/trust for heatmap plotting.

    Uses new schema (confidence, trust) but aliases confidence -> conf
    so the dashboard code can stay unchanged.
    """
    conn = sqlite3.connect(db_path)

    sql = (
        "SELECT "
        "  agent, "
        "  symbol, "
        "  confidence AS conf, "  # alias for compatibility
        "  trust "
        "FROM loop_events "
        "WHERE event_type = 'EXECUTED' "
    )
    params = []

    if agent and agent != "ALL":
        sql += "AND agent = ? "
        params.append(agent)

    if symbol:
        sql += "AND symbol = ? "
        params.append(symbol)

    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df
