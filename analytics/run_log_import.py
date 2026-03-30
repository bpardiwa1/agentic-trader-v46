# analytics/run_log_import.py

from pathlib import Path
from analytics.log_parser import parse_log_file, init_db

def main():
    root = Path(__file__).resolve().parents[1]
    db = root / "analytics" / "analytics.db"
    logs_root = root / "logs"

    print(f"[INIT] Using DB: {db}")
    init_db(db)

    # Directories to scan
    log_dirs = [
        logs_root / "fx_v4.6",
        logs_root / "idx_v4.6",
        logs_root / "xau_v4.6",
        logs_root / "core_v4.6",
    ]

    for log_dir in log_dirs:
        if not log_dir.exists():
            print(f"[SKIP] Missing: {log_dir}")
            continue

        print(f"[SCAN] {log_dir}")

        # --- SCCR FIX START ---
        # Capture:
        #  ✔ *.log
        #  ✔ *.log.YYYY-MM-DD
        log_files = list(log_dir.glob("*.log")) + list(log_dir.glob("*.log.*"))
        # Sort by filename for reproducibility
        log_files = sorted(set(log_files))
        # --- SCCR FIX END ---

        for log_file in log_files:
            try:
                parse_log_file(log_file, db)
                print(f"[IMPORT] Parsed: {log_file}")
            except Exception as e:
                print(f"[ERROR] Failed to parse {log_file}: {e}")

    print("[DONE] Log import complete. Ready for dashboard.")

if __name__ == "__main__":
    main()
