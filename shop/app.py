"""ShopLite app on port 8001 (SPEC §3, §14): REST for the connector, MCP at /mcp for the proxy.

Every route except /health requires `X-API-Key`. An unset or empty server key rejects everything
(fail closed). The key is read here because the shop is the party that checks it.
"""

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from shop.mcp_server import build_mcp
from shop.store import ShopError, Store


def _authorized(api_key: str, supplied: str | None) -> bool:
    return bool(api_key) and supplied is not None and hmac.compare_digest(api_key, supplied)


class _McpAuth:
    """ASGI guard so the MCP endpoint needs the same key as REST."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app, self.api_key = app, api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if not _authorized(self.api_key, headers.get("x-api-key")):
                await JSONResponse({"detail": "unauthorized"}, status_code=401)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


class QuoteReq(BaseModel):
    cart_id: str
    shipping_address_id: str


class OrderReq(BaseModel):
    quote_id: str


class ShareReq(BaseModel):
    cart_id: str
    to: str


class EmailReq(BaseModel):
    to: str
    subject: str
    body: str


def create_app(store: Store, api_key: str) -> FastAPI:
    mcp = build_mcp(store)
    mcp_app = mcp.streamable_http_app()  # creates mcp.session_manager

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="ShopLite", lifespan=lifespan)
    app.state.store = store

    @app.exception_handler(ShopError)
    async def shop_error(_: Request, exc: ShopError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    def auth(x_api_key: str | None = Header(default=None)) -> None:
        if not _authorized(api_key, x_api_key):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    guarded = [Depends(auth)]

    @app.post("/quote", dependencies=guarded)
    def quote(req: QuoteReq) -> dict:
        return store.quote(req.cart_id, req.shipping_address_id).model_dump(mode="json")

    @app.post("/orders", dependencies=guarded)
    def orders(req: OrderReq) -> dict:
        return store.place_order(req.quote_id)

    @app.post("/share_cart", dependencies=guarded)
    def share_cart(req: ShareReq) -> dict:
        return store.share_cart(req.cart_id, req.to)

    @app.post("/send_email", dependencies=guarded)
    def send_email(req: EmailReq) -> dict:
        return store.send_email(req.to, req.subject, req.body)

    @app.delete("/addresses/{address_id}", dependencies=guarded)
    def delete_address(address_id: str) -> dict:
        return store.delete_saved_address(address_id)

    @app.post("/orders/{order_id}/cancel", dependencies=guarded)
    def cancel_order(order_id: str) -> dict:
        return store.cancel_order(order_id)

    @app.get("/resolve/{kind}/{target_id}", dependencies=guarded)
    def resolve(kind: str, target_id: str) -> dict:
        return store.resolve_target(kind, target_id)

    @app.get("/state", dependencies=guarded)
    def state() -> dict:
        return store.state()

    @app.post("/reset", dependencies=guarded)
    def reset() -> dict:
        store.reset()
        return {"ok": True}

    app.mount("/", _McpAuth(mcp_app, api_key))
    return app


# `uvicorn shop.app:app`. With SHOP_API_KEY unset every guarded route returns 401.
app = create_app(
    Store(os.environ.get("SHOP_PUBLIC_URL", "http://localhost:8001")),
    os.environ.get("SHOP_API_KEY", ""),
)
