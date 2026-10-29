"""Approver on port 8002 (SPEC §10; D-004, D-026). Holds the Ed25519 private key and signs a request
only when the UI posts the user's click, proven by `X-Approver-Token` (a UI-only secret the gateway
never holds; an unset token refuses everything). It refuses any request whose expiry is not within
120 s, so the gateway cannot ask for a long-lived approval. Demo-grade: production would use a
passkey on the user's device instead of a shared UI token (README limitation).
"""

import hmac
import os
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, Header, HTTPException
from nacl.signing import SigningKey

from approver.keys import load_signing_key
from approver.signing import sign_request
from intentlock.approvals.requests import APPROVAL_TTL
from intentlock.models import ApprovalRequest


def create_app(
    key: SigningKey | None, clock: Callable[[], float] = time.time, *, ui_token: str = ""
) -> FastAPI:
    app = FastAPI(title="IntentLock approver", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict:
        return {"ok": key is not None}

    @app.get("/public_key")
    def public_key() -> dict:
        if key is None:
            raise HTTPException(503, "approver key not configured")
        return {"public_key": key.verify_key.encode().hex()}

    def ui_click(x_approver_token: str | None = Header(default=None)) -> None:
        supplied = (x_approver_token or "").encode("utf-8", "surrogatepass")
        if not ui_token or not hmac.compare_digest(ui_token.encode(), supplied):
            raise HTTPException(401, "signing needs the UI's approval token")

    @app.post("/sign", dependencies=[Depends(ui_click)])
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
    return create_app(key, ui_token=os.environ.get("APPROVER_UI_TOKEN", ""))
