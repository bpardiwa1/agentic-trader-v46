# ============================================================
# Agentic Trader IDX v4.6 — Environment Loader
# Mirrors XAU v4.6 logic
# ============================================================

from __future__ import annotations
import os
from dotenv import load_dotenv

class EnvNamespace:
    def __init__(self, env_file: str | None = None):
        # Explicitly load idx_v46.env from same folder unless overridden
        env_path = env_file or os.path.join(os.path.dirname(__file__), "idx_v46.env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
            print(f"[INFO] Environment loaded from {env_path}")
        else:
            print(f"[WARN] Env file not found: {env_path}")
        self._env = dict(os.environ)
        for k, v in self._env.items():
            setattr(self, k.lower(), v)

        # convenience pre-cast for confidence
        self.min_conf = float(self.get("AGENT_MIN_CONFIDENCE", 0.55))
        self.AGENT_MIN_CONFIDENCE = self.min_conf

    def get(self, key: str, default=None):
        for variant in (key, key.upper(), key.lower()):
            if variant in self._env:
                raw = self._env[variant]
                return self._cast(raw, default)
        return default

    def _cast(self, raw: str, default):
        if default is None:
            return raw
        if isinstance(default, bool):
            return str(raw).lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            try:
                return int(float(raw))
            except:
                return default
        if isinstance(default, float):
            try:
                return float(raw)
            except:
                return default
        return str(raw)
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        """Safe boolean getter."""
        val = str(self.get(key, default)).strip().lower()
        return val in ("1", "true", "yes", "on")

    def __getattr__(self, key):
        return self._env.get(key.upper()) or self._env.get(key.lower())


# Global environment singleton
ENV = EnvNamespace()
