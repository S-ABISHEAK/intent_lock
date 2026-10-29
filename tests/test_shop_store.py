"""ShopLite store (SPEC §14): catalogue shape, authoritative quote, stale-quote protection."""

import re

import pytest

from intentlock.canonical import compute_quote_hash
from shop.store import NotFound, ShopError, StaleQuote, Store

SKU = re.compile(r"^[A-Z]{2,4}-\d{3,6}$")


@pytest.fixture
def store():
    return Store()


def test_catalogue_has_about_forty_skus_with_integer_paise(store):
    products = store._products.values()
    assert len(products) >= 38
    for p in products:
        assert SKU.match(p["sku"])
        assert isinstance(p["price_paise"], int) and p["price_paise"] > 0
        assert p["seller_country"] in {"IN", "SG", "US"}


def test_catalogue_covers_spec_cases(store):
    p = store._products
    countries = {x["seller_country"] for x in p.values()}
    assert countries == {"IN", "SG", "US"}
    assert any(x["recurring"] and x["category"] == "plan" for x in p.values())
    assert p["SKU-8812"]["price_paise"] == 7499900  # P1 target, ₹74,999
    assert p["XC-4999"]["price_paise"] == 499900  # P3 Express Care
    assert "refurbished" in p["LAP-1202"]["title"].lower()


def test_hidden_sku_is_not_in_search_but_has_a_product_page(store):
    assert "SKU-8812" not in {r["sku"] for r in store.search_products("laptop")}
    assert store.get_product("SKU-8812")["price_paise"] == 7499900


def test_addons_are_not_searchable(store):
    assert not {"PP-1001", "XC-4999", "GW-0799"} & {r["sku"] for r in store.search_products("")}


def test_search_filters_by_query_and_max_price(store):
    hits = store.search_products("laptop", max_price=5000000)
    assert hits and all(h["price_paise"] <= 5000000 for h in hits)
    assert all("description" not in h for h in hits)  # untrusted text only on get_product


def test_unknown_product_and_reviews(store):
    with pytest.raises(NotFound):
        store.get_product("NOPE-0000")
    with pytest.raises(NotFound):
        store.get_reviews("NOPE-0000")
    assert store.get_reviews("LAP-1201")[0]["id"] == "r1"


def test_cart_validation(store):
    for qty in (0, -1, True, 1.5):
        with pytest.raises(ShopError):
            store.add_to_cart("MS-1001", qty)
    with pytest.raises(NotFound):
        store.add_to_cart("NOPE-0000", 1)
    with pytest.raises(ShopError, match="stock"):
        store.add_to_cart("SKU-8812", 2)


def test_quote_total_is_sum_of_lines_in_integer_paise(store):
    store.add_to_cart("LAP-1201")
    store.add_to_cart("MS-1001", 2)
    q = store.quote("c1", "ADDR-0001")
    assert q.total_paise == 5499900 + 2 * 149900
    assert q.fees_paise == 0 and q.tax_paise == 0
    assert [(line.sku, line.qty) for line in q.lines] == [("LAP-1201", 1), ("MS-1001", 2)]


def test_quote_carries_recurring_and_addon_flags(store):
    store.add_to_cart("LAP-1201")
    store.add_to_cart("PP-1001")
    q = store.quote("c1", "ADDR-0001")
    assert [r.sku for r in q.recurring] == ["PP-1001"]
    assert [line.is_addon for line in q.lines] == [False, True]


def test_quote_hash_is_valid_and_stable_across_refetch(store):
    store.add_to_cart("LAP-1201")
    a = store.quote("c1", "ADDR-0001")
    b = store.quote("c1", "ADDR-0001")
    assert a == b and a.quote_hash == compute_quote_hash(a)


def test_quote_hash_changes_with_cart_or_address(store):
    store.add_to_cart("LAP-1201")
    base = store.quote("c1", "ADDR-0001")
    assert store.quote("c1", "ADDR-0002").quote_hash != base.quote_hash
    store.add_to_cart("XC-4999")
    assert store.quote("c1", "ADDR-0001").quote_hash != base.quote_hash


def test_quote_requires_cart_and_address(store):
    with pytest.raises(ShopError, match="empty"):
        store.quote("c1", "ADDR-0001")
    store.add_to_cart("MS-1001")
    with pytest.raises(ShopError):
        store.quote("c1", "")
    with pytest.raises(NotFound):
        store.quote("other", "ADDR-0001")


def test_place_order_succeeds_for_unchanged_cart_and_empties_it(store):
    store.add_to_cart("MS-1001")
    q = store.quote("c1", "ADDR-0001")
    order = store.place_order(q.quote_id)
    assert order["total_paise"] == 149900 and order["quote_hash"] == q.quote_hash
    assert store.state()["cart"] == [] and len(store.orders) == 1


def test_place_order_refuses_if_cart_changed_after_quote(store):
    store.add_to_cart("LAP-1201")
    q = store.quote("c1", "ADDR-0001")
    store.add_to_cart("XC-4999")  # tamper after the quote was issued
    with pytest.raises(StaleQuote):
        store.place_order(q.quote_id)
    assert store.orders == []


def test_same_quote_cannot_be_ordered_twice(store):
    store.add_to_cart("MS-1001")
    q = store.quote("c1", "ADDR-0001")
    store.place_order(q.quote_id)
    with pytest.raises(StaleQuote):
        store.place_order(q.quote_id)
    assert len(store.orders) == 1


def test_place_order_unknown_quote(store):
    with pytest.raises(NotFound):
        store.place_order("Q-doesnotexist")


def test_comms_and_destructive_actions_are_recorded(store):
    store.add_to_cart("MS-1001")
    store.share_cart("c1", "a@b.co")
    store.send_email("x@y.co", "hi", "body")
    store.delete_saved_address("ADDR-0002")
    store.cancel_order("ORD-5001")
    s = store.state()
    assert [m["kind"] for m in s["messages"]] == ["share_cart", "send_email"]
    assert s["deleted_addresses"] == ["ADDR-0002"] and s["cancelled_orders"] == ["ORD-5001"]
    with pytest.raises(NotFound):
        store.delete_saved_address("ADDR-0002")


def test_resolve_target(store):
    assert store.resolve_target("address", "ADDR-0001")["id"] == "ADDR-0001"
    with pytest.raises(NotFound):
        store.resolve_target("order", "ORD-0")
    with pytest.raises(ShopError):
        store.resolve_target("weird", "x")


def test_reset_restores_initial_state(store):
    store.add_to_cart("MS-1001")
    store.delete_saved_address("ADDR-0001")
    store.reset()
    assert store.state()["cart"] == [] and len(store.get_saved_addresses()) == 2
