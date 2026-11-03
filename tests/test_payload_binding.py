"""D-051 (review gap 4): a share_cart approval binds what is shared, not just the recipient.

Before: the approval signed (tool, cart_id, to) and the shop read the cart when sending, so items
added after the user approved were shared too. Now the decision carries the hash of the cart's
contents, the approval signs it, the card shows the items, the connector re-checks it before the
call, and the shop refuses to send if the contents moved in between (like a stale quote).
"""

import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.run import run_agent
from intentlock.approvals.requests import binding_for
from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from intentlock.models import ActionProposal, Decision, Mandate
from intentlock.policy.reasons import make_reason
from shop.app import create_app
from shop.store import Store
from tests.servers import serve
from tests.test_e2e_loop import OPH, UI, ScriptedLLM, turn, world  # noqa: F401

KEY = "payload-test-key"
BASE = "http://shop.test"
TASKS = "eval/tasks/layer_e.jsonl"
SHARE_MANDATE = next(
    json.loads(line)["mandate"] for line in open(TASKS, encoding="utf-8") if '"E-B06"' in line
)


@pytest.fixture
def store():
    s = Store()
    s.add_to_cart("MS-1001")
    return s


@pytest.fixture
def conn(store):
    return ShopConnector(
        BASE, api_key=KEY, client=TestClient(create_app(store, KEY), base_url=BASE)
    )


def test_cart_contents_hash_follows_the_items_not_their_order(store, conn):
    first = conn.cart_contents("c1")
    assert first["items"] == [{"sku": "MS-1001", "qty": 1}]
    store.add_to_cart("KB-2001")
    second = conn.cart_contents("c1")
    assert second["contents_hash"] != first["contents_hash"]
    other = Store()
    other.add_to_cart("KB-2001")
    other.add_to_cart("MS-1001")
    o = ShopConnector(BASE, api_key=KEY, client=TestClient(create_app(other, KEY), base_url=BASE))
    assert o.cart_contents("c1")["contents_hash"] == second["contents_hash"]


def test_share_decision_carries_the_contents_hash(store, conn):
    """none and r2_session here; intentlock needs the gateway's taint step and ledger, so it is
    covered end to end below (the card and the send both use the bound contents)."""
    from intentlock.policy.pipeline import decide
    from intentlock.policy.rule_of_two import SessionFlags

    m = Mandate.model_validate(SHARE_MANDATE)
    p = ActionProposal(
        action_id="a1", session_id="s", tool="share_cart",
        args={"cart_id": "c1", "to": "asha@example.com"}, ts=1,
    )  # fmt: skip
    want = conn.cart_contents("c1")["contents_hash"]
    for config in ("none", "r2_session"):
        d = decide(
            p, m, config, connector=conn, now=1, requested_skus=frozenset(), flags=SessionFlags()
        )
        assert d.quote_hash == want, config


def test_binding_changes_when_the_contents_change():
    m = Mandate.model_validate(SHARE_MANDATE)
    p = ActionProposal(
        action_id="a1", session_id="s", tool="share_cart",
        args={"cart_id": "c1", "to": "asha@example.com"}, ts=1,
    )  # fmt: skip

    def d(h):
        reasons = [make_reason("APPROVAL_REQUIRED", "comms")]
        return Decision(
            action_id="a1", outcome="REVIEW", reasons=reasons, quote_hash=h, config="intentlock"
        )

    assert binding_for(m, "s", p, d("a" * 64)) != binding_for(m, "s", p, d("b" * 64))
    with pytest.raises(ValueError):
        binding_for(m, "s", p, d(None))  # a share approval must bind contents


def test_shop_refuses_to_share_contents_that_moved(store):
    client = TestClient(create_app(store, KEY), base_url=BASE)
    h = ShopConnector(BASE, api_key=KEY, client=client).cart_contents("c1")["contents_hash"]
    store.add_to_cart("KB-2001")
    r = client.post(
        "/share_cart",
        json={"cart_id": "c1", "to": "asha@example.com", "contents_hash": h},
        headers={"X-API-Key": KEY},
    )
    assert r.status_code == 409 and store.messages == []


def test_resolve_cart_needs_the_key(store):
    client = TestClient(create_app(store, KEY), base_url=BASE)
    assert client.get("/resolve/cart/c1").status_code == 401
    with pytest.raises(ConnectorError, match="401"):
        ShopConnector(BASE, api_key="wrong", client=client).cart_contents("c1")


def share_turns():
    return [
        turn(("search_products", {"query": "mouse"})),
        turn(("add_to_cart", {"sku": "MS-1001", "qty": 1})),
        turn(("share_cart", {"cart_id": "c1", "to": "asha@example.com"})),
        turn(content="done"),
    ]


def run_share(w, config, before_approve=None):
    from intentlock.config import Settings
    from intentlock.llm.client import LLMClient

    w.store.reset(attacks=[])
    llm = LLMClient(Settings(llm_cache_dir=str(w.tmp / "llm")), client=ScriptedLLM(share_turns()))
    with serve(w.gateway(config)) as gw_url:
        httpx.post(
            f"{gw_url}/session",
            json={"mandate": SHARE_MANDATE, "confirm_auto_approve_paise": None},
            headers=OPH,
        ).raise_for_status()
        ui = UI(gw_url, w.approver_url, before_approve)
        ui.start()
        try:
            import asyncio

            run = asyncio.run(run_agent("share", gateway_url=gw_url, llm=llm, model="m"))
        finally:
            ui.stop.set()
            ui.join(5)
    return json.loads(run.tool_calls[-1]["result"]), ui


def test_approved_share_sends_exactly_what_the_card_showed(world):  # noqa: F811
    result, ui = run_share(world, "intentlock")
    assert result["status"] == "executed"
    assert ui.cards[0]["summary"]["items"] == [{"sku": "MS-1001", "qty": 1}]
    assert world.store.messages[0]["cart"] == [{"sku": "MS-1001", "qty": 1}]


def test_cart_changed_after_approval_is_payload_changed_and_nothing_is_sent(world):  # noqa: F811
    result, ui = run_share(
        world, "intentlock", before_approve=lambda: world.store.add_to_cart("KB-2001")
    )
    assert result == {"status": "denied", "reasons": ["PAYLOAD_CHANGED"]}
    assert world.store.messages == []


def test_contents_moving_between_the_recheck_and_the_send_are_refused(world, monkeypatch):  # noqa: F811
    """The last window: after the connector's re-check, before the shop sends."""
    orig = ShopConnector._commit_call
    fired = threading.Event()

    def late(self, tool, args, quote, **kw):
        if tool == "share_cart" and not fired.is_set():
            fired.set()
            world.store.add_to_cart("KB-2001")
            time.sleep(0.01)
        return orig(self, tool, args, quote, **kw)

    monkeypatch.setattr(ShopConnector, "_commit_call", late)
    result, _ = run_share(world, "intentlock")
    assert fired.is_set()
    assert result == {"status": "denied", "reasons": ["PAYLOAD_CHANGED"]}
    assert world.store.messages == []
