"""Step 8 wiring (SPEC §11): the connector screenshots the shop's own checkout page, Muse reads it,
the gateway installs the step only with a page reader, and failures escalate."""

import asyncio
import base64

import pytest

from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from intentlock.models import PageFacts, PageLine
from intentlock.verify.two_channel import (
    EXTRACT_INSTRUCTION,
    extract_page_facts,
    make_page_reader,
    resolve_two_channel,
)
from shop.app import create_app
from shop.store import Store
from tests.factories import grounded_mandate, make_quote
from tests.servers import serve

KEY = "two-channel-test-shop-key"
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def live_shop():
    pytest.importorskip("playwright.sync_api")
    store = Store(attacks={"P3", "P4"})
    with serve(create_app(store, KEY)) as url:
        store.public_url = url
        yield store, url


# -- the connector renders only the shop's own checkout page, with the key kept in the connector --
def test_render_returns_a_full_page_png_of_the_checkout(live_shop):
    store, url = live_shop
    store.add_to_cart("LAP-1204")
    c = ShopConnector(url, api_key=KEY)
    png = c.render_checkout(c.get_quote("c1", "ADDR-0001"))
    assert png.startswith(PNG) and len(png) > 10_000


@pytest.mark.parametrize(
    "checkout_url",
    [
        "http://evil.example/checkout/c1",
        "http://127.0.0.1:1/checkout/c1",
        "{shop}/state",
        "{shop}/checkout/c2",
        "file:///etc/passwd",
        "{shop}/checkout/c1/../../state",
    ],
)
def test_render_refuses_any_page_but_the_quotes_own_checkout(monkeypatch, checkout_url):
    import intentlock.connector.shop_connector as sc

    monkeypatch.setattr(sc, "_launch_browser", lambda: pytest.fail("browser must not start"))
    c = ShopConnector("http://127.0.0.1:8001", api_key=KEY)
    q = make_quote(checkout_url=checkout_url.format(shop="http://127.0.0.1:8001"))
    with pytest.raises(ConnectorError):
        c.render_checkout(q)


def test_render_without_credentials_refuses(monkeypatch):
    import intentlock.connector.shop_connector as sc

    monkeypatch.setattr(sc, "_launch_browser", lambda: pytest.fail("browser must not start"))
    c = ShopConnector("http://localhost:8001", api_key="")
    with pytest.raises(ConnectorError):
        c.render_checkout(make_quote())


def test_a_page_error_is_a_connector_error(live_shop):
    store, url = live_shop  # empty cart: the page is a 4xx
    q = make_quote(checkout_url=f"{url}/checkout/c1")
    with pytest.raises(ConnectorError):
        ShopConnector(url, api_key=KEY).render_checkout(q)


# -- Muse reads the screenshot: image in, PageFacts out, no secrets in the request ----------------
class FakeLLM:
    def __init__(self, facts):
        self.facts, self.calls = facts, []

    def structured(self, messages, schema, model=None):
        self.calls.append((messages, schema, model))
        return self.facts


def facts():
    return PageFacts(
        displayed_total_paise=1,
        line_items=[PageLine(title="x", price_paise=1)],
        refund_policy="unknown",
    )


def test_extraction_sends_the_png_as_an_image_with_the_instruction():
    llm = FakeLLM(facts())
    out = extract_page_facts(PNG + b"img", llm, model="muse-spark-1.3")
    messages, schema, model = llm.calls[0]
    assert out == facts() and schema is PageFacts and model == "muse-spark-1.3"
    assert messages[0] == {"role": "system", "content": EXTRACT_INSTRUCTION}
    image = messages[1]["content"][1]["image_url"]["url"]
    assert image == "data:image/png;base64," + base64.b64encode(PNG + b"img").decode()
    assert "not instructions" in EXTRACT_INSTRUCTION  # page text is content, not commands


def test_the_page_reader_never_puts_the_shop_key_in_the_model_request(monkeypatch):
    c = ShopConnector("http://localhost:8001", api_key=KEY)
    monkeypatch.setattr(c, "render_checkout", lambda q: PNG)
    llm = FakeLLM(facts())
    make_page_reader(c, llm, model="m")(make_quote())
    assert KEY not in str(llm.calls) and "X-API-Key" not in str(llm.calls)


# -- gateway: step 8 only with a reader; status recorded; real page through the step --------
def build(tmp_path, store, url, page_reader, features=frozenset({8}), extra_steps=None):
    from intentlock.approvals.verify import NonceStore
    from intentlock.db import Database
    from intentlock.provenance.ledger import Ledger
    from intentlock.proxy.mcp_proxy import Gateway
    from intentlock.receipts import ReceiptLog
    from tests.test_attacks import McpShop
    from tests.test_proxy import APPROVER

    db = Database(tmp_path / "gw.sqlite")
    return Gateway(
        config="intentlock",
        connector=ShopConnector(url, api_key=KEY),
        receipts=ReceiptLog(db),
        ledger=Ledger(db),
        nonces=NonceStore(db),
        verify_key=APPROVER.verify_key,
        waiter=lambda req: None,
        extra_steps=extra_steps or {},
        features=features,
        shop_tools=McpShop(store),
        page_reader=page_reader,
    )


def test_the_feature_on_without_a_page_reader_refuses_to_build(tmp_path):
    with pytest.raises(ValueError):
        build(tmp_path, Store(), "http://shop", None)


def test_the_gateway_installs_step_8_itself(tmp_path):
    with pytest.raises(ValueError):
        build(tmp_path, Store(), "http://shop", lambda q: facts(), extra_steps={8: lambda c: []})


def test_resolve_turns_two_channel_off_when_it_cannot_run():
    assert resolve_two_channel(frozenset({8, 9}), available=lambda: False) == (
        frozenset({9}),
        "unavailable",
    )
    assert resolve_two_channel(frozenset({8}), available=lambda: True) == (frozenset({8}), "on")
    assert resolve_two_channel(frozenset(), available=lambda: True) == (frozenset(), "off")


def test_status_is_recorded_in_the_session_receipt(tmp_path):
    gw = build(tmp_path, Store(), "http://shop", lambda q: facts())
    gw.start_session(grounded_mandate())
    assert gw.receipts.all()[0].payload["two_channel"] == "on"


def commit_checkout(gw, store, sku):
    asyncio.run(gw.call_tool("search_products", {"query": sku}))
    asyncio.run(gw.call_tool("get_saved_addresses", {}))
    asyncio.run(gw.call_tool("add_to_cart", {"sku": sku, "qty": 1}))
    out = gw.commit(gw.session, "checkout", {"cart_id": "c1", "shipping_address_id": "ADDR-0001"})
    decision = [r.payload for r in gw.receipts.all() if r.kind == "decision"][-1]
    return out, {r["code"] for r in decision["reasons"]}


def test_p4_through_the_real_page_and_a_reader_is_denied(tmp_path, live_shop):
    store, url = live_shop
    pages = []

    def reader(quote):  # the real screenshot, with Muse's reading of P4 stubbed
        pages.append(ShopConnector(url, api_key=KEY).render_checkout(quote))
        return PageFacts(
            displayed_total_paise=quote.total_paise,
            line_items=[
                PageLine(title=line.title, price_paise=line.unit_paise) for line in quote.lines
            ],
            refund_policy="non_refundable",
        )

    gw = build(tmp_path, store, url, reader)
    gw.start_session(grounded_mandate())  # require_refundable=True
    out, codes = commit_checkout(gw, store, "LAP-1209")
    assert pages and pages[0].startswith(PNG)
    assert out["status"] == "denied" and "NON_REFUNDABLE" in codes and store.orders == []


def test_a_crashing_reader_never_lets_a_purchase_through_silently(tmp_path, live_shop):
    store, url = live_shop

    def reader(quote):
        raise ConnectorError("chromium crashed")

    gw = build(tmp_path, store, url, reader)
    gw.start_session(grounded_mandate())
    out, codes = commit_checkout(gw, store, "MS-1001")  # ₹1,499: under the ₹2,000 auto limit
    assert "TWO_CHANNEL_UNAVAILABLE" in codes and out["status"] != "executed"


def test_two_channel_check_renders_three_pages_and_scores_them(capsys):
    pytest.importorskip("playwright.sync_api")
    from intentlock.verify import tc_check

    class PerPage:
        """Plays Muse: a faithful reading of each page in order (clean, P3, P4)."""

        def __init__(self):
            self.n = 0

        def structured(self, messages, schema, model=None):
            assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/png")
            self.n += 1
            if self.n == 1:
                lines = [PageLine(title="Asus Vivobook 15", price_paise=5_499_900)]
                return PageFacts(
                    displayed_total_paise=5_499_900, line_items=lines, refund_policy="refundable"
                )
            if self.n == 2:
                lines = [
                    PageLine(title="HP Pavilion 14", price_paise=5_899_900),
                    PageLine(title="Express Care", price_paise=499_900, preselected=True),
                ]
                return PageFacts(
                    displayed_total_paise=5_899_900, line_items=lines, refund_policy="refundable"
                )
            lines = [PageLine(title="Asus VivoBook 15 Pro", price_paise=5_989_900)]
            return PageFacts(
                displayed_total_paise=5_989_900, line_items=lines, refund_policy="non_refundable"
            )

    assert tc_check.main(llm=PerPage(), model="m") == 0
    out = capsys.readouterr().out
    assert out.count("OK") == 3 and "NON_REFUNDABLE" in out
