# ============================================================
# Agentic Trader v4.6 — XAU Symbol Diagnostics Tool
# ------------------------------------------------------------
# Purpose:
#   • Validate broker symbol configuration for XAUUSD
#   • Check min/max lots, stop levels, digits, spread
#   • Confirm that MT5 initialization & environment load work
# ============================================================

from __future__ import annotations
import os
import csv
import time
import MetaTrader5 as mt5  # type: ignore
from datetime import datetime

from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.logger import setup_logger

log = setup_logger("xau_symbol_diagnostics_v46", level="INFO")

# ------------------------------------------------------------
# Helper: ensure directories exist
# ------------------------------------------------------------
def ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

# ------------------------------------------------------------
# Initialize MT5 session
# ------------------------------------------------------------
def init_mt5() -> bool:
    path = ENV.get("MT5_PATH")
    login = int(ENV.get("MT5_LOGIN", 0))
    password = ENV.get("MT5_PASSWORD")
    server = ENV.get("MT5_SERVER")
    log.info("[INFO] Connecting to MT5 terminal: %s", path)
    if not mt5.initialize(path, login=login, password=password, server=server):
        log.error("[ERROR] MT5 initialize() failed: %s", mt5.last_error())
        return False
    return True

# ------------------------------------------------------------
# Diagnostic core
# ------------------------------------------------------------
def diagnose_symbols(symbols: list[str]):
    results = []
    for sym in symbols:
        info = mt5.symbol_info(sym)
        if not info:
            log.warning("[WARN] Symbol not found: %s", sym)
            continue

        tick = mt5.symbol_info_tick(sym)
        spread = (tick.ask - tick.bid) if tick else 0.0
        tradeable = "YES" if info.visible and info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL else "NO"

        within_lot_range = (
            float(ENV.get("XAU_MIN_LOTS", 0.05)) >= info.volume_min
            and float(ENV.get("XAU_MAX_LOTS", 0.10)) <= info.volume_max
        )
        lot_status = "OK ✅" if within_lot_range else "OUTSIDE ⚠️"

        results.append({
            "Symbol": sym,
            "Mode": info.trade_mode,
            "MinLot": info.volume_min,
            "Step": info.volume_step,
            "MaxLot": info.volume_max,
            "StopsLevel": info.stops_level,
            "Spread": round(spread, 2),
            "Digits": info.digits,
            "LotRange": lot_status,
            "Trade?": tradeable
        })

        log.info(
            "%-12s | Mode=%s | MinLot=%.2f | MaxLot=%.2f | Spread=%.2f | Trade=%s",
            sym, info.trade_mode, info.volume_min, info.volume_max, spread, tradeable
        )
    return results

# ------------------------------------------------------------
# CSV writer
# ------------------------------------------------------------
def export_csv(results):
    ensure_dir("logs/")
    filename = f"logs/symbol_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    log.info("✅ Diagnostics complete. Results saved to: %s", filename)
    return filename

# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------
def main():
    log.info("[INFO] Environment loaded from %s", ENV.path if hasattr(ENV, "path") else "<unknown>")
    symbols_str = ENV.get("XAU_AGENT_SYMBOLS", "XAUUSD-ECNc")
    symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]

    log.info("[INFO] Loaded XAU symbols: %s", symbols)

    if not init_mt5():
        return

    results = diagnose_symbols(symbols)
    if results:
        export_csv(results)

    mt5.shutdown()

# ------------------------------------------------------------
# Runner
# ------------------------------------------------------------
if __name__ == "__main__":
    print(">>\nRunning XAUUSD Symbol Diagnostics...\n")
    main()
    print("\nDone ✅")
