"""
analytics/dashboard.py
Streamlit dashboard for Agentic Trader v4.6 analytics.

Run:
    streamlit run analytics/dashboard.py
"""

from pathlib import Path
import streamlit as st
# from analytics.dashboard_trades import render_trades_tab


from analytics.metrics import (
    get_trades_per_day,
    get_skip_reasons,
    get_policy_stats,
    get_trust_conf_heatmap,
)

# ---------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------

def sidebar_controls():
    st.sidebar.title("Agentic Trader v4.6")

    db_path = st.sidebar.text_input(
        "SQLite DB File",
        value="analytics/analytics.db"
    )

    agent = st.sidebar.selectbox(
        "Agent",
        ["ALL", "FX_V46", "XAU_V46", "IDX_V46"],
        index=1
    )
    agent = None if agent == "ALL" else agent

    symbol = st.sidebar.text_input(
        "Symbol (optional)",
        value=""
    )
    symbol = symbol if symbol.strip() else None

    return Path(db_path), agent, symbol

# ---------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------

def render_overview(db_path, agent):
    import pandas as pd
    import sqlite3

    conn = sqlite3.connect(db_path)

    # Trades per day
    query = """
        SELECT 
            DATE(time_close) AS date,
            COUNT(*) AS trades
        FROM trade_history
        WHERE time_close IS NOT NULL
        GROUP BY DATE(time_close)
        ORDER BY date DESC;
    """

    df = pd.read_sql_query(query, conn)

    conn.close()

    if df.empty:
        st.info("No executed trades found.")
        return

    st.subheader("📈 Trades per Day")
    st.bar_chart(df.set_index("date")["trades"])



def render_skips(db_path, agent, symbol):
    st.header("🚫 Skip Reasons")

    df = get_skip_reasons(db_path, agent=agent, symbol=symbol)
    if df.empty:
        st.info("No skip events found.")
        return

    st.subheader("Recent SKIP Events")
    st.dataframe(df)

    st.subheader("Aggregated Policy Stats")
    dfp = get_policy_stats(db_path, agent=agent)

    if dfp.empty:
        st.info("No policy diagnostics available.")
        return

    st.dataframe(dfp)

    dfp_chart = dfp.set_index("symbol")[["skipped_conf_gate", "skipped_atr_floor"]]
    st.bar_chart(dfp_chart)


def render_heatmap(db_path, agent, symbol):
    st.header("🔥 Trust × Confidence Heatmap")

    heatmap = get_trust_conf_heatmap(db_path, agent=agent, symbol=symbol)
    if heatmap.empty:
        st.info("No executed trades available for heatmap.")
        return

    st.dataframe(heatmap)

def render_trades_tab(db_path, agent, symbol):
    from analytics.metrics_trades import (
        get_enriched_trades,
        compute_r_multiples,
        get_equity_curve,
        winrate_by_confidence,
    )

    st.header("💰 Realized P&L & Trade Analytics")

    df = get_enriched_trades(db_path, agent=agent, symbol=symbol)

    if df.empty:
        st.info("No closed trades found in MT5 history.")
        return

    df = compute_r_multiples(df)

    st.subheader("Executed Trades")
    st.dataframe(df)

    eq = get_equity_curve(df)
    st.subheader("📈 Equity Curve")
    st.line_chart(eq.set_index("time_close")["cum_pnl"])

    st.subheader("📊 R-Multiple Distribution")
    st.bar_chart(df["R"])

    # Win rate by confidence bucket
    st.subheader("🎯 Win Rate by Entry Confidence")
    wr = winrate_by_confidence(df)

    if wr is None or wr.empty or "conf_bucket" not in wr.columns:
        st.info("Not enough trades with confidence data yet to compute win rate.")
    else:
        st.dataframe(wr)
        st.bar_chart(wr.set_index("conf_bucket")["win_rate"])


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    st.set_page_config(page_title="Agentic Trader Analytics", layout="wide")

    db_path, agent, symbol = sidebar_controls()

    if not db_path.exists():
        st.error(f"DB not found: {db_path}")
        return

    tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Skips & Policy", "Trust x Conf Heatmap", "Trades & P&L"])

    with tab1:
        render_overview(db_path, agent)

    with tab2:
        render_skips(db_path, agent, symbol)

    with tab3:
        render_heatmap(db_path, agent, symbol)

    with tab4:
        render_trades_tab(db_path, agent, symbol)


if __name__ == "__main__":
    main()
