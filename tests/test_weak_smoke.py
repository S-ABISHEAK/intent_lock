"""`make weak-smoke` (SPEC §13.2 weak agent): passes only if the model really calls a tool."""

from intentlock.llm import weak_smoke


class Fake:
    default_model = "llama-3.1-8b-instant"

    def __init__(self, calls):
        self.calls = calls

    def chat(self, messages, tools, *, model=None, use_cache=True):
        assert use_cache is False and tools[0]["function"]["name"] == "search_products"
        return {"content": None if self.calls else "Sure!", "tool_calls": self.calls}


def test_a_tool_call_passes(capsys):
    call = {"id": "1", "name": "search_products", "arguments": {"query": "wireless mouse"}}
    assert weak_smoke.main(Fake([call])) == 0
    assert "weak smoke OK" in capsys.readouterr().out


def test_a_plain_text_answer_fails():
    assert weak_smoke.main(Fake([])) == 1


def test_no_key_is_reported_without_calling(monkeypatch, capsys):
    monkeypatch.delenv("WEAK_MODEL_API_KEY", raising=False)
    assert weak_smoke.main() == 2 and "WEAK_MODEL_API_KEY" in capsys.readouterr().out


def test_a_404_lists_the_models_without_printing_the_key(monkeypatch, capsys):
    from intentlock.llm.client import LLMError

    class NotFound(Fake):
        def chat(self, *a, **k):
            raise LLMError("LLM chat failed after 3 attempts: API error: NotFoundError")

    monkeypatch.setenv("WEAK_MODEL_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("WEAK_MODEL_ID", "llama-old")
    monkeypatch.setenv("WEAK_MODEL_API_KEY", "gsk_secret")
    monkeypatch.setattr(weak_smoke, "available_models", lambda url, key: ["llama-new", "qwen-x"])
    assert weak_smoke.main(NotFound([])) == 1
    out = capsys.readouterr().out
    assert "llama-new" in out and "llama-old" in out and "gsk_secret" not in out
