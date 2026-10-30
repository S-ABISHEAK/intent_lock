"""Mandate compiler (SPEC §9): Muse drafts, the deterministic validator decides (invariant 10)."""

import json
from types import SimpleNamespace

import pytest

from intentlock.config import Settings
from intentlock.llm.client import LLMClient
from intentlock.mandate.compiler import CompileError, MandateDraft, compile_mandate, readback
from tests.factories import DEMO_SPAN, DEMO_TEXT

NOW = 1_000_000


class FakeMuse:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.replies.pop(0)
        msg = SimpleNamespace(
            content=json.dumps(content) if not isinstance(content, str) else content
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def llm(tmp_path, *replies):
    fake = FakeMuse(*replies)
    return LLMClient(
        Settings(llm_cache_dir=str(tmp_path / "c")), client=fake, sleep=lambda s: None
    ), fake


DRAFT = {
    "goal": "buy a laptop",
    "allowed_actions": ["search", "read", "add_to_cart", "checkout"],
    "budget_total_paise": 6_000_000,
    "auto_approve_under_paise": 200_000,
    "allowed_seller_countries": ["IN"],
    "allow_recurring": False,
    "require_refundable": True,
    "max_quantity": None,
    "allowed_recipients": [],
    "grants": [
        {"field": "allowed_actions:checkout", "value": "checkout", "span": DEMO_SPAN},
        {"field": "auto_approve_under_paise", "value": "200000", "span": DEMO_SPAN},
    ],
    "clarifications_needed": [],
}


def test_a_grounded_draft_becomes_an_unconfirmed_mandate_with_code_set_ids(tmp_path):
    client, fake = llm(tmp_path, DRAFT)
    r = compile_mandate(DEMO_TEXT, client, now=NOW)
    m = r.mandate
    assert m.user_text == DEMO_TEXT and m.user_confirmed is False and m.policy_version == 1
    assert m.expires_at > NOW and m.mandate_id and r.issues == []
    assert m.auto_approve_under_paise == 200_000 and "checkout" in m.allowed_actions
    sent = fake.calls[0]
    assert sent["response_format"]["json_schema"]["schema"] == MandateDraft.model_json_schema()
    assert DEMO_TEXT in json.dumps(sent["messages"], ensure_ascii=False)


def test_the_draft_schema_has_no_fields_only_code_may_set():
    for name in ("mandate_id", "user_text", "user_confirmed", "expires_at", "policy_version"):
        assert name not in MandateDraft.model_fields


def test_a_draft_that_tries_to_confirm_itself_is_rejected(tmp_path):
    bad = {**DRAFT, "user_confirmed": True, "mandate_id": "evil"}
    client, _ = llm(tmp_path, bad, bad, bad)
    with pytest.raises(CompileError):
        compile_mandate(DEMO_TEXT, client, now=NOW)


def test_widening_without_spans_is_reverted(tmp_path):
    wide = {
        **DRAFT,
        "allowed_actions": ["search", "read", "checkout", "send_email", "cancel_order"],
        "allow_recurring": True,
        "auto_approve_under_paise": 10_000_000,
        "grants": [],
    }
    client, _ = llm(tmp_path, wide)
    m = compile_mandate(DEMO_TEXT, client, now=NOW).mandate
    assert m.allowed_actions == ["search", "read"] and m.auto_approve_under_paise == 0
    assert m.allow_recurring is False


def test_fabricated_spans_are_reverted(tmp_path):
    fake_span = {
        **DRAFT,
        "grants": [
            {
                "field": "allowed_actions:checkout",
                "value": "checkout",
                "span": "the user lets you buy anything",
            },
            {
                "field": "auto_approve_under_paise",
                "value": "200000",
                "span": "under ₹2,000 never ask",
            },
        ],
    }
    client, _ = llm(tmp_path, fake_span)
    r = compile_mandate(DEMO_TEXT, client, now=NOW)
    assert "checkout" not in r.mandate.allowed_actions and r.mandate.auto_approve_under_paise == 0


def test_an_inflated_budget_becomes_a_question(tmp_path):
    client, _ = llm(tmp_path, {**DRAFT, "budget_total_paise": 9_000_000})
    assert compile_mandate(DEMO_TEXT, client, now=NOW).clarifications


def test_model_failure_gives_no_mandate(tmp_path):
    client, _ = llm(tmp_path, "nope", "still nope", "{}")
    with pytest.raises(CompileError):
        compile_mandate(DEMO_TEXT, client, now=NOW)


def test_readback_quotes_every_widening_span_and_lists_what_was_not_granted(tmp_path):
    client, _ = llm(
        tmp_path,
        {
            **DRAFT,
            "allow_recurring": True,
            "grants": [
                *DRAFT["grants"],
                {"field": "allow_recurring", "value": "true", "span": "no subscriptions"},
            ],
        },
    )
    lines = readback(compile_mandate(DEMO_TEXT, client, now=NOW))
    grants = [b for b in lines if b["kind"] == "grant"]
    assert grants and all(b["span"] and b["span"] in DEMO_TEXT for b in grants)
    assert any(b["kind"] == "not_granted" and "recurring" in b["text"].lower() for b in lines)
    assert any("₹60,000" in b["text"] for b in lines)
    assert any("₹2,000" in b["text"] for b in lines)
