"""Spend meter and hard cap for paid eval runs (Layer A plan, task 1).

Money is integer micro-dollars (no floats, as with paise). The cap is checked before every API call
and is never retried; a provider reply without token usage stops the run (fail closed: an unmetered
call could overrun the cap). The meter file holds counts only, never a key.
"""

import json
from types import SimpleNamespace

import pytest

from intentlock.config import Settings
from intentlock.llm.client import LLMClient, LLMError
from intentlock.llm.spend import BudgetExceeded, SpendMeter, parse_usd_micro

TOOLS = [{"type": "function", "function": {"name": "search_products", "parameters": {}}}]


def answer(prompt_tokens=1000, completion_tokens=100, usage=True):
    msg = SimpleNamespace(content="done", tool_calls=None)
    u = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=u if usage else None)


class Fake:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        return self.replies.pop(0)


def client(tmp_path, fake, meter):
    s = Settings(llm_cache_dir=str(tmp_path / "llm"), model_api_key="sk-test-DO-NOT-LEAK")
    return LLMClient(s, client=fake, meter=meter)


def meter(tmp_path, cap="1.00", price_in="2.00", price_out="8.00"):
    return SpendMeter(
        price_in_per_mtok=parse_usd_micro(price_in),
        price_out_per_mtok=parse_usd_micro(price_out),
        cap=parse_usd_micro(cap),
        path=tmp_path / "spend.json",
    )


def test_parse_usd_is_exact_integer_micro_dollars():
    assert parse_usd_micro("100") == 100_000_000
    assert parse_usd_micro("0.15") == 150_000
    assert parse_usd_micro("$1.234567") == 1_234_567
    for bad in ("", "abc", "-1", "1e3", "0.0000001", "nan"):
        with pytest.raises(ValueError):
            parse_usd_micro(bad)


def test_cost_is_metered_per_call_and_rounded_up(tmp_path):
    m = meter(tmp_path)  # $2 / Mtok in, $8 / Mtok out
    c = client(tmp_path, Fake([answer(1000, 100), answer(1, 0)]), m)
    c.chat([{"role": "user", "content": "x"}], TOOLS, use_cache=False)
    assert (m.tokens_in, m.tokens_out, m.spent, m.calls) == (1000, 100, 2_800, 1)
    c.chat([{"role": "user", "content": "y"}], TOOLS, use_cache=False)
    assert m.spent == 2_802  # 1 token at $2/Mtok is exactly 2 micro-dollars
    cheap = meter(tmp_path / "cheap", price_in="0.50")  # 1 token = 0.5 micro-dollars
    client(tmp_path, Fake([answer(1, 0)]), cheap).chat(
        [{"role": "user", "content": "z"}], TOOLS, use_cache=False
    )
    assert cheap.spent == 1  # rounded up, never down: the meter may only over-count


def test_cap_stops_before_the_next_call_and_is_never_retried(tmp_path):
    m = meter(tmp_path, cap="0.004")  # 4,000 micro-dollars: one 2,800 call fits, two don't
    fake = Fake([answer(1000, 100), answer(1000, 100), answer(1000, 100)])
    c = client(tmp_path, fake, m)
    c.chat([{"role": "user", "content": "a"}], TOOLS, use_cache=False)
    c.chat([{"role": "user", "content": "b"}], TOOLS, use_cache=False)  # 5,600 now, over the cap
    with pytest.raises(BudgetExceeded) as exc:
        c.chat([{"role": "user", "content": "c"}], TOOLS, use_cache=False)
    assert fake.calls == 2  # the third call never left the process
    assert isinstance(exc.value, LLMError) and "BudgetExceeded" in type(exc.value).__name__


def test_cache_hits_cost_nothing(tmp_path):
    m = meter(tmp_path)
    c = client(tmp_path, Fake([answer(1000, 100)]), m)
    c.chat([{"role": "user", "content": "same"}], TOOLS)
    c.chat([{"role": "user", "content": "same"}], TOOLS)
    assert m.calls == 1 and m.spent == 2_800


def test_a_reply_without_usage_stops_the_run(tmp_path):
    m = meter(tmp_path)
    c = client(tmp_path, Fake([answer(usage=False), answer()]), m)
    with pytest.raises(BudgetExceeded, match="usage"):
        c.chat([{"role": "user", "content": "x"}], TOOLS, use_cache=False)
    with pytest.raises(BudgetExceeded):  # and it stays stopped
        c.chat([{"role": "user", "content": "y"}], TOOLS, use_cache=False)


def test_resume_continues_from_the_persisted_spend(tmp_path):
    m = meter(tmp_path, cap="0.004")
    client(tmp_path, Fake([answer(1000, 100)]), m).chat(
        [{"role": "user", "content": "a"}], TOOLS, use_cache=False
    )
    again = meter(tmp_path, cap="0.004")
    assert (again.spent, again.calls) == (2_800, 1)
    c = client(tmp_path, Fake([answer(1000, 100), answer(1000, 100)]), again)
    c.chat([{"role": "user", "content": "b"}], TOOLS, use_cache=False)
    with pytest.raises(BudgetExceeded):
        c.chat([{"role": "user", "content": "c"}], TOOLS, use_cache=False)


def test_meter_file_has_counts_only(tmp_path):
    m = meter(tmp_path)
    client(tmp_path, Fake([answer()]), m).chat(
        [{"role": "user", "content": "x"}], TOOLS, use_cache=False
    )
    text = (tmp_path / "spend.json").read_text()
    assert "sk-test" not in text
    assert set(json.loads(text)) == {
        "tokens_in", "tokens_out", "spent_micro_usd", "calls", "cap_micro_usd",
        "price_in_micro_usd_per_mtok", "price_out_micro_usd_per_mtok", "stopped",
    }  # fmt: skip


def test_from_env_needs_all_three_names(monkeypatch, tmp_path):
    for name in ("MODEL_PRICE_IN_PER_MTOK", "MODEL_PRICE_OUT_PER_MTOK", "EVAL_SPEND_CAP_USD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="MODEL_PRICE_IN_PER_MTOK"):
        SpendMeter.from_env(tmp_path / "spend.json")
    monkeypatch.setenv("MODEL_PRICE_IN_PER_MTOK", "0.5")
    monkeypatch.setenv("MODEL_PRICE_OUT_PER_MTOK", "2")
    monkeypatch.setenv("EVAL_SPEND_CAP_USD", "100")
    m = SpendMeter.from_env(tmp_path / "spend.json")
    assert (m.price_in_per_mtok, m.price_out_per_mtok, m.cap) == (500_000, 2_000_000, 100_000_000)


def test_without_a_meter_the_client_is_unchanged(tmp_path):
    c = LLMClient(Settings(llm_cache_dir=str(tmp_path)), client=Fake([answer(usage=False)]))
    assert c.chat([{"role": "user", "content": "x"}], TOOLS, use_cache=False)["content"] == "done"
