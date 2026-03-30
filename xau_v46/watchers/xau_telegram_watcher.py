# xau_v46/watchers/xau_telegram_watcher.py
# ============================================================
# Agentic Trader XAU v4.6 — Telegram Watcher (LOG or JSONL)
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
#   - Loads env vars from: xau_v46/watchers/xau_watcher.env
# ============================================================

from __future__ import annotations

import os
import time
import glob
import json
import hashlib
from dataclasses import dataclass
from collections import deque
from typing import Optional
from urllib import request, parse


# ------------------------------------------------------------
# State persistence (offset)
# ------------------------------------------------------------
def load_state(path: str) -> dict:
    try:
        if not path:
            return {"offset": 0}
        if not os.path.exists(path):
            return {"offset": 0}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {"offset": 0}
    except Exception:
        return {"offset": 0}


def save_state(path: str, state: dict) -> None:
    try:
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ------------------------------------------------------------
# Env file loader (simple .env parser; no deps)
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
            if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
                v = v[1:-1]

            if k and k not in os.environ:
                os.environ[k] = v


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _bool_env(name: str, default: bool = False) -> bool:
    val = _env(name, "true" if default else "false").lower()
    return val in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return default


def _csv_set(name: str, default_csv: str = "") -> set[str]:
    raw = _env(name, default_csv)
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    return set(parts)


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
        log_dir=_env("WATCH_LOG_DIR", r"logs\xau_v4.6"),
        log_glob=_env("WATCH_LOG_GLOB", "xau_v46_*.log"),
        event_prefix=_env("WATCH_EVENT_PREFIX", "EVENT").strip(),

        # JSONL mode
        jsonl_file=_env("WATCH_JSONL_FILE", ""),
        jsonl_glob=_env("WATCH_JSONL_GLOB", ""),
        state_file=_env("WATCH_STATE_FILE", r"logs\xau_v4.6\xau_watcher.state.json"),

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
# Event parsing helpers (LOG mode)
# ------------------------------------------------------------
def _event_marker(cfg: Cfg) -> str:
    prefix = (cfg.event_prefix.strip() or "EVENT")
    return f" {prefix} "


def is_event_line(cfg: Cfg, line: str) -> bool:
    marker = _event_marker(cfg)
    prefix = (cfg.event_prefix.strip() or "EVENT")
    return (marker in line) or line.lstrip().startswith(prefix + " ")


def parse_event_json_from_logline(cfg: Cfg, line: str) -> Optional[dict]:
    prefix = (cfg.event_prefix.strip() or "EVENT")
    marker = _event_marker(cfg)

    if marker in line:
        raw_json = line.rsplit(marker, 1)[-1].strip()
    else:
        raw = line.strip()
        if not raw.startswith(prefix + " "):
            return None
        raw_json = raw[len(prefix):].strip()

    if not raw_json.startswith("{"):
        return None

    try:
        obj = json.loads(raw_json)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# ------------------------------------------------------------
# Event object helpers
# ------------------------------------------------------------
def event_name(obj: dict) -> str:
    return str(obj.get("event", "") or "").upper().strip()


def payload(obj: dict) -> dict:
    p = obj.get("payload")
    return p if isinstance(p, dict) else {}


def should_alert(cfg: Cfg, obj: dict) -> bool:
    ev = event_name(obj)
    if not ev:
        return False

    if cfg.include_events and ev not in cfg.include_events:
        return False

    if cfg.exclude_events and ev in cfg.exclude_events:
        return False

    if ev == "DECISION" and cfg.decision_require_side:
        p = payload(obj)
        side = str(p.get("side", "") or "").upper().strip()
        if side not in ("LONG", "SHORT"):
            return False

    return True


# ------------------------------------------------------------
# Dedupe / rate limiting
# ------------------------------------------------------------
def dedupe_key(obj: dict) -> str:
    ev = event_name(obj)
    p = payload(obj)

    for k in ("ticket", "order", "deal", "request_id"):
        v = p.get(k)
        if v:
            base = f"{ev}:{k}:{v}"
            return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()

    cycle_id = p.get("cycle_id")
    symbol = p.get("symbol")
    if cycle_id and symbol:
        base = f"{ev}:cycle:{cycle_id}:sym:{symbol}"
        return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()

    base = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()


# ------------------------------------------------------------
# Message formatting
# ------------------------------------------------------------
def compact(obj: dict, cfg: Cfg) -> str:
    ts = str(obj.get("ts", "") or "")
    asset = str(obj.get("asset", "XAU") or "XAU")
    ev = event_name(obj)
    p = payload(obj)

    symbol = str(p.get("symbol", "") or "")
    side = str(p.get("side", "") or "")
    status = str(p.get("status", "") or "")

    header_parts = [asset, ev]
    if symbol:
        header_parts.append(symbol)
    if side:
        header_parts.append(side)
    if status:
        header_parts.append(status)

    lines = [" | ".join(header_parts)]
    if ts:
        lines.append(f"ts={ts}")

    keys_order = ["cycle_id", "confidence", "policy", "atr_regime", "session", "ticket", "lots", "retcode", "comment", "reason", "error"]
    for k in keys_order:
        if k in p and p.get(k) not in (None, "", [], {}):
            v = p.get(k)
            if isinstance(v, list):
                v = ", ".join([str(x) for x in v[:12]]) + (" ..." if len(v) > 12 else "")
            lines.append(f"{k}={v}")

    if "why" in p and p.get("why"):
        why = p.get("why")
        if isinstance(why, list):
            why_txt = ", ".join([str(x) for x in why[:15]]) + (" ..." if len(why) > 15 else "")
        else:
            why_txt = str(why)
        lines.append(f"why={why_txt}")

    if cfg.show_raw_line:
        lines.append("")
        lines.append("raw_payload=" + json.dumps(p, ensure_ascii=False, default=str))

    msg = "\n".join(lines)
    if len(msg) > cfg.max_message_chars:
        msg = msg[: cfg.max_message_chars] + "…"
    return msg


# ------------------------------------------------------------
# Heartbeat
# ------------------------------------------------------------
def fmt_age(seconds: float) -> str:
    if seconds < 0:
        return "n/a"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"


def heartbeat_message(cfg: Cfg, current_file: Optional[str], last_event_obj: Optional[dict], last_event_ts: float) -> str:
    parts = ["XAU watcher heartbeat ✅"]

    if cfg.heartbeat_include_file:
        parts.append(f"file={(os.path.basename(current_file) if current_file else 'none')}")

    if cfg.heartbeat_include_last_event:
        if last_event_obj is None:
            parts.append("last_event=none")
        else:
            ev = event_name(last_event_obj)
            p = payload(last_event_obj)
            sym = str(p.get("symbol", "") or "")
            ago = fmt_age(time.time() - last_event_ts) if last_event_ts > 0 else "n/a"
            label = f"{ev}{(' ' + sym) if sym else ''}"
            parts.append(f"last_event={label} ({ago} ago)")

    return " | ".join(parts)


# ------------------------------------------------------------
# Attach helpers
# ------------------------------------------------------------
def find_latest_by_glob(pattern: str) -> Optional[str]:
    if not pattern:
        return None
    files = glob.glob(pattern)
    if not files:
        return None
    files.sort(key=lambda p: os.path.getmtime(p))
    return files[-1]


def initial_offset(start_at: str, path: str) -> int:
    if start_at == "head":
        return 0
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base_dir, "xau_watcher.env")
    load_env_file(env_path)

    cfg = load_cfg()

    print(f"[WATCHER] Loaded env file: {env_path}")
    print(f"[WATCHER] WATCH_MODE: {cfg.watch_mode}")
    print(f"[WATCHER] WATCH_START_AT: {cfg.start_at}")
    print(f"[WATCHER] WATCH_STATE_FILE: {cfg.state_file}")

    if not cfg.tg_token or not cfg.tg_chat_id:
        raise SystemExit("Missing TG_BOT_TOKEN or TG_CHAT_ID in xau_watcher.env")

    seen: dict[str, float] = {}
    alert_times = deque()
    last_sent = 0.0

    # Heartbeat state
    last_heartbeat = 0.0
    last_event_seen_at = 0.0
    last_event_obj: Optional[dict] = None
    silence_alert_sent_at = 0.0

    # Attach state
    current_file: Optional[str] = None
    offset = 0

    # Load persisted offset (JSONL mode only)
    persisted = load_state(cfg.state_file)
    persisted_offset = int(persisted.get("offset", 0) or 0)

    while True:
        now = time.time()

        # Heartbeat tick
        if cfg.heartbeat_enabled and cfg.heartbeat_interval_sec > 0:
            if last_heartbeat == 0.0:
                last_heartbeat = now
            elif (now - last_heartbeat) >= cfg.heartbeat_interval_sec:
                hb = heartbeat_message(cfg, current_file, last_event_obj, last_event_seen_at)
                tg_safe_send(cfg, hb, tag="HEARTBEAT")
                print("[WATCHER] Sent heartbeat")
                last_heartbeat = now

        # Silence alert tick
        if cfg.heartbeat_enabled and cfg.heartbeat_silence_alert_sec > 0:
            if last_event_seen_at > 0 and (now - last_event_seen_at) >= cfg.heartbeat_silence_alert_sec:
                if silence_alert_sent_at == 0.0 or (now - silence_alert_sent_at) >= cfg.heartbeat_silence_alert_sec:
                    msg = f"XAU watcher silence alert ⚠️ | no events for {fmt_age(now - last_event_seen_at)}"
                    if current_file and cfg.heartbeat_include_file:
                        msg += f" | file={os.path.basename(current_file)}"
                    tg_safe_send(cfg, msg, tag="SILENCE")
                    print("[WATCHER] Sent silence alert")
                    silence_alert_sent_at = now

        # -------------------------
        # Choose file to watch
        # -------------------------
        if cfg.watch_mode == "jsonl":
            # Prefer explicit file; fallback to glob
            candidate = cfg.jsonl_file.strip()
            if not candidate and cfg.jsonl_glob.strip():
                candidate = find_latest_by_glob(cfg.jsonl_glob.strip()) or ""

            if candidate and candidate != current_file:
                current_file = candidate
                if os.path.exists(current_file):
                    # Use persisted offset if available, otherwise start_at
                    if persisted_offset > 0:
                        offset = persisted_offset
                    else:
                        offset = initial_offset(cfg.start_at, current_file)

                    print(f"[WATCHER] Attached JSONL: {current_file} (offset={offset}, start_at={cfg.start_at})")
                    if cfg.attach_message:
                        tg_safe_send(cfg, f"XAU watcher attached JSONL: {os.path.basename(current_file)} (start_at={cfg.start_at})", tag="TELEGRAM")
                else:
                    # file not yet present
                    print(f"[WATCHER] Waiting for JSONL file: {current_file}")
                    current_file = None
                    time.sleep(2)
                    continue

            if not current_file:
                time.sleep(2)
                continue

            # -------------------------
            # Tail JSONL
            # -------------------------
            try:
                with open(current_file, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(offset)

                    while True:
                        line = f.readline()
                        if not line:
                            break

                        offset = f.tell()
                        save_state(cfg.state_file, {"offset": offset})

                        line = line.strip()
                        if not line or not line.startswith("{"):
                            continue

                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue

                        if not isinstance(obj, dict):
                            continue

                        last_event_seen_at = time.time()
                        last_event_obj = obj

                        if not should_alert(cfg, obj):
                            continue

                        now2 = time.time()

                        # purge dedupe memory
                        for k, ts in list(seen.items()):
                            if now2 - ts > cfg.dedupe_window_sec:
                                seen.pop(k, None)

                        # 5-minute rate limit
                        while alert_times and now2 - alert_times[0] > 300:
                            alert_times.popleft()
                        if len(alert_times) >= cfg.max_alerts_per_5min:
                            continue

                        if now2 - last_sent < cfg.min_seconds_between_alerts:
                            continue

                        key = dedupe_key(obj)
                        if key in seen:
                            continue

                        msg = compact(obj, cfg)
                        tg_safe_send(cfg, msg, tag="TELEGRAM")
                        seen[key] = now2
                        alert_times.append(now2)
                        last_sent = now2

            except FileNotFoundError:
                current_file = None
                offset = 0
            except Exception as e:
                print(f"[WATCHER][ERROR] {type(e).__name__}: {e}")
                time.sleep(2)

            time.sleep(cfg.poll_interval_sec)
            continue

        # -------------------------
        # LOG mode (legacy)
        # -------------------------
        latest = find_latest_by_glob(os.path.join(cfg.log_dir, cfg.log_glob))
        if latest and latest != current_file:
            current_file = latest
            offset = initial_offset(cfg.start_at, current_file)
            print(f"[WATCHER] Attached LOG: {current_file} (offset={offset}, start_at={cfg.start_at})")
            if cfg.attach_message:
                tg_safe_send(cfg, f"XAU watcher attached LOG: {os.path.basename(current_file)} (start_at={cfg.start_at})", tag="TELEGRAM")

        if not current_file:
            time.sleep(2)
            continue

        try:
            with open(current_file, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(offset)

                while True:
                    line = f.readline()
                    if not line:
                        break

                    offset = f.tell()

                    if not is_event_line(cfg, line):
                        continue

                    obj = parse_event_json_from_logline(cfg, line)
                    if not obj:
                        continue

                    last_event_seen_at = time.time()
                    last_event_obj = obj

                    if not should_alert(cfg, obj):
                        continue

                    now2 = time.time()

                    for k, ts in list(seen.items()):
                        if now2 - ts > cfg.dedupe_window_sec:
                            seen.pop(k, None)

                    while alert_times and now2 - alert_times[0] > 300:
                        alert_times.popleft()
                    if len(alert_times) >= cfg.max_alerts_per_5min:
                        continue

                    if now2 - last_sent < cfg.min_seconds_between_alerts:
                        continue

                    key = dedupe_key(obj)
                    if key in seen:
                        continue

                    msg = compact(obj, cfg)
                    tg_safe_send(cfg, msg, tag="TELEGRAM")
                    seen[key] = now2
                    alert_times.append(now2)
                    last_sent = now2

        except FileNotFoundError:
            current_file = None
            offset = 0
        except Exception as e:
            print(f"[WATCHER][ERROR] {type(e).__name__}: {e}")
            time.sleep(2)

        time.sleep(cfg.poll_interval_sec)


if __name__ == "__main__":
    main()
