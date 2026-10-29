"""Approver on port 8002 (SPEC §10; D-004). Holds the Ed25519 private key and signs a request only
when the UI posts the user's click. It refuses any request whose expiry is not within 120 s, so the
gateway cannot ask for a long-lived approval. Demo-grade: production would use a passkey on the
user's device; there is no authentication of the UI here (README limitation).
"""

import os
import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from nacl.signing import SigningKey

from approver.keys import load_signing_key
from approver.signing import sign_request
from intentlock.approvals.requests import APPROVAL_TTL
from intentlock.models import ApprovalRequest


def create_app(key: SigningKey | None, clock: Callable[[], float] = time.time) -> FastAPI:
    app = FastAPI(title="IntentLock approver", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict:
        return {"ok": key is not None}

    @app.get("/public_key")
    def public_key() -> dict:
        if key is None:
            raise HTTPException(503, "approver key not configured")
        return {"public_key": key.verify_key.encode().hex()}

    @app.post("/sign")
    def sign(req: ApprovalRequest) -> dict:
        if key is None:
            raise HTTPException(503, "approver key not configured")
        now = int(clock())
        if not now < req.expires_at <= now + APPROVAL_TTL:
            raise HTTPException(400, "expires_at must be within the next 120 seconds")
        return sign_request(req, key).model_dump()

    return app


def build_from_env() -> FastAPI:
    """`uvicorn --factory approver.app:build_from_env`."""
    path = os.environ.get("APPROVER_KEY_PATH", "keys/approver.key")
    try:
        key = load_signing_key(path)
    except (OSError, ValueError):
        key = None
    return create_app(key)
