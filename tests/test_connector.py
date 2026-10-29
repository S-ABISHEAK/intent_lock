"""Connector get_quote / resolve_target (SPEC §6 step 3, §14.2; CLAUDE.md invariants 3, 8, 2).

The connector is the only place SHOP_API_KEY is read. Every failure must surface as
ConnectorError, which the pipeline maps to DENY QUOTE_UNAVAILABLE (fail closed).
"""

import inspect
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from intentlock.canonical import compute_quote_hash
from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from intentlock.models import Quote
from shop.app import create_app
from shop.store import Store

KEY = "test-shop-key-DO-NOT-LEAK"
BASE = "http://shop.test"


@pytest.fixture
def store():
    s = Store()
    s.add_to_cart("LAP-1201")
    return s


@pytest.fixture
def shop(store):
    return TestClient(create_app(store, KEY), base_url=BASE)


def connector(client, key=KEY):
    return ShopConnector(BASE, api_key=key, client=client)


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE)


def quote_json(store, **overrides):
    q = store.quote("c1", "ADDR-0001").model_dump(mode="json")
    q.update(overrides)
    return q


def reseal(q: dict) -> dict:
    """What a malicious shop would do: edit fields and recompute a valid-looking hash."""
    q["quote_hash"] = compute_quote_hash(Quote.model_validate({**q, "quote_hash": ""}))
    return q


# -- happy path ---------------------------------------------------------------------------------


def test_get_quote_returns_validated_authoritative_quote(shop):
    q = connector(shop).get_quote("c1", "ADDR-0001")
    assert isinstance(q, Quote)
    assert q.total_paise == 5499900 and q.quote_hash == compute_quote_hash(q)


def test_get_quote_sends_the_api_key_header(store):
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=quote_json(store))

    connector(mock(handler)).get_quote("c1", "ADDR-0001")
    assert seen["key"] == KEY
    assert seen["body"] == {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}  # ids only


def test_get_quote_accepts_identifiers_only_never_money():
    params = list(inspect.signature(ShopConnector.get_quote).parameters)
    assert params == ["self", "cart_id", "shipping_address_id"]


# -- fail closed --------------------------------------------------------------------------------


def test_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("SHOP_API_KEY", raising=False)
    calls = []
    c = ShopConnector(BASE, client=mock(lambda r: calls.append(r) or httpx.Response(200)))
    with pytest.raises(ConnectorError, match="credentials"):
        c.get_quote("c1", "ADDR-0001")
    assert calls == []


def test_empty_key_counts_as_missing():
    with pytest.raises(ConnectorError):
        connector(mock(lambda r: httpx.Response(200)), key="").get_quote("c1", "ADDR-0001")


def test_key_defaults_to_environment(monkeypatch, shop):
    monkeypatch.setenv("SHOP_API_KEY", KEY)
    assert ShopConnector(BASE, client=shop).get_quote("c1", "ADDR-0001").cart_id == "c1"


def test_wrong_key_is_rejected_by_shop(shop):
    with pytest.raises(ConnectorError, match="401"):
        connector(shop, key="wrong").get_quote("c1", "ADDR-0001")


def test_shop_down_and_timeout_fail_closed():
    def boom(request):
        raise httpx.ConnectError("refused")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    for handler in (boom, slow):
        with pytest.raises(ConnectorError):
            connector(mock(handler)).get_quote("c1", "ADDR-0001")


def test_empty_cart_is_an_error(store):
    store.remove_from_cart("LAP-1201")
    with pytest.raises(ConnectorError, match="400"):
        connector(TestClient(create_app(store, KEY), base_url=BASE)).get_quote("c1", "ADDR-0001")


@pytest.mark.parametrize("body", ["not json", "[]", "{}", '{"quote_id": "x"}', "null"])
def test_garbage_responses_fail_closed(body):
    with pytest.raises(ConnectorError):
        connector(mock(lambda r: httpx.Response(200, text=body))).get_quote("c1", "ADDR-0001")


def test_float_money_in_response_is_rejected(store):
    q = quote_json(store, total_paise=5499900.5)
    with pytest.raises(ConnectorError):
        connector(mock(lambda r: httpx.Response(200, json=q))).get_quote("c1", "ADDR-0001")


# -- the quote must be internally honest --------------------------------------------------------


def test_tampered_hash_is_rejected(store):
    q = quote_json(store, quote_hash="0" * 64)
    with pytest.raises(ConnectorError, match="hash"):
        connector(mock(lambda r: httpx.Response(200, json=q))).get_quote("c1", "ADDR-0001")


def test_edited_total_with_stale_hash_is_rejected(store):
    q = quote_json(store, total_paise=1)
    with pytest.raises(ConnectorError, match="hash"):
        connector(mock(lambda r: httpx.Response(200, json=q))).get_quote("c1", "ADDR-0001")


def test_total_that_is_not_the_sum_of_its_parts_is_rejected_even_if_resealed(store):
    q = reseal(quote_json(store, total_paise=1))
    with pytest.raises(ConnectorError, match="total"):
        connector(mock(lambda r: httpx.Response(200, json=q))).get_quote("c1", "ADDR-0001")


def test_quote_for_a_different_cart_or_address_is_rejected(store):
    other_cart = reseal(quote_json(store, cart_id="c2"))
    with pytest.raises(ConnectorError, match="cart"):
        connector(mock(lambda r: httpx.Response(200, json=other_cart))).get_quote("c1", "ADDR-0001")
    other_addr = reseal(quote_json(store, shipping_address_id="ADDR-9931"))
    with pytest.raises(ConnectorError, match="address"):
        connector(mock(lambda r: httpx.Response(200, json=other_addr))).get_quote("c1", "ADDR-0001")


# -- credentials never leak ---------------------------------------------------------------------


def test_key_never_appears_in_errors_or_repr_even_if_shop_echoes_it(store):
    def echo(request):
        return httpx.Response(500, text=f"boom {request.headers['x-api-key']}")

    c = connector(mock(echo))
    with pytest.raises(ConnectorError) as exc:
        c.get_quote("c1", "ADDR-0001")
    assert KEY not in str(exc.value) and KEY not in repr(exc.value)
    assert KEY not in repr(c) and KEY not in str(c.__dict__.get("base_url", ""))


# -- resolve_target -----------------------------------------------------------------------------


def test_resolve_target_ok_and_not_found(shop):
    c = connector(shop)
    assert c.resolve_target("address", "ADDR-0001") == {"kind": "address", "id": "ADDR-0001"}
    with pytest.raises(ConnectorError, match="404"):
        c.resolve_target("address", "ADDR-9999")


def test_resolve_target_rejects_unknown_kind_without_calling_shop():
    calls = []
    c = connector(mock(lambda r: calls.append(r) or httpx.Response(200, json={})))
    with pytest.raises(ConnectorError):
        c.resolve_target("../state", "x")
    assert calls == []


def test_quote_with_no_lines_or_zero_quantity_is_rejected(store):
    empty = reseal(quote_json(store, lines=[], total_paise=0))
    with pytest.raises(ConnectorError, match="no items"):
        connector(mock(lambda r: httpx.Response(200, json=empty))).get_quote("c1", "ADDR-0001")
    q = quote_json(store)
    q["lines"][0]["qty"] = 0
    zero = reseal({**q, "total_paise": 0})
    with pytest.raises(ConnectorError, match="no items"):
        connector(mock(lambda r: httpx.Response(200, json=zero))).get_quote("c1", "ADDR-0001")
