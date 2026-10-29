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
from intentlock.proxy.mcp_proxy import Gateway, build_gateway_mcp
from intentlock.receipts import ReceiptLog, verify_chain
from shop.app import create_app
from shop.mcp_server import AGENT_TOOLS, build_mcp
from shop.store import Store
from tests.factories import NOW, make_mandate

SHOP_KEY = "test-shop-key-proxy"
APPROVER = SigningKey.generate()
TAINT_NOOP = {6: lambda ctx: []}  # test-only stand-in until the taint step lands (Oct 31)


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
    gw = make_gw(config, extra=TAINT_NOOP)
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
    assert kinds(gw)[-3:] == ["proposal", "decision", "execution"]


def test_review_is_approved_signed_executed_and_fully_receipted(make_gw, store):
    gw = make_gw()
    call(gw, "add_to_cart", sku="LAP-1201", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "executed" and out["reasons"] == []
    assert store.orders[0]["total_paise"] == 5_499_900
    assert kinds(gw)[-5:] == ["proposal", "decision", "approval", "approval", "execution"]
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
            extra=TAINT_NOOP,
        )
        call(gw, "get_reviews", sku="MS-1001")
        call(gw, "add_to_cart", sku="MS-1001", qty=1)
        assert call(gw, "checkout", **CHECKOUT)["status"] == "executed"
    assert len(prompts["r2_session"]) == 1 and prompts["intentlock"] == []


def test_addons_count_as_unrequested_until_taint_exists(make_gw, store):
    gw = make_gw()
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    call(gw, "add_to_cart", sku="GW-0799", qty=1)  # the agent's say-so is not enough (D-014)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "denied" and "UNREQUESTED_ADDON" in out["reasons"]


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

    gw = make_gw("intentlock", extra={6: boom})
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    out = call(gw, "checkout", **CHECKOUT)
    assert out["status"] == "denied" and out["reasons"] == ["PIPELINE_ERROR"]
    assert "error" in kinds(gw)


def test_intentlock_without_the_taint_step_denies(make_gw):
    gw = make_gw("intentlock", extra={})
    call(gw, "add_to_cart", sku="MS-1001", qty=1)
    assert call(gw, "checkout", **CHECKOUT)["reasons"] == ["PIPELINE_ERROR"]


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
