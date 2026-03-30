from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import MetaTrader5 as mt5  # type: ignore

logger = logging.getLogger("agentic_trader.pnl_reporter")


# -------------------------------------------------------------------
# Data models
# -------------------------------------------------------------------

@dataclass
class DealRow:
    time: dt.datetime
    ticket: int
    position_id: int
    symbol: str
    volume: float
    profit: float
    commission: float
    swap: float
    comment: str

    @property
    def net(self) -> float:
        return self.profit + self.commission + self.swap


@dataclass
class PnLAggregate:
    group: str           # FX / XAU / IDX
    trades: int
    gross: float         # sum of profit
    commission: float    # sum of commission
    swap: float          # sum of swap

    @property
    def net(self) -> float:
        return self.gross + self.commission + self.swap


@dataclass
class PnLSymbolAggregate:
    group: str           # FX / XAU / IDX
    symbol: str
    trades: int
    gross: float
    commission: float
    swap: float

    @property
    def net(self) -> float:
        return self.gross + self.commission + self.swap


# -------------------------------------------------------------------
# Classification helpers (per position_id)
# -------------------------------------------------------------------

def classify_group_from_comment(comment: str) -> Optional[str]:
    c = (comment or "").lower()
    if "fx_v46" in c:
        return "FX"
    if "xau_v46" in c:
        return "XAU"
    if "idx_v46" in c:
        return "IDX"
    return None


def classify_group_from_symbol(symbol: str) -> Optional[str]:
    """Heuristic group classification from symbol when position comments are missing.

    We keep this conservative and aligned with existing naming:
    - XAU: XAUUSD*, GOLD*
    - IDX: NAS100*, UK100*, HK50* (and common index tokens)
    - FX: default fallback
    """
    s = (symbol or "").upper()
    if not s:
        return None

    if "XAU" in s or "GOLD" in s:
        return "XAU"

    # Indices (covers NAS100.s / UK100.s / HK50.s and similar)
    if any(tok in s for tok in ("NAS100", "UK100", "HK50", "US30", "SPX", "GER", "DE40", "JP225", "USTEC", "US500")):
        return "IDX"

    # Default: treat as FX (covers EURUSD-*, GBPUSD-*, etc.)
    return "FX"

def build_position_group_map(deals: Iterable[DealRow]) -> Dict[int, str]:
    """
    For each position_id, decide which group (FX/XAU/IDX) it belongs to
    based on any deal comment that contains fx_v46/xau_v46/idx_v46.
    """
    pos_group: Dict[int, str] = {}

    for d in deals:
        if not d.position_id:
            continue
        g = classify_group_from_comment(d.comment) or classify_group_from_symbol(getattr(d, "symbol", ""))
        if g and d.position_id not in pos_group:
            pos_group[d.position_id] = g

    return pos_group


# -------------------------------------------------------------------
# MT5 helpers
# -------------------------------------------------------------------

def ensure_mt5_initialized() -> None:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
    info = mt5.terminal_info()
    if info is None:
        raise RuntimeError("MT5 terminal_info() returned None (terminal not running?)")


def shutdown_mt5() -> None:
    try:
        mt5.shutdown()
    except Exception:
        pass


def fetch_deals_between(time_from: dt.datetime, time_to: dt.datetime) -> List[DealRow]:
    """
    Fetch deals between [time_from, time_to].
    """
    ensure_mt5_initialized()

    deals = mt5.history_deals_get(time_from, time_to)
    if deals is None:
        return []

    rows: List[DealRow] = []
    for d in deals:
        rows.append(
            DealRow(
                time=getattr(d, "time", time_from),
                ticket=int(getattr(d, "ticket", 0)),
                position_id=int(getattr(d, "position_id", 0)),
                symbol=str(getattr(d, "symbol", "")),
                volume=float(getattr(d, "volume", 0.0)),
                profit=float(getattr(d, "profit", 0.0)),
                commission=float(getattr(d, "commission", 0.0)),
                swap=float(getattr(d, "swap", 0.0)),
                comment=str(getattr(d, "comment", "") or ""),
            )
        )
    return rows


def fetch_deals_for_day(day: dt.date) -> List[DealRow]:
    """
    Fetch ALL deals for the given calendar day.
    """
    time_from = dt.datetime.combine(day, dt.time.min)
    time_to = dt.datetime.combine(day, dt.time.max)
    return fetch_deals_between(time_from, time_to)


def fetch_deals_for_today_rolling() -> List[DealRow]:
    """
    Fetch deals from today's 00:00:00 to *now* (rolling intraday view).
    """
    today = dt.date.today()
    time_from = dt.datetime.combine(today, dt.time.min)
    time_to = dt.datetime.now()
    return fetch_deals_between(time_from, time_to)


# -------------------------------------------------------------------
# Aggregation
# -------------------------------------------------------------------

def aggregate_pnl(deals: Iterable[DealRow]) -> Tuple[Dict[str, PnLAggregate], Dict[str, float]]:
    """
    Aggregate PnL per group (FX/XAU/IDX) based on position_id mapping.
    Only positions whose deals mention fx_v46/xau_v46/idx_v46 in ANY comment
    will be included.
    """
    deals_list = list(deals)
    pos_group = build_position_group_map(deals_list)

    groups: Dict[str, PnLAggregate] = {}

    for d in deals_list:
        group = pos_group.get(d.position_id) or classify_group_from_symbol(getattr(d, "symbol", ""))
        if not group:
            continue

        if group not in groups:
            groups[group] = PnLAggregate(group=group, trades=0, gross=0.0, commission=0.0, swap=0.0)

        agg = groups[group]
        agg.trades += 1
        agg.gross += d.profit
        agg.commission += d.commission
        agg.swap += d.swap

    totals: Dict[str, float] = {g: agg.net for g, agg in groups.items()}
    return groups, totals


def aggregate_pnl_by_symbol(deals: Iterable[DealRow]) -> Dict[Tuple[str, str], PnLSymbolAggregate]:
    """
    Aggregate PnL per (group, symbol) based on position_id mapping.
    """
    deals_list = list(deals)
    pos_group = build_position_group_map(deals_list)

    out: Dict[Tuple[str, str], PnLSymbolAggregate] = {}

    for d in deals_list:
        group = pos_group.get(d.position_id) or classify_group_from_symbol(getattr(d, "symbol", ""))
        if not group:
            continue
        sym = (d.symbol or "").strip()
        if not sym:
            continue

        key = (group, sym)
        if key not in out:
            out[key] = PnLSymbolAggregate(group=group, symbol=sym, trades=0, gross=0.0, commission=0.0, swap=0.0)

        agg = out[key]
        agg.trades += 1
        agg.gross += d.profit
        agg.commission += d.commission
        agg.swap += d.swap

    return out


def ensure_all_groups(groups: Dict[str, PnLAggregate]) -> Dict[str, PnLAggregate]:
    """
    Ensure FX/XAU/IDX rows exist even if there are no deals yet.
    """
    for g in ("FX", "XAU", "IDX"):
        if g not in groups:
            groups[g] = PnLAggregate(group=g, trades=0, gross=0.0, commission=0.0, swap=0.0)
    return groups


# -------------------------------------------------------------------
# Output helpers
# -------------------------------------------------------------------

def ensure_outdir(path: str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def outdir_log_analysis(base_outdir: str = "report/log_analysis") -> Path:
    return ensure_outdir(base_outdir)


def write_daily_snapshot_csv(day: dt.date, groups: Dict[str, PnLAggregate], base_outdir: str) -> Path:
    """
    Snapshot CSV for a specific day (kept for audit / historical export):
      <base_outdir>/YYYY-MM-DD_agentic_pnl.csv
    """
    outdir = outdir_log_analysis(base_outdir)
    path = outdir / f"{day.isoformat()}_agentic_pnl.csv"

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "group", "trades", "gross", "commission", "swap", "net"])
        for g, agg in sorted(groups.items()):
            w.writerow([day.isoformat(), g, agg.trades, f"{agg.gross:.2f}", f"{agg.commission:.2f}", f"{agg.swap:.2f}", f"{agg.net:.2f}"])
    return path


def write_daily_snapshot_by_symbol_csv(day: dt.date, by_sym: Dict[Tuple[str, str], PnLSymbolAggregate], base_outdir: str) -> Path:
    """
    Snapshot CSV per symbol for a specific day (audit):
      <base_outdir>/YYYY-MM-DD_agentic_pnl_by_symbol.csv
    """
    outdir = outdir_log_analysis(base_outdir)
    path = outdir / f"{day.isoformat()}_agentic_pnl_by_symbol.csv"

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "group", "symbol", "trades", "gross", "commission", "swap", "net"])
        for (g, sym), agg in sorted(by_sym.items(), key=lambda x: (x[0][0], x[0][1])):
            w.writerow([day.isoformat(), g, sym, agg.trades, f"{agg.gross:.2f}", f"{agg.commission:.2f}", f"{agg.swap:.2f}", f"{agg.net:.2f}"])
    return path


def upsert_pnl_daily_file(day: dt.date, groups: Dict[str, PnLAggregate], base_outdir: str) -> Path:
    """
    Stable, dashboard-friendly daily PnL file:
      <base_outdir>/pnl_daily.csv
    """
    outdir = outdir_log_analysis(base_outdir)
    path = outdir / "pnl_daily.csv"

    rows: List[Dict[str, str]] = []
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append(dict(row))

    idx: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in rows:
        idx[(row.get("date", ""), row.get("group", ""))] = row

    for g, agg in sorted(groups.items()):
        idx[(day.isoformat(), g)] = {
            "date": day.isoformat(),
            "group": g,
            "trades": str(int(agg.trades)),
            "gross": f"{agg.gross:.2f}",
            "commission": f"{agg.commission:.2f}",
            "swap": f"{agg.swap:.2f}",
            "net": f"{agg.net:.2f}",
        }

    all_rows = list(idx.values())
    all_rows.sort(key=lambda x: (x.get("date", ""), x.get("group", "")))

    with path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["date", "group", "trades", "gross", "commission", "swap", "net"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_rows)

    return path


def upsert_pnl_daily_by_symbol_file(
    day: dt.date,
    by_sym: Dict[Tuple[str, str], PnLSymbolAggregate],
    base_outdir: str,
    known_symbols: Optional[Dict[str, List[str]]] = None,
) -> Path:
    """
    Stable per-symbol daily PnL file:
      <base_outdir>/pnl_daily_by_symbol.csv

    We also backfill zero rows for any known symbols to make the dashboard
    able to show zeros when filtering by asset.
    """
    outdir = outdir_log_analysis(base_outdir)
    path = outdir / "pnl_daily_by_symbol.csv"

    rows: List[Dict[str, str]] = []
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                rows.append(dict(row))

    idx: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    for row in rows:
        idx[(row.get("date", ""), row.get("group", ""), row.get("symbol", ""))] = row

    # Write rows for symbols that traded today
    for (g, sym), agg in by_sym.items():
        idx[(day.isoformat(), g, sym)] = {
            "date": day.isoformat(),
            "group": g,
            "symbol": sym,
            "trades": str(int(agg.trades)),
            "gross": f"{agg.gross:.2f}",
            "commission": f"{agg.commission:.2f}",
            "swap": f"{agg.swap:.2f}",
            "net": f"{agg.net:.2f}",
        }

    # Backfill zeros for known symbols (if provided)
    if known_symbols:
        for g, syms in known_symbols.items():
            for sym in syms:
                key = (day.isoformat(), g, sym)
                if key not in idx:
                    idx[key] = {
                        "date": day.isoformat(),
                        "group": g,
                        "symbol": sym,
                        "trades": "0",
                        "gross": "0.00",
                        "commission": "0.00",
                        "swap": "0.00",
                        "net": "0.00",
                    }

    all_rows = list(idx.values())
    all_rows.sort(key=lambda x: (x.get("date", ""), x.get("group", ""), x.get("symbol", "")))

    with path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["date", "group", "symbol", "trades", "gross", "commission", "swap", "net"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_rows)

    return path


def print_daily_summary(day: dt.date, groups: Dict[str, PnLAggregate], totals: Dict[str, float]) -> None:
    print(f"\n=== Agentic Trader Daily PnL - {day} ===\n")
    print(f"{'Group':<6} {'Trades':>6} {'Gross':>10} {'Comm':>10} {'Swap':>10} {'Net':>10}")
    print("-" * 60)

    for g in sorted(groups.keys()):
        agg = groups[g]
        print(f"{g:<6} {agg.trades:>6d} {agg.gross:>10.2f} {agg.commission:>10.2f} {agg.swap:>10.2f} {agg.net:>10.2f}")

    print("-" * 60)
    total_all = sum(totals.values())
    print(f"{'ALL':<6} {'':>6} {'':>10} {'':>10} {'':>10} {total_all:>10.2f}\n")


# -------------------------------------------------------------------
# Rolling-today writer
# -------------------------------------------------------------------

def _known_symbols_from_snapshot_dir(base_outdir: str) -> Dict[str, List[str]]:
    """
    Build a best-effort list of known symbols per group from the existing
    pnl_daily_by_symbol.csv (if it exists) so we can backfill zeros.
    """
    outdir = outdir_log_analysis(base_outdir)
    path = outdir / "pnl_daily_by_symbol.csv"
    if not path.exists():
        return {}

    known: Dict[str, set] = {}
    with path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            g = (row.get("group") or "").strip()
            sym = (row.get("symbol") or "").strip()
            if not g or not sym:
                continue
            known.setdefault(g, set()).add(sym)

    return {g: sorted(list(syms)) for g, syms in known.items()}


def write_today_rolling_files(base_outdir: str) -> Tuple[Path, Path]:
    """
    Always write today's PnL:
      - snapshot file (YYYY-MM-DD_agentic_pnl.csv)
      - stable upsert file (pnl_daily.csv)
      - stable per-symbol file (pnl_daily_by_symbol.csv)
      - snapshot per-symbol file (YYYY-MM-DD_agentic_pnl_by_symbol.csv)
    """
    day = dt.date.today()
    deals = fetch_deals_for_today_rolling()

    groups, _ = aggregate_pnl(deals)
    groups = ensure_all_groups(groups)
    totals = {g: agg.net for g, agg in groups.items()}

    by_sym = aggregate_pnl_by_symbol(deals)
    known_syms = _known_symbols_from_snapshot_dir(base_outdir)

    snap = write_daily_snapshot_csv(day, groups, base_outdir)
    stable = upsert_pnl_daily_file(day, groups, base_outdir)

    snap_sym = write_daily_snapshot_by_symbol_csv(day, by_sym, base_outdir)
    stable_sym = upsert_pnl_daily_by_symbol_file(day, by_sym, base_outdir, known_symbols=known_syms)

    print_daily_summary(day, groups, totals)
    print(f"Rolling-today snapshot written:        {snap.resolve()}")
    print(f"Rolling-today stable written:          {stable.resolve()}")
    print(f"Rolling-today per-symbol snapshot:     {snap_sym.resolve()}")
    print(f"Rolling-today per-symbol stable written:{stable_sym.resolve()}")
    return snap, stable


# -------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Agentic Trader Daily/Range PnL Reporter")
    parser.add_argument("--date", help="End date YYYY-MM-DD (defaults to today)")
    parser.add_argument("--days-back", type=int, default=0, help="Alternative to --date: end date N days back from today")
    parser.add_argument("--window-days", type=int, default=0, help="If >0, report over this many days ending on chosen date")
    parser.add_argument("--outdir", default="report/log_analysis", help="Output directory (default: report/log_analysis)")
    parser.add_argument("--write-today", action="store_true", default=True, help="Always write today's rolling CSV (default: ON)")
    parser.add_argument("--no-write-today", action="store_true", help="Disable writing today's rolling CSV.")
    parser.add_argument("--no-mt5-shutdown", action="store_true", help="Do not call mt5.shutdown() at end.")
    args = parser.parse_args(argv)

    base_outdir = args.outdir
    outdir_log_analysis(base_outdir)

    print(f"[pnl_reporter] CWD: {os.getcwd()}")
    print(f"[pnl_reporter] OUTDIR: {Path(base_outdir).resolve()}")

    write_today = bool(args.write_today) and not bool(args.no_write_today)

    if args.date:
        end = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        today = dt.date.today()
        end = today - dt.timedelta(days=int(args.days_back or 0))

    try:
        if args.window_days and args.window_days > 1:
            window = int(args.window_days)
            start = end - dt.timedelta(days=window - 1)

            # known symbols (best effort) for zero backfill
            known_syms = _known_symbols_from_snapshot_dir(base_outdir)

            for i in range(window):
                day = start + dt.timedelta(days=i)
                deals = fetch_deals_for_day(day)

                groups, totals = aggregate_pnl(deals)
                groups = ensure_all_groups(groups)
                totals = {g: agg.net for g, agg in groups.items()}

                by_sym = aggregate_pnl_by_symbol(deals)

                snap = write_daily_snapshot_csv(day, groups, base_outdir)
                stable = upsert_pnl_daily_file(day, groups, base_outdir)

                snap_sym = write_daily_snapshot_by_symbol_csv(day, by_sym, base_outdir)
                stable_sym = upsert_pnl_daily_by_symbol_file(day, by_sym, base_outdir, known_symbols=known_syms)

                print(f"[pnl_reporter] Wrote {day}: {snap.name} + {stable.name} + {snap_sym.name} + {stable_sym.name}")

            print(f"Window snapshots written under: {Path(base_outdir).resolve()}")

        else:
            deals = fetch_deals_for_day(end)

            groups, totals = aggregate_pnl(deals)
            groups = ensure_all_groups(groups)
            totals = {g: agg.net for g, agg in groups.items()}

            by_sym = aggregate_pnl_by_symbol(deals)
            known_syms = _known_symbols_from_snapshot_dir(base_outdir)

            snap = write_daily_snapshot_csv(end, groups, base_outdir)
            stable = upsert_pnl_daily_file(end, groups, base_outdir)

            snap_sym = write_daily_snapshot_by_symbol_csv(end, by_sym, base_outdir)
            stable_sym = upsert_pnl_daily_by_symbol_file(end, by_sym, base_outdir, known_symbols=known_syms)

            print_daily_summary(end, groups, totals)
            print(f"Daily snapshot written:        {snap.resolve()}")
            print(f"Stable daily written:          {stable.resolve()}")
            print(f"Daily per-symbol snapshot:     {snap_sym.resolve()}")
            print(f"Stable daily by symbol written:{stable_sym.resolve()}")

        if write_today:
            write_today_rolling_files(base_outdir)

    finally:
        if not args.no_mt5_shutdown:
            shutdown_mt5()


if __name__ == "__main__":
    main()
