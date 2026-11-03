"""Review gap 5 (confused deputy): the gateway never passes the agent's credentials to the merchant.

MCP's security guidance forbids token passthrough. Here the agent sends its own Authorization header
and cookie to the gateway; a recorder in front of the shop sees every request the gateway makes
(MCP reads and REST quotes/orders) and must find only the connector's own shop key, never anything
the agent sent. Together with invariants 7, 8 and 12 this is the deputy boundary.
"""

import asyncio

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from intentlock.models import Mandate
from shop.app import create_app as create_shop
from shop.store import Store
from tests.servers import serve
from tests.test_e2e_loop import OP, OPH

AGENT_SECRET = "agent-bearer-SHOULD-NEVER-REACH-THE-SHOP"
SHOP_KEY = "deputy-shop-key"


class Recorder:
    """ASGI wrapper that keeps every request's headers."""

    def __init__(self, app):
        self.app, self.seen = app, []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.seen.append({k.decode().lower(): v.decode() for k, v in scope["headers"]})
        await self.app(scope, receive, send)


@pytest.fixture
def gateway_and_shop(tmp_path):
    from nacl.signing import SigningKey

    from intentlock.api import broker_waiter
    from intentlock.api import create_app as create_gateway
    from intentlock.approvals.broker import ApprovalBroker
    from intentlock.approvals.verify import NonceStore
    from intentlock.connector.shop_connector import ShopConnector
    from intentlock.db import Database
    from intentlock.provenance.ledger import Ledger
    from intentlock.proxy.mcp_proxy import Gateway
    from intentlock.receipts import ReceiptLog
    from tests.factories import grounded_mandate

    recorder = Recorder(create_shop(Store(), SHOP_KEY))
    with serve(recorder) as shop_url:
        db = Database(tmp_path / "g.sqlite")
        broker = ApprovalBroker()
        gw = Gateway(
            config="intentlock", connector=ShopConnector(shop_url, api_key=SHOP_KEY),
            receipts=ReceiptLog(db), ledger=Ledger(db), nonces=NonceStore(db),
            verify_key=SigningKey.generate().verify_key, waiter=broker_waiter(broker),
            extra_steps={}, features=frozenset(),
        )  # fmt: skip
        with serve(create_gateway(gw, broker, operator_token=OP)) as gw_url:
            m: Mandate = grounded_mandate()
            httpx.post(
                f"{gw_url}/session",
                json={"mandate": m.model_dump(mode="json"), "confirm_auto_approve_paise": 200000},
                headers=OPH,
            ).raise_for_status()
            yield gw_url, recorder


async def agent_calls(gw_url):
    headers = {"Authorization": f"Bearer {AGENT_SECRET}", "Cookie": f"sid={AGENT_SECRET}"}
    async with httpx.AsyncClient(headers=headers, timeout=10) as http:
        async with streamable_http_client(f"{gw_url}/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                await s.call_tool("search_products", {"query": "mouse"})
                await s.call_tool("get_saved_addresses", {})  # a clean source for ADDR-0001
                await s.call_tool("add_to_cart", {"sku": "MS-1001", "qty": 1})
                return await s.call_tool(
                    "checkout", {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}
                )


def test_agent_credentials_never_reach_the_merchant(gateway_and_shop):
    gw_url, recorder = gateway_and_shop
    result = asyncio.run(agent_calls(gw_url))
    assert "executed" in result.content[0].text  # the ₹1,499 mouse: a real read + commit path
    assert len(recorder.seen) >= 4  # MCP reads, the quote and the order all went to the shop
    for headers in recorder.seen:
        assert AGENT_SECRET not in str(headers)
        assert "authorization" not in headers and "cookie" not in headers
    keyed = [h for h in recorder.seen if h.get("x-api-key") == SHOP_KEY]
    assert keyed and len(keyed) == len([h for h in recorder.seen if "x-api-key" in h])


def test_the_agent_cannot_present_its_own_shop_key(gateway_and_shop):
    """Even if the agent guessed the header name, the gateway's outbound requests use only the
    connector's key: an agent-sent X-API-Key never replaces or joins it."""
    gw_url, recorder = gateway_and_shop

    async def calls():
        async with httpx.AsyncClient(headers={"X-API-Key": AGENT_SECRET}, timeout=10) as http:
            async with streamable_http_client(f"{gw_url}/mcp", http_client=http) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    await s.call_tool("search_products", {"query": "mouse"})

    asyncio.run(calls())
    assert recorder.seen and all(h.get("x-api-key") == SHOP_KEY for h in recorder.seen)
