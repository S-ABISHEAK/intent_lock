"""Nov 2 checkpoint HIGH (invariant 8): rendering the checkout page must never send the shop key,
or any request at all, anywhere but one fetch to the shop's own checkout URL. Real Playwright."""

import http.server
import threading

import pytest

from intentlock.connector.shop_connector import ConnectorError, ShopConnector
from tests.factories import make_quote

KEY = "SECRET-SHOP-KEY-123"
PNG = b"\x89PNG\r\n\x1a\n"


def server(handler_body):
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("X-API-Key")))
            handler_body(self)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", seen


def html(h, body, status=200, headers=()):
    h.send_response(status)
    h.send_header("Content-Type", "text/html")
    for k, v in headers:
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(body.encode())


@pytest.fixture
def evil():
    srv, url, seen = server(lambda h: html(h, "<p>evil</p>"))
    yield url, seen
    srv.shutdown()


PAGES = {
    "popup": '<script>window.open("{evil}/popup")</script><p>checkout</p>',
    "meta_refresh": '<meta http-equiv="refresh" content="0;url={evil}/meta"><p>checkout</p>',
    "js_navigation": '<script>location="{evil}/nav"</script><p>checkout</p>',
    "subresources": '<img src="{evil}/img"><link rel="stylesheet" href="{evil}/css">'
    '<script src="{evil}/js"></script><iframe src="{evil}/frame"></iframe>'
    '<script>fetch("{evil}/fetch")</script><link rel="prefetch" href="{evil}/pf">'
    '<link rel="dns-prefetch" href="{evil}"><link rel="preconnect" href="{evil}"><p>checkout</p>',
    "same_origin_subresource": '<img src="/r"><p>checkout</p>',
}


@pytest.mark.parametrize("page", sorted(PAGES))
def test_nothing_the_page_does_reaches_another_origin_or_carries_the_key(evil, page):
    pytest.importorskip("playwright.sync_api")
    evil_url, evil_seen = evil

    def shop(h):
        if h.path == "/r":
            return html(h, "", status=302, headers=[("Location", f"{evil_url}/sub")])
        html(h, PAGES[page].format(evil=evil_url))

    srv, shop_url, shop_seen = server(shop)
    try:
        q = make_quote(checkout_url=f"{shop_url}/checkout/c1")
        png = ShopConnector(shop_url, api_key=KEY).render_checkout(q, timeout_s=5)
    finally:
        srv.shutdown()
    assert png.startswith(PNG)
    assert evil_seen == []  # no request at all, with or without the key
    assert shop_seen == [("/checkout/c1", KEY)]  # one fetch by the connector, none by the browser


def test_a_redirect_from_the_checkout_page_is_refused_and_never_followed(evil):
    evil_url, evil_seen = evil
    srv, shop_url, _ = server(
        lambda h: html(h, "", status=302, headers=[("Location", f"{evil_url}/steal")])
    )
    try:
        q = make_quote(checkout_url=f"{shop_url}/checkout/c1")
        with pytest.raises(ConnectorError):
            ShopConnector(shop_url, api_key=KEY).render_checkout(q, timeout_s=5)
    finally:
        srv.shutdown()
    assert evil_seen == []


def test_a_non_html_answer_is_refused(evil):
    def shop(h):
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.end_headers()
        h.wfile.write(b"{}")

    srv, shop_url, _ = server(shop)
    try:
        q = make_quote(checkout_url=f"{shop_url}/checkout/c1")
        with pytest.raises(ConnectorError):
            ShopConnector(shop_url, api_key=KEY).render_checkout(q, timeout_s=5)
    finally:
        srv.shutdown()


def test_redirects_are_refused_even_with_a_client_that_follows_them(evil):
    import httpx

    evil_url, evil_seen = evil
    srv, shop_url, _ = server(
        lambda h: html(h, "", status=302, headers=[("Location", f"{evil_url}/steal")])
    )
    try:
        client = httpx.Client(base_url=shop_url, follow_redirects=True)
        q = make_quote(checkout_url=f"{shop_url}/checkout/c1")
        with pytest.raises(ConnectorError):
            ShopConnector(shop_url, api_key=KEY, client=client).render_checkout(q, timeout_s=5)
    finally:
        srv.shutdown()
    assert evil_seen == []
