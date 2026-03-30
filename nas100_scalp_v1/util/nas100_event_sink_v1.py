# ============================================================
# NAS100 Scalper v1 — JSONL Event Sink (Watcher-friendly)
# ============================================================

from __future__ import annotations

import json
import os
from datetime import datetime

from nas100_scalp_v1.app.nas100_env_v1 import ENV


def _events_dir() -> str:
    return str(ENV.get("IDX_EVENTS_DIR", "logs/nas100_scalp_v1"))


def _daily_rotate_enabled() -> bool:
    v = str(ENV.get("IDX_EVENTS_DAILY_ROTATE", "true")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _events_path_for_today() -> str:
    base_dir = _events_dir()
    os.makedirs(base_dir, exist_ok=True)

    if _daily_rotate_enabled():
        fname = f"nas100_scalp_events_{datetime.now():%Y-%m-%d}.jsonl"
    else:
        fname = "nas100_scalp_events.jsonl"

    return os.path.join(base_dir, fname)


def emit_event(event_type: str, payload: dict, *, log=None, asset: str = "INDEX") -> None:
    try:
        record = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "asset": asset,
            "event": str(event_type or "").upper(),
            "payload": payload or {},
        }

        # optional: log EVENT line to main log
        try:
            v = str(ENV.get("IDX_EVENTS_LOG_TO_MAIN", "false")).strip().lower()
            log_to_main = v in ("1", "true", "yes", "on")
            if log_to_main and log is not None:
                log.info("EVENT %s", json.dumps(record, ensure_ascii=False, default=str))
        except Exception:
            pass

        # always: jsonl
        try:
            path = _events_path_for_today()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    except Exception:
        pass