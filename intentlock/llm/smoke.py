"""`make smoke`: one muse-spark structured-output call (docs/SETUP.md §6). Bypasses the cache."""

import sys

from pydantic import BaseModel

from intentlock.config import Settings
from intentlock.llm.client import LLMClient, LLMError


class SmokeResult(BaseModel):
    product: str
    price_paise: int
    in_stock: bool


def main() -> int:
    settings = Settings.from_env()
    if not settings.model_api_key:
        print("MODEL_API_KEY is not set. Fill it in .env, then run `make smoke` again.")
        return 2
    client = LLMClient(settings)
    messages = [
        {
            "role": "user",
            "content": "Extract the product, its price in integer paise (₹1 = 100 paise) and "
            "whether it is in stock from: 'Logitech M221 mouse, ₹1,499, in stock.'",
        }
    ]
    try:
        result = client.structured(messages, SmokeResult, use_cache=False)
    except LLMError as exc:
        print(f"smoke FAILED: {exc}")
        return 1
    print(f"model={settings.model_id} mode={client.mode}")
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
