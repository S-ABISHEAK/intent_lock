"""Gateway proxy core (SPEC §3, §6, §8, §10; CLAUDE.md invariants 2, 3, 8, 11, 12).

Reads and cart calls go to the shop through the connector; commit tools never do. Every proposal,
decision, approval and execution writes a receipt; REVIEW waits for a signed approval.
"""

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from approver.signing import sign_request
from intentlock.approvals.broker import ApprovalBroker
from intentlock.approvals.verify import NonceStore
from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from intentlock.db import Database
from intentlock.provenance.ledger import Ledger
from intentlock.proxy.mcp_proxy import Gateway, build_gateway_mcp
from intentlock.receipts import ReceiptLog, verify_chain
from shop.app import create_app
from shop.mcp_server import AGENT_TOOLS, build_mcp
from shop.store import Store
from tests.factories import NOW, make_mandate

SHOP_KEY = "test-shop-key-proxy"
APPROVER = SigningKey.generate()


class FakeShopTools:
    """Plays the shop MCP for reads/cart calls against a real Store."""

    def __init__(self, store):
        self.store, self.calls = store, []

    async def __call__(self, name, args):
        self.calls.append(name)
        fn = {
            "search_products": lambda: {"results": self.store.search_products(**args)},
            "get_product": lambda: self.store.get_product(**args),
            "get_reviews": lambda: {"reviews": self.store.get_reviews(**args)},
            "get_saved_addresses": lambda: {"addresses": self.store.get_saved_addresses()},
            "add_to_cart": lambda: self.store.add_to_cart(**args),
        }.get(name)
        if fn is None:
            raise ConnectorError(f"not forwardable: {name}")
        try:
            return fn()
        except Exception as exc:
            raise ConnectorError(str(exc)) from None


def approve_all(req):
    return sign_request(req, APPROVER)


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def make_gw(store, tmp_path):
    def factory(
        config="r2_session", waiter=approve_all, mandate=None, extra=None, clock=lambda: NOW
    ):
        db = Database(tmp_path / f"{config}.sqlite")
        shop = TestClient(create_app(store, SHOP_KEY), base_url="http://shop")
        gw = Gateway(
            config=config,
            connector=ShopConnector("http://shop", api_key=SHOP_KEY, client=shop),
            receipts=ReceiptLog(db, clock=clock),
            ledger=Ledger(db),
            nonces=NonceStore(db),
            verify_key=APPROVER.verify_key,
            waiter=waiter,
            extra_steps=extra if extra is not None else {},
            features=frozenset(),
            clock=clock,
            shop_tools=FakeShopTools(store),
        )
        gw.start_session(
            mandate if mandate is not None else make_mandate(auto_approve_under_paise=200_000)
        )
        return gw

    return factory


def call(gw, name, **args):
    return asyncio.run(gw.call_tool(name, args))


def kinds(gw):
    return [r.kind for r in gw.receipts.all()]


CHECKOUT = {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}


# -- reads ---------------------------------------------------------------------------------------
def test_reads_are_forwarded_receipted_and_set_flags(make_gw):
    gw = make_gw()
    out = call(gw, "get_reviews", sku="LAP-1201")
    assert out["reviews"][0]["id"] == "r1"
    assert gw.session.flags.untrusted_input and not gw.session.flags.sensitive_access
    assert kinds(gw) == ["mandate", "tool_result"]


def test_failed_read_returns_an_error_and_sets_no_flag(make_gw):
    gw = make_gw()
    out = call(gw, "get_reviews", sku="NOPE-0000")
    assert "error" in out and not gw.session.flags.untrusted_input


def test_read_args_with_floats_do_not_break_receipts(make_gw):
    gw = make_gw()
    call(gw, "search_products", query="mouse", max_price=2000.5)
    assert verify_chain(gw.receipts.all()).ok


# -- commit tools ------------------------------------------------------------------------------
@pytest.mark.parametrize("config", ["none", "r2_session", "intentlock"])
def test_commit_tools_are_never_forwarded_to_the_shop_mcp(make_gw, config):
    gw = make_gw(config)
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    for tool, args in [
        ("checkout", CHECKOUT),
        ("send_email", {"to": "x@y.co", "subject": "s", "body": "b"}),
        ("share_cart", {"cart_id": "c1", "to": "x@y.co"}),
        ("delete_saved_address", {"id": "ADDR-0002"}),
        ("cancel_order", {"id": "ORD-5001"}),
    ]:
        call(gw, tool, **args)
    assert gw.shop_tools.calls == ["add_to_cart"]


def test_unknown_tool_is_denied_and_receipted(make_gw):
    gw = make_gw()
    out = call(gw, "drop_tables", x="1")
    assert out["status"] == "denied" and out["reasons"] == ["UNKNOWN_TOOL"]
    assert kinds(gw)[-2:] == ["proposal", "decision"]


def test_deny_writes_proposal_and_decision_and_never_executes(make_gw, store):
    gw = make_gw(mandate=make_mandate(budget_total_paise=100))
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    out = call(gw, "checkout", **CHECKOUT, total=1)
    assert out["status"] == "denied" and out["reasons"] == ["BUDGET_EXCEEDED"]
    assert kinds(gw)[-2:] == ["proposal", "decision"] and store.orders == []


def test_allow_executes_without_a_prompt(make_gw, store):
    prompts = []
    gw = make_gw(waiter=lambda r: prompts.append(r) or approve_all(r))
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "executed" and prompts == [] and len(store.orders) == 1
    assert kinds(gw)[-4:] == ["proposal", "decision", "execution", "execution"]


def test_review_is_approved_signed_executed_and_fully_receipted(make_gw, store):
    gw = make_gw()
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "executed" and out["reasons"] == []
    assert store.orders[0]["total_paise"] == 5_499_900
    assert kinds(gw)[-6:] == [
        "proposal",
        "decision",
        "approval",
        "approval",
        "execution",
        "execution",
    ]
    events = [r.payload["event"] for r in gw.receipts.all() if r.kind == "execution"]
    assert events == ["started", "finished"]
    events = [r.payload["event"] for r in gw.receipts.all() if r.kind == "approval"]
    assert events == ["requested", "signed"]
    assert verify_chain(gw.receipts.all()).ok


def test_review_not_approved_executes_nothing(make_gw, store):
    gw = make_gw(waiter=lambda req: None)
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "not_approved" and store.orders == []
    assert gw.receipts.all()[-1].payload["event"] == "not_approved"


def test_forged_approval_is_rejected_at_execution(make_gw, store):
    gw = make_gw(waiter=lambda req: sign_request(req, SigningKey.generate()))
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "denied" and out["reasons"] == ["APPROVAL_INVALID_SIGNATURE"]
    assert store.orders == []


def test_cart_tampered_while_waiting_for_approval_is_quote_changed(make_gw, store):
    def waiter(req):
        store.add_to_cart("XC-4999")  # the cart changes while the user looks at the card
        return approve_all(req)

    gw = make_gw(waiter=waiter)
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["reasons"] == ["QUOTE_CHANGED"] and store.orders == []


def test_rule_of_two_vs_intentlock_on_the_same_benign_session(make_gw, store):
    prompts = {"r2_session": [], "intentlock": []}
    for config in prompts:
        store.reset()
        gw = make_gw(
            config,
            waiter=lambda r, c=config: prompts[c].append(r) or approve_all(r),
        )
        call(gw, "search_products", query="mouse")  # where the SKU and seller come from
        call(gw, "get_reviews", sku="MS-1001")  # untrusted read: sets flag A
        call(gw, "get_saved_addresses")  # where ADDR-0001 comes from
        call(gw, "add_to_cart", sku="MS-1001", qty=1)
        assert call(gw, "checkout", **CHECKOUT)["status"] == "executed"
    assert len(prompts["r2_session"]) == 1 and prompts["intentlock"] == []


def test_addon_with_no_clean_source_is_unrequested(make_gw, store):
    gw = make_gw()
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    call(gw, "add_to_cart", sku="GW-0799", qty=1)  # the agent's say-so is not enough (D-014)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "denied" and "UNREQUESTED_ADDON" in out["reasons"]


def test_addon_the_user_asked_for_counts_as_requested(make_gw, store):
    m = make_mandate(
        user_text="Buy a mouse, gift wrapped (GW-0799) please.", auto_approve_under_paise=10**6
    )
    gw = make_gw(mandate=m)
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    call(gw, "add_to_cart", sku="GW-0799", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert "UNREQUESTED_ADDON" not in out.get("reasons", [])


def test_agent_money_and_float_args_are_dropped_and_recorded(make_gw):
    gw = make_gw()
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    call(gw, "checkout", **CHECKOUT, price=1.5, total=0)
    proposal = [r for r in gw.receipts.all() if r.kind == "proposal"][-1]
    assert proposal.payload["args"] == CHECKOUT
    assert proposal.payload["dropped_args"] == ["price", "total"]


def test_pipeline_exception_is_denied_with_an_error_receipt(make_gw):
    def boom(ctx):
        raise RuntimeError("taint exploded")

    gw = make_gw("intentlock")
    gw.extra_steps[6] = boom  # test-only: break the real taint step
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "denied" and out["reasons"] == ["PIPELINE_ERROR"]
    assert "error" in kinds(gw)


def test_the_gateway_always_installs_its_own_taint_step(make_gw):
    with pytest.raises(ValueError):
        make_gw("intentlock", extra={6: lambda ctx: []})
    gw = make_gw("intentlock")
    call(gw, "add_to_cart", sku="MS-1001", qty=1)  # never searched: unknown provenance
    out = call(gw, "checkout", **CHECKOUT)
    assert "UNKNOWN_PROVENANCE" in [r["code"] for r in decisions(gw)[-1]["reasons"]]
    assert out["status"] == "executed"  # approved by the test user after REVIEW


def decisions(gw):
    return [r.payload for r in gw.receipts.all() if r.kind == "decision"]


# -- Oct 31: label before delivery ----------------------------------------------------------
def test_results_reach_the_agent_with_inline_trust_labels(make_gw):
    gw = make_gw()
    out = call(gw, "get_product", sku="LAP-1203")
    assert out["_trust"] == {"description": "untrusted", "seller_note": "untrusted"}
    assert out["sku"] == "LAP-1203"


def test_every_result_is_ledgered_before_the_agent_sees_it(make_gw):
    gw = make_gw()
    call(gw, "get_reviews", sku="LAP-1201")
    sid = gw.session.session_id
    records = gw.ledger.records(sid)
    assert any(r.tool == "get_reviews" and r.label.value == "untrusted" for r in records)
    assert any(r.tool == "user" for r in records)  # the mandate text


def test_nothing_unlabeled_reaches_the_agent_when_the_ledger_fails(make_gw, monkeypatch):
    gw = make_gw()

    def broken(records):
        raise OSError("disk full")

    monkeypatch.setattr(gw.ledger, "add", broken)
    out = call(gw, "get_product", sku="LAP-1203")
    assert out == {"error": "result withheld: provenance could not be recorded"}
    assert "error" in kinds(gw)


def test_waiter_exception_fails_closed(make_gw, store):
    def broken(req):
        raise OSError("ui down")

    gw = make_gw(waiter=broken)
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "denied" and store.orders == [] and "error" in kinds(gw)


def test_no_session_means_no_tools(store, tmp_path):
    db = Database(tmp_path / "x.sqlite")
    gw = Gateway(
        config="none",
        connector=None,
        receipts=ReceiptLog(db),
        ledger=Ledger(db),
        nonces=NonceStore(db),
        verify_key=APPROVER.verify_key,
        waiter=approve_all,
        extra_steps={},
        features=frozenset(),
        shop_tools=FakeShopTools(store),
    )
    assert "error" in asyncio.run(gw.call_tool("search_products", {"query": "x"}))


def test_the_shop_key_never_appears_in_receipts(make_gw):
    gw = make_gw()
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    call(gw, "get_reviews", sku="LAP-1201")
    call(gw, "checkout", **CHECKOUT)
    assert SHOP_KEY not in json.dumps([r.model_dump() for r in gw.receipts.all()])


# -- approval broker -------------------------------------------------------------------------------
def make_req():
    from intentlock.models import ApprovalRequest

    return ApprovalRequest(
        approval_id="ap1",
        nonce="ab" * 16,
        mandate_id="m1",
        policy_version=1,
        session_id="s1",
        action_tool="checkout",
        quote_hash="a" * 64,
        expires_at=NOW + 120,
    )


def test_broker_delivers_a_submitted_approval_to_the_waiter():
    broker, req = ApprovalBroker(), make_req()
    broker.open(req)
    assert [r.approval_id for r in broker.pending()] == ["ap1"]
    t = threading.Timer(0.05, lambda: broker.submit("ap1", sign_request(req, APPROVER)))
    t.start()
    got = broker.wait("ap1", timeout=2)
    assert got is not None and got.approval_id == "ap1" and broker.pending() == []


def test_broker_timeout_reject_and_bad_submissions():
    broker, req = ApprovalBroker(), make_req()
    broker.open(req)
    assert broker.wait("ap1", timeout=0.05) is None
    broker.open(req)
    assert broker.submit("nope", sign_request(req, APPROVER)) is False
    other = sign_request(req.model_copy(update={"approval_id": "ap2"}), APPROVER)
    assert broker.submit("ap1", other) is False  # id in the body must match the URL
    assert broker.reject("ap1") is True and broker.wait("ap1", timeout=1) is None


# -- MCP surface ----------------------------------------------------------------------------------
def test_gateway_mcp_mirrors_the_shop_agent_tools_and_schemas(make_gw, store):
    gw_tools = {
        t.name: t.inputSchema for t in asyncio.run(build_gateway_mcp(make_gw()).list_tools())
    }
    shop_tools = {t.name: t.inputSchema for t in asyncio.run(build_mcp(store).list_tools())}
    assert set(gw_tools) == set(AGENT_TOOLS)
    for name in AGENT_TOOLS:
        assert gw_tools[name]["properties"] == shop_tools[name]["properties"], name
        assert gw_tools[name].get("required") == shop_tools[name].get("required"), name


# -- Oct 30 review fixes -------------------------------------------------------------------
def test_mandate_that_expires_while_waiting_for_approval_does_not_execute(make_gw, store):
    t = {"now": NOW}

    def slow_user(req):
        t["now"] = NOW + 90  # the user takes 90 s; the mandate ends at NOW + 60
        return approve_all(req)

    gw = make_gw(
        waiter=slow_user, mandate=make_mandate(expires_at=NOW + 60), clock=lambda: t["now"]
    )
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out == {"status": "denied", "reasons": ["MANDATE_EXPIRED"]} and store.orders == []


def test_session_replaced_while_waiting_for_approval_does_not_execute(make_gw, store):
    holder = {}

    def replacing_user(req):
        holder["gw"].end_session()  # the operator ends the session; nothing new can start meanwhile
        return approve_all(req)

    gw = make_gw(waiter=replacing_user)
    holder["gw"] = gw
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "denied" and out["reasons"] == ["SESSION_REPLACED"]
    assert store.orders == []


@pytest.mark.parametrize("field", ["approval_id", "nonce", "expires_at"])
def test_a_substituted_approval_is_rejected_even_with_the_same_binding(make_gw, store, field):
    def substitute(req):
        changed = {"approval_id": "other", "nonce": "cd" * 16, "expires_at": req.expires_at - 1}
        return sign_request(req.model_copy(update={field: changed[field]}), APPROVER)

    gw = make_gw(waiter=substitute)
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out == {"status": "denied", "reasons": ["APPROVAL_MISMATCH"]} and store.orders == []


def test_receipt_failure_after_a_real_execution_still_reports_executed(make_gw, store):
    gw = make_gw()
    real_append = gw.receipts.append

    def flaky(kind, payload):
        if kind == "execution" and payload.get("event") == "finished":
            raise OSError("disk full")
        return real_append(kind, payload)

    gw.receipts.append = flaky
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "executed" and out.get("receipt_error") is True
    assert len(store.orders) == 1
    assert [r.payload.get("event") for r in gw.receipts.all() if r.kind == "execution"] == [
        "started"
    ]


# -- checkpoint re-review LOWs ------------------------------------------------------------------
def test_start_session_refuses_while_a_commit_is_waiting(make_gw):
    from intentlock.proxy.mcp_proxy import GatewayBusy

    holder, seen = {}, {}

    def user(req):
        with pytest.raises(GatewayBusy):
            holder["gw"].start_session(make_mandate())
        seen["refused"] = True
        return None

    gw = make_gw(waiter=user)
    holder["gw"] = gw
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    call(gw, "checkout", **CHECKOUT)
    assert seen == {"refused": True}
    gw.start_session(make_mandate())  # free again once nothing is in flight


def test_end_session_stops_all_tools(make_gw):
    gw = make_gw()
    gw.end_session()
    assert "error" in call(gw, "search_products", query="x")
    assert "error" in call(gw, "checkout", **CHECKOUT)


def test_card_summary_resolves_the_shipping_address(make_gw, store):
    seen = {}

    def user(req):
        seen.update(gw.summaries[req.approval_id])
        return None

    gw = make_gw(waiter=user)
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    call(gw, "checkout", **CHECKOUT)
    assert seen["shipping_address"]["label"] == "Home"
    seen.clear()
    call(gw, "checkout", cart_id="c1", shipping_address_id="ADDR-9931")
    assert seen["shipping_address"] is None and seen["shipping_address_id"] == "ADDR-9931"


def test_uncertain_merchant_outcome_is_reported_as_unknown(make_gw, store, monkeypatch):
    from intentlock.connector.shop_connector import ExecutionResult
    from intentlock.policy.reasons import make_reason

    gw = make_gw()
    monkeypatch.setattr(
        gw.connector,
        "execute",
        lambda *a, **k: ExecutionResult(
            executed=False,
            uncertain=True,
            reasons=[make_reason("EXECUTION_FAILED", "outcome unknown")],
        ),
    )
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "unknown" and out["reasons"] == ["EXECUTION_FAILED"]
    finished = [r for r in gw.receipts.all() if r.kind == "execution"][-1].payload
    assert finished["executed"] == "unknown"


# -- Oct 31 review: commit results and unexpected forwarding failures -----------------------------
def test_execution_results_are_ledgered_and_labeled(make_gw, store):
    gw = make_gw()
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "executed" and "_trust" in out["result"]
    assert any(r.tool == "checkout" for r in gw.ledger.records(gw.session.session_id))


def test_unexpected_failure_while_forwarding_is_handled_like_a_shop_error(make_gw):
    gw = make_gw()

    async def broken(name, args):
        raise AttributeError("non-text content")

    gw.shop_tools = broken
    out = call(gw, "get_reviews", sku="LAP-1201")
    assert "error" in out and "tool_result" in kinds(gw)
