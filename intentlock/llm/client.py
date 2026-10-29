"""The only LLM entry point (SPEC §4, CLAUDE.md "Workflow"). Only file allowed to import openai.

`structured()` sends a JSON-schema response format, validates the reply with Pydantic, retries
twice, and caches validated replies on disk keyed by sha256(model, messages, schema). If the
endpoint rejects `response_format`, it falls back to a JSON-only prompt (docs/SETUP.md §6, D-008).
Model output is data: callers may only use it to narrow or escalate (D-003).
"""

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from intentlock.config import Settings

T = TypeVar("T", bound=BaseModel)

MAX_RETRIES = 2
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class LLMError(RuntimeError):
    """Raised after retries are exhausted. Messages never contain credentials."""


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self.settings = settings
        self.default_model = default_model or settings.model_id
        self._client = client or openai.OpenAI(
            base_url=base_url or settings.model_base_url,
            api_key=(api_key if api_key is not None else settings.model_api_key) or "unset",
        )
        self._sleep = sleep
        self.mode = "json_schema"  # becomes "json_only" if the endpoint rejects response_format
        self.cache_hits = 0
        self.cache_misses = 0

    @classmethod
    def weak(cls, settings: Settings, **kwargs: Any) -> "LLMClient":
        """The weak open-weights baseline agent (SPEC §4) on its own OpenAI-compatible endpoint."""
        if not (settings.weak_model_base_url and settings.weak_model_id):
            raise LLMError("weak model not configured (WEAK_MODEL_BASE_URL / WEAK_MODEL_ID)")
        return cls(
            settings,
            base_url=settings.weak_model_base_url,
            api_key=settings.weak_model_api_key,
            default_model=settings.weak_model_id,
            **kwargs,
        )

    # -- cache -------------------------------------------------------------------------------
    def _cache_path(self, model: str, messages: list[dict], schema: dict) -> Path:
        blob = json.dumps(
            {"model": model, "messages": messages, "schema": schema},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        key = hashlib.sha256(blob.encode()).hexdigest()
        return Path(self.settings.llm_cache_dir) / f"{key}.json"

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)

    def _write_cache(self, path: Path, content: str) -> None:
        self._write_json(path, {"content": content})

    def _read_cache(self, path: Path, schema_cls: type[T]) -> T | None:
        """Return a validated cached reply, or None if absent, unreadable or stale."""
        try:
            return self._parse(json.loads(path.read_text(encoding="utf-8"))["content"], schema_cls)
        except (OSError, KeyError, ValueError):
            return None

    # -- request -----------------------------------------------------------------------------
    def _request_kwargs(
        self, model: str, messages: list[dict], schema_cls: type[BaseModel]
    ) -> dict:
        schema = schema_cls.model_json_schema()
        if self.mode == "json_schema":
            return {
                "model": model,
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_cls.__name__, "schema": schema},
                },
            }
        instruction = (
            "Reply with a single JSON object only, no prose and no code fences, that validates "
            f"against this JSON schema:\n{json.dumps(schema, sort_keys=True)}"
        )
        return {"model": model, "messages": [{"role": "system", "content": instruction}, *messages]}

    @staticmethod
    def _rejects_response_format(exc: openai.BadRequestError) -> bool:
        text = str(exc).lower()
        return any(
            w in text for w in ("response_format", "json_schema", "structured", "unsupported")
        )

    def _call_once(self, model: str, messages: list[dict], schema_cls: type[BaseModel]) -> str:
        kwargs = self._request_kwargs(model, messages, schema_cls)
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except openai.BadRequestError as exc:
            if self.mode == "json_schema" and self._rejects_response_format(exc):
                self.mode = "json_only"
                kwargs = self._request_kwargs(model, messages, schema_cls)
                resp = self._client.chat.completions.create(**kwargs)
            else:
                raise
        content = resp.choices[0].message.content
        if not content:
            raise ValueError("empty model response")
        return content

    @staticmethod
    def _parse(content: str, schema_cls: type[T]) -> T:
        text = content.strip()
        m = _FENCE.match(text)
        if m:
            text = m.group(1)
        return schema_cls.model_validate(json.loads(text))

    def structured(
        self,
        messages: list[dict],
        schema_cls: type[T],
        *,
        model: str | None = None,
        use_cache: bool = True,
    ) -> T:
        model = model or self.default_model
        path = self._cache_path(model, messages, schema_cls.model_json_schema())
        if use_cache:
            cached = self._read_cache(path, schema_cls)
            if cached is not None:
                self.cache_hits += 1
                return cached
        self.cache_misses += 1

        attempt_messages = list(messages)
        last_error = "unknown error"
        for attempt in range(MAX_RETRIES + 1):
            try:
                content = self._call_once(model, attempt_messages, schema_cls)
                parsed = self._parse(content, schema_cls)
            except (ValueError, ValidationError) as exc:  # bad JSON / schema mismatch
                last_error = f"invalid structured output: {type(exc).__name__}"
                attempt_messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": "Your previous reply was not valid for the schema. "
                        f"Reply again with only valid JSON. Problem: {str(exc)[:300]}",
                    },
                ]
            except openai.OpenAIError as exc:  # transport, rate limit, auth, bad request
                last_error = f"API error: {type(exc).__name__}"
                if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
                    break
            else:
                if use_cache:
                    self._write_cache(path, content)
                return parsed
            if attempt < MAX_RETRIES:
                self._sleep(0.5 * (attempt + 1))
        raise LLMError(f"LLM call failed after {MAX_RETRIES + 1} attempts: {last_error}")

    # -- tool-calling chat for the agent loop (SPEC §15) -----------------------------------------
    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        model: str | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """One assistant turn: {"content": str | None, "tool_calls": [{id, name, arguments}]}."""
        model = model or self.default_model
        path = self._cache_path(model, messages, {"chat_tools": tools})
        if use_cache:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))["reply"]
                self.cache_hits += 1
                return cached
            except (OSError, KeyError, ValueError):
                self.cache_misses += 1
        last_error = "unknown error"
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=model, messages=messages, tools=tools
                )
                msg = resp.choices[0].message
                reply = {
                    "content": msg.content,
                    "tool_calls": [
                        {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                        for c in (msg.tool_calls or [])
                    ],
                }
            except openai.OpenAIError as exc:
                last_error = f"API error: {type(exc).__name__}"
                if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
                    break
            except (AttributeError, IndexError, TypeError) as exc:
                last_error = f"malformed response: {type(exc).__name__}"
            else:
                if use_cache:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._write_json(path, {"reply": reply})
                return reply
            if attempt < MAX_RETRIES:
                self._sleep(0.5 * (attempt + 1))
        raise LLMError(f"LLM chat failed after {MAX_RETRIES + 1} attempts: {last_error}")
