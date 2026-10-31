"""Gateway app on port 8000 (SPEC §3, §16): the agent's MCP endpoint at /mcp, plus the REST surface
the UI (and, until the UI exists, `python -m intentlock.cli`) uses for sessions, the approval inbox
and receipts. Every REST route except /health needs `X-Operator-Token`, a secret the agent process
never gets; an unset token locks them all (D-026). Approval requests are pushed over SSE with the
UI on Nov 3; today they are polled.
"""

import hmac
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from intentlock.approvals.broker import ApprovalBroker
from intentlock.approvals.verify import NonceStore, load_verify_key
from intentlock.config import Settings
from intentlock.connector.shop_connector import ShopConnector
from intentlock.db import Database
from intentlock.llm.client import LLMClient
from intentlock.mandate.compiler import CompileError, compile_mandate, readback
from intentlock.mandate.grounding import GroundingReport, ground
from intentlock.models import Approval, ApprovalRequest, Mandate, Paise
from intentlock.provenance.ledger import Ledger
from intentlock.proxy.mcp_proxy import Gateway, GatewayBusy, build_gateway_mcp
from intentlock.receipts import ReceiptLog, verify_chain
from intentlock.verify.promptguard import resolve_promptguard


class SessionReq(BaseModel):
    mandate: Mandate | None = None
    # The auto-approve limit as the user typed it in the readback (D-037); must equal the mandate's.
    confirm_auto_approve_paise: Paise | None = None


# Bounded so the grounding regexes stay fast on any input (D-037 final review).
MAX_REQUEST_CHARS = 2_000
MAX_MANDATE_TEXT_CHARS = 8_000


class Answer(BaseModel):
    question: str = Field(max_length=1_000)
    answer: str = Field(max_length=500)


class CompileReq(BaseModel):
    user_text: str = Field(max_length=MAX_REQUEST_CHARS)
    answers: list[Answer] = Field(default=[], max_length=30)


def report_json(report: GroundingReport) -> dict:
    return {
        "mandate": report.mandate.model_dump(mode="json"),
        "readback": readback(report),
        "issues": [i.__dict__ for i in report.issues],
        "clarifications": report.clarifications,
    }


def broker_waiter(broker: ApprovalBroker, clock=time.time):
    """Waits for the UI's signed approval until the request expires (120 s at most)."""

    def wait(req: ApprovalRequest) -> Approval | None:
        broker.open(req)
        return broker.wait(req.approval_id, timeout=req.expires_at - clock())

    return wait


def create_app(
    gateway: Gateway,
    broker: ApprovalBroker,
    *,
    operator_token: str,
    compiler: Callable[..., GroundingReport] | None = None,
) -> FastAPI:
    mcp = build_gateway_mcp(gateway)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="IntentLock gateway",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def operator(x_operator_token: str | None = Header(default=None)) -> None:
        supplied = (x_operator_token or "").encode("utf-8", "surrogatepass")
        if not operator_token or not hmac.compare_digest(operator_token.encode(), supplied):
            raise HTTPException(401, "operator token required")

    guarded = [Depends(operator)]

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "config": gateway.config, "operator_token_set": bool(operator_token)}

    @app.post("/session", dependencies=guarded)
    def start_session(req: SessionReq) -> dict:
        if req.mandate is not None:  # SPEC §9 / D-026: only grounded, confirmed mandates
            if len(req.mandate.user_text) > MAX_MANDATE_TEXT_CHARS:
                raise HTTPException(422, "the request text is too long")
            report = ground(req.mandate)
            if report.mandate != req.mandate or report.clarifications:
                raise HTTPException(
                    422, {"error": "mandate is not grounded", **report_json(report)}
                )
            if not req.mandate.user_confirmed:
                raise HTTPException(422, "the user has not confirmed this mandate")
            auto = req.mandate.auto_approve_under_paise
            if auto and req.confirm_auto_approve_paise != auto:
                raise HTTPException(
                    422, "type the auto-approve amount to confirm purchases without asking"
                )
        try:
            session_id = gateway.start_session(req.mandate)
        except GatewayBusy:
            raise HTTPException(
                409, "a commit is in progress; finish, reject or end it first"
            ) from None
        return {"session_id": session_id, "config": gateway.config}

    @app.post("/mandate/compile", dependencies=guarded)
    def compile_route(req: CompileReq) -> dict:
        if compiler is None:
            raise HTTPException(503, "no mandate compiler configured")
        try:
            answers = [a.model_dump() for a in req.answers]
            report = compiler(req.user_text, answers) if answers else compiler(req.user_text)
            return report_json(report)
        except CompileError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.post("/session/end", dependencies=guarded)
    def end_session() -> dict:
        for r in broker.pending():
            broker.reject(r.approval_id)
        gateway.end_session()
        return {"ok": True}

    @app.get("/approvals/pending", dependencies=guarded)
    def pending() -> list[dict]:
        return [
            {"request": r.model_dump(), "summary": gateway.summaries.get(r.approval_id)}
            for r in broker.pending()
        ]

    @app.post("/approvals/{approval_id}/signature", dependencies=guarded)
    def submit(approval_id: str, approval: Approval) -> dict:
        if not broker.submit(approval_id, approval):
            raise HTTPException(404, "no pending approval with that id")
        return {"ok": True}

    @app.post("/approvals/{approval_id}/reject", dependencies=guarded)
    def reject(approval_id: str) -> dict:
        if not broker.reject(approval_id):
            raise HTTPException(404, "no pending approval with that id")
        return {"ok": True}

    @app.get("/receipts", dependencies=guarded)
    def receipts() -> list[dict]:
        return [r.model_dump() for r in gateway.receipts.all()]

    @app.get("/receipts/verify", dependencies=guarded)
    def verify() -> dict:
        check = verify_chain(gateway.receipts.all())
        return {"ok": check.ok, "head": check.head, "bad_seq": check.bad_seq}

    app.mount("/", mcp_app)
    return app


def build_from_env() -> FastAPI:
    """`uvicorn --factory intentlock.api:build_from_env`. One SQLite file per run."""
    if os.environ.get("APPROVER_UI_TOKEN"):
        raise RuntimeError(
            "APPROVER_UI_TOKEN is in the gateway's environment; the gateway must never hold the"
            " user's approval token (start it with `make dev`, which passes an allow-list)."
        )
    settings = Settings.from_env()
    db = Database(Path(settings.gateway_db_dir) / f"gateway-{int(time.time())}.sqlite")
    broker = ApprovalBroker()
    connector = ShopConnector(settings.shop_url)
    features, scorer, pg_status = resolve_promptguard(settings.features)
    gateway = Gateway(
        config=settings.config,
        connector=connector,
        receipts=ReceiptLog(db),
        ledger=Ledger(db),
        nonces=NonceStore(db),
        verify_key=load_verify_key(settings.approver_public_key_path),
        waiter=broker_waiter(broker),
        extra_steps={},  # taint (6) is built in; two-channel (8) / Prompt Guard (9) are features
        features=features,
        scorer=scorer,
        promptguard_status=pg_status,
    )
    llm = LLMClient(settings)

    def compiler(text: str, answers: list[dict] | None = None) -> GroundingReport:
        return compile_mandate(
            text, llm, now=int(time.time()), model=settings.model_id, answers=answers
        )

    return create_app(gateway, broker, operator_token=settings.operator_token, compiler=compiler)
