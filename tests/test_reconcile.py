"""D-056 (second review: ambiguous execution): a checkout whose response was lost is resolved by
asking the merchant what happened to that quote, never by retrying it.

States: CONFIRMED (an order for this quote exists and matches it) -> executed; NOT_PLACED (no order)
or CANCELLED -> not executed; anything else stays UNKNOWN. A confirmation that contradicted the
quote (D-049) is not reconciled: that merchant already disagreed with itself, so it stays unknown.
"""

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from intentlock.approvals.verify import NonceStore
from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from intentlock.db import Database
from intentlock.models import ActionProposal, Decision
from intentlock.policy.reasons import make_reason
from shop.app import create_app
from shop.store import Store
from tests.test_e2e_loop import run_scenario, world  # noqa: F401

KEY = "reconcile-key"
BASE = "http://shop.test"


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


def checkout(conn, tmp_path):
    q = conn.get_quote("c1", "ADDR-0001")
    p = ActionProposal(
        action_id="a1", session_id="s", tool="checkout",
        args={"cart_id": "c1", "shipping_address_id": "ADDR-0001"}, ts=1,
    )  # fmt: skip
    d = Decision(
        action_id="a1", outcome="ALLOW", reasons=[make_reason("ALLOWED_IN_MANDATE")],
        quote_hash=q.quote_hash, config="intentlock",
    )  # fmt: skip
    return conn.execute(
        p, d, approval=None, expected=None, verify_key=SigningKey.generate().verify_key,
        nonces=NonceStore(Database(tmp_path / "n.sqlite")), clock=lambda: 1,
    )  # fmt: skip


def lose_the_response(monkeypatch, *, place: bool):
    """The order call reaches the shop (or not), but no answer comes back."""
    calls = []
    orig = ShopConnector._commit_call

    def lost(self, tool, args, quote, **kw):
        calls.append(tool)
        if place:
            orig(self, tool, args, quote, **kw)
        raise ConnectorError("shop did not answer: ReadTimeout", maybe_sent=True)

    monkeypatch.setattr(ShopConnector, "_commit_call", lost)
    return calls


def codes(res):
    return [r.code for r in res.reasons]


def test_a_lost_response_for_a_placed_order_is_confirmed(conn, store, tmp_path, monkeypatch):
    calls = lose_the_response(monkeypatch, place=True)
    res = checkout(conn, tmp_path)
    assert res.executed and not res.uncertain and res.reconciled == "CONFIRMED"
    assert res.result["id"] == store.orders[0]["id"]
    assert calls == ["checkout"] and len(store.orders) == 1  # never retried


def test_a_lost_response_with_no_order_is_not_placed(conn, store, tmp_path, monkeypatch):
    calls = lose_the_response(monkeypatch, place=False)
    res = checkout(conn, tmp_path)
    assert not res.executed and not res.uncertain and res.reconciled == "NOT_PLACED"
    assert codes(res) == ["EXECUTION_FAILED"] and calls == ["checkout"] and store.orders == []


def test_a_cancelled_order_is_reported_cancelled(conn, store, tmp_path, monkeypatch):
    orig = ShopConnector._commit_call

    def placed_then_cancelled(self, tool, args, quote, **kw):
        order = orig(self, tool, args, quote, **kw)
        store.cancel_order(order["id"])
        raise ConnectorError("shop did not answer: ReadTimeout", maybe_sent=True)

    monkeypatch.setattr(ShopConnector, "_commit_call", placed_then_cancelled)
    res = checkout(conn, tmp_path)
    assert not res.executed and not res.uncertain and res.reconciled == "CANCELLED"


def test_if_the_lookup_fails_the_outcome_stays_unknown(conn, store, tmp_path, monkeypatch):
    lose_the_response(monkeypatch, place=True)
    real = ShopConnector._request

    def lookup_down(self, method, path, json=None):
        if path.startswith("/orders/by-quote/"):
            raise ConnectorError("shop unreachable: ConnectError")
        return real(self, method, path, json)

    monkeypatch.setattr(ShopConnector, "_request", lookup_down)
    res = checkout(conn, tmp_path)
    assert res.uncertain and res.reconciled == "UNKNOWN" and codes(res) == ["EXECUTION_FAILED"]


def test_a_found_order_that_differs_from_the_quote_stays_unknown(
    conn, store, tmp_path, monkeypatch
):
    lose_the_response(monkeypatch, place=True)
    real_lookup = Store.order_by_quote
    monkeypatch.setattr(
        Store, "order_by_quote", lambda self, qid: {**real_lookup(self, qid), "total_paise": 1}
    )
    res = checkout(conn, tmp_path)
    assert res.uncertain and res.reconciled == "UNKNOWN"


def test_a_contradicting_confirmation_is_not_reconciled(conn, store, tmp_path, monkeypatch):
    """D-049 / review MED-3 still holds: the merchant already disagreed with its own quote."""
    honest = store.place_order
    monkeypatch.setattr(store, "place_order", lambda qid: {**honest(qid), "total_paise": 649_800})
    res = checkout(conn, tmp_path)
    assert res.uncertain and codes(res) == ["EXECUTION_MISMATCH"] and res.reconciled is None


def test_the_lookup_route_needs_the_key(store):
    client = TestClient(create_app(store, KEY), base_url=BASE)
    assert client.get("/orders/by-quote/Q-1").status_code == 401
    r = client.get("/orders/by-quote/Q-none", headers={"X-API-Key": KEY})
    assert r.status_code == 404


def test_the_gateway_reports_a_reconciled_purchase_as_executed(world, monkeypatch):  # noqa: F811
    """End to end: the response is lost, the order exists, the receipt records CONFIRMED."""
    from tests.test_session_limits import buy_again_and_again, checkouts

    lose_the_response(monkeypatch, place=True)
    s = run_scenario(world, "intentlock", buy_again_and_again("MS-1001", 1))
    [result] = checkouts(s)
    assert result["status"] == "executed" and len(world.store.orders) == 1
    finished = [r for r in s.receipts if r["payload"].get("event") == "finished"]
    assert finished[-1]["payload"]["reconciled"] == "CONFIRMED"
