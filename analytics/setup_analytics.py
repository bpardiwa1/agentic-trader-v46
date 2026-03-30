# analytics/setup_analytics.py

from pathlib import Path
from analytics.log_parser import init_db
from analytics.mt5_history import init_history_db

def main():
    db = Path("analytics/db/analytics.db")
    init_db(db)          # creates agent_runs + loop_events
    init_history_db(db)  # creates live_positions + trade_history
    print(f"Initialized {db}")

if __name__ == "__main__":
    main()
