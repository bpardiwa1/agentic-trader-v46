# xau_v46/app/xau_env_v46.py
from __future__ import annotations
import os
from dotenv import load_dotenv

class EnvNamespace:
    def __init__(self, env_file: str | None = None):
        env_path = env_file or os.path.join(os.path.dirname(__file__), "xau_v46.env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
            print(f"[INFO] Environment loaded from {env_path}")
        else:
            print(f"[WARN] Env file not found: {env_path}")
        self._env = dict(os.environ)
        for k, v in self._env.items():
            setattr(self, k.lower(), v)
        self.min_conf = float(self.get("AGENT_MIN_CONFIDENCE", 0.55))
        self.AGENT_MIN_CONFIDENCE = self.min_conf

    def get(self, key: str, default=None):
        for variant in (key, key.upper(), key.lower()):
            if variant in self._env:
                raw = self._env[variant]
                return self._cast(raw, default)
        return default

    def _cast(self, raw: str, default):
        if default is None: return raw
        if isinstance(default, bool): return str(raw).lower() in ("1","true","yes","on")
        if isinstance(default, int):
            try: return int(float(raw))
            except: return default
        if isinstance(default, float):
            try: return float(raw)
            except: return default
        return str(raw)

    def __getattr__(self, key): return self._env.get(key.upper()) or self._env.get(key.lower())

ENV = EnvNamespace()

def _safe_cast_env():
    ENV.agent_max_open = int(float(ENV.get("AGENT_MAX_OPEN", 5)))
    ENV.agent_max_per_symbol = int(float(ENV.get("AGENT_MAX_PER_SYMBOL", 2)))
    ENV.agent_min_confidence = float(ENV.get("AGENT_MIN_CONFIDENCE", 0.55))
    ENV.cooldown_sec = int(float(ENV.get("COOLDOWN_SEC", 300)))
    ENV.fx_min_lots = float(ENV.get("FX_MIN_LOTS", 0.03))
    ENV.fx_max_lots = float(ENV.get("FX_MAX_LOTS", 0.50))

_safe_cast_env()
