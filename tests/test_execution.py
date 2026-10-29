"""Connector execution (SPEC §6.2; §17 test 3; CLAUDE.md invariants 2, 3, 6).

Order: re-fetch the quote (or re-resolve the target), verify the approval, consume the nonce
atomically, then call the merchant. Every failure is a non-executed result with a DENY code.
"""

import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from approver.signing import sign_request
from intentlock.approvals.requests import binding_for, new_request
from intentlock.approvals.verify import NonceStore
from intentlock.connector.shop_connector import ShopConnector
from intentlock.db import Database
from intentlock.models import Decision
from intentlock.policy.reasons import make_reason
from shop.app import create_app
from shop.store import Store
from tests.factories import NOW, make_mandate, make_proposal

SHOP_KEY = "test-shop-key-exec"
APPROVER = SigningKey.generate()
MANDATE = make_mandate(allowed_recipients=["a@example.com"])


@pytest.fixture
def store():
    s = Store()
    s.add_to_cart("LAP-1201")
    return s


@pytest.fixture
def conn(store):
    return ShopConnector(
        "http://shop",
        api_key=SHOP_KEY,
        client=TestClient(create_app(store, SHOP_KEY), base_url="http://shop"),
    )


@pytest.fixture
def nonces(tmp_path):
    return NonceStore(Database(tmp_path / "run.sqlite"))


def decision(outcome, quote_hash):
    code = {
        "ALLOW": "ALLOWED_IN_MANDATE",
        "REVIEW": "APPROVAL_REQUIRED",
        "DENY": "BUDGET_EXCEEDED",
    }[outcome]
    return Decision(
        action_id="a1",
        outcome=outcome,
        reasons=[make_reason(code)],
        quote_hash=quote_hash,
        config="intentlock",
    )


def approved(proposal, d, key=APPROVER, now=NOW, session="s1"):
    req = new_request(MANDATE, session, proposal, d, now=now)
    return sign_request(req, key)


def run(conn, nonces, proposal, d, approval=None, now=NOW, session="s1"):
    expected = binding_for(MANDATE, session, proposal, d) if d.outcome == "REVIEW" else None
    return conn.execute(
        proposal,
        d,
        approval=approval,
        expected=expected,
        verify_key=APPROVER.verify_key,
        nonces=nonces,
        now=now,
    )


def codes(res):
    return [r.code for r in res.reasons]


def checkout_review(conn):
    q = conn.get_quote("c1", "ADDR-0001")
    p = make_proposal()
    return p, decision("REVIEW", q.quote_hash), q


# -- happy paths ---------------------------------------------------------------------------
def test_review_with_valid_approval_places_the_order_from_the_quote(conn, nonces, store):
    p, d, q = checkout_review(conn)
    res = run(conn, nonces, p, d, approved(p, d))
    assert res.executed and codes(res) == []
    assert len(store.orders) == 1 and store.orders[0]["total_paise"] == q.total_paise
    assert res.result["quote_hash"] == q.quote_hash


def test_allow_executes_without_an_approval(conn, nonces, store):
    q = conn.get_quote("c1", "ADDR-0001")
    res = run(conn, nonces, make_proposal(), decision("ALLOW", q.quote_hash))
    assert res.executed and len(store.orders) == 1


# -- quote changed (TOCTOU) ----------------------------------------------------------------
def test_tampered_cart_after_approval_is_quote_changed_and_nothing_executes(conn, nonces, store):
    p, d, _ = checkout_review(conn)
    ap = approved(p, d)
    store.add_to_cart("XC-4999")  # tamper after the user approved
    res = run(conn, nonces, p, d, ap)
    assert not res.executed and codes(res) == ["QUOTE_CHANGED"] and store.orders == []
    assert nonces.consume(ap.nonce, now=NOW) is True  # not burned by a failed pre-check


def test_allow_with_a_stale_quote_hash_is_quote_changed(conn, nonces, store):
    q = conn.get_quote("c1", "ADDR-0001")
    store.add_to_cart("MS-1001")
    res = run(conn, nonces, make_proposal(), decision("ALLOW", q.quote_hash))
    assert codes(res) == ["QUOTE_CHANGED"] and store.orders == []


def test_shop_409_at_order_time_is_quote_changed(store, nonces):
    real = TestClient(create_app(store, SHOP_KEY), base_url="http://shop")

    def handler(request):
        if request.url.path == "/orders":
            return httpx.Response(409, json={"detail": "stale"})
        r = real.request(
            request.method, request.url.path, content=request.content, headers=dict(request.headers)
        )
        return httpx.Response(
            r.status_code, content=r.content, headers={"content-type": "application/json"}
        )

    c = ShopConnector(
        "http://shop",
        api_key=SHOP_KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://shop"),
    )
    p, d, _ = checkout_review(c)
    res = run(c, nonces, p, d, approved(p, d))
    assert codes(res) == ["QUOTE_CHANGED"] and store.orders == []


def test_merchant_failure_after_consume_is_execution_failed_and_the_nonce_stays_used(store, nonces):
    real = TestClient(create_app(store, SHOP_KEY), base_url="http://shop")

    def handler(request):
        if request.url.path == "/orders":
            return httpx.Response(500)
        r = real.request(
            request.method, request.url.path, content=request.content, headers=dict(request.headers)
        )
        return httpx.Response(
            r.status_code, content=r.content, headers={"content-type": "application/json"}
        )

    c = ShopConnector(
        "http://shop",
        api_key=SHOP_KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://shop"),
    )
    p, d, _ = checkout_review(c)
    ap = approved(p, d)
    res = run(c, nonces, p, d, ap)
    assert codes(res) == ["EXECUTION_FAILED"] and not res.executed
    assert nonces.consume(ap.nonce, now=NOW) is False


# -- approval failures ----------------------------------------------------------------------
def test_review_without_approval_is_denied(conn, nonces, store):
    p, d, _ = checkout_review(conn)
    res = run(conn, nonces, p, d, None)
    assert codes(res) == ["APPROVAL_MISSING"] and store.orders == []


def test_deny_decisions_are_never_executed(conn, nonces, store):
    q = conn.get_quote("c1", "ADDR-0001")
    with pytest.raises(ValueError):
        run(conn, nonces, make_proposal(), decision("DENY", q.quote_hash))
    assert store.orders == []


def test_expired_approval(conn, nonces, store):
    p, d, _ = checkout_review(conn)
    res = run(conn, nonces, p, d, approved(p, d), now=NOW + 120)
    assert codes(res) == ["APPROVAL_EXPIRED"] and store.orders == []


def test_wrong_key(conn, nonces, store):
    p, d, _ = checkout_review(conn)
    res = run(conn, nonces, p, d, approved(p, d, key=SigningKey.generate()))
    assert codes(res) == ["APPROVAL_INVALID_SIGNATURE"] and store.orders == []


def test_approval_for_another_session_is_a_mismatch(conn, nonces, store):
    p, d, _ = checkout_review(conn)
    res = run(conn, nonces, p, d, approved(p, d, session="s-other"), session="s1")
    assert codes(res) == ["APPROVAL_MISMATCH"] and store.orders == []


def test_replayed_approval_on_an_identical_cart_is_rejected(conn, nonces, store):
    p, d, _ = checkout_review(conn)
    ap = approved(p, d)
    assert run(conn, nonces, p, d, ap).executed
    store.add_to_cart("LAP-1201")  # same cart contents -> same quote hash again
    res = run(conn, nonces, p, d, ap)
    assert codes(res) == ["APPROVAL_REPLAYED"] and len(store.orders) == 1


def test_concurrent_executions_of_one_approval_place_exactly_one_order(conn, nonces, store):
    p, d, _ = checkout_review(conn)
    ap = approved(p, d)
    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(run(conn, nonces, p, d, ap))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r.executed for r in results) == 1 and len(store.orders) == 1


# -- comms and destructive tools ------------------------------------------------------------
def test_send_email_executes_with_an_action_bound_approval(conn, nonces, store):
    p = make_proposal("send_email", to="a@example.com", subject="hi", body="b", price=9.5)
    d = decision("REVIEW", None)
    res = run(conn, nonces, p, d, approved(p, d))
    assert res.executed and store.messages[0]["to"] == "a@example.com"


def test_approval_for_one_recipient_cannot_send_to_another(conn, nonces, store):
    ok = make_proposal("send_email", to="a@example.com", subject="hi", body="b")
    evil = make_proposal("send_email", to="evil@x.co", subject="hi", body="b")
    d = decision("REVIEW", None)
    res = run(conn, nonces, evil, d, approved(ok, d))
    assert codes(res) == ["APPROVAL_MISMATCH"] and store.messages == []


def test_share_cart_and_cancel_order_execute(conn, nonces, store):
    for p in (
        make_proposal("share_cart", cart_id="c1", to="a@example.com"),
        make_proposal("cancel_order", id="ORD-5001"),
    ):
        d = decision("REVIEW", None)
        assert run(conn, nonces, p, d, approved(p, d)).executed
    assert store.messages[0]["kind"] == "share_cart" and store.cancelled_orders == ["ORD-5001"]


def test_delete_target_that_vanished_is_not_executed(conn, nonces, store):
    p = make_proposal("delete_saved_address", id="ADDR-0002")
    d = decision("REVIEW", None)
    ap = approved(p, d)
    store.delete_saved_address("ADDR-0002")
    res = run(conn, nonces, p, d, ap)
    assert codes(res) == ["QUOTE_UNAVAILABLE"] and not res.executed


def test_delete_executes(conn, nonces, store):
    p = make_proposal("delete_saved_address", id="ADDR-0002")
    d = decision("REVIEW", None)
    assert run(conn, nonces, p, d, approved(p, d)).executed
    assert store.deleted_addresses == ["ADDR-0002"]


def test_the_shop_key_never_appears_in_execution_results(conn, nonces):
    p, d, _ = checkout_review(conn)
    res = run(conn, nonces, p, d, approved(p, d))
    assert SHOP_KEY not in repr(res)
