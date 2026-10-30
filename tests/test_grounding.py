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


# -- Oct 31 checkpoint review: the user's clause decides, not the quoted span ------------------
REFUSALS = [
    "A mouse please. No gift wrap (GW-0799), please.",
    "I don’t want gift wrap GW-0799",
    "I donʼt want GW-0799",
    "I didn't ask for GW-0799",
    "I won't need GW-0799",
    "remove GW-0799",
    "Cancel the GW-0799",
    "Decline GW-0799",
    "Gift wrap GW-0799: none",
    "laptop w/o GW-0799",
    "GW-0799 nahi chahiye",
]


@pytest.mark.parametrize("text", REFUSALS)
@pytest.mark.parametrize("span", ["GW-0799", "gift wrap (GW-0799), please", "full"])
def test_a_refusal_never_grants_whatever_span_is_quoted(text, span):
    span = text if span == "full" else span
    if span not in text:
        pytest.skip("span not in this text")
    m = make_mandate(user_text=text, grants=[addon("GW-0799", span)])
    assert granted_addons(m) == frozenset()


def test_a_clause_without_any_affirmative_word_does_not_grant():
    m = make_mandate(user_text="GW-0799?", grants=[addon("GW-0799", "GW-0799")])
    assert granted_addons(m) == frozenset()


def test_the_sku_must_be_a_whole_token_of_the_users_text():
    for text in ("Add PP-10012 please", "Add XPP-1001 please"):
        m = make_mandate(user_text=text, grants=[addon("PP-1001", "PP-1001")])
        assert granted_addons(m) == frozenset(), text


def test_affirmed_in_one_clause_refused_in_another_is_refused():
    text = "Add gift wrap GW-0799. Actually, no GW-0799."
    m = make_mandate(user_text=text, grants=[addon("GW-0799", "Add gift wrap GW-0799")])
    assert granted_addons(m) == frozenset()


@pytest.mark.parametrize(
    "text,span",
    [
        ("Laptop with the ProtectPlus plan PP-1001 please.", "with the ProtectPlus plan PP-1001"),
        ("Please add gift wrap GW-0799.", "add gift wrap GW-0799"),
        ("I want PP-1001 too", "PP-1001"),
        ("Laptop plus XC-4999 express care", "plus XC-4999"),
    ],
)
def test_plain_affirmative_requests_still_grant(text, span):
    sku = [t for t in ("GW-0799", "PP-1001", "XC-4999") if t in text][0]
    assert granted_addons(make_mandate(user_text=text, grants=[addon(sku, span)])) == {sku}


# ================================ Nov 1: the full validator (SPEC §9; §17 test 6) ===========
from intentlock.mandate.grounding import ground, parse_money_paise  # noqa: E402


@pytest.mark.parametrize(
    "text,paise",
    [
        ("under ₹2,000", 200_000),
        ("under 2000", 200_000),
        ("rs 2000", 200_000),
        ("Rs.2,000 max", 200_000),
        ("INR 2,000", 200_000),
        ("2k", 200_000),
        ("₹2k", 200_000),
        ("2 thousand", 200_000),
        ("₹60,000 all-in", 6_000_000),
        ("1.5 lakh", 15_000_000),
        ("₹1,00,000", 10_000_000),
        ("₹1,499.50", 149_950),
    ],
)
def test_money_forms_parse_to_integer_paise(text, paise):
    assert paise in parse_money_paise(text)
    assert all(isinstance(v, int) for v in parse_money_paise(text))


@pytest.mark.parametrize("text", ["no numbers here", "buy LAP-1201", "₹0.001"])
def test_non_amounts_parse_to_nothing_useful(text):
    assert all(v >= 1 for v in parse_money_paise(text))
    if "LAP-1201" in text:
        assert 120_100 not in parse_money_paise(text)  # SKU digits are not money


DEMO = (
    "Laptop under ₹60,000 all-in, Indian seller, no subscriptions, must be refundable; "
    "you can buy accessories under ₹2,000 without asking."
)


def G(field, value, span):
    return {"field": field, "value": value, "span": span}


def grounded(**over):
    data = dict(
        user_text=DEMO,
        allowed_actions=["search", "read", "add_to_cart", "checkout"],
        budget_total_paise=6_000_000,
        auto_approve_under_paise=200_000,
        allowed_seller_countries=["IN"],
        allow_recurring=False,
        require_refundable=True,
        max_quantity=1,
        allowed_recipients=[],
        user_confirmed=False,
        grants=[
            G(
                "allowed_actions:checkout",
                "checkout",
                "you can buy accessories under ₹2,000 without asking",
            ),
            G(
                "auto_approve_under_paise",
                "200000",
                "you can buy accessories under ₹2,000 without asking",
            ),
        ],
    )
    data.update(over)
    return make_mandate(**data)


def codes(report):
    return {i.code for i in report.issues}


def test_the_spec_demo_mandate_is_grounded_unchanged():
    m = grounded()
    r = ground(m)
    assert r.mandate == m and r.issues == [] and r.clarifications == []


def test_grounding_is_idempotent():
    r = ground(
        grounded(allowed_actions=["search", "read", "checkout", "send_email"], allow_recurring=True)
    )
    assert ground(r.mandate).mandate == r.mandate


# -- §17 test 6, exactly ------------------------------------------------------------------------
def test_auto_approve_limit_with_no_span_is_reverted():
    r = ground(
        grounded(
            grants=[
                G(
                    "allowed_actions:checkout",
                    "checkout",
                    "you can buy accessories under ₹2,000 without asking",
                )
            ]
        )
    )
    assert r.mandate.auto_approve_under_paise == 0 and "GRANT_MISSING_SPAN" in codes(r)


def test_span_not_in_the_user_text_is_reverted():
    r = ground(
        grounded(
            grants=[
                G(
                    "allowed_actions:checkout",
                    "checkout",
                    "you can buy accessories under ₹2,000 without asking",
                ),
                G("auto_approve_under_paise", "200000", "buy anything under ₹2,000 freely"),
            ]
        )
    )
    assert r.mandate.auto_approve_under_paise == 0 and "GRANT_SPAN_NOT_IN_TEXT" in codes(r)


def test_number_mismatch_is_reverted():
    text = "Buy a mouse. Don't ask me for anything under 2000."
    m = grounded(
        user_text=text,
        auto_approve_under_paise=500_000,
        budget_total_paise=None,
        allowed_seller_countries=None,
        grants=[
            G("allowed_actions:checkout", "checkout", "Buy a mouse"),
            G("auto_approve_under_paise", "500000", "Don't ask me for anything under 2000"),
        ],
    )
    r = ground(m)
    assert r.mandate.auto_approve_under_paise == 0 and "GRANT_NUMBER_MISMATCH" in codes(r)


# -- widening needs the right words, and no refusal --------------------------------------------
def test_checkout_needs_a_buying_word_in_its_span():
    r = ground(
        grounded(
            grants=[
                G("allowed_actions:checkout", "checkout", "Laptop under ₹60,000 all-in"),
                G(
                    "auto_approve_under_paise",
                    "200000",
                    "you can buy accessories under ₹2,000 without asking",
                ),
            ]
        )
    )
    assert "checkout" not in r.mandate.allowed_actions and "GRANT_KEYWORD_MISSING" in codes(r)


def test_actions_without_any_grant_are_reverted_to_search_and_read():
    r = ground(
        grounded(
            allowed_actions=["search", "read", "checkout", "send_email", "delete_saved_address"]
        )
    )
    assert r.mandate.allowed_actions == ["search", "read", "add_to_cart", "checkout"]
    assert "GRANT_MISSING_SPAN" in codes(r)


def test_no_subscriptions_reverts_allow_recurring():
    r = ground(
        grounded(
            allow_recurring=True,
            grants=[*grounded().grants, G("allow_recurring", "true", "no subscriptions")],
        )
    )
    assert r.mandate.allow_recurring is False and "GRANT_NEGATED" in codes(r)


def test_a_real_subscription_request_is_granted():
    text = "Get me the monthly phone plan PLN-2001."
    m = grounded(
        user_text=text,
        allow_recurring=True,
        budget_total_paise=None,
        allowed_seller_countries=None,
        auto_approve_under_paise=0,
        grants=[
            G("allowed_actions:checkout", "checkout", "Get me the monthly phone plan"),
            G("allow_recurring", "true", "monthly phone plan"),
        ],
    )
    r = ground(m)
    assert r.mandate.allow_recurring is True and r.issues == []


def test_recipients_need_the_address_in_an_unrefused_span():
    text = "Email my cart to asha@example.com. Don't email bob@example.com."
    m = grounded(
        user_text=text,
        allowed_actions=["search", "read", "share_cart"],
        budget_total_paise=None,
        allowed_seller_countries=None,
        auto_approve_under_paise=0,
        allowed_recipients=["asha@example.com", "bob@example.com", "evil@x.co"],
        grants=[
            G("allowed_actions:share_cart", "share_cart", "Email my cart to asha@example.com"),
            G("allowed_recipients", "asha@example.com", "Email my cart to asha@example.com"),
            G("allowed_recipients", "bob@example.com", "Don't email bob@example.com"),
        ],
    )
    r = ground(m)
    assert r.mandate.allowed_recipients == ["asha@example.com"]
    assert {"GRANT_NEGATED", "GRANT_MISSING_SPAN"} <= codes(r)


def test_without_asking_is_not_a_refusal_but_dont_buy_is():
    r = ground(grounded())
    assert "checkout" in r.mandate.allowed_actions
    text = "Don't buy anything, just compare laptops under ₹60,000."
    m = grounded(
        user_text=text,
        auto_approve_under_paise=0,
        allowed_seller_countries=None,
        grants=[G("allowed_actions:checkout", "checkout", "Don't buy anything")],
    )
    assert "checkout" not in ground(m).mandate.allowed_actions


def test_invalid_requested_addon_grants_are_dropped():
    r = ground(
        grounded(grants=[*grounded().grants, G("requested_addon", "PP-1001", "no subscriptions")])
    )
    assert all(g.field != "requested_addon" for g in r.mandate.grants)


def test_unknown_grants_and_actions_are_dropped():
    r = ground(
        grounded(
            allowed_actions=["search", "read", "checkout", "wire_money"],
            grants=[*grounded().grants, G("god_mode", "true", "Laptop under ₹60,000 all-in")],
        )
    )
    assert "wire_money" not in r.mandate.allowed_actions and "GRANT_UNKNOWN" in codes(r)
    assert all(g.field != "god_mode" for g in r.mandate.grants)


# -- narrowing fields --------------------------------------------------------------------------
def test_budget_must_be_an_amount_the_user_said():
    r = ground(grounded(budget_total_paise=9_000_000))
    assert r.clarifications and "CLARIFICATION_NEEDED" in codes(r)


def test_countries_wider_than_the_user_named_are_trimmed():
    r = ground(grounded(allowed_seller_countries=["IN", "US"]))
    assert r.mandate.allowed_seller_countries == ["IN"] and "GRANT_SPAN_NOT_IN_TEXT" in codes(r)


def test_countries_the_user_never_named_are_cleared_to_nobody_with_a_question():
    r = ground(grounded(allowed_seller_countries=["US"]))
    assert r.mandate.allowed_seller_countries == [] and r.clarifications


def test_unstated_country_restriction_is_accepted_as_narrowing():
    text = "Buy a mouse under ₹2,000."
    m = grounded(
        user_text=text,
        allowed_seller_countries=["IN"],
        budget_total_paise=200_000,
        auto_approve_under_paise=0,
        require_refundable=False,
        grants=[G("allowed_actions:checkout", "checkout", "Buy a mouse")],
    )
    assert ground(m).mandate.allowed_seller_countries == ["IN"]


def test_model_clarifications_are_kept():
    r = ground(grounded(clarifications_needed=["Which colour?"]))
    assert "Which colour?" in r.clarifications


def test_grounding_never_confirms():
    assert ground(grounded(user_confirmed=False)).mandate.user_confirmed is False
