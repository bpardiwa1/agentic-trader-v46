
# analytics/run_mt5_import.py

from pathlib import Path
import os

from analytics.mt5_history import (
    init_history_db,
    mt5_connect,
    snapshot_live_positions,
    import_trade_history,
)


def main():
    # Project root (agentic-trader)
    root = Path(__file__).resolve().parents[1]

    # SQLite DB path
    db = root / "analytics" / "analytics.db"
    print(f"[INIT] Using DB: {db}")

    # Ensure history tables exist
    init_history_db(db)

    # ---- MT5 connection settings ----
    # You can:
    #  - set these as environment variables, OR
    #  - hard-code them here temporarily.
    login = int(os.getenv("MT5_LOGIN", "18023893"))
    password = os.getenv("MT5_PASSWORD", "VishyVahis786!")
    server = os.getenv("MT5_SERVER", "VTMarkets-Live 3")
    path = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")

    print(f"[MT5] Connecting to {server} as {login} ...")
    mt5_connect(login, password, server, path)
    print("[MT5] Connected.")

    # Snapshot current open positions
    n_pos = snapshot_live_positions(db)
    print(f"[MT5] Captured {n_pos} live positions.")

    # Import last 30 days of closed trades
    n_deals = import_trade_history(db, days_back=30)
    print(f"[MT5] Imported {n_deals} closed deals.")

    print("[DONE] MT5 history import complete.")


if __name__ == "__main__":
    main()
