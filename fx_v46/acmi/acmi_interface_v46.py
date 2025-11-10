"""
Agentic Trader FX v4.6 — ACMI Interface
---------------------------------------
Lightweight connector between the agent and dashboard.
If the dashboard is not running, this will just log updates safely.
"""

from __future__ import annotations
import logging
import MetaTrader5 as mt5  # optional, for connection status

log = logging.getLogger("acmi_interface")

class _ACMI:
    def __init__(self):
        try:
            if mt5.initialize():
                log.info("[ACMI] ✅ Connected to MT5")
            else:
                log.warning("[ACMI] ⚠️ Could not connect to MT5: %s", mt5.last_error())
        except Exception as e:
            log.warning("[ACMI] ⚠️ Initialization failed: %s", e)

    def post_status(self, symbol: str, payload: dict):
        """Post trade status to dashboard or log if dashboard is inactive."""
        try:
            log.info("[ACMI] %s → %s", symbol, payload)
        except Exception as e:
            log.warning("[ACMI] Failed to post status for %s: %s", symbol, e)

# Global singleton used by agents
ACMI = _ACMI()
