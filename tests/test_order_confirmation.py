"""D-049 (review gap 3): the merchant's order confirmation must match the approved quote.

The quote and the screenshot can agree while the order the merchant records differs (in a real
deployment, the payment request). After placing the order the connector compares the confirmation
with the quote it executed: quote hash, total, lines and shipping address. Any difference cancels
the order immediately and is reported as EXECUTION_MISMATCH; if the cancel fails too, the outcome
is flagged unknown, never reported as safe. A confirmation missing these fields counts as a
mismatch (fail closed).
"""

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from intentlock.approvals.verify import NonceStore
from intentlock.connector.shop_connector import ShopConnector
from intentlock.db import Database
from intentlock.models import ActionProposal, Decision
from intentlock.policy.reasons import make_reason
from shop.app import create_app
from shop.store import ShopError, Store

KEY = "confirm-test-key"
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


def lying(store, monkeypatch, **changes):
    honest = store.place_order

    def place(quote_id):
        order = honest(quote_id)
        order.update(changes)
        return order

    monkeypatch.setattr(store, "place_order", place)


def codes(res):
    return [r.code for r in res.reasons]


def test_a_matching_confirmation_executes(conn, store, tmp_path):
    res = checkout(conn, tmp_path)
    assert res.executed and store.orders[0]["status"] == "placed"


@pytest.mark.parametrize(
    "changes",
    [
        {"total_paise": 649_800},
        {"shipping_address_id": "ADDR-9931"},
        {"quote_hash": "f" * 64},
        {"lines": [{"sku": "MS-1001", "qty": 4, "unit_paise": 149_900}]},
    ],
    ids=["total", "address", "hash", "lines"],
)
def test_a_differing_confirmation_is_cancelled_and_reported_unknown(
    conn, store, tmp_path, monkeypatch, changes
):
    """Review MED-3: a merchant whose confirmation disagrees can't vouch for the cancel either, so
    the outcome is never reported as settled."""
    lying(store, monkeypatch, **changes)
    res = checkout(conn, tmp_path)
    assert not res.executed and res.uncertain and codes(res) == ["EXECUTION_MISMATCH"]
    assert store.cancelled_orders == [store.orders[0]["id"]]
    assert "cancel requested" in res.reasons[0].detail


def test_a_confirmation_missing_fields_is_a_mismatch(conn, store, tmp_path, monkeypatch):
    honest = store.place_order
    monkeypatch.setattr(
        store, "place_order", lambda qid: {"id": honest(qid)["id"], "status": "placed"}
    )
    res = checkout(conn, tmp_path)
    assert codes(res) == ["EXECUTION_MISMATCH"] and res.uncertain
    assert store.cancelled_orders == []  # no quote_id: nothing proves which order is ours


def test_if_the_cancel_fails_the_outcome_is_unknown_not_safe(conn, store, tmp_path, monkeypatch):
    lying(store, monkeypatch, total_paise=649_800)

    def refuse(order_id):
        raise ShopError("cannot cancel")

    monkeypatch.setattr(store, "cancel_order", refuse)
    res = checkout(conn, tmp_path)
    assert not res.executed and res.uncertain and codes(res) == ["EXECUTION_MISMATCH"]


def test_a_confirmation_naming_another_order_never_cancels_it(conn, store, tmp_path, monkeypatch):
    """Review MED-3: a wrong total plus a historical order id. The past order is not touched."""
    lying(store, monkeypatch, total_paise=649_800, id="ORD-5001", quote_id="Q-someone-else")
    res = checkout(conn, tmp_path)
    assert res.uncertain and codes(res) == ["EXECUTION_MISMATCH"]
    assert "ORD-5001" not in store.cancelled_orders


def test_confirmation_numbers_must_be_real_integers(conn, store, tmp_path, monkeypatch):
    """Review LOW-1: qty=True or a float total is not a match."""
    honest = store.place_order

    def place(qid):
        o = honest(qid)
        o["lines"] = [{**ln, "qty": True} for ln in o["lines"]]
        return o

    monkeypatch.setattr(store, "place_order", place)
    assert codes(checkout(conn, tmp_path)) == ["EXECUTION_MISMATCH"]
