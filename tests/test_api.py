"""Gateway REST surface (Oct 30 review MED 1): operator routes need a token the agent never has."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from approver.signing import sign_request
from intentlock.api import broker_waiter, create_app
from intentlock.approvals.broker import ApprovalBroker
from intentlock.approvals.verify import NonceStore
from intentlock.connector.shop_connector import ShopConnector
from intentlock.db import Database
from intentlock.provenance.ledger import Ledger
from intentlock.proxy.mcp_proxy import Gateway
from intentlock.receipts import ReceiptLog
from shop.app import create_app as create_shop
from shop.store import Store
from tests.factories import grounded_mandate, make_mandate
from tests.test_proxy import FakeShopTools

OP = "operator-token-test"
H = {"X-Operator-Token": OP}
SHOP_KEY = "api-test-shop-key"
APPROVER = SigningKey.generate()
MANDATE = grounded_mandate().model_dump(mode="json")


@pytest.fixture
def env(tmp_path):
    store, broker = Store(), ApprovalBroker()
    db = Database(tmp_path / "gw.sqlite")
    gw = Gateway(
        config="r2_session",
        connector=ShopConnector(
            "http://shop",
            api_key=SHOP_KEY,
            client=TestClient(create_shop(store, SHOP_KEY), base_url="http://shop"),
        ),
        receipts=ReceiptLog(db),
        ledger=Ledger(db),
        nonces=NonceStore(db),
        verify_key=APPROVER.verify_key,
        waiter=broker_waiter(broker),
        extra_steps={},
        features=frozenset(),
        shop_tools=FakeShopTools(store),
    )
    return gw, broker, store


OPERATOR_ROUTES = [
    ("post", "/session"),
    ("get", "/approvals/pending"),
    ("post", "/approvals/x/signature"),
    ("post", "/approvals/x/reject"),
    ("get", "/receipts"),
    ("get", "/receipts/verify"),
]


@pytest.mark.parametrize("method,path", OPERATOR_ROUTES)
def test_operator_routes_need_the_token(env, method, path):
    gw, broker, _ = env
    c = TestClient(create_app(gw, broker, operator_token=OP))
    assert getattr(c, method)(path).status_code == 401
    assert getattr(c, method)(path, headers={"X-Operator-Token": "wrong"}).status_code == 401


@pytest.mark.parametrize("method,path", OPERATOR_ROUTES)
def test_an_unset_operator_token_locks_every_operator_route(env, method, path):
    gw, broker, _ = env
    c = TestClient(create_app(gw, broker, operator_token=""))
    assert getattr(c, method)(path, headers={"X-Operator-Token": ""}).status_code == 401


def test_health_stays_open_and_docs_are_off(env):
    gw, broker, _ = env
    c = TestClient(create_app(gw, broker, operator_token=OP))
    assert c.get("/health").status_code == 200
    assert c.get("/docs").status_code in (401, 404)


def test_session_with_the_token_works(env):
    gw, broker, _ = env
    c = TestClient(create_app(gw, broker, operator_token=OP))
    r = c.post("/session", headers=H, json={"mandate": MANDATE})
    assert r.status_code == 200 and gw.session.session_id == r.json()["session_id"]


def test_pending_cards_show_what_the_user_is_approving(env):
    gw, broker, store = env
    c = TestClient(create_app(gw, broker, operator_token=OP))
    c.post("/session", headers=H, json={"mandate": MANDATE})
    store.add_to_cart("LAP-1201")
    t = threading.Thread(
        target=gw.commit,
        args=(gw.session, "checkout", {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}),
    )
    t.start()
    for _ in range(200):
        cards = c.get("/approvals/pending", headers=H).json()
        if cards:
            break
        time.sleep(0.01)
    card = cards[0]
    assert card["summary"]["tool"] == "checkout" and card["summary"]["total_paise"] == 5_499_900
    assert card["summary"]["lines"][0]["sku"] == "LAP-1201"
    assert card["summary"]["reasons"] == ["APPROVAL_REQUIRED"]

    # MED 1: the session can't be swapped while a card is pending
    r = c.post("/session", headers=H, json={"mandate": MANDATE})
    assert r.status_code == 409

    signed = sign_request(type(broker.pending()[0]).model_validate(card["request"]), APPROVER)
    ok = c.post(
        f"/approvals/{card['request']['approval_id']}/signature",
        headers=H,
        json=signed.model_dump(),
    )
    assert ok.status_code == 200
    t.join(10)
    assert len(store.orders) == 1


def test_end_session_rejects_pending_cards(env):
    gw, broker, store = env
    c = TestClient(create_app(gw, broker, operator_token=OP))
    c.post("/session", headers=H, json={"mandate": MANDATE})
    store.add_to_cart("LAP-1201")
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            gw.commit(gw.session, "checkout", {"cart_id": "c1", "shipping_address_id": "ADDR-0001"})
        )
    )
    t.start()
    for _ in range(200):
        if c.get("/approvals/pending", headers=H).json():
            break
        time.sleep(0.01)
    assert c.post("/session/end", headers=H).status_code == 200
    t.join(10)
    assert result["status"] == "not_approved" and store.orders == []
    assert c.post("/session/end").status_code == 401


def test_gateway_refuses_to_start_with_the_ui_token_in_its_environment(monkeypatch):
    from intentlock.api import build_from_env

    monkeypatch.setenv("APPROVER_UI_TOKEN", "should-not-be-here")
    with pytest.raises(RuntimeError, match="APPROVER_UI_TOKEN"):
        build_from_env()


# -- Nov 1: only grounded, confirmed mandates start a session (SPEC §9, D-026) --------------------
def client_for(env, compiler=None):
    gw, broker, _ = env
    return TestClient(create_app(gw, broker, operator_token=OP, compiler=compiler))


def test_an_ungrounded_mandate_cannot_start_a_session(env):
    ungrounded = make_mandate(expires_at=4_102_444_800).model_dump(
        mode="json"
    )  # checkout, no grant
    r = client_for(env).post("/session", headers=H, json={"mandate": ungrounded})
    assert r.status_code == 422 and "allowed_actions:checkout" in r.text


def test_an_unconfirmed_mandate_cannot_start_a_session(env):
    m = grounded_mandate(user_confirmed=False).model_dump(mode="json")
    assert client_for(env).post("/session", headers=H, json={"mandate": m}).status_code == 422


def test_a_mandate_with_open_questions_cannot_start_a_session(env):
    m = grounded_mandate(clarifications_needed=["Which colour?"]).model_dump(mode="json")
    assert client_for(env).post("/session", headers=H, json={"mandate": m}).status_code == 422


def test_the_demo_mandate_file_starts_a_session(env):
    from intentlock.cli import DEMO_MANDATE

    m = json.loads(DEMO_MANDATE.read_text())
    assert client_for(env).post("/session", headers=H, json={"mandate": m}).status_code == 200


def test_compile_returns_a_readback_and_an_unconfirmed_mandate(env):
    from intentlock.mandate.grounding import ground

    report = ground(grounded_mandate(user_confirmed=False))
    c = client_for(env, compiler=lambda text: report)
    r = c.post("/mandate/compile", headers=H, json={"user_text": "anything"})
    body = r.json()
    assert r.status_code == 200 and body["mandate"]["user_confirmed"] is False
    assert any(b["kind"] == "grant" and b["span"] for b in body["readback"])
    assert c.post("/mandate/compile", json={"user_text": "x"}).status_code == 401


def test_compile_without_a_model_or_on_failure_gives_no_mandate(env):
    from intentlock.mandate.compiler import CompileError

    assert (
        client_for(env).post("/mandate/compile", headers=H, json={"user_text": "x"}).status_code
        == 503
    )

    def broken(text):
        raise CompileError("muse down")

    c = client_for(env, compiler=broken)
    assert c.post("/mandate/compile", headers=H, json={"user_text": "x"}).status_code == 502
