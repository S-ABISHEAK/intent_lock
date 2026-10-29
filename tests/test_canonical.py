"""Canonicalization (SPEC §5): deterministic bytes, no floats anywhere (CLAUDE.md invariant 4)."""

import hashlib
import math
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from intentlock.canonical import canonical, canonical_hash, compute_quote_hash
from intentlock.models import Quote, QuoteLine, RecurringTerm


def make_quote(**overrides) -> Quote:
    data = dict(
        quote_id="q1",
        cart_id="c1",
        lines=[
            QuoteLine(
                sku="LAP-1201",
                title="Asus Vivobook 15",
                qty=1,
                unit_paise=5499900,
                seller_id="SEL-IN01",
                seller_country="IN",
            )
        ],
        fees_paise=0,
        tax_paise=0,
        total_paise=5499900,
        recurring=[],
        shipping_address_id="ADDR-0001",
        checkout_url="http://localhost:8001/checkout/c1",
        quote_hash="",
    )
    data.update(overrides)
    return Quote(**data)


def test_sorted_keys_and_compact_separators():
    assert canonical({"b": 1, "a": [1, 2], "c": {"z": None, "y": True}}) == (
        '{"a":[1,2],"b":1,"c":{"y":true,"z":null}}'
    )


def test_key_order_does_not_change_output():
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})


def test_non_ascii_is_not_escaped():
    assert canonical({"k": "₹1,499"}) == '{"k":"₹1,499"}'


def test_pydantic_model_is_dumped_in_json_mode():
    q = make_quote()
    out = canonical(q)
    assert '"unit_paise":5499900' in out
    assert out == canonical(q.model_dump(mode="json"))


@pytest.mark.parametrize(
    "bad",
    [
        {"price": 1.5},
        {"price": 1.0},
        {"nested": {"deep": [1, 2, {"x": 3.0}]}},
        [0.1],
        {"price": float("nan")},
        {"price": float("inf")},
        {"price": math.pi},
    ],
)
def test_any_float_raises(bad):
    with pytest.raises(TypeError, match="float"):
        canonical(bad)


def test_float_inside_a_model_dict_field_raises():
    from intentlock.models import ActionProposal

    proposal = ActionProposal(
        action_id="a1",
        session_id="s1",
        tool="checkout",
        args={"cart_id": "c1", "total": 100.5},
        ts=1,
    )
    with pytest.raises(TypeError, match="float"):
        canonical(proposal)


@pytest.mark.parametrize(
    "bad", [{"x": Decimal("1")}, {"x": {1, 2}}, {"x": b"bytes"}, {"x": object()}]
)
def test_non_json_types_raise(bad):
    with pytest.raises(TypeError):
        canonical(bad)


def test_non_string_keys_raise():
    with pytest.raises(TypeError, match="key"):
        canonical({1: "a"})


def test_bool_is_not_treated_as_a_float_and_stays_bool():
    assert canonical({"a": True, "b": 0}) == '{"a":true,"b":0}'


def test_canonical_hash_is_sha256_of_canonical():
    obj = {"a": 1}
    assert canonical_hash(obj) == hashlib.sha256(b'{"a":1}').hexdigest()


def test_canonical_hash_exclude_removes_top_level_field():
    assert canonical_hash({"a": 1, "h": "zzz"}, exclude={"h"}) == canonical_hash({"a": 1})


def test_quote_hash_ignores_the_quote_hash_field_itself():
    q1 = make_quote(quote_hash="")
    q2 = make_quote(quote_hash="something-else")
    assert compute_quote_hash(q1) == compute_quote_hash(q2)


def test_quote_hash_changes_when_any_money_field_changes():
    base = compute_quote_hash(make_quote())
    assert compute_quote_hash(make_quote(total_paise=5499901)) != base
    assert compute_quote_hash(make_quote(fees_paise=1)) != base
    assert (
        compute_quote_hash(
            make_quote(recurring=[RecurringTerm(sku="PP-1001", amount_paise=1, interval="month")])
        )
        != base
    )


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=15,
)


@given(json_values)
def test_canonical_is_deterministic_and_parseable(value):
    import json

    out = canonical(value)
    assert out == canonical(value)
    assert json.loads(out) == value
