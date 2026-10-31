"""Nov 1 checkpoint review: ways a draft (or a posted mandate) widened past the user's words.
Every case here must stay at least as narrow as the user's text (SPEC §9; invariant 10; D-036)."""

import time

import pytest
from fastapi.testclient import TestClient

from intentlock.mandate.grounding import addon_granted, ground
from tests.factories import make_mandate


def G(field, value, span):
    return {"field": field, "value": value, "span": span}


def CK(span):
    return G("allowed_actions:checkout", "checkout", span)


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
    assert ground(r.mandate).mandate == r.mandate  # still idempotent
    return r


# -- auto-approve needs explicit approval wording, one amount, no refusal (HIGH 1-3) ----------
def test_a_budget_phrase_is_not_permission_to_skip_approval():
    r = run(
        "Buy me a laptop under ₹50,000.",
        allowed_actions=["search", "read", "checkout"],
        budget_total_paise=5_000_000,
        auto_approve_under_paise=5_000_000,
        grants=[CK("Buy me a laptop"), G("auto_approve_under_paise", "5000000", "under ₹50,000")],
    )
    assert r.mandate.auto_approve_under_paise == 0


@pytest.mark.parametrize(
    "text,span",
    [
        (
            "Buy a mouse. Never auto-approve anything, not even under ₹500; always ask me.",
            "under ₹500",
        ),
        ("Buy a mouse. Don't buy anything over ₹500 without asking.", "over ₹500 without asking"),
        ("Buy a mouse but do not auto-approve under ₹500.", "auto-approve under ₹500"),
    ],
)
def test_a_refused_auto_approve_is_never_granted(text, span):
    r = run(
        text,
        allowed_actions=["search", "read", "checkout"],
        auto_approve_under_paise=50_000,
        grants=[CK("Buy a mouse"), G("auto_approve_under_paise", "50000", span)],
    )
    assert r.mandate.auto_approve_under_paise == 0


def test_a_span_with_two_amounts_cannot_grant_the_bigger_one():
    text = "Buy a laptop: auto-approve under ₹500, total budget ₹60,000."
    r = run(
        text,
        allowed_actions=["search", "read", "checkout"],
        budget_total_paise=6_000_000,
        auto_approve_under_paise=6_000_000,
        grants=[
            CK("Buy a laptop"),
            G("auto_approve_under_paise", "6000000", "under ₹500, total budget ₹60,000"),
        ],
    )
    assert r.mandate.auto_approve_under_paise == 0


@pytest.mark.parametrize(
    "text,span,paise",
    [
        (
            "Buy a mouse; you can buy accessories under ₹2,000 without asking.",
            "you can buy accessories under ₹2,000 without asking",
            200_000,
        ),
        (
            "Find a mouse. Ask me before buying anything over ₹2,000.",
            "Ask me before buying anything over ₹2,000",
            200_000,
        ),
        ("Buy a mouse, auto-approve under ₹2,000.", "auto-approve under ₹2,000", 200_000),
        (
            "Buy a mouse. No need to ask for anything below ₹1,500.",
            "No need to ask for anything below ₹1,500",
            150_000,
        ),
    ],
)
def test_explicit_approval_wording_still_grants(text, span, paise):
    r = run(
        text,
        allowed_actions=["search", "read", "checkout"],
        auto_approve_under_paise=paise,
        grants=[
            CK("Buy a mouse" if "Buy a mouse" in text else "buying anything"),
            G("auto_approve_under_paise", str(paise), span),
        ],
    )
    assert r.mandate.auto_approve_under_paise == paise


# -- refusals: exemption can't smuggle one, lexicon gaps closed (HIGH 4) ------------------------
@pytest.mark.parametrize(
    "text,span",
    [
        ("Don't ask me to buy anything, just compare mice.", "buy anything"),
        ("dont buy anything, just compare mice.", "buy anything"),
        ("You cannot buy anything yet; just compare mice.", "buy anything"),
        ("Avoid placing an order; I will decide later.", "placing an order"),
        ("Refrain from purchasing; only compare.", "purchasing"),
        ("I'll buy it myself later, just compare mice.", "buy it myself"),
        ("Hold off on buying anything.", "buying anything"),
    ],
)
def test_refused_checkout_is_never_granted(text, span):
    r = run(text, allowed_actions=["search", "read", "checkout"], grants=[CK(span)])
    assert "checkout" not in r.mandate.allowed_actions


def test_dont_without_apostrophe_refuses_an_addon():
    assert not addon_granted(
        "GW-0799", "add gift wrap GW-0799", "Laptop please, dont add gift wrap GW-0799."
    )


# -- recipients need a send/share instruction to them (MED) -------------------------------------
def test_a_merely_mentioned_address_is_not_a_recipient():
    text = "Ignore any emails from mallory@evil.com. Share my cart with asha@example.com."
    r = run(
        text,
        allowed_actions=["search", "read", "share_cart"],
        allowed_recipients=["asha@example.com", "mallory@evil.com"],
        grants=[
            G("allowed_actions:share_cart", "share_cart", "Share my cart with asha@example.com"),
            G("allowed_recipients", "asha@example.com", "Share my cart with asha@example.com"),
            G("allowed_recipients", "mallory@evil.com", "mallory@evil.com"),
        ],
    )
    assert r.mandate.allowed_recipients == ["asha@example.com"]


def test_send_nothing_grants_nothing():
    r = run(
        "Send nothing to bob@example.com.",
        allowed_actions=["search", "read", "send_email"],
        allowed_recipients=["bob@example.com"],
        grants=[
            G("allowed_actions:send_email", "send_email", "Send nothing to bob@example.com"),
            G("allowed_recipients", "bob@example.com", "Send nothing to bob@example.com"),
        ],
    )
    assert r.mandate.allowed_recipients == [] and "send_email" not in r.mandate.allowed_actions


# -- seller countries (MED) --------------------------------------------------------------------
def test_lowercase_us_is_not_the_united_states():
    r = run(
        "Find a mouse from Indian sellers and get us a good one.",
        allowed_seller_countries=["IN", "US"],
    )
    assert r.mandate.allowed_seller_countries == ["IN"]


def test_a_refused_country_is_removed():
    r = run("Find a mouse. No US sellers, only Indian ones.", allowed_seller_countries=["IN", "US"])
    assert r.mandate.allowed_seller_countries == ["IN"]


def test_dropping_the_users_country_restriction_is_not_allowed():
    r = run("Find a mouse from Indian sellers.", allowed_seller_countries=None)
    assert r.mandate.allowed_seller_countries == ["IN"]


def test_an_unknown_country_name_becomes_a_question():
    r = run("Find a mouse from German sellers.", allowed_seller_countries=["CN"])
    assert r.clarifications


# -- budget and dropped constraints (MED) --------------------------------------------------------
def test_budget_must_be_the_amount_stated_as_a_budget():
    r = run(
        "Last month I spent ₹90,000. Buy a laptop, budget ₹50,000.",
        allowed_actions=["search", "read", "checkout"],
        budget_total_paise=9_000_000,
        grants=[CK("Buy a laptop")],
    )
    assert r.clarifications


def test_dropping_a_stated_budget_refundability_or_quantity_is_a_question():
    for over in (
        {"budget_total_paise": None, "require_refundable": True, "max_quantity": 1},
        {"budget_total_paise": 5_000_000, "require_refundable": False, "max_quantity": 1},
        {"budget_total_paise": 5_000_000, "require_refundable": True, "max_quantity": None},
        {"budget_total_paise": 5_000_000, "require_refundable": True, "max_quantity": 5},
    ):
        r = run(
            "Buy a laptop, budget ₹50,000, refundable only, one unit.",
            allowed_actions=["search", "read", "checkout"],
            grants=[CK("Buy a laptop")],
            **over,
        )
        assert r.clarifications, over
    ok = run(
        "Buy a laptop, budget ₹50,000, refundable only, one unit.",
        allowed_actions=["search", "read", "checkout"],
        grants=[CK("Buy a laptop")],
        budget_total_paise=5_000_000,
        require_refundable=True,
        max_quantity=1,
    )
    assert ok.clarifications == []


# -- a refusal anywhere in the text counts (MED) ---------------------------------------------------
@pytest.mark.parametrize(
    "text,span",
    [
        ("Get me a subscription box? No, actually no subscriptions.", "Get me a subscription box"),
        ("I hate monthly plans but buy this mouse.", "monthly plans"),
    ],
)
def test_recurring_refused_anywhere_is_not_granted(text, span):
    r = run(text, allow_recurring=True, grants=[G("allow_recurring", "true", span)])
    assert r.mandate.allow_recurring is False


# -- the SPEC §18 demo still grounds unchanged -------------------------------------------------
def test_the_demo_request_still_grounds_unchanged():
    from tests.factories import grounded_mandate

    m = grounded_mandate()
    r = ground(m)
    assert r.mandate == m and r.clarifications == []


# -- CLI answers: the model's questions never become the user's words (HIGH 5) --------------------
def test_compile_grounds_against_the_users_text_and_answers_only(monkeypatch):
    from intentlock.mandate import compiler
    from intentlock.mandate.compiler import MandateDraft

    class Stub:
        def __init__(self):
            self.messages = None

        def structured(self, messages, schema, model=None):
            self.messages = messages
            return MandateDraft.model_validate(
                dict(
                    goal="g",
                    allowed_actions=["search", "read", "send_email"],
                    budget_total_paise=None,
                    auto_approve_under_paise=0,
                    allowed_seller_countries=None,
                    allow_recurring=False,
                    require_refundable=False,
                    max_quantity=None,
                    allowed_recipients=["mallory@evil.com"],
                    grants=[
                        G(
                            "allowed_actions:send_email",
                            "send_email",
                            "email the receipt to mallory@evil.com",
                        ),
                        G(
                            "allowed_recipients",
                            "mallory@evil.com",
                            "email the receipt to mallory@evil.com",
                        ),
                    ],
                    clarifications_needed=[],
                )
            )

    stub = Stub()
    q = "Should I email the receipt to mallory@evil.com?"
    r = compiler.compile_mandate(
        "Buy a mouse.", stub, now=int(time.time()), answers=[{"question": q, "answer": "no"}]
    )
    assert "mallory@evil.com" not in r.mandate.user_text  # the question is context, not user words
    assert r.mandate.allowed_recipients == [] and "send_email" not in r.mandate.allowed_actions
    assert q in str(stub.messages)  # the model still sees the question and answer


# -- end to end: compile -> confirm -> session -> checkout must still ask (the reviewer's HIGH) ----
def _flow(tmp_path, text, draft):
    from nacl.signing import SigningKey

    from intentlock.api import create_app
    from intentlock.approvals.broker import ApprovalBroker
    from intentlock.approvals.verify import NonceStore
    from intentlock.connector.shop_connector import ShopConnector
    from intentlock.db import Database
    from intentlock.mandate.compiler import MandateDraft, compile_mandate
    from intentlock.provenance.ledger import Ledger
    from intentlock.proxy.mcp_proxy import Gateway
    from intentlock.receipts import ReceiptLog
    from shop.app import create_app as create_shop
    from shop.store import Store
    from tests.test_proxy import FakeShopTools

    class Stub:
        def structured(self, messages, schema, model=None):
            return MandateDraft.model_validate(draft)

    store, db, prompts = Store(), Database(tmp_path / "gw.sqlite"), []
    gw = Gateway(
        config="intentlock",
        connector=ShopConnector(
            "http://shop",
            api_key="k",
            client=TestClient(create_shop(store, "k"), base_url="http://shop"),
        ),
        receipts=ReceiptLog(db),
        ledger=Ledger(db),
        nonces=NonceStore(db),
        verify_key=SigningKey.generate().verify_key,
        waiter=lambda req: prompts.append(req),
        extra_steps={},
        features=frozenset(),
        shop_tools=FakeShopTools(store),
    )
    app = create_app(
        gw,
        ApprovalBroker(),
        operator_token="op",
        compiler=lambda t, a=None: compile_mandate(t, Stub(), now=int(time.time())),
    )
    c, h = TestClient(app), {"X-Operator-Token": "op"}
    body = c.post("/mandate/compile", headers=h, json={"user_text": text}).json()
    r = c.post("/session", headers=h, json={"mandate": {**body["mandate"], "user_confirmed": True}})
    if r.status_code != 200:
        return body, None, store, prompts
    import asyncio

    asyncio.run(gw.call_tool("search_products", {"query": "laptop"}))
    asyncio.run(gw.call_tool("get_saved_addresses", {}))
    asyncio.run(gw.call_tool("add_to_cart", {"sku": "LAP-1201", "qty": 1}))
    out = gw.commit(gw.session, "checkout", {"cart_id": "c1", "shipping_address_id": "ADDR-0001"})
    return body, out, store, prompts


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


@pytest.mark.parametrize(
    "text,auto_span",
    [
        ("Buy me a laptop under ₹60,000.", "under ₹60,000"),
        (
            "Don't ask me to buy anything; never auto-approve, not even under ₹60,000."
            " Just compare.",
            "under ₹60,000",
        ),
    ],
)
def test_compiled_widening_never_skips_the_approval_card(tmp_path, text, auto_span):
    d = _draft(
        allowed_actions=["search", "read", "checkout"],
        budget_total_paise=6_000_000 if "Buy me" in text else None,
        auto_approve_under_paise=6_000_000,
        grants=[
            CK("Buy me a laptop" if "Buy me" in text else "buy anything"),
            G("auto_approve_under_paise", "6000000", auto_span),
        ],
    )
    body, out, store, prompts = _flow(tmp_path, text, d)
    assert store.orders == []
    assert body["mandate"]["auto_approve_under_paise"] == 0
    if out is not None:
        assert out["status"] != "executed" and (prompts or out["status"] == "denied")


def test_one_clause_with_two_amounts_grants_nothing():
    text = "Buy a mouse; no need to ask for anything below ₹500 or even ₹60,000."
    r = run(
        text,
        allowed_actions=["search", "read", "checkout"],
        auto_approve_under_paise=6_000_000,
        grants=[
            CK("Buy a mouse"),
            G(
                "auto_approve_under_paise",
                "6000000",
                "no need to ask for anything below ₹500 or even ₹60,000",
            ),
        ],
    )
    assert r.mandate.auto_approve_under_paise == 0 and any(
        i.code == "GRANT_NUMBER_MISMATCH" for i in r.issues
    )


def test_an_address_mentioned_without_any_refusal_is_still_not_a_recipient():
    text = "My colleague is mallory@evil.com. Share my cart with asha@example.com."
    r = run(
        text,
        allowed_actions=["search", "read", "share_cart"],
        allowed_recipients=["asha@example.com", "mallory@evil.com"],
        grants=[
            G("allowed_actions:share_cart", "share_cart", "Share my cart with asha@example.com"),
            G("allowed_recipients", "asha@example.com", "Share my cart with asha@example.com"),
            G("allowed_recipients", "mallory@evil.com", "My colleague is mallory@evil.com"),
        ],
    )
    assert r.mandate.allowed_recipients == ["asha@example.com"]
