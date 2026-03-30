"""
Trades & P&L Dashboard Tab
"""

import streamlit as st
from analytics.metrics_trades import (
    get_enriched_trades,
    compute_r_multiples,
    get_equity_curve,
    winrate_by_confidence,
)


def render_trades_tab(db_path, agent, symbol):
    st.header("💰 Realized P&L & Trade Analytics")

    # Load enriched trades with CONF + TRUST
    df = get_enriched_trades(db_path, agent=agent, symbol=symbol)

    if df.empty:
        st.info("No closed trades found in MT5 history.")
        return

    # Compute R-multiples
    df = compute_r_multiples(df)

    st.subheader("Executed Trades (with CONF & TRUST at entry)")
    st.dataframe(df)

    # Equity curve
    st.subheader("📈 Equity Curve")
    eq = get_equity_curve(df)
    st.line_chart(eq.set_index("time_close")["cum_pnl"])

    # R-multiple distribution
    st.subheader("📊 R-Multiple Distribution")
    st.bar_chart(df["R"])

    # Win rate by confidence bucket
    st.subheader("🎯 Win Rate by Entry Confidence")
    wr = winrate_by_confidence(df)
    st.dataframe(wr)
    st.bar_chart(wr.set_index("conf_bucket"))
