# fx_v46/util/fx_event_sink.py
# ============================================================
# FX v4.6 — Shared JSONL Event Sink (Watcher-friendly)
# ------------------------------------------------------------
# Writes:
#   1) log line:  EVENT {...}
#   2) jsonl line: {...}
#
# Daily rotation:
#   logs/fx_v4.6/fx_events_YYYY-MM-DD.jsonl   (default)
# ============================================================

from __future__ import annotations

import json
import os
from datetime import datetime

from fx_v46.app.fx_env_v46 import ENV


def _events_dir() -> str:
    return str(ENV.get("FX_EVENTS_DIR", "logs/fx_v4.6"))


def _daily_rotate_enabled() -> bool:
    v = str(ENV.get("FX_EVENTS_DAILY_ROTATE", "true")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _events_path_for_today() -> str:
    base_dir = _events_dir()
    os.makedirs(base_dir, exist_ok=True)

    if _daily_rotate_enabled():
        fname = f"fx_events_{datetime.now():%Y-%m-%d}.jsonl"
    else:
        fname = "fx_events.jsonl"

    return os.path.join(base_dir, fname)


def emit_event(event_type: str, payload: dict, *, log=None, asset: str = "FX") -> None:
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

        # 1) human log (optional, default OFF for FX to keep logs readable)
        try:
            v = str(ENV.get("FX_EVENTS_LOG_TO_MAIN", "false")).strip().lower()
            log_to_main = v in ("1", "true", "yes", "on")
            if log_to_main and log is not None:
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