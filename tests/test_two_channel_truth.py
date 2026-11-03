"""D-053 (Checkout Truth Engine, channel 2): the checkout page shows the delivery address and each
line's quantity; Muse reads them; Python compares them with the quote. Escalation only."""

from fastapi.testclient import TestClient

from eval.run_g import facts_from_page
from intentlock.connector.shop_connector import ShopConnector
from intentlock.models import PageFacts, PageLine
from intentlock.policy.reasons import REGISTRY
from intentlock.verify.two_channel import address_matches, compare, make_two_channel_step
from shop.app import create_app
from shop.pages import render
from shop.store import Store
from tests.factories import grounded_mandate, make_line, make_quote

HOME = "12 Lake View Road, Chennai 600001, IN"


def facts(total, lines, address=""):
    return PageFacts(
        displayed_total_paise=total, line_items=lines, refund_policy="refundable",
        delivery_address=address,
    )  # fmt: skip


def quote(qty=1):
    line = make_line(
        sku="MS-1001", title="Logitech M221 Wireless Mouse", qty=qty, unit_paise=149_900
    )
    return make_quote(lines=[line])


def codes(rs):
    return [r.code for r in rs]


def test_new_codes_are_review_from_the_two_channel_step():
    for code in ("QUANTITY_DISPLAY_MISMATCH", "ADDRESS_DISPLAY_MISMATCH"):
        assert REGISTRY[code] == ("REVIEW", "two_channel")


def test_quantity_on_the_page_must_match_the_quote():
    q = quote(qty=3)
    shown = [PageLine(title="Logitech M221 Wireless Mouse", price_paise=449_700, qty=1)]
    assert "QUANTITY_DISPLAY_MISMATCH" in codes(compare(facts(449_700, shown), q, None))
    same = [PageLine(title="Logitech M221 Wireless Mouse", price_paise=449_700, qty=3)]
    assert codes(compare(facts(449_700, same), q, None)) == []


def test_a_page_line_without_a_quantity_reads_as_one():
    assert PageLine(title="x", price_paise=1).qty == 1


def test_address_on_the_page_must_be_the_quotes_address():
    q, line = quote(), [PageLine(title="Logitech M221 Wireless Mouse", price_paise=149_900)]
    ok = facts(149_900, line, "Deliver to: Home, 12 Lake View Rd., Chennai 600001")
    assert codes(compare(ok, q, None, address_line=HOME)) == []
    other = facts(149_900, line, "Deliver to: 44 MG Road, Bengaluru 560001")
    assert codes(compare(other, q, None, address_line=HOME)) == ["ADDRESS_DISPLAY_MISMATCH"]
    unread = facts(149_900, line, "")  # the page shows none, or Muse didn't read it
    assert codes(compare(unread, q, None, address_line=HOME)) == []
    assert codes(compare(other, q, None)) == []  # no saved address to compare with


def test_address_matching_needs_the_pin_and_most_words():
    assert address_matches("Home - 12 lake view road chennai 600001", HOME)
    assert not address_matches("12 Lake View Road, Chennai 600002", HOME)  # wrong PIN
    assert not address_matches("Flat 9, Sea View, Chennai 600001", HOME)  # same PIN, other street


def test_the_step_looks_up_the_saved_address_and_fails_to_review():
    q = quote()
    page = facts(149_900, [PageLine(title="Logitech M221 Wireless Mouse", price_paise=149_900)],
                 "44 MG Road, Bengaluru 560001")  # fmt: skip

    class Ctx:
        proposal = type("P", (), {"tool": "checkout"})()
        quote, mandate = q, None

    step = make_two_channel_step(lambda _q: page, {}, address_of=lambda _id: HOME)
    assert codes(step(Ctx())) == ["ADDRESS_DISPLAY_MISMATCH"]

    def broken(_id):
        raise RuntimeError("shop down")

    step = make_two_channel_step(lambda _q: page, {}, address_of=broken)
    assert codes(step(Ctx())) == ["TWO_CHANNEL_UNAVAILABLE"]


def shop_with_quote():
    s = Store()
    s.add_to_cart("MS-1001", 2)
    client = TestClient(create_app(s, "k"), base_url="http://shop.test")
    c = ShopConnector("http://shop.test", api_key="k", client=client)
    c.get_quote("c1", "ADDR-0001")
    return s


def test_checkout_page_shows_the_quotes_delivery_address_and_quantities():
    s = shop_with_quote()
    page = s.checkout_page("c1")
    assert page["delivery"]["line"] == HOME
    html = render(page)
    assert "Deliver to" in html and "12 Lake View Road" in html and "&times; 2" in html


def test_page_oracle_reads_quantity_and_address():
    f = facts_from_page(shop_with_quote().checkout_page("c1"))
    assert f.line_items[0].qty == 2 and "12 Lake View Road" in f.delivery_address


def test_layer_g_page_oracle_without_a_quote_has_no_address():
    s = Store()
    s.add_to_cart("MS-1001")
    assert s.checkout_page("c1")["delivery"] is None
    assert facts_from_page(s.checkout_page("c1")).delivery_address == ""


def test_demo_mandate_unaffected_by_matching_page():
    q = quote()
    ok = facts(149_900, [PageLine(title="Logitech M221 Wireless Mouse", price_paise=149_900)], HOME)
    assert compare(ok, q, grounded_mandate(), address_line=HOME) == []


def test_page_text_without_the_saved_address_is_flagged_even_if_the_reading_is_empty():
    """Review LOW: an empty reading (e.g. the page told the reader to omit it) must not skip the
    deterministic check against the page's own text."""
    from intentlock.verify.two_channel import PageEvidence

    q = quote()
    blank = facts(
        149_900, [PageLine(title="Logitech M221 Wireless Mouse", price_paise=149_900)], ""
    )

    class Ctx:
        proposal = type("P", (), {"tool": "checkout"})()
        quote, mandate = q, None

    dom = "Deliver to: Office, 44 MG Road, Bengaluru 560001\nTotal ₹1,499"
    step = make_two_channel_step(lambda _q: PageEvidence(blank, dom), {}, address_of=lambda _: HOME)
    assert "ADDRESS_DISPLAY_MISMATCH" in codes(step(Ctx()))
    ok_dom = f"Deliver to: Home, {HOME}\nTotal ₹1,499"
    step = make_two_channel_step(
        lambda _q: PageEvidence(blank, ok_dom), {}, address_of=lambda _: HOME
    )
    assert "ADDRESS_DISPLAY_MISMATCH" not in codes(step(Ctx()))


def test_the_latest_quote_decides_the_delivery_address():
    """Review LOW: re-quoting an earlier address moves it to the end."""
    s = Store()
    s.add_to_cart("MS-1001")
    client = TestClient(create_app(s, "k"), base_url="http://shop.test")
    c = ShopConnector("http://shop.test", api_key="k", client=client)
    c.get_quote("c1", "ADDR-0001")
    c.get_quote("c1", "ADDR-0002")
    c.get_quote("c1", "ADDR-0001")
    assert s.checkout_page("c1")["delivery"]["line"] == HOME
