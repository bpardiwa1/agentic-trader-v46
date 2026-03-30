# analytics/log_parser.py

from __future__ import annotations

import sqlite3
import re
from pathlib import Path
from typing import Dict, Any

# -----------------------------------------------------------
# Regexes for log parsing
# -----------------------------------------------------------

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"\[[A-Z]+\] "
    r"(?P<logger>[^ ]+) - "
    r"(?P<msg>.*)$"
)

DEBUG_RE = re.compile(
    r"\[DEBUG] (?P<symbol>\S+) .* "
    r"ATR%=(?P<atr_pct>[-0-9.]+) \| "
    r"CONF=(?P<conf>[-0-9.]+) "
    r"TRUST=(?P<trust>[-0-9.]+) "
    r"LOT=(?P<lot>[-0-9.]+) "
    r"WHY=(?P<why>.*)$"
)

EXEC_RE = re.compile(
    r"\[EXECUTED] (?P<symbol>\S+) (?P<side>LONG|SHORT) ok"
)

SKIP_RE = re.compile(
    r"\[SKIP] (?P<symbol>\S+) .*conf=(?P<conf>[-0-9.]+), "
    r"reason=(?P<reasons>.*)\)$"
)


# -----------------------------------------------------------
# DB init
# -----------------------------------------------------------

def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS loop_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            agent       TEXT,
            symbol      TEXT,
            event_type  TEXT,
            side        TEXT,
            confidence  REAL,
            trust       REAL,
            atr_pct     REAL,
            policy      TEXT,
            reasons     TEXT,
            raw         TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _infer_agent(logger_name: str) -> str:
    """Map logger name -> FX_V46 / XAU_V46 / IDX_V46 / CORE_V46."""
    ln = logger_name.lower()
    if ln.startswith("fx_v46"):
        return "FX_V46"
    if ln.startswith("xau_v46"):
        return "XAU_V46"
    if ln.startswith("idx_v46"):
        return "IDX_V46"
    if ln.startswith("core_v46"):
        return "CORE_V46"
    return logger_name.upper()


# -----------------------------------------------------------
# Main parse function
# -----------------------------------------------------------

def parse_log_file(log_path: Path, db_path: Path) -> int:
    """
    Parse one log file and insert events into loop_events.
    Returns number of rows inserted.
    """
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    inserted = 0
    last_debug_by_symbol: Dict[str, Dict[str, Any]] = {}

    with log_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue

            ts = m.group("ts")
            logger = m.group("logger")
            msg = m.group("msg")
            agent = _infer_agent(logger)

            # --- DEBUG snapshot ---------------------------------------------
            dm = DEBUG_RE.search(msg)
            if dm:
                info = dm.groupdict()
                info["ts"] = ts
                last_debug_by_symbol[info["symbol"]] = info
                continue

            # --- SKIP event --------------------------------------------------
            sm = SKIP_RE.search(msg)
            if sm:
                d = sm.groupdict()
                cur.execute(
                    """
                    INSERT INTO loop_events (
                        ts, agent, symbol, event_type, side,
                        confidence, trust, atr_pct, policy, reasons, raw
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        agent,
                        d["symbol"],
                        "SKIP",
                        None,
                        float(d["conf"]),
                        None,
                        None,
                        None,
                        d["reasons"],
                        msg.strip(),
                    ),
                )
                inserted += 1
                continue

            # --- EXECUTED trade ---------------------------------------------
            em = EXEC_RE.search(msg)
            if em:
                symbol = em.group("symbol")
                side = em.group("side")

                dbg = last_debug_by_symbol.get(symbol, {})
                conf = float(dbg["conf"]) if "conf" in dbg else None
                trust = float(dbg["trust"]) if "trust" in dbg else None
                atr_pct = float(dbg["atr_pct"]) if "atr_pct" in dbg else None
                reasons = dbg.get("why")

                cur.execute(
                    """
                    INSERT INTO loop_events (
                        ts, agent, symbol, event_type, side,
                        confidence, trust, atr_pct, policy, reasons, raw
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        agent,
                        symbol,
                        "EXECUTED",
                        side,
                        conf,
                        trust,
                        atr_pct,
                        None,
                        reasons,
                        msg.strip(),
                    ),
                )
                inserted += 1
                continue

    conn.commit()
    conn.close()
    return inserted
