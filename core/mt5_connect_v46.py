# ============================================================
# Agentic Trader v4.6 — Core MT5 Connection Helper
# ------------------------------------------------------------
# Centralized shared MT5 initialization logic for:
#   • fx_v46
#   • xau_v46
#   • indices_v46
#
# Reads environment from any module's EnvNamespace.
# Automatically falls back to known working terminals.
# ============================================================

from __future__ import annotations
import MetaTrader5 as mt5  # type: ignore
import time
import os
from datetime import datetime

# Lazy import to avoid circular deps
from fx_v46.util.logger import setup_logger

# log = setup_logger(f"core.mt5_connect_v46__{datetime.now():%H%M%S}", level="INFO")

asset = os.getenv("ASSET", "GENERIC").upper()
timestamp = datetime.now().strftime("%H%M%S")

log = setup_logger(f"mt5_connect_{asset}_{timestamp}")

def _debug_env_dump():
    print("\n===== [MT5 CONNECT ENV DUMP] =====")
    for k in sorted(os.environ.keys()):
        if k.startswith("MT5") or k.startswith("IDX") or k.startswith("INDICES"):
            print(f"{k} = {os.environ[k]}")
    print("==================================\n")


# ============================================================
# Known terminal fallback paths
# ============================================================
KNOWN_TERMINALS = [
    r"C:\Users\Bomi\AppData\Roaming\MetaQuotes\Terminal\9BB124B7D418C7FB69DF2865535BA9BF\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\VTMarkets MetaTrader 5\terminal64.exe",
]


# ============================================================
# Initialization function
# ============================================================
def ensure_mt5_initialized(env=None, max_retries: int = 3, delay: float = 2.0) -> bool:
    """
    Universal MT5 initializer shared across asset modules.

    Args:
        env: EnvNamespace or dict with MT5_* keys
        max_retries: Number of reconnect attempts
        delay: Delay (seconds) between attempts

    Returns:
        bool: True if connected successfully, False otherwise
    """

    if env is None:
        log.warning("[MT5] Env not provided, relying on defaults")

    mt5_path = (env.get("MT5_PATH") if env else None) or ""
    mt5_login = (env.get("MT5_LOGIN") if env else None) or ""
    mt5_password = (env.get("MT5_PASSWORD") if env else None) or ""
    mt5_server = (env.get("MT5_SERVER") if env else None) or ""

    tried_paths = []

    def _try_init(path: str):
        tried_paths.append(path)
        log.info("[MT5] Trying terminal: %s", path)
        if not mt5.initialize(path):
            log.warning("[MT5] initialize() failed: %s", mt5.last_error())
            return False

        if mt5_login and mt5_password and mt5_server:
            if not mt5.login(int(mt5_login), password=mt5_password, server=mt5_server):
                log.warning("[MT5] login() failed: %s", mt5.last_error())
                mt5.shutdown()
                return False

        info = mt5.terminal_info()
        if info is None:
            log.warning("[MT5] terminal_info() unavailable, shutting down.")
            mt5.shutdown()
            return False

        log.info("[MT5] Connected successfully → %s | Server: %s", info.name, mt5_server or info.server)
        return True

    # --------------------------------------------------------
    # Attempt sequence
    # --------------------------------------------------------
    for attempt in range(1, max_retries + 1):
        # _debug_env_dump()
        log.info("[MT5] Initialization attempt %d/%d", attempt, max_retries)

        # 1️⃣ Try env path
        if mt5_path and os.path.exists(mt5_path):
            if _try_init(mt5_path):
                return True

        # 2️⃣ Try fallback known terminals
        for fallback_path in KNOWN_TERMINALS:
            if fallback_path not in tried_paths and os.path.exists(fallback_path):
                if _try_init(fallback_path):
                    return True

        log.warning("[MT5] Attempt %d failed, retrying in %.1fs...", attempt, delay)
        time.sleep(delay)

    log.error("[MT5] All %d attempts failed (%s)", max_retries, datetime.now().strftime("%H:%M:%S"))
    return False


# ============================================================
# Safe shutdown
# ============================================================
def safe_shutdown():
    """Gracefully close MT5 session."""
    try:
        mt5.shutdown()
        log.info("[MT5] Shutdown successful.")
    except Exception as e:
        log.warning("[MT5] Shutdown failed: %s", e)


# ============================================================
# Self-test
# ============================================================
if __name__ == "__main__":
    print("Testing core.mt5_connect_v46...")
    ok = ensure_mt5_initialized()
    if ok:
        print("✅ MT5 connected successfully.")
    else:
        print("❌ MT5 connection failed.")
    safe_shutdown()
