"""Span grounding (SPEC §9; invariant 10). Today: requested add-on grants (D-031); the full
validator for every widening field arrives with the mandate compiler (Nov 1)."""

import pytest

from intentlock.mandate.grounding import granted_addons
from intentlock.models import Grant
from tests.factories import make_mandate


def addon(value, span):
    return Grant(field="requested_addon", value=value, span=span)


TEXT = "Laptop under ₹60,000 with the ProtectPlus plan PP-1001, and gift wrap GW-0799 please."


def test_a_grounded_grant_requests_the_addon():
    m = make_mandate(user_text=TEXT, grants=[addon("PP-1001", "with the ProtectPlus plan PP-1001")])
    assert granted_addons(m) == {"PP-1001"}


def test_a_sku_merely_mentioned_in_the_users_words_is_not_requested():
    assert granted_addons(make_mandate(user_text=TEXT)) == frozenset()


def test_span_must_be_verbatim_in_the_users_text():
    m = make_mandate(user_text=TEXT, grants=[addon("PP-1001", "please add PP-1001 for me")])
    assert granted_addons(m) == frozenset()


def test_span_must_contain_the_granted_sku():
    m = make_mandate(user_text=TEXT, grants=[addon("XC-4999", "with the ProtectPlus plan PP-1001")])
    assert granted_addons(m) == frozenset()


@pytest.mark.parametrize(
    "text,span",
    [
        ("No gift wrap (GW-0799), please.", "No gift wrap (GW-0799)"),
        ("Do not add PP-1001.", "Do not add PP-1001"),
        ("Laptop without PP-1001", "without PP-1001"),
        ("I don't want GW-0799", "I don't want GW-0799"),
        ("Never add XC-4999", "Never add XC-4999"),
        ("Everything except PP-1001", "except PP-1001"),
    ],
)
def test_a_negated_span_never_requests_anything(text, span):
    sku = [t for t in ("GW-0799", "PP-1001", "XC-4999") if t in span][0]
    m = make_mandate(user_text=text, grants=[addon(sku, span)])
    assert granted_addons(m) == frozenset()


def test_case_and_spacing_are_normalized():
    m = make_mandate(
        user_text="laptop  with   pp-1001 please", grants=[addon("pp-1001", "with pp-1001")]
    )
    assert granted_addons(m) == {"PP-1001"}


def test_other_grant_fields_are_ignored():
    m = make_mandate(
        user_text=TEXT,
        grants=[
            Grant(
                field="auto_approve_under_paise", value="PP-1001", span="ProtectPlus plan PP-1001"
            )
        ],
    )
    assert granted_addons(m) == frozenset()


def test_no_mandate_requests_nothing():
    assert granted_addons(None) == frozenset()
