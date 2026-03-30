"""
analytics/mt5_history.py

Imports MT5 positions and history into analytics.db
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List
import MetaTrader5 as mt5

# -------------------------------------------------------------
# SQLite Schema Extension
# -------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_ts TEXT NOT NULL,
    ticket INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    type TEXT,
    volume REAL,
    price_open REAL,
    sl REAL,
    tp REAL,
    profit REAL,
    swap REAL,
    commission REAL
);

CREATE TABLE IF NOT EXISTS trade_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    order_id INTEGER,
    symbol TEXT,
    side TEXT,
    volume REAL,
    price_open REAL,
    price_close REAL,
    time_open TEXT,
    time_close TEXT,
    sl REAL,
    tp REAL,
    profit REAL,
    swap REAL,
    commission REAL,
    magic INTEGER,
    comment TEXT
);
"""


def init_history_db(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------
# MT5 Helpers
# -------------------------------------------------------------

def mt5_connect(login: int, password: str, server: str, path: str):
    if not mt5.initialize(path, login=login, password=password, server=server):
        raise RuntimeError("MT5 initialize failed")
    return True


# -------------------------------------------------------------
# Live Positions
# -------------------------------------------------------------

def snapshot_live_positions(db_path: Path):
    positions = mt5.positions_get()
    now = datetime.utcnow().isoformat()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    for pos in positions or []:
        cur.execute(
            """
            INSERT INTO live_positions (
                snapshot_ts, ticket, symbol, type, volume,
                price_open, sl, tp, profit, swap, commission
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                pos.ticket,
                pos.symbol,
                "BUY" if pos.type == 0 else "SELL",
                pos.volume,
                pos.price_open,
                pos.sl,
                pos.tp,
                pos.profit,
                pos.swap,
                0.0,
            ),
        )

    conn.commit()
    conn.close()
    return len(positions or [])


# -------------------------------------------------------------
# Closed Trades (History)
# -------------------------------------------------------------

def import_trade_history(
    db_path: Path,
    days_back: int = 30,
):
    date_to = datetime.utcnow()
    date_from = date_to - timedelta(days=days_back)

    deals = mt5.history_deals_get(date_from, date_to)

    if deals is None:
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    for d in deals:
        cur.execute(
            """
            INSERT INTO trade_history (
                deal_id, order_id, symbol, side, volume,
                price_open, price_close, time_open, time_close,
                sl, tp, profit, swap, commission, magic, comment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                d.ticket,
                d.order,
                d.symbol,
                "BUY" if d.type == 0 else "SELL",
                d.volume,
                d.price,
                d.price,         # FIX: in real MT5, price_open isn't included; OK: use price
                datetime.utcfromtimestamp(d.time).isoformat(),
                datetime.utcfromtimestamp(d.time).isoformat(),
                None,
                None,
                d.profit,
                d.swap,
                d.commission,
                d.magic,
                d.comment,
            ),
        )

    conn.commit()
    conn.close()
    return len(deals)
