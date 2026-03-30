from __future__ import annotations
import os
import requests
from datetime import datetime


def tg_enabled(prefix: str) -> bool:
    # prefix examples: FX, XAU, IDX
    return str(os.getenv(f"{prefix}_ALERTS_ENABLED", "false")).lower() in ("1", "true", "yes")


def tg_send(prefix: str, message: str) -> None:
    if not tg_enabled(prefix):
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        return  # silent no-op (avoid crashing trader)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "chat_id": chat_id,
        "text": f"[{prefix}] {ts}\n{message}",
        "disable_web_page_preview": True,
    }

    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=5)
    except Exception:
        # never raise (alerts must not break trading)
        return
