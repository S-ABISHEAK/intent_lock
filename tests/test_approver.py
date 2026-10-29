"""Approver service (SPEC §10; CLAUDE.md invariant 7): holds the private key, signs only short-lived
requests, and the gateway can verify with the public key alone."""

import os
import stat

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from approver.app import create_app
from approver.keys import generate_keys, load_signing_key
from intentlock.approvals.requests import Binding
from intentlock.approvals.verify import load_verify_key, verify_approval
from intentlock.models import Approval

NOW = 1_000_000
KEY = SigningKey.generate()


def request_body(expires_at=NOW + 120):
    return {
        "approval_id": "ap1",
        "nonce": "ab" * 16,
        "mandate_id": "m1",
        "policy_version": 1,
        "session_id": "s1",
        "action_tool": "checkout",
        "quote_hash": "a" * 64,
        "expires_at": expires_at,
    }


UI = "ui-token-test"


@pytest.fixture
def client():
    return TestClient(
        create_app(KEY, clock=lambda: NOW, ui_token=UI), headers={"X-Approver-Token": UI}
    )


def test_signs_a_valid_request_and_the_signature_verifies(client):
    r = client.post("/sign", json=request_body())
    assert r.status_code == 200
    ap = Approval.model_validate(r.json())
    b = Binding("m1", 1, "s1", "checkout", "a" * 64)
    assert verify_approval(ap, verify_key=KEY.verify_key, expected=b, now=NOW) == []


@pytest.mark.parametrize("expires_at", [NOW, NOW - 1, NOW + 121, NOW + 10**6])
def test_refuses_requests_outside_the_120s_window(client, expires_at):
    assert client.post("/sign", json=request_body(expires_at)).status_code == 400


def test_accepts_exactly_120s(client):
    assert client.post("/sign", json=request_body(NOW + 120)).status_code == 200


def test_refuses_malformed_requests(client):
    assert client.post("/sign", json={**request_body(), "extra": 1}).status_code == 422
    assert client.post("/sign", json={**request_body(), "expires_at": 1.5}).status_code == 422


def test_without_a_key_it_refuses_to_sign():
    c = TestClient(
        create_app(None, clock=lambda: NOW, ui_token=UI), headers={"X-Approver-Token": UI}
    )
    assert c.post("/sign", json=request_body()).status_code == 503


def test_public_key_endpoint_matches_and_never_reveals_the_private_key(client):
    body = client.get("/public_key").json()
    assert body == {"public_key": KEY.verify_key.encode().hex()}
    assert bytes(KEY).hex() not in client.get("/public_key").text


def test_key_generation_writes_a_private_file_and_never_overwrites(tmp_path):
    priv, pub = generate_keys(tmp_path)
    assert stat.S_IMODE(os.stat(priv).st_mode) == 0o600
    sk = load_signing_key(priv)
    assert load_verify_key(pub) == sk.verify_key
    with pytest.raises(FileExistsError):
        generate_keys(tmp_path)


def test_loading_a_missing_or_corrupt_key_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_signing_key(tmp_path / "nope.key")
    bad = tmp_path / "bad.pub"
    bad.write_text("zz")
    with pytest.raises(ValueError):
        load_verify_key(bad)


def test_signing_needs_the_ui_token_the_gateway_never_holds():
    c = TestClient(create_app(KEY, clock=lambda: NOW, ui_token=UI))
    assert c.post("/sign", json=request_body()).status_code == 401
    assert (
        c.post("/sign", json=request_body(), headers={"X-Approver-Token": "wrong"}).status_code
        == 401
    )


def test_an_unset_ui_token_refuses_every_signature():
    c = TestClient(create_app(KEY, clock=lambda: NOW, ui_token=""))
    assert c.post("/sign", json=request_body(), headers={"X-Approver-Token": ""}).status_code == 401


def test_auth_is_checked_before_the_body():
    c = TestClient(create_app(KEY, clock=lambda: NOW, ui_token=UI))
    assert c.post("/sign", json={}).status_code == 401
