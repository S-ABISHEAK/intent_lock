"""Gateway app on port 8000 (SPEC §3, §16): the agent's MCP endpoint at /mcp, plus the REST surface
the UI (and, until the UI exists, `python -m intentlock.cli`) uses for sessions, the approval inbox
and receipts. Approval requests are pushed over SSE with the UI on Nov 3; today they are polled.
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from intentlock.approvals.broker import ApprovalBroker
from intentlock.approvals.verify import NonceStore, load_verify_key
from intentlock.config import Settings
from intentlock.connector.shop_connector import ShopConnector
from intentlock.db import Database
from intentlock.models import Approval, ApprovalRequest, Mandate
from intentlock.proxy.mcp_proxy import Gateway, build_gateway_mcp
from intentlock.receipts import ReceiptLog, verify_chain


class SessionReq(BaseModel):
    mandate: Mandate | None = None


def broker_waiter(broker: ApprovalBroker, clock=time.time):
    """Waits for the UI's signed approval until the request expires (120 s at most)."""

    def wait(req: ApprovalRequest) -> Approval | None:
        broker.open(req)
        return broker.wait(req.approval_id, timeout=req.expires_at - clock())

    return wait


def create_app(gateway: Gateway, broker: ApprovalBroker) -> FastAPI:
    mcp = build_gateway_mcp(gateway)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="IntentLock gateway", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "config": gateway.config}

    @app.post("/session")
    def start_session(req: SessionReq) -> dict:
        return {"session_id": gateway.start_session(req.mandate), "config": gateway.config}

    @app.get("/approvals/pending")
    def pending() -> list[dict]:
        return [r.model_dump() for r in broker.pending()]

    @app.post("/approvals/{approval_id}/signature")
    def submit(approval_id: str, approval: Approval) -> dict:
        if not broker.submit(approval_id, approval):
            raise HTTPException(404, "no pending approval with that id")
        return {"ok": True}

    @app.post("/approvals/{approval_id}/reject")
    def reject(approval_id: str) -> dict:
        if not broker.reject(approval_id):
            raise HTTPException(404, "no pending approval with that id")
        return {"ok": True}

    @app.get("/receipts")
    def receipts() -> list[dict]:
        return [r.model_dump() for r in gateway.receipts.all()]

    @app.get("/receipts/verify")
    def verify() -> dict:
        check = verify_chain(gateway.receipts.all())
        return {"ok": check.ok, "head": check.head, "bad_seq": check.bad_seq}

    app.mount("/", mcp_app)
    return app


def build_from_env() -> FastAPI:
    """`uvicorn --factory intentlock.api:build_from_env`. One SQLite file per run."""
    settings = Settings.from_env()
    db = Database(Path(settings.gateway_db_dir) / f"gateway-{int(time.time())}.sqlite")
    broker = ApprovalBroker()
    connector = ShopConnector(settings.shop_url)
    gateway = Gateway(
        config=settings.config,
        connector=connector,
        receipts=ReceiptLog(db),
        nonces=NonceStore(db),
        verify_key=load_verify_key(settings.approver_public_key_path),
        waiter=broker_waiter(broker),
        extra_steps={},  # taint (6) lands Oct 31; two-channel (8) / Prompt Guard (9) are features
        features=settings.features,
    )
    return create_app(gateway, broker)
