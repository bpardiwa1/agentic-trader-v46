# ============================================================
# Agentic Trader idx_v46 — Environment Loader (IDX_* only)
# ============================================================

from __future__ import annotations
import os
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore


class EnvNamespace:
    def __init__(self, env_file: str | None = None):
        default_path = Path(__file__).resolve().parent / "idx_v46.env"
        env_path = Path(env_file).resolve() if env_file else default_path

        if load_dotenv and env_path.exists():
            load_dotenv(env_path, override=True)
            print(f"[ENV] Loaded: {env_path}")
        else:
            if not env_path.exists():
                print(f"[ENV] (warn) file not found: {env_path}")
            else:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if not line or line.strip().startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
                print(f"[ENV] Parsed: {env_path}")
        self._env = dict(os.environ)

    def get(self, key: str, default=None):
        for k in (key, key.upper(), key.lower()):
            if k in self._env:
                raw = self._env[k]
                return self._cast(raw, default)
        return default

    def _cast(self, raw: str, default):
        if default is None:
            return raw
        if isinstance(default, bool):
            return str(raw).lower() in ("1","true","yes","on")
        if isinstance(default, int):
            try: return int(float(raw))
            except Exception: return default
        if isinstance(default, float):
            try: return float(raw)
            except Exception: return default
        return str(raw)


ENV = EnvNamespace()
