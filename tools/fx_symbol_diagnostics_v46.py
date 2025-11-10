"""
Agentic Trader FX v4.6 — Symbol Diagnostics Utility (Full Broker Audit)
-----------------------------------------------------------------------
Verifies broker symbol parameters, compares against .env guardrails,
detects the correct VTMarkets terminal, and exports a full CSV report.

Usage:
    python -m tools.fx_symbol_diagnostics_v46
"""

from __future__ import annotations
import os, csv, glob
from datetime import datetime
import MetaTrader5 as mt5
from fx_v46.app.fx_env_v46 import ENV


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _as_list(val: str) -> list[str]:
    return [s.strip() for s in val.split(",") if s.strip()] if val else []


def _detect_terminal_path() -> str | None:
    """Auto-detect MT5 terminal path (prefers VTMarkets)."""
    base = os.path.expanduser(r"~\AppData\Roaming\MetaQuotes\Terminal")
    if not os.path.exists(base):
        return None
    candidates = glob.glob(os.path.join(base, "*"))
    vt_paths = [p for p in candidates if os.path.exists(os.path.join(p, "MQL5"))]
    for p in vt_paths:
        if "VTMarkets" in p or "VT" in p:
            return p
    return vt_paths[0] if vt_paths else None


def _fmt_warn(text: str, cond: bool) -> str:
    return f"{text} ⚠️" if cond else text


# ------------------------------------------------------------
# Main diagnostic
# ------------------------------------------------------------
def main():
    symbols = _as_list(ENV.get("AGENT_SYMBOLS", ""))
    if not symbols:
        print("[WARN] No AGENT_SYMBOLS found in env file.")
        return

    fx_min = float(ENV.get("FX_MIN_LOTS", 0.01))
    fx_max = float(ENV.get("FX_MAX_LOTS", 0.30))

    # --- Detect MT5 terminal ---
    term_path = _detect_terminal_path()
    if term_path:
        print(f"[INFO] Connecting to MT5 terminal: {term_path}")
        ok = mt5.initialize(term_path)
    else:
        print("[WARN] No MT5 terminal path detected, using default.")
        ok = mt5.initialize()

    if not ok:
        print(f"[ERROR] MT5 initialize() failed: {mt5.last_error()}")
        return

    # --- CSV setup ---
    os.makedirs("logs", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join("logs", f"symbol_diagnostics_{ts}.csv")

    headers = [
        "Symbol", "TradeMode", "TradeEnabled",
        "MinLot", "LotStep", "MaxLot",
        "StopsLevelPts", "Spread", "Digits",
        "ContractSize", "MarginCurrency", "MarginRate",
        "Env_MinLot", "Env_MaxLot", "LotRangeStatus", "Comment"
    ]
    rows = []

    print("\nBroker Symbol Diagnostics (v4.6)")
    print("-" * 140)
    print(f"{'Symbol':<15} {'Mode':<6} {'MinLot':<8} {'Step':<6} {'MaxLot':<8} "
          f"{'StopsLvl':<9} {'Spread':<8} {'Digits':<7} {'LotRange':<11} "
          f"{'MarginCur':<10} {'Contract':<10} {'Trade?':<7}")
    print("-" * 140)

    for sym in symbols:
        info = mt5.symbol_info(sym)
        if not info:
            print(f"{sym:<15} NOT FOUND ⚠️  (Add to Market Watch)")
            rows.append({"Symbol": sym, "Comment": "NOT FOUND in Market Watch"})
            continue

        trade_enabled = (info.trade_mode == 2 and info.visible)
        lot_status = "OK"
        comment = ""

        # --- Env lot range check ---
        if info.volume_min < fx_min or info.volume_max > fx_max:
            lot_status = "OUTSIDE ⚠️"
            comment += (
                f"Lot range [{info.volume_min:.2f}-{info.volume_max:.2f}] "
                f"outside env [{fx_min:.2f}-{fx_max:.2f}]"
            )

        # --- Disabled / market closed ---
        if not trade_enabled:
            if info.trade_mode == 4:
                comment += (" | " if comment else "") + "Market closed / disabled by broker"
            else:
                comment += (" | " if comment else "") + "Symbol not tradeable or hidden"

        # --- Margin info ---
        margin_currency = getattr(info, "currency_margin", "?")
        contract_size = getattr(info, "trade_contract_size", 0)
        margin_rate = getattr(info, "margin_rate", 0)

        print(f"{sym:<15} {info.trade_mode:<6} {info.volume_min:<8.2f} {info.volume_step:<6.2f} "
              f"{info.volume_max:<8.2f} {info.trade_stops_level:<9} {info.spread:<8.1f} "
              f"{info.digits:<7} {_fmt_warn(lot_status, lot_status != 'OK'):<11} "
              f"{margin_currency:<10} {contract_size:<10.1f} "
              f"{'YES' if trade_enabled else 'NO ⚠️':<7}")

        rows.append({
            "Symbol": sym,
            "TradeMode": info.trade_mode,
            "TradeEnabled": "YES" if trade_enabled else "NO",
            "MinLot": info.volume_min,
            "LotStep": info.volume_step,
            "MaxLot": info.volume_max,
            "StopsLevelPts": info.trade_stops_level,
            "Spread": info.spread,
            "Digits": info.digits,
            "ContractSize": contract_size,
            "MarginCurrency": margin_currency,
            "MarginRate": margin_rate,
            "Env_MinLot": fx_min,
            "Env_MaxLot": fx_max,
            "LotRangeStatus": lot_status,
            "Comment": comment,
        })

    # --- Write CSV ---
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    print("-" * 140)
    print(f"\n✅ Diagnostics complete. Results saved to: {csv_path}")
    print(f"   Env Lot Range: {fx_min:.2f} - {fx_max:.2f}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
