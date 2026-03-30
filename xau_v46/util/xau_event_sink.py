# xau_v46/util/xau_event_sink.py
# ============================================================
# XAU v4.6 — Shared JSONL Event Sink (Watcher-friendly)
# ------------------------------------------------------------
# Writes:
#   1) log line:  EVENT {...}
#   2) jsonl line: {...}
#
# Daily rotation:
#   logs/xau_v4.6/xau_events_YYYY-MM-DD.jsonl   (default)
# ============================================================

from __future__ import annotations

import json
import os
from datetime import datetime

from xau_v46.app.xau_env_v46 import ENV


def _events_dir() -> str:
    return str(ENV.get("XAU_EVENTS_DIR", "logs/xau_v4.6"))


def _daily_rotate_enabled() -> bool:
    v = str(ENV.get("XAU_EVENTS_DAILY_ROTATE", "true")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _events_path_for_today() -> str:
    base_dir = _events_dir()
    os.makedirs(base_dir, exist_ok=True)

    if _daily_rotate_enabled():
        fname = f"xau_events_{datetime.now():%Y-%m-%d}.jsonl"
    else:
        fname = "xau_events.jsonl"

    return os.path.join(base_dir, fname)


def emit_event(event_type: str, payload: dict, *, log=None, asset: str = "XAU") -> None:
    """
    Emit an event in two streams:
      - human log:  EVENT {json}
      - jsonl file: {json}\n

    Never throws (safe for trading flow).
    """
    try:
        record = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "asset": asset,
            "event": str(event_type or "").upper(),
            "payload": payload or {},
        }

        # 1) human log (optional)
        try:
            if log is not None:
                log.info("EVENT %s", json.dumps(record, ensure_ascii=False, default=str))
        except Exception:
            pass

        # 2) jsonl file
        try:
            path = _events_path_for_today()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    except Exception:
        # never break trading
        pass
