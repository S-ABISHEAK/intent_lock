"""LLM client (SPEC §4): validation, 2 retries, disk cache, JSON-only fallback, no key leaks."""

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

import intentlock.llm.client as llm_module  # openai is imported only inside the client
from intentlock.config import Settings
from intentlock.llm.client import MAX_RETRIES, LLMClient, LLMError

SECRET = "sk-test-SECRET-1234567890"


class Out(BaseModel):
    product: str
    price_paise: int


GOOD = json.dumps({"product": "mouse", "price_paise": 149900})
REQ = httpx.Request("POST", "http://llm.test/v1/chat/completions")


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def bad_request(msg):
    return llm_module.openai.BadRequestError(
        msg, response=httpx.Response(400, request=REQ), body=None
    )


class FakeOpenAI:
    """Scripted chat.completions.create: each item is a reply content string or an exception."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return reply(item)


def make(tmp_path, script):
    fake = FakeOpenAI(script)
    settings = Settings(model_api_key=SECRET, llm_cache_dir=str(tmp_path / "cache"))
    return LLMClient(settings, client=fake, sleep=lambda s: None), fake


MSG = [{"role": "user", "content": "extract"}]


def test_returns_validated_object_and_sends_json_schema_format(tmp_path):
    client, fake = make(tmp_path, [GOOD])
    out = client.structured(MSG, Out)
    assert out == Out(product="mouse", price_paise=149900)
    fmt = fake.calls[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"] == Out.model_json_schema()
    assert fake.calls[0]["model"] == "muse-spark-1.3"


def test_model_override_is_used(tmp_path):
    client, fake = make(tmp_path, [GOOD])
    client.structured(MSG, Out, model="muse-spark-1.3-contributor")
    assert fake.calls[0]["model"] == "muse-spark-1.3-contributor"


def test_invalid_output_is_retried_then_succeeds(tmp_path):
    client, fake = make(tmp_path, ["not json", json.dumps({"product": "x"}), GOOD])
    assert client.structured(MSG, Out).price_paise == 149900
    assert len(fake.calls) == 3  # 1 attempt + 2 retries
    assert "not valid" in fake.calls[1]["messages"][-1]["content"]


def test_gives_up_after_two_retries(tmp_path):
    client, fake = make(tmp_path, ["bad"] * 5)
    with pytest.raises(LLMError, match="3 attempts"):
        client.structured(MSG, Out)
    assert len(fake.calls) == MAX_RETRIES + 1


def test_transport_errors_are_retried(tmp_path):
    client, fake = make(tmp_path, [llm_module.openai.APIConnectionError(request=REQ), GOOD])
    assert client.structured(MSG, Out).product == "mouse"
    assert len(fake.calls) == 2


def test_auth_error_does_not_retry(tmp_path):
    err = llm_module.openai.AuthenticationError(
        "bad key", response=httpx.Response(401, request=REQ), body=None
    )
    client, fake = make(tmp_path, [err, GOOD])
    with pytest.raises(LLMError):
        client.structured(MSG, Out)
    assert len(fake.calls) == 1


def test_cache_hit_skips_the_network(tmp_path):
    client, fake = make(tmp_path, [GOOD])
    client.structured(MSG, Out)
    again = client.structured(MSG, Out)
    assert again.product == "mouse"
    assert len(fake.calls) == 1
    assert (client.cache_hits, client.cache_misses) == (1, 1)


def test_cache_key_depends_on_model_messages_and_schema(tmp_path):
    client, fake = make(tmp_path, [GOOD, GOOD, GOOD])
    client.structured(MSG, Out)
    client.structured(MSG, Out, model="other-model")
    client.structured([{"role": "user", "content": "different"}], Out)
    assert len(fake.calls) == 3


def test_use_cache_false_bypasses_read_and_write(tmp_path):
    client, fake = make(tmp_path, [GOOD, GOOD])
    client.structured(MSG, Out, use_cache=False)
    client.structured(MSG, Out, use_cache=False)
    assert len(fake.calls) == 2
    assert not (tmp_path / "cache").exists()


def test_invalid_replies_are_never_cached(tmp_path):
    client, _ = make(tmp_path, ["bad"] * 3)
    with pytest.raises(LLMError):
        client.structured(MSG, Out)
    assert not list((tmp_path / "cache").glob("*.json")) if (tmp_path / "cache").exists() else True


def test_corrupt_cache_entry_falls_back_to_live_call(tmp_path):
    client, fake = make(tmp_path, [GOOD, GOOD])
    client.structured(MSG, Out)
    (entry,) = (tmp_path / "cache").glob("*.json")
    entry.write_text("{ corrupt")
    assert client.structured(MSG, Out).product == "mouse"
    assert len(fake.calls) == 2


def test_falls_back_to_json_only_when_response_format_is_rejected(tmp_path):
    client, fake = make(
        tmp_path,
        [bad_request("response_format json_schema is not supported"), "```json\n" + GOOD + "\n```"],
    )
    out = client.structured(MSG, Out)
    assert out.price_paise == 149900
    assert client.mode == "json_only"
    assert "response_format" not in fake.calls[1]
    assert fake.calls[1]["messages"][0]["role"] == "system"
    assert "JSON schema" in fake.calls[1]["messages"][0]["content"]


def test_unrelated_bad_request_does_not_switch_mode(tmp_path):
    client, _ = make(tmp_path, [bad_request("context too long"), GOOD])
    client.structured(MSG, Out)
    assert client.mode == "json_schema"


def test_api_key_never_appears_in_errors_repr_or_cache(tmp_path):
    client, _ = make(tmp_path, ["bad"] * 3)
    with pytest.raises(LLMError) as exc:
        client.structured(MSG, Out)
    assert SECRET not in str(exc.value)
    assert SECRET not in repr(client.settings)
    ok, _ = make(tmp_path, [GOOD])
    ok.structured(MSG, Out)
    for f in (tmp_path / "cache").glob("*.json"):
        assert SECRET not in f.read_text()


def test_settings_from_env_validates_config_and_hides_key():
    s = Settings.from_env({"MODEL_API_KEY": SECRET, "CONFIG": "r2_session"})
    assert s.config == "r2_session" and SECRET not in repr(s)
    with pytest.raises(ValueError):
        Settings.from_env({"CONFIG": "bogus"})
    assert Settings.from_env({}).config == "intentlock"


def test_schemas_with_float_fields_work_with_and_without_the_cache(tmp_path):
    from pydantic import Field

    class Scored(BaseModel):
        label: str
        score: float = Field(ge=0.5, le=1.0)  # a float bound ends up in the JSON schema

    good = json.dumps({"label": "x", "score": 0.75})
    client, fake = make(tmp_path, [good, good])
    assert client.structured(MSG, Scored, use_cache=False).score == 0.75
    assert client.structured(MSG, Scored).score == 0.75
    assert client.structured(MSG, Scored).score == 0.75  # served from cache
    assert len(fake.calls) == 2
