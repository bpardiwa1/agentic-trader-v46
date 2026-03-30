# ============================================================
# Agentic Trader — PnL & Behaviour Dashboard (Streamlit)
# ============================================================

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Dict, List

import pandas as pd
import streamlit as st

# --------------------------
# Paths (edit if needed)
# --------------------------
REPORT_DIR = str(Path("report").resolve())
LOG_ANALYSIS_DIR = str((Path(REPORT_DIR) / "log_analysis").resolve())



# --------------------------
# Validation / guardrails
# --------------------------
def validate_pnl_integrity(
    *,
    df_raw: pd.DataFrame,
    df_filtered: pd.DataFrame,
    df_aggregated: pd.DataFrame,
    group: str,
    lookback_days: int = 14,
) -> None:
    """
    Validate that PnL aggregation did not silently drop data.
    Works for single- and multi-symbol groups (FX/IDX/XAU).
    Emits Streamlit errors (non-fatal) to avoid silent 'all zeros' tables.
    """
    if df_raw is None or df_raw.empty:
        return

    if "date" not in df_raw.columns or "group" not in df_raw.columns:
        return

    # Ensure datetime
    df_raw2 = df_raw.copy()
    df_raw2 = _force_datetime_col(df_raw2, "date").dropna(subset=["date"])
    if df_raw2.empty:
        return

    max_date = df_raw2["date"].max().date()
    min_date = (max_date - dt.timedelta(days=int(lookback_days)))

    recent = df_raw2[
        (df_raw2["group"].astype(str).str.upper() == str(group).upper())
        & (df_raw2["date"].dt.date >= min_date)
    ].copy()

    if recent.empty or "trades" not in recent.columns:
        return

    raw_trades = float(recent["trades"].fillna(0).sum())
    if raw_trades <= 0:
        return  # no trades in lookback window => nothing to validate

    if df_filtered is None or df_filtered.empty:
        st.error(
            f"PnL validation failed for {group}: {int(raw_trades)} trades exist in the last "
            f"{lookback_days} days in raw data, but the filtered dataset is empty."
        )
        return

    if df_aggregated is None or df_aggregated.empty:
        st.error(
            f"PnL validation failed for {group}: {int(raw_trades)} trades exist in the last "
            f"{lookback_days} days, but the aggregated output is empty (dashboard aggregation bug)."
        )
        return

    agg_trades = float(df_aggregated["trades"].fillna(0).sum()) if "trades" in df_aggregated.columns else 0.0
    agg_net = float(df_aggregated["net"].fillna(0).sum()) if "net" in df_aggregated.columns else 0.0

    if agg_trades <= 0 and abs(agg_net) < 1e-9:
        st.error(
            f"PnL validation failed for {group}: {int(raw_trades)} trades exist in the last "
            f"{lookback_days} days, but aggregated output shows ZERO trades and ZERO net. "
            f"This usually indicates a join/aggregation mismatch (e.g., symbol vs group)."
        )


def compute_group_daily_from_symbol(pnl_sym: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate pnl_daily_by_symbol rows into group-level daily PnL.
    This is used as a safe fallback when pnl_daily.csv for a group is missing/broken.
    """
    if pnl_sym is None or pnl_sym.empty:
        return pd.DataFrame()

    need_cols = [c for c in ["date", "group", "trades", "gross", "commission", "swap", "net"] if c in pnl_sym.columns]
    if "date" not in need_cols or "group" not in need_cols:
        return pd.DataFrame()

    df = pnl_sym[need_cols].copy()
    df = _force_datetime_col(df, "date").dropna(subset=["date"])

    agg_map = {c: "sum" for c in ["trades", "gross", "commission", "swap", "net"] if c in df.columns}
    out = df.groupby(["date", "group"], dropna=False, as_index=False).agg(agg_map)
    return out


def _normalize_tag(tag: str) -> str:
    tag = (tag or "").strip()
    if tag.startswith("by_symbol_"):
        return tag[len("by_symbol_") :]
    if tag.startswith("by_asset_"):
        return tag[len("by_asset_") :]
    return tag


def _available_tags(log_dir) -> list[str]:
    """Discover tags from existing log_analysis CSVs.
    Looks for files like log_daily_<tag>.csv and returns sorted unique <tag>.
    """
    tags: set[str] = set()
    try:
        for p in Path(log_dir).glob("log_daily_*.csv"):
            name = p.name
            if name.startswith("log_daily_") and name.endswith(".csv"):
                t = name[len("log_daily_") : -len(".csv")]
                if t:
                    tags.add(t)
    except Exception:
        pass
    # Always include common defaults (helps first-run UX)
    tags.update({"idx_v46", "fx_v46", "xau_v46"})
    return sorted(tags)


# --------------------------
# CSV helpers
# --------------------------
@st.cache_data(show_spinner=False)
def _safe_read_csv(path: str) -> pd.DataFrame:
    try:
        if not path:
            return pd.DataFrame()
        p = Path(path)
        if not p.exists():
            return pd.DataFrame()
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _force_datetime_col(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return df
    out = df.copy()
    out[col] = pd.to_datetime(out[col], errors="coerce")
    return out


def _list_daily_pnl_files(report_dir: str) -> List[str]:
    base = Path(report_dir)
    if not base.exists():
        return []
    return sorted([p.name for p in base.glob("*_agentic_pnl.csv")])


def _log_analysis_exists(log_dir: str) -> bool:
    return Path(log_dir).exists()


# --------------------------
# Log analyzer CSV loaders
# --------------------------
def load_log_daily(tag: str) -> pd.DataFrame:
    return _safe_read_csv(str(Path(LOG_ANALYSIS_DIR) / f"log_daily_{tag}.csv"))


def load_log_daily_by_symbol(tag: str) -> pd.DataFrame:
    return _safe_read_csv(str(Path(LOG_ANALYSIS_DIR) / f"log_daily_by_symbol_{tag}.csv"))


def load_log_daily_reasons(tag: str) -> pd.DataFrame:
    return _safe_read_csv(str(Path(LOG_ANALYSIS_DIR) / f"log_daily_reasons_{tag}.csv"))


def load_log_daily_reasons_by_symbol(tag: str) -> pd.DataFrame:
    return _safe_read_csv(str(Path(LOG_ANALYSIS_DIR) / f"log_daily_reasons_by_symbol_{tag}.csv"))


def load_log_summary(tag: str) -> pd.DataFrame:
    return _safe_read_csv(str(Path(LOG_ANALYSIS_DIR) / f"log_summary_{tag}.csv"))


# --------------------------
# Attribution CSV helpers (tag-scoped with fallback)
# --------------------------
ATTR_FILES = [
    # Preferred enriched (Step 3)
    "analysis_loss_by_reason_by_symbol_enriched.csv",

    # Legacy / fallback
    "analysis_loss_by_reason.csv",
    "analysis_pnl_by_policy.csv",
    "analysis_pnl_by_regime.csv",

    # Optional daily-sliced versions
    "analysis_loss_by_reason_daily.csv",
]



def _find_attr_file(filename: str, tag: str | None = None) -> Path | None:
    def _tagged(name: str) -> list[str]:
        if not tag:
            return [name]
        if not name.lower().endswith(".csv"):
            return [name]
        stem = name[:-4]
        if stem.endswith(f"_{tag}"):
            return [name]
        return [f"{stem}_{tag}.csv", name]

    candidates = [
        Path(LOG_ANALYSIS_DIR) / filename,
        Path(REPORT_DIR) / "log_analysis" / filename,
        Path(REPORT_DIR) / filename,
        Path(filename),
    ]

    for p in candidates:
        for name in _tagged(p.name):
            pp = p.with_name(name)
            if pp.exists():
                return pp
    return None


def get_attr_locations(tag: str | None = None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for fname in ATTR_FILES:
        p = _find_attr_file(fname, tag=tag)
        out[fname] = str(p) if p else ""
    return out


@st.cache_data(show_spinner=False)
def load_attr_csv(filename: str, tag: str | None = None) -> pd.DataFrame:
    p = _find_attr_file(filename, tag=tag)
    if not p:
        return pd.DataFrame()
    return _safe_read_csv(str(p))

# --------------------------
# Guardrail suggestions (read-only)
# --------------------------
def _to_num(s, default=0.0):
    try:
        return float(pd.to_numeric(s, errors="coerce"))
    except Exception:
        return default


def _standardize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Accept either (trades, net_pnl) or weighted (trades_w, net_pnl_w) outputs."""
    if df.empty:
        return df
    out = df.copy()
    if "trades" not in out.columns and "trades_w" in out.columns:
        out["trades"] = pd.to_numeric(out["trades_w"], errors="coerce").fillna(0)
    if "net_pnl" not in out.columns and "net_pnl_w" in out.columns:
        out["net_pnl"] = pd.to_numeric(out["net_pnl_w"], errors="coerce").fillna(0)
    # coerce if present
    if "trades" in out.columns:
        out["trades"] = pd.to_numeric(out["trades"], errors="coerce").fillna(0)
    if "net_pnl" in out.columns:
        out["net_pnl"] = pd.to_numeric(out["net_pnl"], errors="coerce").fillna(0)
    return out


def _pick_symbol_scope(df: pd.DataFrame, asset_choice: str) -> pd.DataFrame:
    if df.empty:
        return df
    if asset_choice != "ALL" and "symbol" in df.columns:
        return df[df["symbol"].astype(str) == str(asset_choice)].copy()
    return df


def _date_filter(df: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    if df.empty:
        return df
    if "date" not in df.columns:
        return df
    out = df.copy()
    out = _force_datetime_col(out, "date").dropna(subset=["date"]).copy()
    out = out[(out["date"].dt.date >= start_date) & (out["date"].dt.date <= end_date)].copy()
    return out


def _score_guardrail(severity_abs_loss: float, trades: float, share_of_losses: float) -> float:
    """
    Higher score = higher priority.
    severity_abs_loss: absolute loss (positive number)
    trades: trade count (or weighted count)
    share_of_losses: fraction 0..1
    """
    # simple, robust heuristic: loss dominates; share and trades add confidence
    return (severity_abs_loss * 1.0) + (share_of_losses * 100.0) + (min(trades, 200.0) * 0.2)


def generate_guardrail_suggestions(
    loss_by_reason_df: pd.DataFrame,
    pnl_by_regime_df: pd.DataFrame,
    reasons_sym_df: pd.DataFrame,
    start_date,
    end_date,
    asset_choice: str,
    loss_only: bool = True,
    top_k: int = 8,
) -> pd.DataFrame:
    """
    Ranked guardrail suggestions using:
      - analysis_loss_by_reason(_daily)
      - analysis_pnl_by_regime (optional)
      - log_daily_reasons_by_symbol (context)
    """

    # --- prep ---
    lbr = _standardize_cols(loss_by_reason_df)
    reg = _standardize_cols(pnl_by_regime_df)
    rs = reasons_sym_df.copy() if isinstance(reasons_sym_df, pd.DataFrame) else pd.DataFrame()

    # filter to selected range + asset
    lbr = _pick_symbol_scope(_date_filter(lbr, start_date, end_date), asset_choice)
    reg = _pick_symbol_scope(_date_filter(reg, start_date, end_date), asset_choice)
    rs = _pick_symbol_scope(_date_filter(rs, start_date, end_date), asset_choice)

    if lbr.empty or "reason" not in lbr.columns or "net_pnl" not in lbr.columns:
        return pd.DataFrame(columns=["priority", "symbol", "guardrail", "trigger", "evidence", "suggested_action"])

    df = lbr.copy()
    if loss_only:
        df = df[df["net_pnl"] < 0].copy()

    if df.empty:
        return pd.DataFrame(columns=["priority", "symbol", "guardrail", "trigger", "evidence", "suggested_action"])

    # total losses per symbol (negative numbers)
    if "symbol" in df.columns:
        total_loss_by_sym = df.groupby("symbol")["net_pnl"].sum()
    else:
        total_loss_by_sym = pd.Series({"ALL": df["net_pnl"].sum()})

    # select columns
    cols_needed = ["reason", "net_pnl", "trades"]
    if "symbol" in df.columns:
        cols_needed = ["symbol"] + cols_needed

    df2 = df[cols_needed].copy()
    df2["abs_loss"] = (-df2["net_pnl"]).clip(lower=0)
    df2["reason"] = df2["reason"].astype(str).fillna("(no_reason)")

    suggestions: list[dict] = []

    def _skip_count_for(reason: str) -> float:
        if rs.empty or "reason" not in rs.columns:
            return 0.0
        rr = rs[rs["reason"].astype(str) == reason].copy()
        if rr.empty:
            return 0.0
        if "count" in rr.columns:
            return float(pd.to_numeric(rr["count"], errors="coerce").fillna(0).sum())
        return 0.0

    def _regime_loss(regime_name: str) -> float:
        if reg.empty or "dominant_regime" not in reg.columns or "net_pnl" not in reg.columns:
            return 0.0
        rrr = reg[reg["dominant_regime"].astype(str).str.upper() == regime_name.upper()].copy()
        if rrr.empty:
            return 0.0
        return float(pd.to_numeric(rrr["net_pnl"], errors="coerce").fillna(0).sum())

    # Build per-row suggestions
    for _, row in df2.sort_values("abs_loss", ascending=False).head(200).iterrows():
        symbol = row["symbol"] if "symbol" in df2.columns else "ALL"
        reason = str(row["reason"])
        abs_loss = float(row["abs_loss"])
        trades = float(row.get("trades", 0.0))

        # total loss for this symbol as POSITIVE
        sym_net = float(total_loss_by_sym.get(symbol, df2["net_pnl"].sum()))
        total_loss_pos = max(0.0, -sym_net)

        share = (abs_loss / total_loss_pos) if total_loss_pos > 0 else 0.0

        guardrail: str
        trigger: str
        action: str

        if reason in ("atr_quiet", "regime_quiet", "quiet", "regime=QUIET"):
            guardrail = "QUIET regime bleed-stop"
            trigger = "dominant_regime==QUIET AND rolling net_pnl<0"
            action = "Block new entries in QUIET for this symbol (or raise MIN_CONF + tighten trade window)."
        elif reason.lower() == "h1_conflict":
            guardrail = "H1 conflict hard-block"
            trigger = "H1_conflict present"
            action = "SKIP trade when H1_conflict appears (or require much higher confidence)."
        elif reason in ("bear_neutral", "bull_neutral", "mixed_or_neutral"):
            guardrail = "Neutral-state clamp"
            trigger = f"reason=={reason}"
            action = "Raise MIN_CONF / require stronger EMA gap or RSI confirmation in neutral state."
        elif reason.startswith("conf<"):
            guardrail = "Low-confidence clamp"
            trigger = reason
            action = "Increase MIN_CONF for this symbol or reduce size when conf below threshold."
        elif reason.startswith("swing_lock_disabled"):
            guardrail = "Swing-lock enforcement"
            trigger = "swing_lock_disabled AND ATR_LVL==normal"
            action = "Re-enable swing lock when ATR is NORMAL/HOT; allow disabled only in QUIET."
        elif reason in ("(no_reason)", "no_reason"):
            guardrail = "Decision-context kill-switch"
            trigger = "Executed trade without mapped decision reason"
            action = "Treat as critical: require PREVIEW/DECIDE mapping before allowing further trades."
        else:
            guardrail = f"Reason clamp: {reason}"
            trigger = f"reason=={reason}"
            action = "Consider adding a stricter threshold or secondary confirmation for this reason."

        skip_ct = _skip_count_for(reason)
        evidence = (
            f"abs_loss={abs_loss:.2f}, trades={trades:.2f}, "
            f"share_of_symbol_losses={share*100:.1f}%, skip_ct_in_range={skip_ct:.0f}"
        )

        priority = _score_guardrail(abs_loss, trades, share)

        suggestions.append(
            {
                "priority": priority,
                "symbol": symbol,
                "guardrail": guardrail,
                "trigger": trigger,
                "evidence": evidence,
                "suggested_action": action,
            }
        )

    # Regime-level suggestion if QUIET bleeding overall
    quiet_loss = _regime_loss("QUIET")
    if quiet_loss < 0:
        priority = _score_guardrail(abs(quiet_loss), 100.0, 0.5)
        suggestions.append(
            {
                "priority": priority,
                "symbol": asset_choice if asset_choice != "ALL" else "ALL",
                "guardrail": "Global QUIET regime bleed-stop",
                "trigger": "dominant_regime==QUIET AND day_net_pnl<0 (N days)",
                "evidence": f"QUIET net_pnl={quiet_loss:.2f} in range",
                "suggested_action": "Reduce/disable trading in QUIET or raise MIN_CONF + tighten session window.",
            }
        )

    out = pd.DataFrame(suggestions)
    if out.empty:
        return out

    out = (
        out.sort_values("priority", ascending=False)
        .drop_duplicates(subset=["symbol", "guardrail"])
        .head(top_k)
        .reset_index(drop=True)
    )
    return out



# ==========================
# 🔧 NORMALIZE ATTRIBUTION SCHEMA
# ==========================

def _coerce_pnl_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Accept both legacy and weighted attribution schemas.
    Normalizes to:
      - net_pnl
      - trades
    """
    if df.empty:
        return df

    out = df.copy()

    if "net_pnl" not in out.columns and "net_pnl_w" in out.columns:
        out["net_pnl"] = pd.to_numeric(out["net_pnl_w"], errors="coerce").fillna(0.0)

    if "trades" not in out.columns and "trades_w" in out.columns:
        out["trades"] = pd.to_numeric(out["trades_w"], errors="coerce").fillna(0.0)

    return out

# --------------------------
# Page
# --------------------------
st.set_page_config(page_title="Agentic Trader – PnL & Behaviour", layout="wide")
st.title("Agentic Trader – PnL & Behaviour Dashboard")

# --------------------------
# Sidebar: paths + filters
# --------------------------
st.sidebar.header("Paths")
st.sidebar.write(f"REPORT_DIR:\n{REPORT_DIR}")
st.sidebar.write(f"LOG_ANALYSIS_DIR:\n{LOG_ANALYSIS_DIR}")
st.sidebar.write(f"Exists: {_log_analysis_exists(LOG_ANALYSIS_DIR)}")

daily_files = _list_daily_pnl_files(REPORT_DIR)
with st.sidebar.expander("Daily files (sample)", expanded=False):
    st.write(daily_files[:10] + (["..."] if len(daily_files) > 10 else []))

st.sidebar.header("Filters")
tags = _available_tags(LOG_ANALYSIS_DIR)
default_idx = tags.index("idx_v46") if "idx_v46" in tags else 0
tag = st.sidebar.selectbox("Agent / Tag", options=tags, index=default_idx)
base_tag = _normalize_tag(tag)

group = st.sidebar.selectbox("Group", options=["IDX", "FX", "XAU"], index=0)

# PnL date range
st.sidebar.header("Range")
range_preset = st.sidebar.selectbox(
    "Range preset",
    options=["Manual", "1D", "7D", "14D", "30D", "90D", "YTD"],
    index=3,  # default = 30D
)


# defaults
today = dt.date.today()
default_start = today - dt.timedelta(days=30)
if range_preset == "1D":
    start_date = today - dt.timedelta(days=1)
    end_date = today
elif range_preset == "7D":
    start_date = today - dt.timedelta(days=7)
    end_date = today
elif range_preset == "14D":
    start_date = today - dt.timedelta(days=14)
    end_date = today
elif range_preset == "30D":
    start_date = today - dt.timedelta(days=30)
    end_date = today
elif range_preset == "90D":
    start_date = today - dt.timedelta(days=90)
    end_date = today
elif range_preset == "YTD":
    start_date = dt.date(today.year, 1, 1)
    end_date = today
else:
    start_date = st.sidebar.date_input("Start date", value=default_start)
    end_date = st.sidebar.date_input("End date", value=today)


align_behaviour = st.sidebar.checkbox("Align behaviour to selected PnL date range", value=True)

# --------------------------
# Load PnL CSVs (stable files)
# --------------------------
pnl_group_path = str(Path(LOG_ANALYSIS_DIR) / "pnl_daily.csv")
pnl_sym_path = str(Path(LOG_ANALYSIS_DIR) / "pnl_daily_by_symbol.csv")

# Keep raw copies for validation / fallback aggregation
pnl_group_all = _safe_read_csv(pnl_group_path)
pnl_sym_all = _safe_read_csv(pnl_sym_path)

pnl_group_all = _force_datetime_col(pnl_group_all, "date").dropna(subset=["date"]).copy()
pnl_sym_all = _force_datetime_col(pnl_sym_all, "date").dropna(subset=["date"]).copy()

# Filter to selected group (FX/IDX/XAU)
pnl_group = pnl_group_all.copy()
pnl_sym_df = pnl_sym_all.copy()

if not pnl_group.empty and "group" in pnl_group.columns:
    pnl_group = pnl_group[pnl_group["group"].astype(str).str.upper() == group].copy()
if not pnl_sym_df.empty and "group" in pnl_sym_df.columns:
    pnl_sym_df = pnl_sym_df[pnl_sym_df["group"].astype(str).str.upper() == group].copy()

# If group-level file is present but looks broken (IDX often shows zeros while by_symbol has data),
# compute group-level daily totals from the by-symbol file.
if (not pnl_sym_df.empty) and (not pnl_group.empty) and ("trades" in pnl_group.columns):
    group_trades_sum = float(pnl_group["trades"].fillna(0).sum())
    sym_trades_sum = float(pnl_sym_df["trades"].fillna(0).sum()) if "trades" in pnl_sym_df.columns else 0.0
    if group_trades_sum <= 0 and sym_trades_sum > 0:
        st.warning(
            f"PnL notice for {group}: pnl_daily.csv shows 0 trades, but pnl_daily_by_symbol.csv has trades. "
            "Using by-symbol aggregation as fallback."
        )
        pnl_group = compute_group_daily_from_symbol(pnl_sym_df)

# Asset selector from pnl_daily_by_symbol when available
asset_options = ["ALL"]
if not pnl_sym_df.empty and "symbol" in pnl_sym_df.columns:
    asset_options += sorted({str(x) for x in pnl_sym_df["symbol"].dropna().unique().tolist()})
asset_choice = st.sidebar.selectbox("Asset (drilldown)", options=asset_options, index=0)

# --------------------------
# Load behaviour/log CSVs (tag-scoped)
# --------------------------
log_df = load_log_daily(base_tag)
reasons_df = load_log_daily_reasons(base_tag)
log_sym_df = load_log_daily_by_symbol(base_tag)
reasons_sym_df = load_log_daily_reasons_by_symbol(base_tag)
summary_df = load_log_summary(base_tag)

# Ensure datetime on log dfs
log_df = _force_datetime_col(log_df, "date").dropna(subset=["date"]).copy() if not log_df.empty else log_df
log_sym_df = _force_datetime_col(log_sym_df, "date").dropna(subset=["date"]).copy() if not log_sym_df.empty else log_sym_df
reasons_df = _force_datetime_col(reasons_df, "date").dropna(subset=["date"]).copy() if not reasons_df.empty else reasons_df
reasons_sym_df = _force_datetime_col(reasons_sym_df, "date").dropna(subset=["date"]).copy() if not reasons_sym_df.empty else reasons_sym_df

# Behaviour range alignment
beh_start = start_date
beh_end = end_date
if not align_behaviour:
    beh_start = start_date
    beh_end = end_date

# --------------------------
# PnL selection (group vs asset)
# --------------------------
pnl_sel = pnl_group[(pnl_group["date"].dt.date >= start_date) & (pnl_group["date"].dt.date <= end_date)].copy()

# Validate PnL integrity (group-level) to catch silent drop/zero aggregation
validate_pnl_integrity(
    df_raw=pnl_group_all,
    df_filtered=pnl_group,
    df_aggregated=pnl_sel,
    group=group,
    lookback_days=14,
)


pnl_asset_sel = pd.DataFrame()
pnl_sym_range = pd.DataFrame()
if not pnl_sym_df.empty:
    tmp = pnl_sym_df.copy()
    tmp = tmp[(tmp["date"].dt.date >= start_date) & (tmp["date"].dt.date <= end_date)].copy()
    pnl_sym_range = tmp
    if asset_choice != "ALL" and "symbol" in tmp.columns:
        pnl_asset_sel = tmp[tmp["symbol"].astype(str) == str(asset_choice)].copy()

if asset_choice != "ALL":
    if not pnl_asset_sel.empty:
        pnl_view = pnl_asset_sel
    elif "symbol" in pnl_sel.columns:
        pnl_view = pnl_sel[pnl_sel["symbol"].astype(str) == str(asset_choice)].copy()
        if pnl_view.empty:
            st.info("No per-asset PnL rows found in pnl_daily.csv for this asset. Showing group PnL.")
            pnl_view = pnl_sel
    else:
        st.info(
            "PnL per-asset is not available for this selection (missing pnl_daily_by_symbol.csv and pnl_daily.csv has no symbol column). Showing group PnL."
        )
        pnl_view = pnl_sel
else:
    pnl_view = pnl_sel

if pnl_view.empty:
    st.warning("No PnL data in selected range.")
    st.stop()

# --------------------------
# PnL daily breakdown
# --------------------------
st.subheader("PnL (daily breakdown)")
st.dataframe(pnl_view.reset_index(drop=True), use_container_width=True)

# Totals by asset (range)
if not pnl_sym_range.empty:
    st.subheader("PnL totals by asset (range)")
    cols = [c for c in ["symbol", "trades", "gross", "commission", "swap", "net"] if c in pnl_sym_range.columns]
    by_asset = pnl_sym_range.groupby("symbol", dropna=False)[cols[1:]].sum().reset_index() if "symbol" in cols else pnl_sym_range
    st.dataframe(by_asset.reset_index(drop=True), use_container_width=True)

# --------------------------
# Behaviour daily breakdown
# --------------------------
st.subheader("Behaviour (daily breakdown)")
if log_df.empty:
    st.info(f"Missing log_daily_{base_tag}.csv")
else:
    b = log_df[(log_df["date"].dt.date >= beh_start) & (log_df["date"].dt.date <= beh_end)].copy()
    st.dataframe(b.reset_index(drop=True), use_container_width=True)

# Behaviour by asset (daily)
st.subheader("Behaviour by asset (daily)")
if log_sym_df.empty:
    st.info(f"Missing log_daily_by_symbol_{base_tag}.csv")
else:
    b = log_sym_df[(log_sym_df["date"].dt.date >= beh_start) & (log_sym_df["date"].dt.date <= beh_end)].copy()
    if asset_choice != "ALL" and "symbol" in b.columns:
        b = b[b["symbol"].astype(str) == str(asset_choice)].copy()
    st.dataframe(b.reset_index(drop=True), use_container_width=True)

# --------------------------
# Why did we trade today?
# --------------------------
st.subheader("Why did we trade today?")

# pick a day from pnl_view
day_options = sorted({d.date() for d in pnl_view["date"]})
pick_day = st.selectbox("Pick a day", options=day_options, index=len(day_options) - 1)

# day pnl row
day_df = pnl_view[pnl_view["date"].dt.date == pick_day].copy()
day_net = float(pd.to_numeric(day_df["net"], errors="coerce").fillna(0).sum()) if "net" in day_df.columns else 0.0
day_trades = int(pd.to_numeric(day_df["trades"], errors="coerce").fillna(0).sum()) if "trades" in day_df.columns else 0

# exec/skip from behaviour-by-symbol if available
exec_count = 0
skip_count = 0
if not log_sym_df.empty:
    dd = log_sym_df[log_sym_df["date"].dt.date == pick_day].copy()
    if asset_choice != "ALL" and "symbol" in dd.columns:
        dd = dd[dd["symbol"].astype(str) == str(asset_choice)].copy()
    if "exec_count" in dd.columns:
        exec_count = int(pd.to_numeric(dd["exec_count"], errors="coerce").fillna(0).sum())
    if "skip_count" in dd.columns:
        skip_count = int(pd.to_numeric(dd["skip_count"], errors="coerce").fillna(0).sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Day Net PnL", f"{day_net:.2f}")
c2.metric("Day trades", f"{day_trades}")
c3.metric("Exec/Skip", f"{exec_count}/{skip_count}")
ratio = (100.0 * exec_count / (exec_count + skip_count)) if (exec_count + skip_count) > 0 else 0.0
c4.metric("Exec ratio", f"{ratio:.2f}%")

# Top SKIP reasons (from reasons_by_symbol)
if reasons_sym_df.empty:
    st.info(f"Missing log_daily_reasons_by_symbol_{base_tag}.csv")
else:
    rr = reasons_sym_df[reasons_sym_df["date"].dt.date == pick_day].copy()
    if asset_choice != "ALL" and "symbol" in rr.columns:
        rr = rr[rr["symbol"].astype(str) == str(asset_choice)].copy()
    if rr.empty:
        st.info("No SKIP reasons captured for that day/asset.")
    else:
        st.caption(f"Top SKIP reasons (that day, {asset_choice}):")
        rr["count"] = pd.to_numeric(rr.get("count", 0), errors="coerce").fillna(0)
        rr = rr.sort_values("count", ascending=False)
        st.dataframe(rr.head(30).reset_index(drop=True), use_container_width=True)

# --------------------------
# Loss concentration & attribution (tag-scoped)
# --------------------------
st.subheader("Loss concentration & attribution")

locs = get_attr_locations(tag=base_tag)
with st.expander("Attribution CSV locations (debug)", expanded=False):
    st.code(json.dumps(locs, indent=2))

loss_only = st.checkbox("Loss-only (show negative net PnL only)", value=True)

# Prefer enriched attribution if present
loss_by_reason = load_attr_csv(
    "analysis_loss_by_reason_by_symbol_enriched.csv", tag=base_tag
)

# Fallback to legacy if enriched missing
if loss_by_reason.empty:
    loss_by_reason = load_attr_csv("analysis_loss_by_reason.csv", tag=base_tag)

loss_by_reason_daily = load_attr_csv("analysis_loss_by_reason_daily.csv", tag=base_tag)

pnl_by_policy = load_attr_csv("analysis_pnl_by_policy.csv", tag=base_tag)
pnl_by_regime = load_attr_csv("analysis_pnl_by_regime.csv", tag=base_tag)


# If the files exist but are out of date vs the current selection, we filter by range and asset here.
def _filter_range_asset(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "date" in out.columns:
        out = _force_datetime_col(out, "date").dropna(subset=["date"]).copy()
        out = out[(out["date"].dt.date >= start_date) & (out["date"].dt.date <= end_date)].copy()
    if asset_choice != "ALL" and "symbol" in out.columns:
        out = out[out["symbol"].astype(str) == str(asset_choice)].copy()
    return out


loss_by_reason = _filter_range_asset(loss_by_reason)
loss_by_reason_daily = _filter_range_asset(loss_by_reason_daily)
pnl_by_policy = _filter_range_asset(pnl_by_policy)
pnl_by_regime = _filter_range_asset(pnl_by_regime)
loss_by_reason = _coerce_pnl_cols(loss_by_reason)
loss_by_reason_daily = _coerce_pnl_cols(loss_by_reason_daily)
pnl_by_policy = _coerce_pnl_cols(pnl_by_policy)
pnl_by_regime = _coerce_pnl_cols(pnl_by_regime)


if loss_by_reason.empty and pnl_by_policy.empty and pnl_by_regime.empty:
    st.info(
        "No attribution CSVs found (or none in selected range).\n\n"
        "Make sure you ran:\n"
        f"python -m app.analysis.analysis_loss_attribution --tag {base_tag} --group {group} --outdir report/log_analysis\n\n"
        f"And that log_analyzer has already generated log_daily_reasons_by_symbol_{base_tag}.csv under report/log_analysis."
    )
else:
    if not loss_by_reason.empty:
        st.markdown("#### Loss concentration by reason")

        df = loss_by_reason.copy()
        if "net_pnl" in df.columns:
            df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce")
            if loss_only:
                df = df[df["net_pnl"] < 0].copy()
            df = df.sort_values("net_pnl", ascending=True)

        top_n = st.slider("Show top N reasons", 10, 200, 50, step=10)
        st.dataframe(df.head(top_n).reset_index(drop=True), use_container_width=True)

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("#### PnL by policy")
        if pnl_by_policy.empty:
            st.info("Missing analysis_pnl_by_policy_<tag>.csv (or empty in selected range)")
        else:
            df = pnl_by_policy.copy()
            if "net_pnl" in df.columns:
                df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce")
                if loss_only:
                    df = df[df["net_pnl"] < 0].copy()
                df = df.sort_values("net_pnl", ascending=True)
            st.dataframe(df.reset_index(drop=True), use_container_width=True)

    with c2:
        st.markdown("#### PnL by regime")
        if pnl_by_regime.empty:
            st.info("Missing analysis_pnl_by_regime_<tag>.csv (or empty in selected range)")
        else:
            df = pnl_by_regime.copy()
            if "net_pnl" in df.columns:
                df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce")
                if loss_only:
                    df = df[df["net_pnl"] < 0].copy()
                df = df.sort_values("net_pnl", ascending=True)
            st.dataframe(df.reset_index(drop=True), use_container_width=True)
    # --------------------------
    # Auto guardrail suggestions (read-only)
    # --------------------------
    st.markdown("#### Auto guardrail suggestions (read-only)")

    # choose which loss_by_reason dataframe to feed (daily preferred if it exists)
    _lbr_for_ai = loss_by_reason_daily if not loss_by_reason_daily.empty else loss_by_reason

    suggestions_df = generate_guardrail_suggestions(
        loss_by_reason_df=_lbr_for_ai,
        pnl_by_regime_df=pnl_by_regime,
        reasons_sym_df=reasons_sym_df,
        start_date=start_date,
        end_date=end_date,
        asset_choice=asset_choice,
        loss_only=loss_only,
        top_k=8,
    )

    if suggestions_df.empty:
        st.info("No guardrail suggestions available for the current selection (missing or empty attribution data).")
    else:
        st.dataframe(suggestions_df, use_container_width=True)

