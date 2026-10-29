"""Settings read from environment variables (SPEC §4, §16; docs/SETUP.md §4).

Nothing here reads SHOP_API_KEY: only intentlock/connector/ may (CLAUDE.md invariant 8). Secrets are
excluded from `repr` so they cannot leak through logging or tracebacks.
"""

import os
from dataclasses import dataclass, field
from typing import Literal, cast

ConfigName = Literal["none", "r2_session", "intentlock"]
CONFIGS: tuple[str, ...] = ("none", "r2_session", "intentlock")


@dataclass(frozen=True)
class Settings:
    model_api_key: str = field(default="", repr=False)
    model_base_url: str = "https://api.meta.ai/v1"
    model_id: str = "muse-spark-1.3"
    model_id_eval: str = "muse-spark-1.3-contributor"
    llm_cache_dir: str = ".cache/llm"
    config: ConfigName = "intentlock"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        config = e.get("CONFIG") or "intentlock"
        if config not in CONFIGS:
            raise ValueError(f"CONFIG must be one of {CONFIGS}, got {config!r}")
        return cls(
            model_api_key=e.get("MODEL_API_KEY", ""),
            model_base_url=e.get("MODEL_BASE_URL") or cls.model_base_url,
            model_id=e.get("MODEL_ID") or cls.model_id,
            model_id_eval=e.get("MODEL_ID_EVAL") or cls.model_id_eval,
            llm_cache_dir=e.get("LLM_CACHE_DIR") or cls.llm_cache_dir,
            config=cast(ConfigName, config),
        )
