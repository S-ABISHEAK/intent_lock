"""ShopLite REST + MCP (SPEC §14, D-012): key required everywhere, commit tools inert over MCP."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from shop.app import create_app
from shop.mcp_server import AGENT_TOOLS, COMMIT_TOOLS, build_mcp
from shop.store import Store

KEY = "test-shop-key"
H = {"X-API-Key": KEY}


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def client(store):
    with TestClient(create_app(store, KEY)) as c:
        yield c


def test_health_is_open_but_everything_else_needs_the_key(client):
    assert client.get("/health").status_code == 200
    for method, path in [
        ("post", "/quote"),
        ("post", "/orders"),
        ("post", "/share_cart"),
        ("post", "/send_email"),
        ("delete", "/addresses/ADDR-0001"),
        ("post", "/orders/ORD-5001/cancel"),
        ("get", "/resolve/address/ADDR-0001"),
        ("get", "/state"),
        ("post", "/reset"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path
        assert getattr(client, method)(path, headers={"X-API-Key": "wrong"}).status_code == 401


def test_empty_server_key_rejects_everything_even_an_empty_header(store):
    with TestClient(create_app(store, "")) as c:
        assert c.get("/state").status_code == 401
        assert c.get("/state", headers={"X-API-Key": ""}).status_code == 401


def test_mcp_endpoint_needs_the_key(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_quote_then_order_flow(client, store):
    store.add_to_cart("MS-1001")
    q = client.post("/quote", headers=H, json={"cart_id": "c1", "shipping_address_id": "ADDR-0001"})
    assert q.status_code == 200 and q.json()["total_paise"] == 149900
    o = client.post("/orders", headers=H, json={"quote_id": q.json()["quote_id"]})
    assert o.status_code == 200 and o.json()["quote_hash"] == q.json()["quote_hash"]
    assert client.get("/state", headers=H).json()["orders"][0]["total_paise"] == 149900


def test_tampered_cart_gives_409_and_no_order(client, store):
    store.add_to_cart("LAP-1201")
    q = client.post("/quote", headers=H, json={"cart_id": "c1", "shipping_address_id": "ADDR-0001"})
    store.add_to_cart("XC-4999")
    r = client.post("/orders", headers=H, json={"quote_id": q.json()["quote_id"]})
    assert r.status_code == 409 and store.orders == []


def test_errors_map_to_http_status(client):
    assert (
        client.post(
            "/quote", headers=H, json={"cart_id": "c1", "shipping_address_id": "A"}
        ).status_code
        == 400
    )
    assert client.post("/orders", headers=H, json={"quote_id": "Q-nope"}).status_code == 404
    assert client.get("/resolve/address/ADDR-9999", headers=H).status_code == 404


def test_comms_and_destructive_endpoints(client, store):
    store.add_to_cart("MS-1001")
    assert (
        client.post("/share_cart", headers=H, json={"cart_id": "c1", "to": "a@b.co"}).status_code
        == 200
    )
    body = {"to": "a@b.co", "subject": "s", "body": "b"}
    assert client.post("/send_email", headers=H, json=body).status_code == 200
    assert client.delete("/addresses/ADDR-0002", headers=H).status_code == 200
    assert client.post("/orders/ORD-5001/cancel", headers=H).status_code == 200
    assert client.post("/reset", headers=H).status_code == 200
    assert client.get("/state", headers=H).json()["messages"] == []


# -- MCP surface (called in-process; the HTTP transport is exercised with the proxy on Oct 30) --


def run(coro):
    return asyncio.run(coro)


def test_mcp_exposes_exactly_the_agent_tools_and_no_internal_ones(store):
    names = {t.name for t in run(build_mcp(store).list_tools())}
    assert names == set(AGENT_TOOLS)
    assert not {"get_quote", "place_order"} & names


def test_mcp_read_and_cart_tools_work_against_the_shared_store(store):
    mcp = build_mcp(store)
    run(mcp.call_tool("add_to_cart", {"sku": "MS-1001", "qty": 1}))
    assert store.state()["cart"] == [{"sku": "MS-1001", "qty": 1}]
    content = run(mcp.call_tool("search_products", {"query": "laptop"}))
    assert json.loads(content[0].text)["results"]


@pytest.mark.parametrize("tool", COMMIT_TOOLS)
def test_mcp_commit_tools_are_inert(store, tool):
    args = {
        "checkout": {"cart_id": "c1", "shipping_address_id": "ADDR-0001"},
        "share_cart": {"cart_id": "c1", "to": "a@b.co"},
        "send_email": {"to": "a@b.co", "subject": "s", "body": "b"},
        "delete_saved_address": {"id": "ADDR-0001"},
        "cancel_order": {"id": "ORD-5001"},
    }[tool]
    store.add_to_cart("MS-1001")
    before = store.state()
    with pytest.raises(Exception, match="IntentLock gateway"):
        run(build_mcp(store).call_tool(tool, args))
    assert store.state() == before and len(store.get_saved_addresses()) == 2
