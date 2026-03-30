from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date as _date, timedelta as _timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# -------------------------------------------------------------------
# Regex patterns
# -------------------------------------------------------------------

TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
CONF_RE = re.compile(r"\bconf\s*=\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
REASONS_RE = re.compile(r"reason\s*=\s*\[([^\]]*)\]", re.IGNORECASE)

# policy/regime may appear as policy=strict or tokens like policy_flexible inside reason lists
POLICY_RE = re.compile(r"\bpolicy\s*[:=]\s*(strict|flexible|aggressive)\b", re.IGNORECASE)

# Accept: policy_flexible, idx_policy_flexible, idx_policy_flexible_block
POLICY_TOKEN_RE = re.compile(
    r"\b(?:idx_)?policy_(strict|flexible|aggressive)(?:_block)?\b",
    re.IGNORECASE,
)

REGIME_RE = re.compile(r"\bregime\s*[:=]\s*(quiet|normal|hot)\b", re.IGNORECASE)

# Accept: regime_normal, atr_quiet, atr_normal, atr_hot
REGIME_TOKEN_RE = re.compile(
    r"\b(?:regime_|atr_)(quiet|normal|hot)\b",
    re.IGNORECASE,
)


# -------------------------------------------------------------------
# Data models
# -------------------------------------------------------------------


@dataclass
class LogEvent:
    date: str  # 'YYYY-MM-DD' extracted from timestamp, or 'unknown'
    agent: str  # fx_v46 / xau_v46 / idx_v46 / unknown
    kind: str  # EXEC / SKIP / PREVIEW
    symbol: str
    conf: Optional[float]
    # Optional: may or may not exist in older logs
    policy: Optional[str] = None  # strict | flexible | aggressive (lowercase)
    regime: Optional[str] = None  # QUIET | NORMAL | HOT (uppercase)
    reasons: List[str] = field(default_factory=list)
    raw: str = ""  # whole line for debugging


@dataclass
class SummaryRow:
    agent: str
    symbol: str
    kind: str
    count: int
    avg_conf: Optional[float]


@dataclass
class DailyStats:
    exec_count: int = 0
    skip_count: int = 0
    exec_conf_sum: float = 0.0
    exec_conf_n: int = 0
    skip_conf_sum: float = 0.0
    skip_conf_n: int = 0
    policy_counter: Counter = field(default_factory=Counter)
    regime_counter: Counter = field(default_factory=Counter)
    completeness_pct: float = 0.0

    @property
    def exec_ratio(self) -> float:
        total = self.exec_count + self.skip_count
        return (self.exec_count / total * 100.0) if total > 0 else 0.0

    @property
    def avg_exec_conf(self) -> Optional[float]:
        return (self.exec_conf_sum / self.exec_conf_n) if self.exec_conf_n > 0 else None

    @property
    def avg_skip_conf(self) -> Optional[float]:
        return (self.skip_conf_sum / self.skip_conf_n) if self.skip_conf_n > 0 else None

    @property
    def dominant_policy(self) -> Optional[str]:
        return self.policy_counter.most_common(1)[0][0] if self.policy_counter else None

    @property
    def dominant_regime(self) -> Optional[str]:
        return self.regime_counter.most_common(1)[0][0] if self.regime_counter else None

    def compute_completeness(self) -> None:
        """
        A lightweight "data completeness" score.
        For daily stats, we score availability of:
          - conf on EXEC/SKIP
          - policy (if present)
          - regime (if present)
        """
        parts = []
        parts.append(1.0 if (self.exec_conf_n > 0 or self.skip_conf_n > 0) else 0.0)
        parts.append(1.0 if sum(self.policy_counter.values()) > 0 else 0.0)
        parts.append(1.0 if sum(self.regime_counter.values()) > 0 else 0.0)
        self.completeness_pct = sum(parts) / len(parts) * 100.0


@dataclass
class DailySymbolStats(DailyStats):
    pass


# -------------------------------------------------------------------
# Parsing helpers
# -------------------------------------------------------------------


def _detect_agent(line: str) -> str:
    low = line.lower()
    if "idx_v46" in low:
        return "idx_v46"
    if "fx_v46" in low:
        return "fx_v46"
    if "xau_v46" in low:
        return "xau_v46"
    return "unknown"


def _parse_reasons(blob: str) -> List[str]:
    """
    Parse a reasons list from inside the [...] portion.
    Accepts forms like:  'a', 'b', 'policy_flexible'
    """
    if not blob:
        return []
    items = []
    for part in blob.split(","):
        p = part.strip().strip("'").strip('"')
        if p:
            items.append(p)
    return items


def _parse_policy_regime(line: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract policy/regime if available in the log line.

    Returns:
      policy: strict|flexible|aggressive (lowercase) or None
      regime: QUIET|NORMAL|HOT (uppercase) or None
    """
    policy: Optional[str] = None
    regime: Optional[str] = None

    m_pol = POLICY_RE.search(line)
    if m_pol:
        policy = m_pol.group(1).strip().lower()
    else:
        m_pol = POLICY_TOKEN_RE.search(line)
        if m_pol:
            policy = m_pol.group(1).strip().lower()

    m_reg = REGIME_RE.search(line)
    if m_reg:
        regime = m_reg.group(1).strip().upper()
    else:
        m_reg = REGIME_TOKEN_RE.search(line)
        if m_reg:
            regime = m_reg.group(1).strip().upper()

    return policy, regime


def normalize_symbol(agent: str, raw: str) -> str:
    """
    Normalize symbol tokens from log lines into canonical tradable symbols.

    Handles cases like:
      NAS100....=ALIGNED_BULL
      UK100...._rsi_bear
      XAUUSD|something
    """
    if not raw:
        return "UNKNOWN"

    s = str(raw).strip()

    # 1) Remove the "...." bridge used in logs
    s = s.replace("....", "")

    # 2) Cut at known separators
    for sep in ("=", "|", ","):
        if sep in s:
            s = s.split(sep, 1)[0]

    # 3) Remove trailing dots / underscores and suffix fragments
    s = s.rstrip(".")
    if "_" in s:
        s = s.split("_", 1)[0]

    s_up = s.upper().strip()
    if not s_up:
        return "UNKNOWN"

    if agent == "idx_v46":
        # IDX uses .s suffix in MT5
        if s_up.endswith(".S"):
            return s_up[:-2] + ".s"
        if "." not in s_up:
            return f"{s_up}.s"
        return s_up.replace(".S", ".s")

    # FX/XAU: just uppercase, preserve broker suffix if present
    return s_up


def parse_line(line: str, default_agent: Optional[str] = None) -> Optional[LogEvent]:
    low = line.lower()
    tag: Optional[str] = None
    kind: Optional[str] = None

    if "[preview]" in low:
        kind = "PREVIEW"
        tag = "[PREVIEW]"
    elif "[executed]" in low:
        kind = "EXEC"
        tag = "[EXECUTED]"
    elif "[executor]" in low:
        kind = "EXEC"
        tag = "[EXECUTOR]"
    elif "[skip]" in low:
        kind = "SKIP"
        tag = "[SKIP]"
    else:
        return None

    m_ts = TIMESTAMP_RE.match(line)
    date = m_ts.group(1) if m_ts else "unknown"

    after = line.split(tag, 1)[1] if tag in line else line
    parts = after.strip().split()
    if not parts:
        return None

    agent = _detect_agent(line)
    if agent == "unknown" and default_agent:
        agent = default_agent

    symbol = normalize_symbol(agent, parts[0])

    m_conf = CONF_RE.search(line)
    conf = float(m_conf.group(1)) if m_conf else None

    reasons: List[str] = []
    m_reasons = REASONS_RE.search(line)
    if m_reasons:
        reasons = _parse_reasons(m_reasons.group(1))

    policy, regime = _parse_policy_regime(line)

    return LogEvent(
        date=date,
        agent=agent,
        kind=kind or "UNKNOWN",
        symbol=symbol,
        conf=conf,
        policy=policy,
        regime=regime,
        reasons=reasons,
        raw=line.rstrip("\n"),
    )


def parse_log_file(path: Path, default_agent: Optional[str] = None) -> List[LogEvent]:
    events: List[LogEvent] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            ev = parse_line(line, default_agent=default_agent)
            if ev:
                events.append(ev)
    return events


# -------------------------------------------------------------------
# Enrich EXEC with last PREVIEW confidence/policy/regime/reasons
# -------------------------------------------------------------------


def enrich_exec_conf(events: List[LogEvent]) -> None:
    last_preview: Dict[Tuple[str, str], LogEvent] = {}

    for ev in events:
        key = (ev.agent, ev.symbol)

        if ev.kind == "PREVIEW":
            last_preview[key] = ev
            continue

        if ev.kind == "EXEC":
            prev = last_preview.get(key)
            if prev:
                if ev.conf is None and prev.conf is not None:
                    ev.conf = prev.conf
                if ev.policy is None and prev.policy is not None:
                    ev.policy = prev.policy
                if ev.regime is None and prev.regime is not None:
                    ev.regime = prev.regime
                if not ev.reasons and prev.reasons:
                    ev.reasons = list(prev.reasons)


# -------------------------------------------------------------------
# Aggregations
# -------------------------------------------------------------------


def summarize_events(events: List[LogEvent]) -> Tuple[List[SummaryRow], Dict[str, int]]:
    by_key: Dict[Tuple[str, str, str], List[Optional[float]]] = defaultdict(list)
    skip_reasons: Counter = Counter()

    for ev in events:
        by_key[(ev.agent, ev.symbol, ev.kind)].append(ev.conf)

        if ev.kind == "SKIP":
            for r in (ev.reasons or []):
                skip_reasons[r] += 1

    rows: List[SummaryRow] = []
    for (agent, symbol, kind), confs in by_key.items():
        count = len(confs)
        vals = [c for c in confs if c is not None]
        avg_conf = (sum(vals) / len(vals)) if vals else None
        rows.append(SummaryRow(agent=agent, symbol=symbol, kind=kind, count=count, avg_conf=avg_conf))

    rows.sort(key=lambda r: (r.agent, r.symbol, r.kind))
    return rows, dict(skip_reasons)


def _ensure_day_row(by_day: Dict[str, DailyStats], d: str) -> None:
    if d not in by_day:
        by_day[d] = DailyStats()


def _ensure_day_symbol_row(by_key: Dict[Tuple[str, str], DailySymbolStats], d: str, sym: str) -> None:
    k = (d, sym)
    if k not in by_key:
        by_key[k] = DailySymbolStats()


def daily_stats(events: List[LogEvent]) -> Dict[str, DailyStats]:
    by_day: Dict[str, DailyStats] = {}
    for ev in events:
        d = ev.date
        _ensure_day_row(by_day, d)
        st = by_day[d]

        if ev.kind == "EXEC":
            st.exec_count += 1
            if ev.conf is not None:
                st.exec_conf_sum += ev.conf
                st.exec_conf_n += 1
        elif ev.kind == "SKIP":
            st.skip_count += 1
            if ev.conf is not None:
                st.skip_conf_sum += ev.conf
                st.skip_conf_n += 1

        if ev.policy:
            st.policy_counter[ev.policy] += 1
        if ev.regime:
            st.regime_counter[ev.regime] += 1

    for _, st in by_day.items():
        st.compute_completeness()
    return dict(sorted(by_day.items(), key=lambda kv: kv[0]))


def daily_stats_by_symbol(events: List[LogEvent]) -> Dict[Tuple[str, str], DailySymbolStats]:
    by_key: Dict[Tuple[str, str], DailySymbolStats] = {}
    for ev in events:
        d = ev.date
        sym = ev.symbol
        _ensure_day_symbol_row(by_key, d, sym)
        st = by_key[(d, sym)]

        if ev.kind == "EXEC":
            st.exec_count += 1
            if ev.conf is not None:
                st.exec_conf_sum += ev.conf
                st.exec_conf_n += 1
        elif ev.kind == "SKIP":
            st.skip_count += 1
            if ev.conf is not None:
                st.skip_conf_sum += ev.conf
                st.skip_conf_n += 1

        if ev.policy:
            st.policy_counter[ev.policy] += 1
        if ev.regime:
            st.regime_counter[ev.regime] += 1

    for _, st in by_key.items():
        st.compute_completeness()
    return dict(sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])))


def daily_reason_counts(events: List[LogEvent]) -> Dict[str, Counter]:
    by_day: Dict[str, Counter] = defaultdict(Counter)
    for ev in events:
        if ev.kind != "SKIP":
            continue
        for r in (ev.reasons or []):
            by_day[ev.date][r] += 1
    return dict(sorted(by_day.items(), key=lambda kv: kv[0]))


def daily_reason_counts_by_symbol(events: List[LogEvent]) -> Dict[Tuple[str, str], Counter]:
    by_key: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for ev in events:
        if ev.kind != "SKIP":
            continue
        for r in (ev.reasons or []):
            by_key[(ev.date, ev.symbol)][r] += 1
    return dict(sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])))


# --- NEW: EXEC reasons + EXEC by policy (for loss concentration / policy attribution) ---


def daily_exec_reason_counts_by_symbol(events: List[LogEvent]) -> Dict[Tuple[str, str], Counter]:
    by_key: Dict[Tuple[str, str], Counter] = defaultdict(Counter)
    for ev in events:
        if ev.kind != "EXEC":
            continue
        rs = ev.reasons or []
        if not rs:
            rs = ["(no_reason)"]
        for r in rs:
            by_key[(ev.date, ev.symbol)][r] += 1
    return dict(sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])))


def daily_exec_by_policy(events: List[LogEvent]) -> Dict[Tuple[str, str, str], int]:
    # (date, symbol, policy) -> exec_count
    out: Dict[Tuple[str, str, str], int] = defaultdict(int)
    for ev in events:
        if ev.kind != "EXEC":
            continue
        pol = (ev.policy or "unknown").lower()
        out[(ev.date, ev.symbol, pol)] += 1
    return dict(sorted(out.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])))


# -------------------------------------------------------------------
# CSV writers
# -------------------------------------------------------------------


def write_csv_summary(outdir: Path, tag: str, rows: List[SummaryRow]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"log_summary_{tag}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["agent", "symbol", "kind", "count", "avg_conf"])
        for r in rows:
            w.writerow([r.agent, r.symbol, r.kind, r.count, "" if r.avg_conf is None else f"{r.avg_conf:.4f}"])
    return path


def write_csv_skip_reasons(outdir: Path, tag: str, reasons: Dict[str, int]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"log_skip_reasons_{tag}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["reason", "count"])
        for reason, count in sorted(reasons.items(), key=lambda kv: kv[1], reverse=True):
            w.writerow([reason, count])
    return path


def write_csv_daily(outdir: Path, tag: str, by_day: Dict[str, DailyStats]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"log_daily_{tag}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "date",
                "exec_count",
                "skip_count",
                "exec_ratio_pct",
                "avg_exec_conf",
                "avg_skip_conf",
                "dominant_policy",
                "dominant_regime",
                "completeness_pct",
            ]
        )
        for d, st in by_day.items():
            w.writerow(
                [
                    d,
                    st.exec_count,
                    st.skip_count,
                    f"{st.exec_ratio:.2f}",
                    "" if st.avg_exec_conf is None else f"{st.avg_exec_conf:.4f}",
                    "" if st.avg_skip_conf is None else f"{st.avg_skip_conf:.4f}",
                    st.dominant_policy,
                    st.dominant_regime,
                    f"{st.completeness_pct:.1f}",
                ]
            )
    return path


def write_csv_daily_by_symbol(outdir: Path, tag: str, by_key: Dict[Tuple[str, str], DailySymbolStats]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"log_daily_by_symbol_{tag}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "date",
                "symbol",
                "exec_count",
                "skip_count",
                "exec_ratio_pct",
                "avg_exec_conf",
                "avg_skip_conf",
                "dominant_policy",
                "dominant_regime",
                "completeness_pct",
            ]
        )
        for (d, sym), st in by_key.items():
            w.writerow(
                [
                    d,
                    sym,
                    st.exec_count,
                    st.skip_count,
                    f"{st.exec_ratio:.2f}",
                    "" if st.avg_exec_conf is None else f"{st.avg_exec_conf:.4f}",
                    "" if st.avg_skip_conf is None else f"{st.avg_skip_conf:.4f}",
                    st.dominant_policy,
                    st.dominant_regime,
                    f"{st.completeness_pct:.1f}",
                ]
            )
    return path


def write_csv_daily_reasons(outdir: Path, tag: str, by_day: Dict[str, Counter]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"log_daily_reasons_{tag}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "reason", "count"])
        for d, counter in by_day.items():
            for reason, count in counter.most_common():
                w.writerow([d, reason, count])
    return path


def write_csv_daily_reasons_by_symbol(outdir: Path, tag: str, by_key: Dict[Tuple[str, str], Counter]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"log_daily_reasons_by_symbol_{tag}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "reason", "count"])
        for (d, sym), counter in by_key.items():
            for reason, count in counter.most_common():
                w.writerow([d, sym, reason, count])
    return path


# --- NEW writers ---


def write_csv_daily_exec_reasons_by_symbol(outdir: Path, tag: str, by_key: Dict[Tuple[str, str], Counter]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"log_daily_exec_reasons_by_symbol_{tag}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "reason", "exec_count"])
        for (d, sym), counter in by_key.items():
            for reason, count in counter.most_common():
                w.writerow([d, sym, reason, count])
    return path


def write_csv_daily_exec_by_policy(outdir: Path, tag: str, rows: Dict[Tuple[str, str, str], int]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"log_daily_exec_by_policy_{tag}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "policy", "exec_count"])
        for (d, sym, pol), n in rows.items():
            w.writerow([d, sym, pol, n])
    return path


def print_console_summary(rows: List[SummaryRow], reasons: Dict[str, int]) -> None:
    print("\n=== Agentic Trader Log Summary ===\n")
    print(f"{'Agent':<10} {'Symbol':<12} {'Kind':<7} {'Count':>6} {'AvgConf':>8}")
    print("-" * 48)
    for r in rows:
        avg = "" if r.avg_conf is None else f"{r.avg_conf:.4f}"
        print(f"{r.agent:<10} {r.symbol:<12} {r.kind:<7} {r.count:>6} {avg:>8}")

    exec_total = sum(r.count for r in rows if r.kind == "EXEC")
    skip_total = sum(r.count for r in rows if r.kind == "SKIP")
    total = exec_total + skip_total
    ratio = (exec_total / total * 100.0) if total > 0 else 0.0

    print("\nTotals:")
    print(f"  EXEC: {exec_total}")
    print(f"  SKIP: {skip_total}")
    print(f"  EXEC ratio: {ratio:.2f}%")

    print("\nTop SKIP reasons:")
    for reason, count in sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:20]:
        print(f"  {reason:<30} {count}")


def _fill_missing_days(by_day: Dict[str, DailyStats]) -> Dict[str, DailyStats]:
    if not by_day:
        return by_day
    keys = sorted(by_day.keys())
    try:
        d0 = _date.fromisoformat(keys[0])
        d1 = _date.fromisoformat(keys[-1])
    except Exception:
        return by_day

    out: Dict[str, DailyStats] = dict(by_day)
    cur = d0
    while cur <= d1:
        s = cur.isoformat()
        if s not in out:
            out[s] = DailyStats()
        cur += _timedelta(days=1)

    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", nargs="+", required=True, help="One or more glob patterns for log files")
    ap.add_argument("--tag", required=True, help="Tag used for output filenames (e.g. fx_v46)")
    ap.add_argument("--outdir", default="report/log_analysis", help="Output directory for CSV summaries")
    ap.add_argument(
        "--include-today",
        action="store_true",
        default=True,
        help="Ensure today's date exists in log_daily_<tag>.csv even if there are zero events (default: ON).",
    )
    ap.add_argument("--no-include-today", action="store_true", help="Disable include-today behaviour.")
    ap.add_argument(
        "--fill-missing-days",
        action="store_true",
        default=False,
        help="Fill missing dates between min/max with zero rows (useful for charts).",
    )
    args = ap.parse_args()

    files: List[Path] = []
    for pattern in args.paths:
        files.extend([Path(p) for p in Path().glob(pattern)])

    if not files:
        print("No log files matched the provided paths.")
        return

    events: List[LogEvent] = []
    for p in sorted(set(files)):
        events.extend(parse_log_file(p, default_agent=args.tag))

    outdir = Path(args.outdir)

    if not events:
        print("No matching EXEC/SKIP/PREVIEW events found in the provided log files.")
        by_day: Dict[str, DailyStats] = {}
        by_key: Dict[Tuple[str, str], DailySymbolStats] = {}

        if args.include_today and not args.no_include_today:
            today = _date.today().isoformat()
            _ensure_day_row(by_day, today)
            by_day[today].compute_completeness()

        write_csv_daily(outdir, args.tag, dict(sorted(by_day.items(), key=lambda kv: kv[0])))
        write_csv_daily_by_symbol(outdir, args.tag, dict(sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1]))))
        print(f"\nCSV summaries written under: {outdir}\n")
        return

    enrich_exec_conf(events)

    rows, reasons = summarize_events(events)
    by_day = daily_stats(events)
    by_day_reasons = daily_reason_counts(events)

    by_key = daily_stats_by_symbol(events)
    by_key_reasons = daily_reason_counts_by_symbol(events)

    # NEW
    by_key_exec_reasons = daily_exec_reason_counts_by_symbol(events)
    exec_by_policy = daily_exec_by_policy(events)

    if args.include_today and not args.no_include_today:
        today = _date.today().isoformat()
        _ensure_day_row(by_day, today)
        by_day[today].compute_completeness()

        # For per-symbol daily stats, also ensure "today rows" exist for all seen symbols
        seen_symbols = sorted({e.symbol for e in events if e.symbol and e.symbol.lower() != "unknown"})
        for sym in seen_symbols:
            _ensure_day_symbol_row(by_key, today, sym)
            by_key[(today, sym)].compute_completeness()

    if args.fill_missing_days:
        by_day = _fill_missing_days(by_day)
        for _, st in by_day.items():
            st.compute_completeness()

    write_csv_summary(outdir, args.tag, rows)
    write_csv_skip_reasons(outdir, args.tag, reasons)
    write_csv_daily(outdir, args.tag, by_day)
    write_csv_daily_reasons(outdir, args.tag, by_day_reasons)

    # per-symbol outputs
    write_csv_daily_by_symbol(outdir, args.tag, by_key)
    write_csv_daily_reasons_by_symbol(outdir, args.tag, by_key_reasons)

    # NEW outputs
    write_csv_daily_exec_reasons_by_symbol(outdir, args.tag, by_key_exec_reasons)
    write_csv_daily_exec_by_policy(outdir, args.tag, exec_by_policy)

    print_console_summary(rows, reasons)
    print(f"\nCSV summaries written under: {outdir}\n")


if __name__ == "__main__":
    main()
