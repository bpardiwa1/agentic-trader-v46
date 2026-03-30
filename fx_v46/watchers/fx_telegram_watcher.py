# fx_v46/watchers/fx_telegram_watcher.py
# ============================================================
# Agentic Trader FX v4.6 — Telegram Watcher (LOG or JSONL)
# ------------------------------------------------------------
# WATCH_MODE:
#   - "log"   : parse EVENT {...} lines from rotating daily .log files
#   - "jsonl" : tail a JSONL stream file (preferred) with state offset
#
# JSONL mode supports:
#   - WATCH_JSONL_FILE (explicit file path) OR
#   - WATCH_JSONL_GLOB (glob pattern; attaches to newest match)
#   - WATCH_STATE_FILE for persistent offset across restarts
#   - automatic re-attach on rotation (newest file changes)
#
# Requirements:
#   - No external deps
#   - Loads env vars from: fx_v46/watchers/fx_watcher.env
# ============================================================

from __future__ import annotations

import os
import time
import glob
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib import request, parse


# ------------------------------------------------------------
# Env loader (simple .env reader; does not overwrite existing OS env)
# ------------------------------------------------------------
def load_env_file(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Env file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()

            # strip inline comment only if not quoted
            if v and not (v.startswith('"') or v.startswith("'")):
                if "#" in v:
                    v = v.split("#", 1)[0].strip()

            # unquote
            if len(v) >= 2 and (
                (v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")
            ):
                v = v[1:-1]

            if k and k not in os.environ:
                os.environ[k] = v


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _bool_env(key: str, default: bool = False) -> bool:
    val = _env(key, str(default)).strip().lower()
    return val in ("1", "true", "yes", "on")


def _int_env(key: str, default: int) -> int:
    try:
        return int(float(_env(key, str(default))))
    except Exception:
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except Exception:
        return default


def _csv_set(key: str, default_csv: str) -> set[str]:
    raw = _env(key, default_csv)
    items = []
    for part in raw.split(","):
        s = part.strip()
        if s:
            items.append(s)
    return set(items)


@dataclass
class Cfg:
    tg_token: str
    tg_chat_id: str

    watch_mode: str  # "log" or "jsonl"

    # LOG mode
    log_dir: str
    log_glob: str
    event_prefix: str

    # JSONL mode
    jsonl_file: str
    jsonl_glob: str
    state_file: str

    include_events: set[str]
    exclude_events: set[str]
    decision_require_side: bool

    dedupe_window_sec: int
    min_seconds_between_alerts: float
    max_alerts_per_5min: int

    show_raw_line: bool
    max_message_chars: int

    start_at: str  # "tail" or "head"
    attach_message: bool
    poll_interval_sec: float

    # Heartbeat
    heartbeat_enabled: bool
    heartbeat_interval_sec: int
    heartbeat_silence_alert_sec: int
    heartbeat_include_file: bool
    heartbeat_include_last_event: bool


def load_cfg() -> Cfg:
    return Cfg(
        tg_token=_env("TG_BOT_TOKEN"),
        tg_chat_id=_env("TG_CHAT_ID"),

        watch_mode=_env("WATCH_MODE", "log").lower().strip(),

        # LOG mode
        log_dir=_env("WATCH_LOG_DIR", r"logs\fx_v4.6"),
        log_glob=_env("WATCH_LOG_GLOB", "fx_v46_*.log"),
        event_prefix=_env("WATCH_EVENT_PREFIX", "EVENT").strip(),

        # JSONL mode
        jsonl_file=_env("WATCH_JSONL_FILE", ""),
        jsonl_glob=_env("WATCH_JSONL_GLOB", ""),
        state_file=_env("WATCH_STATE_FILE", r"logs\fx_v4.6\fx_watcher.state.json"),

        include_events=_csv_set(
            "WATCH_INCLUDE_EVENTS",
            "DECISION,TRADE_START,EXECUTED,ORDER_RESULT,FAILED,ERROR,TRADE_END,BLOCKED,SKIP,RISK,ORDER_SEND",
        ),
        exclude_events=_csv_set("WATCH_EXCLUDE_EVENTS", ""),

        decision_require_side=_bool_env("WATCH_DECISION_REQUIRE_SIDE", True),

        dedupe_window_sec=_int_env("WATCH_DEDUPE_WINDOW_SEC", 900),
        min_seconds_between_alerts=_float_env("WATCH_MIN_SECONDS_BETWEEN_ALERTS", 0.2),
        max_alerts_per_5min=_int_env("WATCH_MAX_ALERTS_PER_5MIN", 60),

        show_raw_line=_bool_env("WATCH_SHOW_RAW_LINE", False),
        max_message_chars=_int_env("WATCH_MAX_MESSAGE_CHARS", 3500),

        start_at=_env("WATCH_START_AT", "tail").lower(),  # tail|head
        attach_message=_bool_env("WATCH_ATTACH_MESSAGE", True),
        poll_interval_sec=_float_env("WATCH_POLL_INTERVAL_SEC", 0.5),

        heartbeat_enabled=_bool_env("HEARTBEAT_ENABLED", True),
        heartbeat_interval_sec=_int_env("HEARTBEAT_INTERVAL_SEC", 1800),
        heartbeat_silence_alert_sec=_int_env("HEARTBEAT_SILENCE_ALERT_SEC", 900),
        heartbeat_include_file=_bool_env("HEARTBEAT_INCLUDE_FILE", True),
        heartbeat_include_last_event=_bool_env("HEARTBEAT_INCLUDE_LAST_EVENT", True),
    )


# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------
def tg_send(cfg: Cfg, text: str) -> None:
    url = f"https://api.telegram.org/bot{cfg.tg_token}/sendMessage"
    payload = {"chat_id": cfg.tg_chat_id, "text": text, "disable_web_page_preview": True}
    data = parse.urlencode(payload).encode()
    req = request.Request(url, data=data, method="POST")
    with request.urlopen(req, timeout=15) as resp:
        _ = resp.read()


def tg_safe_send(cfg: Cfg, text: str, tag: str = "TELEGRAM") -> None:
    try:
        tg_send(cfg, text)
    except Exception as e:
        print(f"[WATCHER][{tag}_ERROR] {type(e).__name__}: {e}")


# ------------------------------------------------------------
# Helpers: file attach, dedupe, rate limits
# ------------------------------------------------------------
def _now_ts() -> float:
    return time.time()


def _hash_key(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


class Dedupe:
    def __init__(self, window_sec: int):
        self.window_sec = max(1, int(window_sec))
        self._seen: dict[str, float] = {}

    def seen_recently(self, key: str) -> bool:
        t = _now_ts()
        cutoff = t - self.window_sec
        for k in list(self._seen.keys()):
            if self._seen[k] < cutoff:
                del self._seen[k]
        if key in self._seen:
            return True
        self._seen[key] = t
        return False


class RateLimiter:
    def __init__(self, max_per_5min: int, min_between: float):
        self.max_per_5min = max(1, int(max_per_5min))
        self.min_between = max(0.0, float(min_between))
        self._sent: list[float] = []
        self._last_sent: float = 0.0

    def allow(self) -> bool:
        t = _now_ts()
        if (t - self._last_sent) < self.min_between:
            return False
        cutoff = t - 300.0
        self._sent = [x for x in self._sent if x >= cutoff]
        if len(self._sent) >= self.max_per_5min:
            return False
        self._sent.append(t)
        self._last_sent = t
        return True


def _pick_newest(glob_pattern: str) -> str:
    matches = glob.glob(glob_pattern)
    if not matches:
        return ""
    matches.sort(key=lambda p: os.path.getmtime(p))
    return matches[-1]


def _short_path(p: str) -> str:
    return p.replace("\\", "/").split("/")[-1]


def _truncate(s: str, max_chars: int) -> str:
    if max_chars <= 0:
        return s
    if len(s) <= max_chars:
        return s
    return s[: max(0, max_chars - 20)] + "...(truncated)"


# ------------------------------------------------------------
# Parsing: EVENT {...} from log, or JSONL line
# ------------------------------------------------------------
def _extract_event_json_from_line(line: str, prefix: str) -> Optional[dict]:
    """
    Find first occurrence of `prefix` token in line; parse JSON after it.
    Example: "2026-.. INFO ... EVENT {...}"
    """
    try:
        idx = line.find(prefix)
        if idx < 0:
            return None
        payload = line[idx + len(prefix):].strip()
        if not payload:
            return None
        j0 = payload.find("{")
        if j0 < 0:
            return None
        payload = payload[j0:]
        return json.loads(payload)
    except Exception:
        return None


def _jsonl_parse(line: str) -> Optional[dict]:
    try:
        line = line.strip()
        if not line:
            return None
        return json.loads(line)
    except Exception:
        return None


def _event_side_ok(evt: dict) -> bool:
    side = str(evt.get("side", "") or "")
    return side in ("LONG", "SHORT") or bool(side)


def _fmt_event(cfg: Cfg, evt: dict, raw_line: str = "") -> str:
    event = str(evt.get("event", "EVENT"))
    sym = str(evt.get("symbol", evt.get("sym", "")) or "")
    side = str(evt.get("side", "") or "")
    conf = evt.get("confidence", evt.get("conf", None))

    ts = str(evt.get("ts", ""))
    if ts:
        ts = ts.replace("T", " ").replace("Z", "")
    header = f"[FX][{event}]"
    if sym:
        header += f" {sym}"
    if side:
        header += f" {side}"

    lines = [header]
    if ts:
        lines.append(f"ts: {ts}")

    for k in ("ticket", "order", "deal", "attempts", "reason", "policy", "regime", "atr_level", "atr_pct"):
        if k in evt and evt.get(k) not in (None, "", [], {}):
            lines.append(f"{k}: {evt.get(k)}")

    if conf is not None:
        lines.append(f"confidence: {conf}")

    why = evt.get("why")
    if isinstance(why, list) and why:
        lines.append(f"why: {', '.join(str(x) for x in why[:12])}")

    if cfg.show_raw_line and raw_line:
        lines.append("")
        lines.append("raw:")
        lines.append(raw_line.strip())

    msg = "\n".join(lines)
    return _truncate(msg, cfg.max_message_chars)


# ------------------------------------------------------------
# State (JSONL mode): store offset and current file
# ------------------------------------------------------------
def _load_state(path: str) -> dict:
    try:
        if not path:
            return {}
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(path: str, state: dict) -> None:
    try:
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


# ------------------------------------------------------------
# Watch loops
# ------------------------------------------------------------
def _watch_log(cfg: Cfg) -> None:
    log_glob = os.path.join(cfg.log_dir, cfg.log_glob)
    current_file = ""
    file_pos = 0

    dedupe = Dedupe(cfg.dedupe_window_sec)
    limiter = RateLimiter(cfg.max_alerts_per_5min, cfg.min_seconds_between_alerts)

    last_event_ts = 0.0
    last_event_summary = ""
    last_heartbeat = 0.0

    if cfg.attach_message:
        tg_safe_send(cfg, f"[FX][WATCHER] attached (mode=log) dir={cfg.log_dir} glob={cfg.log_glob}")

    while True:
        try:
            newest = _pick_newest(log_glob)
            if newest and newest != current_file:
                current_file = newest
                try:
                    with open(current_file, "rb") as f:
                        if cfg.start_at == "tail":
                            f.seek(0, os.SEEK_END)
                            file_pos = f.tell()
                        else:
                            f.seek(0, os.SEEK_SET)
                            file_pos = f.tell()
                except Exception:
                    file_pos = 0

            if current_file:
                with open(current_file, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(file_pos)
                    while True:
                        line = f.readline()
                        if not line:
                            break
                        file_pos = f.tell()

                        evt = _extract_event_json_from_line(line, cfg.event_prefix)
                        if not evt:
                            continue

                        ev_name = str(evt.get("event", "") or "")
                        if cfg.include_events and ev_name and ev_name not in cfg.include_events:
                            continue
                        if cfg.exclude_events and ev_name and ev_name in cfg.exclude_events:
                            continue

                        if ev_name == "DECISION" and cfg.decision_require_side:
                            if not _event_side_ok(evt):
                                continue

                        kparts = [
                            ev_name,
                            str(evt.get("symbol", "")),
                            str(evt.get("side", "")),
                            str(evt.get("reason", "")),
                            str(evt.get("ticket", "")),
                        ]
                        dkey = _hash_key("|".join(kparts))
                        if dedupe.seen_recently(dkey):
                            continue

                        if not limiter.allow():
                            continue

                        msg = _fmt_event(cfg, evt, raw_line=line)
                        tg_safe_send(cfg, msg)

                        last_event_ts = _now_ts()
                        last_event_summary = f"{ev_name} {evt.get('symbol','')}".strip()

            if cfg.heartbeat_enabled:
                now = _now_ts()
                if now - last_heartbeat >= cfg.heartbeat_interval_sec:
                    parts = ["[FX][HEARTBEAT] watcher alive (mode=log)"]
                    if cfg.heartbeat_include_file:
                        parts.append(f"file: {_short_path(current_file) if current_file else '-'}")
                    if cfg.heartbeat_include_last_event and last_event_ts > 0:
                        ago = int(now - last_event_ts)
                        parts.append(f"last_event: {last_event_summary} ({ago}s ago)")
                    tg_safe_send(cfg, "\n".join(parts), tag="HEARTBEAT")
                    last_heartbeat = now

                if cfg.heartbeat_silence_alert_sec > 0 and last_event_ts > 0:
                    silence = now - last_event_ts
                    if silence >= cfg.heartbeat_silence_alert_sec:
                        parts = ["[FX][SILENCE] no events seen recently"]
                        if cfg.heartbeat_include_file:
                            parts.append(f"file: {_short_path(current_file) if current_file else '-'}")
                        parts.append(f"silence_sec: {int(silence)}")
                        if last_event_summary:
                            parts.append(f"last_event: {last_event_summary}")
                        tg_safe_send(cfg, "\n".join(parts), tag="SILENCE")
                        last_event_ts = now

        except Exception as e:
            print(f"[WATCHER][ERROR] {type(e).__name__}: {e}")

        time.sleep(cfg.poll_interval_sec)


def _watch_jsonl(cfg: Cfg) -> None:
    dedupe = Dedupe(cfg.dedupe_window_sec)
    limiter = RateLimiter(cfg.max_alerts_per_5min, cfg.min_seconds_between_alerts)

    state = _load_state(cfg.state_file)
    current_file = state.get("file", "")
    file_pos = int(state.get("pos", 0))

    last_event_ts = float(state.get("last_event_ts", 0.0))
    last_event_summary = str(state.get("last_event_summary", ""))

    last_heartbeat = 0.0

    if cfg.attach_message:
        tg_safe_send(cfg, f"[FX][WATCHER] attached (mode=jsonl) state={_short_path(cfg.state_file)}")

    while True:
        try:
            target = cfg.jsonl_file.strip()
            if not target and cfg.jsonl_glob.strip():
                target = _pick_newest(cfg.jsonl_glob.strip())

            if target and target != current_file:
                current_file = target
                try:
                    with open(current_file, "rb") as f:
                        if cfg.start_at == "tail":
                            f.seek(0, os.SEEK_END)
                            file_pos = f.tell()
                        else:
                            f.seek(0, os.SEEK_SET)
                            file_pos = f.tell()
                except Exception:
                    file_pos = 0

                state.update({"file": current_file, "pos": file_pos})
                _save_state(cfg.state_file, state)

            if current_file and os.path.exists(current_file):
                with open(current_file, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(file_pos)
                    while True:
                        line = f.readline()
                        if not line:
                            break
                        file_pos = f.tell()

                        evt = _jsonl_parse(line)
                        if not evt:
                            continue

                        ev_name = str(evt.get("event", "") or "")
                        if cfg.include_events and ev_name and ev_name not in cfg.include_events:
                            continue
                        if cfg.exclude_events and ev_name and ev_name in cfg.exclude_events:
                            continue

                        if ev_name == "DECISION" and cfg.decision_require_side:
                            if not _event_side_ok(evt):
                                continue

                        kparts = [
                            ev_name,
                            str(evt.get("symbol", "")),
                            str(evt.get("side", "")),
                            str(evt.get("reason", "")),
                            str(evt.get("ticket", "")),
                        ]
                        dkey = _hash_key("|".join(kparts))
                        if dedupe.seen_recently(dkey):
                            continue

                        if not limiter.allow():
                            continue

                        msg = _fmt_event(cfg, evt, raw_line=line)
                        tg_safe_send(cfg, msg)

                        last_event_ts = _now_ts()
                        last_event_summary = f"{ev_name} {evt.get('symbol','')}".strip()

                state.update(
                    {
                        "file": current_file,
                        "pos": file_pos,
                        "last_event_ts": last_event_ts,
                        "last_event_summary": last_event_summary,
                    }
                )
                _save_state(cfg.state_file, state)

            if cfg.heartbeat_enabled:
                now = _now_ts()
                if now - last_heartbeat >= cfg.heartbeat_interval_sec:
                    parts = ["[FX][HEARTBEAT] watcher alive (mode=jsonl)"]
                    if cfg.heartbeat_include_file:
                        parts.append(f"file: {_short_path(current_file) if current_file else '-'}")
                    if cfg.heartbeat_include_last_event and last_event_ts > 0:
                        ago = int(now - last_event_ts)
                        parts.append(f"last_event: {last_event_summary} ({ago}s ago)")
                    tg_safe_send(cfg, "\n".join(parts), tag="HEARTBEAT")
                    last_heartbeat = now

                if cfg.heartbeat_silence_alert_sec > 0 and last_event_ts > 0:
                    silence = now - last_event_ts
                    if silence >= cfg.heartbeat_silence_alert_sec:
                        parts = ["[FX][SILENCE] no events seen recently"]
                        if cfg.heartbeat_include_file:
                            parts.append(f"file: {_short_path(current_file) if current_file else '-'}")
                        parts.append(f"silence_sec: {int(silence)}")
                        if last_event_summary:
                            parts.append(f"last_event: {last_event_summary}")
                        tg_safe_send(cfg, "\n".join(parts), tag="SILENCE")
                        last_event_ts = now

        except Exception as e:
            print(f"[WATCHER][ERROR] {type(e).__name__}: {e}")

        time.sleep(cfg.poll_interval_sec)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(here, "fx_watcher.env")
    load_env_file(env_path)

    enabled = _bool_env("TELEGRAM_ENABLED", True)
    cfg = load_cfg()

    if not enabled:
        print("[WATCHER] TELEGRAM_ENABLED=false (exiting)")
        return

    if not cfg.tg_token or not cfg.tg_chat_id:
        print("[WATCHER] Missing TG_BOT_TOKEN or TG_CHAT_ID in env (exiting)")
        return

    mode = cfg.watch_mode
    if mode not in ("log", "jsonl"):
        mode = "log"

    if mode == "jsonl":
        _watch_jsonl(cfg)
    else:
        _watch_log(cfg)


if __name__ == "__main__":
    main()