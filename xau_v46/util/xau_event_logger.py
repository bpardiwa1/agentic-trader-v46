# xau_v46/util/xau_event_logger.py
# ============================================================
# Agentic Trader XAU v4.6 — JSONL Event Logger
# ============================================================
# Writes watcher-friendly one-line JSON records to:
#   logs/xau_v4.6/xau_events.jsonl
#
# IMPORTANT:
#  - Best-effort only: must never break trading flow.
#  - Payload is JSON-serializable; unknown types are stringified.
# ============================================================

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def _iso_now() -> str:
    # Local time with timezone offset (preferred for cross-module correlation)
    return datetime.now().astimezone().isoformat(timespec="seconds")


def emit_event_jsonl(
    *,
    event_type: str,
    payload: Dict[str, Any],
    log_dir: str = "logs/xau_v4.6",
    filename: str = "xau_events.jsonl",
    asset: str = "XAU",
    run_id: Optional[str] = None,
) -> None:
    """
    Append a single JSON object per line to the JSONL stream.
    """
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        path = Path(log_dir) / filename

        record: Dict[str, Any] = {
            "ts": _iso_now(),
            "asset": asset,
            "event": str(event_type or "").upper(),
            "payload": payload or {},
        }
        if run_id:
            record["run_id"] = run_id

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # Never allow event logging to break trading flow
        return
