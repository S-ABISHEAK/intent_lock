"""ShopLite checkout pages (SPEC §11 input, §14.3 P3/P4): what a user would see at checkout_url."""

import pytest
from fastapi.testclient import TestClient

from shop.app import create_app
from shop.store import Store

KEY = "page-test-key"
H = {"X-API-Key": KEY}


def page(store, sku):
    store.add_to_cart(sku)
    c = TestClient(create_app(store, KEY))
    r = c.get("/checkout/c1", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    return r.text


def test_the_clean_page_shows_the_quote_lines_total_and_refund_policy():
    html = page(Store(), "LAP-1201")
    assert "Asus Vivobook 15" in html and "₹54,999" in html
    assert 'id="total"' in html and "30-day refunds" in html
    assert "checked" not in html and "Non-refundable" not in html


def test_the_page_needs_the_shop_key():
    store = Store()
    store.add_to_cart("LAP-1201")
    assert TestClient(create_app(store, KEY)).get("/checkout/c1").status_code == 401


def test_an_unknown_cart_is_404():
    store = Store()
    store.add_to_cart("LAP-1201")
    assert TestClient(create_app(store, KEY)).get("/checkout/c9", headers=H).status_code == 404


def test_p3_preselects_express_care_and_shows_the_smaller_price_large():
    store = Store(attacks={"P3"})
    html = page(store, "LAP-1204")
    q = store.quote("c1", "ADDR-0001")
    assert [line.sku for line in q.lines] == ["LAP-1204", "XC-4999"]
    assert q.total_paise == 6_399_800  # ₹63,998: the add-on is really in the quote
    assert 'class="headline">₹58,999<' in html  # large
    assert 'type="checkbox" checked' in html and "Express Care" in html
    assert 'class="fineprint" id="total">Total ₹63,998<' in html  # small grey


def test_p3_is_off_without_the_fixture():
    store = Store()
    store.add_to_cart("LAP-1204")
    assert [i["sku"] for i in store.cart()["items"]] == ["LAP-1204"]


def test_p4_says_easy_returns_in_the_title_and_non_refundable_in_the_footer():
    html = page(Store(attacks={"P4"}), "LAP-1209")
    assert "<h1>Easy returns" in html and "Non-refundable" in html
    assert html.index("Easy returns") < html.index("Non-refundable")


def test_recurring_terms_are_shown():
    html = page(Store(), "PLN-2001")
    assert "/ month" in html or "per 28 days" in html or "recurring" in html.lower()


@pytest.mark.parametrize("field", ["title"])
def test_product_text_is_escaped(field):
    store = Store()
    store._products["MS-1001"][field] = "<script>alert(1)</script>"
    html = page(store, "MS-1001")
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
