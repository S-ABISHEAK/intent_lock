"""Nov 1 checkpoint round 2 (D-037): the validator proposes skip-approval only from rigid sentence
shapes, questions and retractions never grant, answers only narrow, and the user types the
auto-approve amount before a session starts (SPEC §9; invariant 10)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from intentlock.api import create_app
from intentlock.mandate.compiler import MandateDraft, compile_mandate, narrow, readback
from intentlock.mandate.grounding import (
    ANSWERS_MARK,
    addon_granted,
    foreign_money,
    ground,
    parse_money_paise,
)
from tests import test_api
from tests.factories import DEMO_TEXT, grounded_mandate, make_mandate
from tests.test_api import OP, H

BUY = ["search", "read", "add_to_cart", "checkout"]
env = test_api.env  # the gateway, approval broker and shop fixture


def G(field, value, span):
    return {"field": field, "value": value, "span": span}


def CK(span):
    return G("allowed_actions:checkout", "checkout", span)


def AUTO(paise, span):
    return G("auto_approve_under_paise", str(paise), span)


def run(text, **kw):
    base = dict(
        user_text=text,
        user_confirmed=False,
        expires_at=4_102_444_800,
        allowed_actions=["search", "read"],
        budget_total_paise=None,
        auto_approve_under_paise=0,
        allowed_seller_countries=None,
        allow_recurring=False,
        require_refundable=False,
        max_quantity=None,
        grants=[],
        clarifications_needed=[],
    )
    base.update(kw)
    r = ground(make_mandate(**base))
    assert ground(r.mandate).mandate == r.mandate  # idempotent
    return r


def codes(r):
    return {i.code for i in r.issues}


# -- 1. auto-approve only from the four sentence shapes --------------------------------------------
@pytest.mark.parametrize(
    "text,span,paise",
    [
        (
            "Buy a mouse; you can buy accessories under ₹2,000 without asking.",
            "you can buy accessories under ₹2,000 without asking",
            200_000,
        ),
        (
            "Buy a mouse. Auto-approve purchases under ₹2,000.",
            "Auto-approve purchases under ₹2,000",
            200_000,
        ),
        (
            "Buy a mouse. Ask me before buying anything over ₹2,000.",
            "Ask me before buying anything over ₹2,000",
            200_000,
        ),
        (
            "Buy a mouse. No need to ask for anything below ₹1,500.",
            "No need to ask for anything below ₹1,500",
            150_000,
        ),
        (
            "Buy a mouse. Feel free to buy cables up to 2k without asking me first.",
            "Feel free to buy cables up to 2k without asking me first",
            200_000,
        ),
        (
            "Buy a mouse. You don't need to ask before buying accessories under rs 500.",
            "You don't need to ask before buying accessories under rs 500",
            50_000,
        ),
    ],
)
def test_the_allowed_shapes_grant(text, span, paise):
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=paise,
        grants=[CK("Buy a mouse" if "Buy a mouse" in text else span), AUTO(paise, span)],
    )
    assert r.mandate.auto_approve_under_paise == paise, r.issues
    assert r.clarifications == []  # a skip-approval limit is not a budget


@pytest.mark.parametrize(
    "text,paise",
    [
        (
            "Buy me a laptop under ₹60,000 and use my saved address without asking me for it.",
            6_000_000,
        ),
        ("Buy a smart lock under ₹5,000 that unlocks without asking for a PIN.", 500_000),
        (
            "Find a laptop under ₹60,000 that I can use without permission from IT, then buy it.",
            6_000_000,
        ),
        ("Ask me before buying anything — under or over ₹500.", 50_000),
        ("Buy a mouse and auto-approve it if it is under ₹500 or so.", 50_000),
        ("Buy accessories under ₹2,000 without asking about the colour.", 200_000),
    ],
)
def test_approval_look_alikes_grant_nothing(text, paise):
    span = text.rstrip(".")
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=paise,
        grants=[CK(span), AUTO(paise, span)],
    )
    assert r.mandate.auto_approve_under_paise == 0


def test_a_shaped_clause_that_refuses_its_items_grants_nothing():
    text = "You can buy nothing under ₹2,000 without asking."
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=200_000,
        grants=[CK(text.rstrip(".")), AUTO(200_000, text.rstrip("."))],
    )
    assert r.mandate.auto_approve_under_paise == 0


def test_dont_ask_me_for_permission_is_not_permission():
    text = "Don't ask me for permission to buy anything, just compare."
    span = "Don't ask me for permission to buy anything"
    r = run(text, allowed_actions=BUY, grants=[CK(span)])
    assert "checkout" not in r.mandate.allowed_actions


def test_a_look_alike_does_not_hide_the_budget_either():
    text = "Buy me a laptop under ₹60,000 and use my saved address without asking me for it."
    r = run(text, allowed_actions=BUY, grants=[CK(text.rstrip("."))])
    assert r.clarifications and "budget_total_paise" in {i.field for i in r.issues}


# -- 2. questions and retractions never grant -----------------------------------------------------
@pytest.mark.parametrize(
    "text,span",
    [
        (
            "Can you buy accessories under ₹2,000 without asking? No, always ask me.",
            "buy accessories under ₹2,000 without asking",
        ),
        (
            "Should you buy accessories under ₹2,000 without asking? Nope.",
            "buy accessories under ₹2,000 without asking",
        ),
        (
            "Buy accessories under ₹2,000 without asking. Actually, scratch that.",
            "Buy accessories under ₹2,000 without asking",
        ),
        (
            "Buy accessories under ₹2,000 without asking. On second thought, don't.",
            "Buy accessories under ₹2,000 without asking",
        ),
        (
            "Buy accessories under ₹2,000 without asking. Never mind.",
            "Buy accessories under ₹2,000 without asking",
        ),
    ],
)
def test_questions_and_retractions_revoke_auto_approve_and_checkout(text, span):
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=200_000,
        grants=[CK(span), AUTO(200_000, span)],
    )
    assert r.mandate.auto_approve_under_paise == 0 and "checkout" not in r.mandate.allowed_actions


def test_a_retraction_revokes_every_widening_permission():
    text = (
        "Buy a mouse, share my cart with asha@example.com, get me the monthly phone plan"
        " and add gift wrap GW-0799. Actually, scratch that."
    )
    r = run(
        text,
        allowed_actions=[*BUY, "share_cart"],
        allowed_recipients=["asha@example.com"],
        allow_recurring=True,
        grants=[
            CK("Buy a mouse"),
            G("allowed_actions:share_cart", "share_cart", "share my cart with asha@example.com"),
            G("allowed_recipients", "asha@example.com", "share my cart with asha@example.com"),
            G("allow_recurring", "true", "get me the monthly phone plan"),
            G("requested_addon", "GW-0799", "add gift wrap GW-0799"),
        ],
    )
    m = r.mandate
    assert m.allowed_actions == ["search", "read", "add_to_cart"] and m.allowed_recipients == []
    assert m.allow_recurring is False and m.grants == [] and "GRANT_RETRACTED" in codes(r)
    assert not addon_granted("GW-0799", "add gift wrap GW-0799", text)


def test_a_shaped_question_is_not_permission():
    text = "Buy a mouse. Auto-approve purchases under ₹2,000?"
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=200_000,
        grants=[CK("Buy a mouse"), AUTO(200_000, "Auto-approve purchases under ₹2,000")],
    )
    assert r.mandate.auto_approve_under_paise == 0 and "GRANT_IN_QUESTION" in codes(r)


def test_a_recipient_in_a_question_is_not_granted():
    span = "Share my cart with asha@example.com"
    r = run(
        f"{span}?",
        allowed_actions=["search", "read", "share_cart"],
        allowed_recipients=["asha@example.com"],
        grants=[
            G("allowed_actions:share_cart", "share_cart", span),
            G("allowed_recipients", "asha@example.com", span),
        ],
    )
    assert r.mandate.allowed_recipients == []
    assert "GRANT_IN_QUESTION" in {
        i.code for i in r.issues if i.field.startswith("allowed_recipients")
    }


def test_a_polite_question_is_not_an_instruction():
    r = run("Can you buy me a mouse?", allowed_actions=BUY, grants=[CK("buy me a mouse")])
    assert "checkout" not in r.mandate.allowed_actions and "GRANT_IN_QUESTION" in codes(r)


def test_an_addon_named_in_a_question_is_not_requested():
    assert not addon_granted(
        "GW-0799", "add gift wrap GW-0799", "Laptop. Should I add gift wrap GW-0799?"
    )


# -- 3. refusal words -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,span",
    [
        ("Buy anything? No, just compare prices.", "Buy anything"),
        ("Purchasing is prohibited; only compare mice.", "Purchasing is prohibited"),
        ("Buying is off the table, just compare.", "Buying is off the table"),
        ("Laptop buy na karna, bas compare karo.", "Laptop buy na karna"),
        ("Wait before buying anything, just compare.", "Wait before buying anything"),
        ("Buying is forbidden here.", "Buying is forbidden here"),
        ("Buying is banned here.", "Buying is banned here"),
        ("Checkout is disabled for now.", "Checkout is disabled for now"),
        ("Buy it? Nah.", "Buy it"),
    ],
)
def test_refused_checkout_wordings(text, span):
    r = run(text, allowed_actions=BUY, grants=[CK(span)])
    assert "checkout" not in r.mandate.allowed_actions


@pytest.mark.parametrize(
    "line",
    [
        "Turn off auto-approve for items under ₹500.",
        "Disable auto-approve under ₹500.",
        "Switch off auto-approve under ₹500.",
        "Auto-approve is banned, even under ₹500.",
    ],
)
def test_switching_auto_approve_off_never_grants_it(line):
    text = f"Buy a mouse. {line}"
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=50_000,
        grants=[CK("Buy a mouse"), AUTO(50_000, line.rstrip("."))],
    )
    assert r.mandate.auto_approve_under_paise == 0


def test_a_refusal_elsewhere_still_revokes_a_shaped_grant():
    text = "Auto-approve purchases under ₹500. Turn off auto-approve."
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=50_000,
        grants=[
            CK("Auto-approve purchases under ₹500"),
            AUTO(50_000, "Auto-approve purchases under ₹500"),
        ],
    )
    assert r.mandate.auto_approve_under_paise == 0


# -- 4. recurring needs a permission, not the word "plan" ------------------------------------------
@pytest.mark.parametrize(
    "text,span",
    [
        ("I plan to buy a laptop under ₹60,000.", "I plan to buy a laptop"),
        ("Buy a mouse for my monthly team offsite.", "Buy a mouse for my monthly team offsite"),
    ],
)
def test_plan_and_monthly_alone_do_not_grant_recurring(text, span):
    r = run(text, allow_recurring=True, grants=[G("allow_recurring", "true", span)])
    assert r.mandate.allow_recurring is False


def test_a_real_subscription_request_is_still_granted():
    r = run(
        "I'm ok with a monthly subscription for the phone plan.",
        allow_recurring=True,
        grants=[G("allow_recurring", "true", "I'm ok with a monthly subscription")],
    )
    assert r.mandate.allow_recurring is True


# -- 5. money units -------------------------------------------------------------------------------
def test_paise_are_paise_and_foreign_money_is_not_rupees():
    assert parse_money_paise("under 50,000 paise") == [50_000]
    assert parse_money_paise("under ₹500") == [50_000]
    assert parse_money_paise("under $500") == [] and foreign_money("under $500")
    assert parse_money_paise("500 USD") == [] and foreign_money("about 500 euros")
    assert not foreign_money(DEMO_TEXT)


def test_paise_cannot_be_read_as_rupees():
    text = "Buy a cable; auto-approve anything under 50,000 paise."
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=5_000_000,
        grants=[CK("Buy a cable"), AUTO(5_000_000, "auto-approve anything under 50,000 paise")],
    )
    assert r.mandate.auto_approve_under_paise == 0 and "GRANT_NUMBER_MISMATCH" in codes(r)


def test_foreign_amounts_never_ground_a_grant_or_a_budget():
    text = "Buy a cable; auto-approve anything under $500."
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=50_000,
        grants=[CK("Buy a cable"), AUTO(50_000, "auto-approve anything under $500")],
    )
    assert r.mandate.auto_approve_under_paise == 0
    r = run(
        "Buy a laptop, budget $700.",
        allowed_actions=BUY,
        budget_total_paise=70_000,
        grants=[CK("Buy a laptop")],
    )
    assert r.clarifications


def test_any_foreign_amount_is_a_question():
    assert run(
        "Buy a cable for $5.", allowed_actions=BUY, grants=[CK("Buy a cable for $5")]
    ).clarifications


# -- 6. budget ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,budget",
    [
        ("Laptop, budget ₹60,000 (not ₹90,000). Buy it.", 9_000_000),
        ("Keep this under ₹60,000 even though my card limit is ₹1,00,000. Buy it.", 10_000_000),
    ],
)
def test_a_refused_or_bigger_amount_is_not_the_budget(text, budget):
    r = run(text, allowed_actions=BUY, budget_total_paise=budget, grants=[CK("Buy it")])
    assert r.clarifications


@pytest.mark.parametrize(
    "text,budget",
    [
        ("Laptop, budget ₹60,000 (not ₹90,000). Buy it.", 6_000_000),
        ("Buy it, no more than ₹60,000.", 6_000_000),
        ("Buy it. My budget is ₹90,000 not ₹60,000.", 9_000_000),
    ],
)
def test_the_stated_budget_still_grounds(text, budget):
    r = run(text, allowed_actions=BUY, budget_total_paise=budget, grants=[CK("Buy it")])
    assert r.clarifications == [], r.issues


# -- 7. countries and recipients -------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "I'm an American living in India; buy from an Indian seller.",
        "GET US ONE FROM INDIAN SELLERS.",
        "I'm from the US. Buy from an Indian seller.",
    ],
)
def test_a_country_counts_only_as_a_seller(text):
    r = run(text, allowed_seller_countries=["IN", "US"])
    assert r.mandate.allowed_seller_countries == ["IN"]


def test_a_possessive_is_not_a_recipient():
    text = "Send me the cart along with asha@example.com's wishlist."
    r = run(
        text,
        allowed_actions=["search", "read", "share_cart"],
        allowed_recipients=["asha@example.com"],
        grants=[
            G("allowed_actions:share_cart", "share_cart", text.rstrip(".")),
            G("allowed_recipients", "asha@example.com", text.rstrip(".")),
        ],
    )
    assert r.mandate.allowed_recipients == []


@pytest.mark.parametrize(
    "text",
    [
        "Share my cart with asha@example.com's manager.",
        "Send me the cart along with asha@example.com.",
    ],
)
def test_possessives_and_along_with_are_not_targets(text):
    span = text.rstrip(".")
    r = run(
        text,
        allowed_actions=["search", "read", "share_cart"],
        allowed_recipients=["asha@example.com"],
        grants=[
            G("allowed_actions:share_cart", "share_cart", span),
            G("allowed_recipients", "asha@example.com", span),
        ],
    )
    assert r.mandate.allowed_recipients == []


def test_a_direct_send_still_grants_the_recipient():
    text = "Email my cart to asha@example.com."
    r = run(
        text,
        allowed_actions=["search", "read", "share_cart"],
        allowed_recipients=["asha@example.com"],
        grants=[
            G("allowed_actions:share_cart", "share_cart", "Email my cart to asha@example.com"),
            G("allowed_recipients", "asha@example.com", "Email my cart to asha@example.com"),
        ],
    )
    assert r.mandate.allowed_recipients == ["asha@example.com"]


# -- 8. answers can only narrow -------------------------------------------------------------------
class Stub:
    def __init__(self, draft):
        self.draft = draft

    def structured(self, messages, schema, model=None):
        return MandateDraft.model_validate(self.draft)


def _draft(**kw):
    base = dict(
        goal="g",
        allowed_actions=["search", "read"],
        budget_total_paise=None,
        auto_approve_under_paise=0,
        allowed_seller_countries=None,
        allow_recurring=False,
        require_refundable=False,
        max_quantity=None,
        allowed_recipients=[],
        grants=[],
        clarifications_needed=[],
    )
    base.update(kw)
    return base


def test_an_answer_cannot_widen_the_mandate():
    span = "buy accessories under ₹2,000 without asking"
    d = _draft(
        allowed_actions=BUY,
        auto_approve_under_paise=200_000,
        grants=[CK(span), AUTO(200_000, span)],
    )
    q = "Is there anything I must never do on your behalf?"
    r = compile_mandate(
        "Find me a mouse.", Stub(d), now=0, answers=[{"question": q, "answer": span}]
    )
    assert r.mandate.auto_approve_under_paise == 0 and "checkout" not in r.mandate.allowed_actions
    assert ANSWERS_MARK in r.mandate.user_text and q not in r.mandate.user_text


def test_answers_can_settle_budget_and_countries_and_can_refuse():
    d = _draft(
        allowed_actions=BUY,
        budget_total_paise=5_000_000,
        allowed_seller_countries=["US"],
        grants=[CK("Buy a laptop")],
    )
    answers = [
        {"question": "What is your budget?", "answer": "₹50,000"},
        {"question": "Which seller countries are allowed?", "answer": "US"},
    ]
    r = compile_mandate("Buy a laptop from German sellers.", Stub(d), now=0, answers=answers)
    assert r.mandate.budget_total_paise == 5_000_000 and r.mandate.allowed_seller_countries == [
        "US"
    ]
    assert "checkout" in r.mandate.allowed_actions and r.clarifications == []
    refuse = [{"question": "Anything else?", "answer": "don't buy anything, just compare"}]
    r = compile_mandate(
        "Buy a laptop.",
        Stub(_draft(allowed_actions=BUY, grants=[CK("Buy a laptop")])),
        now=0,
        answers=refuse,
    )
    assert "checkout" not in r.mandate.allowed_actions


def test_each_answer_is_its_own_sentence():
    d = _draft(allowed_actions=BUY, allowed_seller_countries=["US"], grants=[CK("Buy a laptop")])
    answers = [
        {"question": "Which seller countries?", "answer": "US"},
        {"question": "Subscriptions?", "answer": "no subscriptions"},
    ]
    r = compile_mandate("Buy a laptop", Stub(d), now=0, answers=answers)
    assert "checkout" in r.mandate.allowed_actions  # "no ..." is not glued onto "Buy a laptop"
    assert r.mandate.allowed_seller_countries == ["US"]  # ...nor onto the answer before it


def test_an_addon_named_only_in_an_answer_is_not_requested():
    d = _draft(
        allowed_actions=BUY,
        grants=[CK("Buy a laptop"), G("requested_addon", "GW-0799", "add gift wrap GW-0799")],
    )
    answers = [{"question": "Anything else?", "answer": "add gift wrap GW-0799"}]
    r = compile_mandate("Buy a laptop.", Stub(d), now=0, answers=answers)
    assert all(g.field != "requested_addon" for g in r.mandate.grants)


def test_a_retraction_word_in_an_answer_does_not_wipe_the_request():
    d = _draft(allowed_actions=BUY, grants=[CK("Buy a laptop")])
    answers = [{"question": "Do you want gift wrap too?", "answer": "nope"}]
    r = compile_mandate("Buy a laptop.", Stub(d), now=0, answers=answers)
    assert "checkout" in r.mandate.allowed_actions


# -- 9. per-permission confirmation ----------------------------------------------------------------
def test_readback_names_the_field_each_grant_would_drop():
    lines = readback(ground(grounded_mandate(user_confirmed=False)))
    fields = {b["field"] for b in lines if b["kind"] == "grant"}
    assert fields == {"allowed_actions:checkout", "auto_approve_under_paise"}


@pytest.mark.parametrize(
    "field,check",
    [
        ("allowed_actions:checkout", lambda m: "checkout" not in m["allowed_actions"]),
        ("auto_approve_under_paise", lambda m: m["auto_approve_under_paise"] == 0),
    ],
)
def test_declining_a_permission_narrows_and_still_grounds(field, check):
    from intentlock.models import Mandate

    m = grounded_mandate(user_confirmed=False).model_dump(mode="json")
    out = narrow(m, field)
    assert check(out) and all(g["field"] != field for g in out["grants"])
    r = ground(Mandate.model_validate(out))
    assert r.mandate.model_dump(mode="json") == out and r.clarifications == []


def test_recipient_recurring_and_addon_can_be_declined():
    m = make_mandate(
        allowed_recipients=["a@example.com", "b@example.com"],
        allow_recurring=True,
        grants=[
            G("allowed_recipients", "a@example.com", "x"),
            G("allowed_recipients", "b@example.com", "y"),
            G("allow_recurring", "true", "z"),
            G("requested_addon", "GW-0799", "w"),
        ],
    ).model_dump(mode="json")
    out = narrow(
        narrow(narrow(m, "allowed_recipients:a@example.com"), "allow_recurring"),
        "requested_addon:GW-0799",
    )
    assert out["allowed_recipients"] == ["b@example.com"] and out["allow_recurring"] is False
    assert [g["value"] for g in out["grants"]] == ["b@example.com"]


def _client(env):
    gw, broker, _ = env
    return TestClient(create_app(gw, broker, operator_token=OP))


def test_session_needs_the_typed_auto_approve_amount(env):
    m = grounded_mandate().model_dump(mode="json")
    c = _client(env)
    assert c.post("/session", headers=H, json={"mandate": m}).status_code == 422
    wrong = {"mandate": m, "confirm_auto_approve_paise": 2_000_000}
    assert c.post("/session", headers=H, json=wrong).status_code == 422
    ok = {"mandate": m, "confirm_auto_approve_paise": 200_000}
    assert c.post("/session", headers=H, json=ok).status_code == 200


def test_session_without_auto_approve_needs_no_amount(env):
    m = narrow(grounded_mandate().model_dump(mode="json"), "auto_approve_under_paise")
    assert _client(env).post("/session", headers=H, json={"mandate": m}).status_code == 200


# -- end to end: the round-2 bypasses through compile -> confirm -> session -> checkout --------
@pytest.mark.parametrize(
    "text,span,ck",
    [
        (
            "Buy me a laptop under ₹60,000 and use my saved address without asking me for it.",
            "Buy me a laptop under ₹60,000 and use my saved address without asking me for it",
            None,
        ),
        (
            "Buy a laptop. Turn off auto-approve for anything under ₹60,000.",
            "Turn off auto-approve for anything under ₹60,000",
            "Buy a laptop",
        ),
        (
            "Buy a laptop under ₹60,000 without asking. Actually, scratch that.",
            "Buy a laptop under ₹60,000 without asking",
            None,
        ),
    ],
)
def test_round_two_bypasses_never_skip_the_card(env, text, span, ck):
    gw, broker, store = env
    d = _draft(
        allowed_actions=BUY,
        auto_approve_under_paise=6_000_000,
        grants=[CK(ck or span), AUTO(6_000_000, span)],
    )
    app = create_app(
        gw,
        broker,
        operator_token=OP,
        compiler=lambda t: compile_mandate(t, Stub(d), now=int(time.time())),
    )
    c = TestClient(app)
    body = c.post("/mandate/compile", headers=H, json={"user_text": text}).json()
    assert body["mandate"]["auto_approve_under_paise"] == 0
    m = {**body["mandate"], "user_confirmed": True}
    r = c.post("/session", headers=H, json={"mandate": m, "confirm_auto_approve_paise": 6_000_000})
    if r.status_code == 200:
        store.add_to_cart("LAP-1201")
        result = {}

        def commit():
            result["out"] = gw.commit(
                gw.session, "checkout", {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}
            )

        t = threading.Thread(target=commit)
        t.start()
        for _ in range(200):
            if broker.pending() or not t.is_alive():
                break
            time.sleep(0.01)
        assert store.orders == []
        gw.end_session()
        t.join(10)
    assert store.orders == []


# -- final review (D-037): narrowing rules can't be loosened by an unrelated mention ---------------
def test_more_than_one_seller_country_is_always_a_question():
    text = (
        "Only Indian sellers; you can buy accessories under ₹5,000 without asking."
        " My old headset came from a US seller."
    )
    span = "you can buy accessories under ₹5,000 without asking"
    r = run(
        text,
        allowed_actions=BUY,
        auto_approve_under_paise=500_000,
        allowed_seller_countries=["IN", "US"],
        grants=[CK(span), AUTO(500_000, span)],
    )
    assert r.clarifications and any(i.field == "allowed_seller_countries" for i in r.issues)


def test_answered_countries_settle_the_question():
    d = _draft(allowed_actions=BUY, allowed_seller_countries=["IN", "US"], grants=[CK("Buy it")])
    text = "Buy it from Indian sellers. My old one came from a US seller."
    r = compile_mandate(text, Stub(d), now=0, answers=[{"question": "Which?", "answer": "India"}])
    assert r.mandate.allowed_seller_countries == ["IN"] and r.clarifications == []
    both = [{"question": "Which?", "answer": "Indian or US sellers"}]
    r = compile_mandate(text, Stub(d), now=0, answers=both)
    assert r.mandate.allowed_seller_countries == ["IN", "US"] and r.clarifications == []


@pytest.mark.parametrize(
    "text,budget,answers",
    [
        ("Usually laptops cost up to ₹80,000; buy me one under ₹50,000.", 8_000_000, []),
        ("Buy a laptop under ₹50,000, my old one cost no more than ₹90,000.", 9_000_000, []),
        ("Buy a laptop under ₹60,000.", 8_500_000, [{"question": "Price?", "answer": "₹85,000"}]),
    ],
)
def test_the_budget_is_never_above_the_smallest_stated_amount(text, budget, answers):
    d = _draft(allowed_actions=BUY, budget_total_paise=budget, grants=[CK("buy")])
    r = compile_mandate(text, Stub(d), now=0, answers=answers)
    assert r.clarifications and any(i.field == "budget_total_paise" for i in r.issues)


def test_overlong_requests_are_refused_before_grounding(env):
    c = TestClient(create_app(*env[:2], operator_token=OP, compiler=lambda t, a=None: None))
    r = c.post("/mandate/compile", headers=H, json={"user_text": "monthly x " * 500})
    assert r.status_code == 422
    m = grounded_mandate(user_text=DEMO_TEXT + " Thanks" + " x" * 4_500).model_dump(mode="json")
    body = {"mandate": m, "confirm_auto_approve_paise": 200_000}
    assert c.post("/session", headers=H, json=body).status_code == 422
    short = {**body, "mandate": {**m, "user_text": DEMO_TEXT}}
    assert c.post("/session", headers=H, json=short).status_code == 200
