# ============================================================
# Agentic Trader — Loss Attribution (per tag, group, asset)
# ------------------------------------------------------------
# Produces attribution CSVs under report/log_analysis so the
# Streamlit pnl_dashboard.py can render loss concentration.
#
# Design goal:
# - Works for IDX/FX/XAU
# - Does NOT depend on MT5 history or trade DB columns beyond the
#   already-generated pnl_daily_by_symbol.csv
# - Extracts "why" reasons/policy/regime from logs by matching
#   EXECUTOR events to the most recent DECIDE/PREVIEW for that symbol.
#
# Outputs (written to --outdir):
#   analysis_loss_by_reason_<tag>.csv   (date, symbol, reason, trades_w, net_pnl_w)
#   analysis_pnl_by_policy_<tag>.csv    (date, symbol, policy, trades, net_pnl)
#   analysis_pnl_by_regime_<tag>.csv    (date, symbol, dominant_regime, trades, net_pnl)
#
# Run examples:
#   python -m app.analysis.analysis_loss_attribution --tag idx_v46 --group IDX --outdir report/log_analysis
#   python -m app.analysis.analysis_loss_attribution --tag fx_v46  --group FX  --outdir report/log_analysis
#   python -m app.analysis.analysis_loss_attribution --tag xau_v46 --group XAU --outdir report/log_analysis
# ============================================================

from __future__ import annotations

import argparse
import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


# -----------------------------
# Log parsing (robust)
# -----------------------------

# Example lines:
# 2025-12-18 00:07:36 [INFO] idx_v46_2025-12-17 - [DECIDE] NAS100.s ... why=['ema_rsi_bear', ...] ...
# 2025-12-18 00:07:36 [INFO] idx_v46_2025-12-17 - [PREVIEW] NAS100.s conf=... policy=flexible regime=NORMAL ... reason=[...]
# 2025-12-18 00:07:36 [INFO] idx_v46_2025-12-17 - [EXECUTOR] NAS100.s SHORT ok (... ticket=238108981)

TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[")
KIND_RE = re.compile(r"\[(?P<kind>DECIDE|PREVIEW|EXECUTOR)\]\s+(?P<sym>[A-Za-z0-9_.\-]+)")
WHY_RE = re.compile(r"\bwhy=\[(?P<why>[^\]]*)\]")
REASON_RE = re.compile(r"\breason=\[(?P<why>[^\]]*)\]")
POLICY_RE = re.compile(r"\bpolicy=(?P<policy>[A-Za-z0-9_\-]+)")
REGIME_RE = re.compile(r"\bregime=(?P<regime>[A-Za-z0-9_\-]+)")
TICKET_RE = re.compile(r"\bticket=(?P<ticket>\d+)\b")


def _parse_list(txt: str) -> List[str]:
    # input like: "'a', 'b', 'c'" or "a, b"
    if not txt:
        return []
    parts: List[str] = []
    for p in txt.split(","):
        p = p.strip().strip("'").strip('"').strip()
        if p:
            parts.append(p)
    return parts


@dataclass
class ExecEvent:
    date: dt.date
    symbol: str
    ticket: Optional[str]
    reasons: List[str]
    policy: Optional[str]
    regime: Optional[str]


def iter_log_files(tag: str) -> Iterable[Path]:
    """
    Find log files likely containing the given tag.
    We match:
      - logs/**/{tag}*.log*
      - logs/**/*{tag}*.log*
    so it works with:
      idx_v46_2025-12-17.log.2025-12-18
      fx_v46_2025-12-12.log.2025-12-18
      etc.
    """
    roots = [Path("logs"), Path("report") / "logs", Path(".") / "logs"]
    seen: set[Path] = set()
    patterns = [
        f"**/{tag}*.log*",
        f"**/*{tag}*.log*",
    ]
    for r in roots:
        if not r.exists():
            continue
        for pat in patterns:
            for p in r.glob(pat):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    yield p


def parse_exec_events(tag: str) -> List[ExecEvent]:
    """
    Parse logs and return EXECUTOR events with attached reasons/policy/regime.
    Matching strategy:
      - Keep last DECIDE and PREVIEW per symbol (timestamped).
      - When EXECUTOR appears, attach the most recent DECIDE (prefer why=) and
        the most recent PREVIEW (policy/regime + reason=) within a small window.
    """
    last_decide: Dict[str, Tuple[dt.datetime, List[str], Optional[str], Optional[str]]] = {}
    last_preview: Dict[str, Tuple[dt.datetime, List[str], Optional[str], Optional[str]]] = {}

    out: List[ExecEvent] = []
    files = sorted(iter_log_files(tag))
    if not files:
        return out

    for fp in files:
        try:
            for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
                m_ts = TS_RE.match(line)
                if not m_ts:
                    continue
                ts = dt.datetime.strptime(m_ts.group("ts"), "%Y-%m-%d %H:%M:%S")

                m_kind = KIND_RE.search(line)
                if not m_kind:
                    continue

                kind = m_kind.group("kind")
                sym = m_kind.group("sym")

                policy = None
                regime = None
                mp = POLICY_RE.search(line)
                if mp:
                    policy = mp.group("policy")
                mr = REGIME_RE.search(line)
                if mr:
                    regime = mr.group("regime")

                if kind == "DECIDE":
                    mw = WHY_RE.search(line)
                    reasons = _parse_list(mw.group("why")) if mw else []
                    last_decide[sym] = (ts, reasons, policy, regime)

                elif kind == "PREVIEW":
                    mw = REASON_RE.search(line)
                    reasons = _parse_list(mw.group("why")) if mw else []
                    last_preview[sym] = (ts, reasons, policy, regime)

                elif kind == "EXECUTOR":
                    mt = TICKET_RE.search(line)
                    ticket = mt.group("ticket") if mt else None

                    # Attach most recent decide/preview within 10 seconds
                    reasons: List[str] = []
                    pol = None
                    reg = None

                    if sym in last_decide:
                        ts0, why0, pol0, reg0 = last_decide[sym]
                        if abs((ts - ts0).total_seconds()) <= 10:
                            reasons = why0 or reasons
                            pol = pol0 or pol
                            reg = reg0 or reg

                    if sym in last_preview:
                        ts1, why1, pol1, reg1 = last_preview[sym]
                        if abs((ts - ts1).total_seconds()) <= 10:
                            if not reasons:
                                reasons = why1
                            pol = pol or pol1
                            reg = reg or reg1

                    out.append(
                        ExecEvent(
                            date=ts.date(),
                            symbol=sym,
                            ticket=ticket,
                            reasons=reasons,
                            policy=pol,
                            regime=reg,
                        )
                    )
        except Exception:
            continue

    return out


# -----------------------------
# PnL inputs
# -----------------------------

def load_pnl_daily_by_symbol(outdir: Path) -> pd.DataFrame:
    """
    Expects report/log_analysis/pnl_daily_by_symbol.csv produced by daily_pnl_reporter.py
    Columns usually: date, group, symbol, trades, gross, commission, swap, net
    """
    fp = outdir / "pnl_daily_by_symbol.csv"
    if not fp.exists():
        return pd.DataFrame()
    df = pd.read_csv(fp)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).copy()
        df["date_only"] = df["date"].dt.date
    else:
        df["date_only"] = pd.NaT

    for c in ["trades", "gross", "commission", "swap", "net"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    return df


# -----------------------------
# Attribution math
# -----------------------------

def build_attribution(
    pnl_df: pd.DataFrame,
    exec_events: List[ExecEvent],
    group: str,
    start_date: Optional[dt.date] = None,
    end_date: Optional[dt.date] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Returns 3 dataframes with DATE INCLUDED (so dashboard can filter):
      loss_by_reason: date, symbol, reason, trades_w, net_pnl_w
      pnl_by_policy:  date, symbol, policy, trades, net_pnl
      pnl_by_regime:  date, symbol, dominant_regime, trades, net_pnl
    """
    if pnl_df.empty:
        return (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    df = pnl_df.copy()
    if "group" in df.columns:
        df = df[df["group"].astype(str).str.upper() == str(group).upper()].copy()

    if start_date:
        df = df[df["date_only"] >= start_date].copy()
    if end_date:
        df = df[df["date_only"] <= end_date].copy()

    if df.empty:
        return (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    ev = pd.DataFrame([e.__dict__ for e in exec_events])
    if ev.empty:
        return (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    ev["date_only"] = pd.to_datetime(ev["date"], errors="coerce").dt.date
    ev["symbol"] = ev["symbol"].astype(str)

    sym_set = set(df["symbol"].astype(str).unique()) if "symbol" in df.columns else set()
    if sym_set:
        ev = ev[ev["symbol"].isin(sym_set)].copy()
    if start_date:
        ev = ev[ev["date_only"] >= start_date].copy()
    if end_date:
        ev = ev[ev["date_only"] <= end_date].copy()

    rows_reason = []
    rows_policy = []
    rows_regime = []

    key_cols = ["date_only", "symbol"]
    pnl_key = df.groupby(key_cols, as_index=False).agg(
        trades=("trades", "sum"),
        net=("net", "sum"),
    )

    ev_g = ev.groupby(key_cols, as_index=False).agg(
        n_exec=("symbol", "size"),
        policies=("policy", lambda s: [x for x in s.dropna().astype(str).tolist() if x]),
        regimes=("regime", lambda s: [x for x in s.dropna().astype(str).tolist() if x]),
        reasons=("reasons", lambda s: [y for x in s.tolist() for y in (x or [])]),
    )

    merged = pnl_key.merge(ev_g, on=key_cols, how="left")
    merged["n_exec"] = merged["n_exec"].fillna(0).astype(int)

    for _, r in merged.iterrows():
        date_only = r["date_only"]
        sym = str(r["symbol"])
        trades = float(r["trades"]) if not pd.isna(r["trades"]) else 0.0
        net = float(r["net"]) if not pd.isna(r["net"]) else 0.0

        reasons = r.get("reasons") if isinstance(r.get("reasons"), list) else []
        policies = r.get("policies") if isinstance(r.get("policies"), list) else []
        regimes = r.get("regimes") if isinstance(r.get("regimes"), list) else []

        pol = policies[0] if policies else "unknown"
        reg = regimes[0] if regimes else "unknown"

        rows_policy.append({"date": date_only, "symbol": sym, "policy": pol, "trades": trades, "net_pnl": net})
        rows_regime.append({"date": date_only, "symbol": sym, "dominant_regime": reg, "trades": trades, "net_pnl": net})

        if not reasons:
            rows_reason.append({"date": date_only, "symbol": sym, "reason": "(no_reason)", "trades_w": trades, "net_pnl_w": net})
            continue

        cnt = defaultdict(int)
        for w in reasons:
            cnt[str(w)] += 1
        tot = sum(cnt.values()) or 1

        for reason, c in cnt.items():
            w = c / tot
            rows_reason.append(
                {"date": date_only, "symbol": sym, "reason": reason, "trades_w": trades * w, "net_pnl_w": net * w}
            )

    loss_by_reason = pd.DataFrame(rows_reason)
    pnl_by_policy = pd.DataFrame(rows_policy)
    pnl_by_regime = pd.DataFrame(rows_regime)

    return loss_by_reason, pnl_by_policy, pnl_by_regime


# -----------------------------
# CLI
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="Agent tag, e.g. idx_v46, fx_v46, xau_v46")
    ap.add_argument("--group", required=True, help="Group name in pnl_daily_by_symbol.csv, e.g. IDX/FX/XAU")
    ap.add_argument("--outdir", default="report/log_analysis", help="Output directory (default report/log_analysis)")
    ap.add_argument("--start", default=None, help="Optional start date (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="Optional end date (YYYY-MM-DD)")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    start_date = dt.date.fromisoformat(args.start) if args.start else None
    end_date = dt.date.fromisoformat(args.end) if args.end else None

    pnl_df = load_pnl_daily_by_symbol(outdir)
    if pnl_df.empty:
        print(f"[analysis_loss_attribution] Missing pnl_daily_by_symbol.csv under: {outdir}")
        return 2

    exec_events = parse_exec_events(args.tag)

    loss_by_reason, pnl_by_policy, pnl_by_regime = build_attribution(
        pnl_df=pnl_df,
        exec_events=exec_events,
        group=args.group,
        start_date=start_date,
        end_date=end_date,
    )

    f1 = outdir / f"analysis_loss_by_reason_{args.tag}.csv"
    f2 = outdir / f"analysis_pnl_by_policy_{args.tag}.csv"
    f3 = outdir / f"analysis_pnl_by_regime_{args.tag}.csv"

    if not loss_by_reason.empty:
        loss_by_reason.to_csv(f1, index=False)
    if not pnl_by_policy.empty:
        pnl_by_policy.to_csv(f2, index=False)
    if not pnl_by_regime.empty:
        pnl_by_regime.to_csv(f3, index=False)

    wrote = []
    for fp in [f1, f2, f3]:
        if fp.exists():
            wrote.append(str(fp))

    if not wrote:
        print("[analysis_loss_attribution] No EXECUTOR/DECIDE/PREVIEW matches found. Check log paths and tag.")
        return 3

    print("[analysis_loss_attribution] Wrote:")
    for w in wrote:
        print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
