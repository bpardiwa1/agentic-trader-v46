# analytics/run_analytics_pipeline.py

from pathlib import Path
from analytics.log_parser import init_db, parse_log_file
from analytics.mt5_history import init_history_db

def main():
    root = Path(__file__).resolve().parents[1]
    db = root / "analytics" / "analytics.db"
    logs_root = root / "logs"

    # 0. Init DB
    print(f"[INIT] Creating schema in {db}")
    init_db(db)
    init_history_db(db)

    # 1. Import all logs
    log_dirs = [
        logs_root / "fx_v4.6",
        logs_root / "idx_v4.6",
        logs_root / "xau_v4.6",
        logs_root / "core_v4.6",
    ]

    for d in log_dirs:
        print(f"[SCAN] {d}")
        for f in sorted(d.glob("*.log")):
            print(f"[LOAD] {f}")
            parse_log_file(f, db)

    print("[DONE] Analytics pipeline prepped.")
    print("Now launch dashboard: streamlit run analytics/dashboard.py")

if __name__ == "__main__":
    main()
