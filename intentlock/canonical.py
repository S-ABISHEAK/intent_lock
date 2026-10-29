"""Canonical JSON and hashing (SPEC §5).

`canonical()` is the only way anything is serialized for hashing or signing. It raises on floats,
non-string keys and non-JSON types instead of coercing them (CLAUDE.md invariant 4).
"""

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from intentlock.models import Quote


def _check(value: Any, path: str) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        raise TypeError(f"float is not allowed in canonical input (at {path}); use integer paise")
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"non-string key {key!r} in canonical input (at {path})")
            _check(item, f"{path}.{key}")
        return
    raise TypeError(f"{type(value).__name__} is not allowed in canonical input (at {path})")


def _plain(obj: Any, exclude: Iterable[str] = ()) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude=set(exclude))
    if exclude:
        if not isinstance(obj, dict):
            raise TypeError("exclude is only supported for models and dicts")
        return {k: v for k, v in obj.items() if k not in set(exclude)}
    return obj


def canonical(obj: Any) -> str:
    data = _plain(obj)
    _check(data, "$")
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(obj: Any, exclude: Iterable[str] = ()) -> str:
    return hashlib.sha256(canonical(_plain(obj, exclude)).encode("utf-8")).hexdigest()


def compute_quote_hash(quote: Quote) -> str:
    """sha256 of canonical(quote minus quote_hash) (SPEC §5)."""
    return canonical_hash(quote, exclude={"quote_hash"})
