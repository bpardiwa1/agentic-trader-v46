from __future__ import annotations

import os
import requests
from datetime import datetime


def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_get(key: str, default: str = "") -> str:
    """
    Read config from OS env first (os.environ), then fall back to module ENV loaders.
    This prevents silent no-op when bots load .env into ENV but not os.environ.
    """
    v = os.getenv(key, "").strip()
    if v:
        return v

    # --- fallbacks (best-effort, non-fatal) ---------------------------
    # NOTE: order does not matter much; we just try the env modules.
    try:
        from xau_v46.app.xau_env_v46 import ENV as XAU_ENV  # type: ignore
        v2 = str(XAU_ENV.get(key, "")).strip()
        if v2:
            return v2
    except Exception:
        pass

    try:
        from fx_v46.app.fx_env_v46 import ENV as FX_ENV  # type: ignore
        v2 = str(FX_ENV.get(key, "")).strip()
        if v2:
            return v2
    except Exception:
        pass

    try:
        from idx_v46.app.idx_env_v46 import ENV as IDX_ENV  # type: ignore
        v2 = str(IDX_ENV.get(key, "")).strip()
        if v2:
            return v2
    except Exception:
        pass

    return default


def tg_enabled(prefix: str) -> bool:
    """
    Enable flags supported (any one true enables):
      - {PREFIX}_ALERTS_ENABLED   (e.g. XAU_ALERTS_ENABLED=true)
      - TELEGRAM_ENABLED         (global switch)
    """
    prefix = str(prefix or "").strip().upper()
    if not prefix:
        return _truthy(_env_get("TELEGRAM_ENABLED", "false"))

    if _truthy(_env_get(f"{prefix}_ALERTS_ENABLED", "false")):
        return True

    return _truthy(_env_get("TELEGRAM_ENABLED", "false"))


def tg_send(prefix: str, message: str) -> None:
    """
    Best-effort Telegram send. Never raises.
    Adds minimal delivery verification (checks HTTP and 'ok' field).
    """
    prefix = str(prefix or "").strip().upper()
    if not tg_enabled(prefix):
        return

    token = _env_get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = _env_get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        # silent no-op (alerts must not break trading)
        return

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "chat_id": chat_id,
        "text": f"[{prefix}] {ts}\n{message}",
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=5,
        )

        # If Telegram rejects (e.g. chat not found / forbidden), do NOT crash trader,
        # but also do not pretend it worked.
        if r.status_code != 200:
            return

        data = {}
        try:
            data = r.json()
        except Exception:
            data = {}

        if isinstance(data, dict) and data.get("ok") is True:
            return

        # Not ok -> silent (by design)
        return

    except Exception:
        # never raise (alerts must not break trading)
        return
