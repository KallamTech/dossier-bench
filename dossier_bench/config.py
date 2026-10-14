from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "runs_dir": "runs",
    "baselines_dir": "baselines",
    # any OpenAI-compatible endpoint; price_per_m is USD per million tokens, for the judge's own cost line
    "judge": {"base_url": "https://api.openai.com/v1", "api_key": "", "model": "", "price_per_m": {"input": 0, "output": 0}},
    # `module:Class` of the system under test, plus keyword arguments for its constructor
    "adapter": {"class": "dossier_bench.baselines.llm:LLMBaseline", "options": {}},
    "thresholds": {
        "quality": {"min_abs": 0.6, "max_drop": 0.10},
        "latency": {"max_increase_pct": 25},
        "cost": {"max_increase_pct": 20},
    },
}

ENV = {
    "DOSSIER_JUDGE_BASE_URL": ("judge", "base_url"),
    "DOSSIER_JUDGE_API_KEY": ("judge", "api_key"),
    "DOSSIER_JUDGE_MODEL": ("judge", "model"),
}


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


class Config:
    defaults: dict[str, Any] = DEFAULTS
    env: dict[str, tuple[str, str]] = ENV
    filename = "dossier-bench.yaml"

    def __init__(self, data: dict[str, Any] | None = None):
        self.data = merge(self.defaults, data or {})

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        p = Path(path or os.environ.get("DOSSIER_CONFIG") or cls.filename)
        cfg = cls(yaml.safe_load(p.read_text()) if p.exists() else {})
        cfg.apply_env()
        return cfg

    def apply_env(self) -> None:
        for env, keys in self.env.items():
            if os.environ.get(env):
                self.data.setdefault(keys[0], {})[keys[1]] = os.environ[env]

    def __getitem__(self, k: str) -> Any:
        return self.data[k]

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def require(self, *keys: str) -> Any:
        v = self.get(*keys)
        if v in (None, ""):
            raise SystemExit(f"config: {'.'.join(keys)} is required for this command")
        return v

    def snapshot(self) -> dict[str, Any]:
        """Config without secrets, for run metadata."""
        def mask(d: dict) -> dict:
            return {k: mask(v) if isinstance(v, dict) else ("***" if "key" in k.lower() and v else v) for k, v in d.items()}

        return mask(self.data)
