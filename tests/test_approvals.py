"""Approvals (SPEC §5, §6.2, §10; §17 test 3; CLAUDE.md invariant 6).

Signed by the approver's Ed25519 key over canonical(every request field), verified with the public
key only, bound to mandate + policy version + session + tool + quote hash, valid for <= 120 s, and
single use through a UNIQUE nonce insert.
"""

import threading

import pytest
from nacl.signing import SigningKey

from approver.signing import sign_request
from intentlock.approvals.requests import (
    APPROVAL_TTL,
    Binding,
    action_hash,
    binding_for,
    new_request,
)
from intentlock.approvals.verify import NonceStore, verify_approval
from intentlock.db import Database
from intentlock.models import Decision
from intentlock.policy.reasons import make_reason
from tests.factories import NOW, make_mandate, make_proposal

KEY = SigningKey.generate()
PUB = KEY.verify_key
OTHER = SigningKey.generate()
QH = "a" * 64


def review(quote_hash=QH, tool="checkout"):
    return Decision(
        action_id="a1",
        outcome="REVIEW",
        reasons=[make_reason("APPROVAL_REQUIRED")],
        quote_hash=quote_hash,
        config="intentlock",
    )


def fresh(now=NOW):
    req = new_request(make_mandate(), "s1", make_proposal(), review(), now=now)
    return req, sign_request(req, KEY)


def binding(**overrides):
    data = dict(
        mandate_id="m1", policy_version=1, session_id="s1", action_tool="checkout", quote_hash=QH
    )
    data.update(overrides)
    return Binding(**data)


def codes(reasons):
    return [r.code for r in reasons]


# -- requests ----------
def test_request_carries_every_binding_field_and_a_120s_expiry():
    req, _ = fresh()
    assert (req.mandate_id, req.policy_version, req.session_id, req.action_tool) == (
        "m1",
        1,
        "s1",
        "checkout",
    )
    assert req.quote_hash == QH and req.expires_at == NOW + APPROVAL_TTL == NOW + 120


def test_nonces_and_ids_are_unique_and_long():
    reqs = [fresh()[0] for _ in range(200)]
    assert len({r.nonce for r in reqs}) == 200 and len({r.approval_id for r in reqs}) == 200
    assert all(len(r.nonce) >= 32 for r in reqs)  # >= 128 bits of hex


@pytest.mark.parametrize("outcome", ["ALLOW", "DENY"])
def test_only_review_decisions_get_an_approval_request(outcome):
    d = review().model_copy(update={"outcome": outcome})
    with pytest.raises(ValueError):
        new_request(make_mandate(), "s1", make_proposal(), d, now=NOW)


def test_checkout_request_needs_the_decided_quote_hash():
    with pytest.raises(ValueError):
        new_request(make_mandate(), "s1", make_proposal(), review(quote_hash=None), now=NOW)


def test_non_checkout_tools_bind_to_the_action_hash():
    p = make_proposal("send_email", to="a@example.com", subject="s", body="b")
    req = new_request(make_mandate(), "s1", p, review(quote_hash=None), now=NOW)
    assert req.quote_hash == action_hash(p) and req.action_tool == "send_email"


def test_action_hash_changes_with_any_identifier_but_ignores_money_args():
    base = make_proposal("send_email", to="a@example.com", subject="s", body="b")
    other = make_proposal("send_email", to="evil@x.co", subject="s", body="b")
    priced = make_proposal("send_email", to="a@example.com", subject="s", body="b", price=1.5)
    assert action_hash(base) != action_hash(other)
    assert action_hash(base) == action_hash(priced)


def test_binding_for_matches_the_request():
    req, _ = fresh()
    b = binding_for(make_mandate(), "s1", make_proposal(), review())
    assert b == Binding(
        req.mandate_id, req.policy_version, req.session_id, req.action_tool, req.quote_hash
    )


# -- verification ----------
def test_valid_approval_verifies():
    _, ap = fresh()
    assert verify_approval(ap, verify_key=PUB, expected=binding(), now=NOW) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_id", "other"),
        ("nonce", "0" * 32),
        ("mandate_id", "m2"),
        ("policy_version", 2),
        ("session_id", "s2"),
        ("action_tool", "send_email"),
        ("quote_hash", "b" * 64),
        ("expires_at", NOW + 60),
    ],
)
def test_changing_any_signed_field_invalidates_the_signature(field, value):
    _, ap = fresh()
    forged = ap.model_copy(update={field: value})
    assert codes(verify_approval(forged, verify_key=PUB, expected=binding(), now=NOW)) == [
        "APPROVAL_INVALID_SIGNATURE"
    ]


def test_signature_from_the_wrong_key_is_rejected():
    req, _ = fresh()
    forged = sign_request(req, OTHER)
    assert codes(verify_approval(forged, verify_key=PUB, expected=binding(), now=NOW)) == [
        "APPROVAL_INVALID_SIGNATURE"
    ]


@pytest.mark.parametrize("sig", ["", "zz", "00", "ab" * 64 + "ff", "not hex at all", "ab" * 63])
def test_malformed_signatures_are_rejected_without_raising(sig):
    _, ap = fresh()
    bad = ap.model_copy(update={"signature": sig})
    assert codes(verify_approval(bad, verify_key=PUB, expected=binding(), now=NOW)) == [
        "APPROVAL_INVALID_SIGNATURE"
    ]


def test_expiry_boundary():
    _, ap = fresh()
    assert verify_approval(ap, verify_key=PUB, expected=binding(), now=NOW + 119) == []
    assert codes(verify_approval(ap, verify_key=PUB, expected=binding(), now=NOW + 120)) == [
        "APPROVAL_EXPIRED"
    ]
    assert codes(verify_approval(ap, verify_key=PUB, expected=binding(), now=NOW + 121)) == [
        "APPROVAL_EXPIRED"
    ]


def test_validity_window_longer_than_120s_is_rejected_even_if_signed():
    req, _ = fresh()
    long = sign_request(req.model_copy(update={"expires_at": NOW + 3600}), KEY)
    assert codes(verify_approval(long, verify_key=PUB, expected=binding(), now=NOW)) == [
        "APPROVAL_MISMATCH"
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("mandate_id", "m2"),
        ("policy_version", 2),
        ("session_id", "s-other"),
        ("action_tool", "share_cart"),
        ("quote_hash", "c" * 64),
    ],
)
def test_validly_signed_approval_for_a_different_binding_is_rejected(field, value):
    _, ap = fresh()
    out = verify_approval(ap, verify_key=PUB, expected=binding(**{field: value}), now=NOW)
    assert codes(out) == ["APPROVAL_MISMATCH"]


def test_expired_and_mismatched_report_both():
    _, ap = fresh()
    out = verify_approval(ap, verify_key=PUB, expected=binding(session_id="x"), now=NOW + 500)
    assert set(codes(out)) == {"APPROVAL_EXPIRED", "APPROVAL_MISMATCH"}


# -- single use ----------
@pytest.fixture
def nonces(tmp_path):
    return NonceStore(Database(tmp_path / "run.sqlite"))


def test_nonce_can_be_consumed_exactly_once(nonces):
    assert nonces.consume("n1", now=NOW) is True
    assert nonces.consume("n1", now=NOW) is False
    assert nonces.consume("n2", now=NOW) is True


def test_nonce_is_single_use_across_separate_handles(tmp_path):
    a = NonceStore(Database(tmp_path / "run.sqlite"))
    b = NonceStore(Database(tmp_path / "run.sqlite"))
    assert a.consume("n", now=NOW) is True and b.consume("n", now=NOW) is False


def test_concurrent_double_consume_exactly_one_succeeds(tmp_path):
    results, barrier = [], threading.Barrier(20)

    def worker():
        store = NonceStore(Database(tmp_path / "run.sqlite"))
        barrier.wait()
        results.append(store.consume("race", now=NOW))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and results.count(False) == 19
