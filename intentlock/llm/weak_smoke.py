"""`make weak-smoke`: one tool-calling turn on the weak baseline agent's model (SPEC §4, §13.2).
Bypasses the cache and never prints the key."""

import sys

import httpx

from intentlock.config import Settings
from intentlock.llm.client import LLMClient, LLMError

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_products",
        "description": "Search the shop catalogue.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


def available_models(base_url: str, api_key: str) -> list[str]:
    """The model IDs the endpoint serves (OpenAI-compatible GET /models), to diagnose a 404."""
    try:
        r = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return [f"(could not list models: {type(exc).__name__})"]


def main(client: LLMClient | None = None) -> int:
    settings = Settings.from_env()
    if client is None:
        if not settings.weak_model_api_key:
            print("WEAK_MODEL_API_KEY is not set in .env")
            return 2
        try:
            client = LLMClient.weak(settings)
        except LLMError as exc:
            print(f"weak smoke FAILED: {exc}")
            return 2
    messages = [
        {"role": "system", "content": "You are a shopping assistant. Use the tools."},
        {"role": "user", "content": "Find me a wireless mouse."},
    ]
    try:
        reply = client.chat(messages, [SEARCH_TOOL], use_cache=False)
    except LLMError as exc:
        print(f"weak smoke FAILED: {exc}")
        print(f"base_url={settings.weak_model_base_url!r} model={settings.weak_model_id!r}")
        if "NotFound" in str(exc) and settings.weak_model_base_url:
            ids = available_models(settings.weak_model_base_url, settings.weak_model_api_key)
            print("models this endpoint serves:", ", ".join(ids) or "none")
        return 1
    calls = reply.get("tool_calls") or []
    print(f"model={client.default_model} tool_calls={[(c['name'], c['arguments']) for c in calls]}")
    if not any(c["name"] == "search_products" for c in calls):
        print("weak smoke FAILED: the model answered without calling the tool")
        return 1
    print("weak smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
