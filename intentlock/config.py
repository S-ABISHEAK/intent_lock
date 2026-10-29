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
    weak_model_base_url: str = ""
    weak_model_api_key: str = field(default="", repr=False)
    weak_model_id: str = ""
    shop_url: str = "http://localhost:8001"
    approver_url: str = "http://localhost:8002"
    gateway_url: str = "http://localhost:8000"
    gateway_db_dir: str = ".cache/runs"
    approver_public_key_path: str = "keys/approver.pub"
    features: frozenset[int] = frozenset()  # optional pipeline steps 8-9 (D-021)

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
            weak_model_base_url=e.get("WEAK_MODEL_BASE_URL", ""),
            weak_model_api_key=e.get("WEAK_MODEL_API_KEY", ""),
            weak_model_id=e.get("WEAK_MODEL_ID", ""),
            shop_url=e.get("SHOP_URL") or cls.shop_url,
            approver_url=e.get("APPROVER_URL") or cls.approver_url,
            gateway_url=e.get("GATEWAY_URL") or cls.gateway_url,
            gateway_db_dir=e.get("GATEWAY_DB_DIR") or cls.gateway_db_dir,
            approver_public_key_path=e.get("APPROVER_PUBLIC_KEY_PATH")
            or cls.approver_public_key_path,
            features=frozenset(
                step
                for step, var in ((8, "TWO_CHANNEL"), (9, "PROMPT_GUARD"))
                if e.get(var, "off").lower() in ("1", "on", "true", "yes")
            ),
        )
