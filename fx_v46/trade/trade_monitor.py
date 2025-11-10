"""
Agentic Trader FX v4 — Trade Monitor Service
--------------------------------------------
Runs as a FastAPI microservice under Uvicorn.
Monitors closed trades, updates trust, and enforces guardrails.
"""

from __future__ import annotations
import asyncio
import datetime as dt
import MetaTrader5 as mt5  # type: ignore
from fastapi import FastAPI
from fx_v4.trust.trust_engine import update_trust
from fx_v4.app.fx_env import ENV
from fx_v4.util.logger import setup_logger

log = setup_logger("fx_trade_monitor", level="INFO")

app = FastAPI(title="Agentic Trader FX v4 — Trade Monitor", version="4.0")

# Global state
last_check = dt.datetime.now() - dt.timedelta(minutes=10)
seen_tickets: set[int] = set()
poll_interval = 60  # seconds


# -------------------------------------------------------------
# Core monitoring logic
# -------------------------------------------------------------
async def monitor_loop():
    global last_check
    while True:
        try:
            now = dt.datetime.now()
            deals = mt5.history_deals_get(last_check, now)
            last_check = now

            if deals:
                for d in deals:
                    ticket = int(getattr(d, "ticket", 0))
                    symbol = getattr(d, "symbol", "")
                    profit = float(getattr(d, "profit", 0.0))
                    if ticket in seen_tickets or not symbol:
                        continue
                    seen_tickets.add(ticket)
                    update_trust(symbol, profit > 0)
                    log.info("[MONITOR] %s closed profit=%.2f → trust %s", symbol, profit, "UP" if profit > 0 else "DOWN")

            # Enforce guardrails
            positions = mt5.positions_get()
            total_open = len(positions or [])
            if total_open >= ENV.agent_max_open:
                log.warning("[GUARDRAIL] Global max open trades reached (%d/%d)", total_open, ENV.agent_max_open)
            per_symbol = {}
            for p in positions or []:
                per_symbol[p.symbol] = per_symbol.get(p.symbol, 0) + 1
            for sym, count in per_symbol.items():
                if count > ENV.agent_max_per_symbol:
                    log.warning("[GUARDRAIL] %s exceeded per-symbol limit (%d/%d)", sym, count, ENV.agent_max_per_symbol)

        except Exception as e:
            log.exception("[TradeMonitor] loop error: %s", e)

        await asyncio.sleep(poll_interval)


# -------------------------------------------------------------
# FastAPI routes
# -------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    log.info("🚀 Trade Monitor started (interval=%ss)", poll_interval)
    if not mt5.initialize():
        log.warning("[MT5] Initialization failed: %s", mt5.last_error())
    asyncio.create_task(monitor_loop())


@app.get("/status")
async def get_status():
    return {
        "uptime": str(dt.datetime.now() - last_check),
        "open_positions": len(mt5.positions_get() or []),
        "poll_interval": poll_interval,
    }
