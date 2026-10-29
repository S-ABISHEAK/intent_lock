"""SPEC §5 models: integer paise, strict types, unknown fields rejected."""

import pytest
from pydantic import BaseModel, ValidationError

import intentlock.models as models
from intentlock.models import (
    Approval,
    Decision,
    Grant,
    Mandate,
    ProvenanceRecord,
    Quote,
    Reason,
    Receipt,
    TrustLabel,
)

MANDATE = dict(
    mandate_id="m1",
    user_text="Find a laptop under ₹60,000 all-in.",
    goal="buy laptop",
    allowed_actions=["search", "read"],
    budget_total_paise=6000000,
    auto_approve_under_paise=0,
    allowed_seller_countries=["IN"],
    allow_recurring=False,
    require_refundable=False,
    max_quantity=1,
    allowed_recipients=[],
    grants=[],
    clarifications_needed=[],
    policy_version=1,
    user_confirmed=False,
    expires_at=4102444800,
)


def test_mandate_round_trips_through_json():
    m = Mandate(**MANDATE)
    assert Mandate.model_validate_json(m.model_dump_json()) == m


def test_trust_labels_cover_spec_and_derived_untrusted():
    assert {t.value for t in TrustLabel} == {
        "user",
        "trusted_api",
        "untrusted",
        "sensitive",
        "derived_untrusted",
    }


@pytest.mark.parametrize(
    "field", ["budget_total_paise", "auto_approve_under_paise", "expires_at", "policy_version"]
)
def test_mandate_rejects_float_in_numeric_fields(field):
    with pytest.raises(ValidationError):
        Mandate(**{**MANDATE, field: 1000.0})


def test_mandate_rejects_bool_as_int_and_negative_money():
    with pytest.raises(ValidationError):
        Mandate(**{**MANDATE, "budget_total_paise": True})
    with pytest.raises(ValidationError):
        Mandate(**{**MANDATE, "budget_total_paise": -1})


def test_mandate_rejects_unknown_fields_and_missing_fields():
    with pytest.raises(ValidationError):
        Mandate(**{**MANDATE, "surprise": 1})
    missing = {k: v for k, v in MANDATE.items() if k != "user_confirmed"}
    with pytest.raises(ValidationError):
        Mandate(**missing)


def test_quote_rejects_float_money():
    with pytest.raises(ValidationError):
        Quote(
            quote_id="q",
            cart_id="c",
            lines=[],
            fees_paise=0,
            tax_paise=0,
            total_paise=100.5,
            recurring=[],
            shipping_address_id="ADDR-0001",
            checkout_url="u",
            quote_hash="h",
        )


def test_decision_outcome_and_config_are_closed_sets():
    ok = dict(action_id="a", outcome="DENY", reasons=[], quote_hash=None, config="none")
    Decision(**ok)
    with pytest.raises(ValidationError):
        Decision(**{**ok, "outcome": "MAYBE"})
    with pytest.raises(ValidationError):
        Decision(**{**ok, "config": "other"})


def test_reason_source_is_closed_set():
    Reason(code="BUDGET_EXCEEDED", source="rule")
    with pytest.raises(ValidationError):
        Reason(code="X", source="llm")


def test_receipt_kind_is_closed_set():
    ok = dict(seq=0, prev_hash="", row_hash="h", kind="decision", payload={}, ts=1)
    Receipt(**ok)
    with pytest.raises(ValidationError):
        Receipt(**{**ok, "kind": "other"})


def test_approval_has_every_binding_field():
    assert set(Approval.model_fields) == {
        "approval_id",
        "nonce",
        "mandate_id",
        "policy_version",
        "session_id",
        "action_tool",
        "quote_hash",
        "expires_at",
        "signature",
    }


def test_grant_and_provenance_record_shape():
    Grant(field="auto_approve_under_paise", value="200000", span="over ₹2,000")
    ProvenanceRecord(
        record_id="r1",
        session_id="s",
        tool="get_reviews",
        label=TrustLabel.UNTRUSTED,
        field_path="reviews[0].text",
        raw="x",
        tokens=[],
        ts=1,
    )


def test_no_model_field_is_annotated_as_float():
    for name in dir(models):
        obj = getattr(models, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            for fname, info in obj.model_fields.items():
                assert "float" not in str(info.annotation), f"{name}.{fname} is typed float"
