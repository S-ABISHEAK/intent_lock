"""Hard rules on the authoritative quote (SPEC §6.1, step 4). Pure functions; exact boundaries."""

import pytest

from intentlock.policy.context import DecisionContext
from intentlock.policy.hard_rules import hard_rules
from tests.factories import NOW, make_line, make_mandate, make_proposal, make_quote, monthly


def ctx(quote=None, mandate=None, requested=frozenset(), proposal=None) -> DecisionContext:
    return DecisionContext(
        proposal=proposal or make_proposal(),
        mandate=mandate or make_mandate(),
        config="intentlock",
        now=NOW,
        quote=quote,
        requested_skus=requested,
    )


def codes(c: DecisionContext) -> list[str]:
    return [r.code for r in hard_rules(c)]


def test_clean_quote_has_no_violations():
    assert codes(ctx(make_quote())) == []


# BUDGET_EXCEEDED -----------------------------------------------------------------------------
def test_total_exactly_at_budget_is_allowed():
    q = make_quote([make_line(unit_paise=6_000_000)])
    assert codes(ctx(q)) == []


def test_one_paisa_over_budget_is_denied():
    q = make_quote([make_line(unit_paise=6_000_001)])
    assert codes(ctx(q)) == ["BUDGET_EXCEEDED"]


def test_budget_counts_fees_and_tax_not_just_lines():
    q = make_quote([make_line(unit_paise=5_999_000)], fees=500, tax=501)
    assert codes(ctx(q)) == ["BUDGET_EXCEEDED"]


def test_no_budget_means_no_budget_rule():
    q = make_quote([make_line(unit_paise=99_999_900)])
    assert codes(ctx(q, make_mandate(budget_total_paise=None))) == []


# RECURRING_NOT_ALLOWED -----------------------------------------------------------------------
def test_recurring_denied_unless_allowed():
    q = make_quote(recurring=[monthly()])
    assert codes(ctx(q)) == ["RECURRING_NOT_ALLOWED"]
    assert codes(ctx(q, make_mandate(allow_recurring=True))) == []


# SELLER_NOT_ALLOWED --------------------------------------------------------------------------
def test_foreign_seller_denied():
    q = make_quote([make_line(seller_country="SG")])
    assert codes(ctx(q)) == ["SELLER_NOT_ALLOWED"]


def test_one_foreign_line_among_many_is_enough():
    q = make_quote([make_line(), make_line(sku="MS-1001", unit_paise=1, seller_country="US")])
    assert codes(ctx(q)) == ["SELLER_NOT_ALLOWED"]


def test_seller_country_match_ignores_case():
    q = make_quote([make_line(seller_country="in")])
    assert codes(ctx(q, make_mandate(allowed_seller_countries=["IN"]))) == []


def test_none_means_unrestricted_but_empty_list_allows_nobody():
    q = make_quote([make_line(seller_country="US")])
    assert codes(ctx(q, make_mandate(allowed_seller_countries=None))) == []
    assert codes(ctx(q, make_mandate(allowed_seller_countries=[]))) == ["SELLER_NOT_ALLOWED"]


# QUANTITY_EXCEEDED ---------------------------------------------------------------------------
def test_quantity_at_limit_ok_and_one_over_denied():
    assert (
        codes(ctx(make_quote([make_line(qty=2, unit_paise=1000)]), make_mandate(max_quantity=2)))
        == []
    )
    over = make_quote([make_line(qty=3, unit_paise=1000)])
    assert codes(ctx(over, make_mandate(max_quantity=2))) == ["QUANTITY_EXCEEDED"]


def test_quantity_uses_the_quote_not_agent_args():
    q = make_quote([make_line(qty=5, unit_paise=1000)])
    p = make_proposal(cart_id="c1", shipping_address_id="ADDR-0001")
    assert codes(ctx(q, proposal=p)) == ["QUANTITY_EXCEEDED"]


def test_no_max_quantity_means_unlimited():
    q = make_quote([make_line(qty=50, unit_paise=1000)])
    assert codes(ctx(q, make_mandate(max_quantity=None))) == []


# UNREQUESTED_ADDON ---------------------------------------------------------------------------
def addon_quote():
    return make_quote(
        [
            make_line(),
            make_line(sku="XC-4999", title="Express Care", unit_paise=1000, is_addon=True),
        ]
    )


def test_addon_the_agent_never_added_is_denied():
    assert codes(ctx(addon_quote(), requested=frozenset({"LAP-1201"}))) == ["UNREQUESTED_ADDON"]


def test_addon_the_agent_explicitly_added_is_not_an_unrequested_addon():
    assert codes(ctx(addon_quote(), requested=frozenset({"LAP-1201", "XC-4999"}))) == []


def test_non_addon_lines_never_trigger_the_addon_rule():
    assert codes(ctx(make_quote(), requested=frozenset())) == []


# RECIPIENT_NOT_ALLOWED -----------------------------------------------------------------------
def mail(to, tool="send_email"):
    if tool == "send_email":
        return make_proposal("send_email", to=to, subject="s", body="b")
    return make_proposal("share_cart", cart_id="c1", to=to)


@pytest.mark.parametrize("tool", ["send_email", "share_cart"])
def test_recipient_must_be_allowed(tool):
    m = make_mandate(allowed_recipients=["asha@example.com"])
    assert codes(ctx(mandate=m, proposal=mail("asha@example.com", tool))) == []
    assert codes(ctx(mandate=m, proposal=mail("evil@shoplite-support.co", tool))) == [
        "RECIPIENT_NOT_ALLOWED"
    ]


def test_empty_allowed_recipients_allows_nobody():
    assert codes(ctx(proposal=mail("asha@example.com"))) == ["RECIPIENT_NOT_ALLOWED"]


def test_recipient_match_is_case_insensitive():
    m = make_mandate(allowed_recipients=["Asha@Example.com"])
    assert codes(ctx(mandate=m, proposal=mail("ASHA@example.COM"))) == []


@pytest.mark.parametrize(
    "to",
    [
        "asha@example.com,evil@x.co",
        "asha@example.com; evil@x.co",
        "asha@example.com evil@x.co",
        "Asha <asha@example.com>",
        "asha@example.com\nevil@x.co",
    ],
)
def test_every_recipient_in_a_multi_address_field_must_be_allowed(to):
    m = make_mandate(allowed_recipients=["asha@example.com"])
    assert codes(ctx(mandate=m, proposal=mail(to))) == ["RECIPIENT_NOT_ALLOWED"]


# misuse fails loudly (pipeline turns it into DENY PIPELINE_ERROR) ----------------------------
def test_checkout_without_a_quote_raises_instead_of_passing():
    with pytest.raises(ValueError):
        hard_rules(ctx(quote=None))


def test_destructive_tools_have_no_hard_rules():
    p = make_proposal("delete_saved_address", id="ADDR-0001")
    assert codes(ctx(proposal=p)) == []


def test_multiple_violations_are_all_reported():
    q = make_quote([make_line(seller_country="US", unit_paise=7_000_000)], recurring=[monthly()])
    assert set(codes(ctx(q))) == {"BUDGET_EXCEEDED", "RECURRING_NOT_ALLOWED", "SELLER_NOT_ALLOWED"}
