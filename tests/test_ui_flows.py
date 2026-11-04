"""The console in a real browser (D-060): real shop, approver and gateway, the real Vite dev server
with its guard and proxy, and Chromium. The agent side is scripted calls into the gateway's tool
entry point (the same `call_tool` its MCP endpoint uses).

Skipped when Playwright or ui/node_modules is missing. Screenshots go to the test's tmp dir, or to
$UI_SHOTS when set (for visual review).
"""

import asyncio
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from nacl.signing import SigningKey

from approver.app import create_app as create_approver
from intentlock.api import broker_waiter
from intentlock.api import create_app as create_gateway
from intentlock.approvals.broker import ApprovalBroker
from intentlock.approvals.verify import NonceStore
from intentlock.connector.shop_connector import ExecutionResult, ShopConnector
from intentlock.db import Database
from intentlock.policy.reasons import make_reason
from intentlock.provenance.ledger import Ledger
from intentlock.proxy.mcp_proxy import Gateway
from intentlock.receipts import ReceiptLog
from shop.app import create_app as create_shop
from shop.store import Store
from tests.factories import grounded_mandate
from tests.servers import serve
from tests.test_proxy import FakeShopTools

sync_api = pytest.importorskip("playwright.sync_api")
UI = Path(__file__).resolve().parent.parent / "ui"
VITE = UI / "node_modules" / ".bin" / "vite"
if not VITE.exists():
    pytest.skip("ui/node_modules is not installed", allow_module_level=True)

SHOP_KEY = "ui-flow-shop-key"
OP = "ui-flow-operator-token"
UI_TOKEN = "ui-flow-approver-token"
APPROVER = SigningKey.generate()
CHECKOUT = {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}
XSS = '<img src=x onerror="window.__xss=1">'


class Swap:
    """An ASGI app whose target can be replaced, so ports (and Vite's proxy) stay fixed."""

    def __init__(self):
        self.app = None

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


async def down(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 503, "headers": []})
    await send({"type": "http.response.body", "body": b"gateway down"})


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    shop, gate = Swap(), Swap()
    port = free_port()
    with (
        serve(shop) as shop_url,
        serve(gate) as gateway_url,
        serve(create_approver(APPROVER, ui_token=UI_TOKEN)) as approver_url,
    ):
        env = {
            **os.environ,
            "UI_PORT": str(port),
            "GATEWAY_URL": gateway_url,
            "APPROVER_URL": approver_url,
            "GATEWAY_OPERATOR_TOKEN": OP,
            "APPROVER_UI_TOKEN": UI_TOKEN,
        }
        vite = subprocess.Popen(
            [str(VITE), "--logLevel", "error"], cwd=UI, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.time() + 30
            while True:
                try:
                    if httpx.get(base + "/", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.time() > deadline:
                    raise RuntimeError("vite did not start")
                time.sleep(0.2)
            with sync_api.sync_playwright() as pw:
                browser = pw.chromium.launch()
                yield SimpleNamespace(
                    shop=shop, gate=gate, shop_url=shop_url, base=base, browser=browser,
                    tmp=tmp_path_factory.mktemp("ui"),
                )  # fmt: skip
                browser.close()
        finally:
            vite.terminate()
            vite.wait(10)


@pytest.fixture
def world(stack, tmp_path):
    """A fresh shop, gateway and session for each test, behind the same ports."""
    store = Store()
    stack.shop.app = create_shop(store, SHOP_KEY)
    db = Database(tmp_path / "gw.sqlite")
    broker = ApprovalBroker()
    gw = Gateway(
        config="intentlock",
        connector=ShopConnector(stack.shop_url, api_key=SHOP_KEY),
        receipts=ReceiptLog(db),
        ledger=Ledger(db),
        nonces=NonceStore(db),
        verify_key=APPROVER.verify_key,
        waiter=broker_waiter(broker),
        extra_steps={},
        features=frozenset(),
        shop_tools=FakeShopTools(store),
    )
    app = create_gateway(gw, broker, operator_token=OP)
    stack.gate.app = app
    gw.start_session(grounded_mandate())
    threads = []

    def call(name, **args):  # on its own thread: Playwright's sync API owns this thread's loop
        out = {}

        def run():
            try:
                out["r"] = asyncio.run(gw.call_tool(name, args))
            except BaseException as exc:  # surfaced below, not lost in the thread
                out["e"] = exc

        t = threading.Thread(target=run)
        t.start()
        t.join(30)
        if "e" in out:
            raise out["e"]
        return out["r"]

    def background(name, **args):
        out = {}
        t = threading.Thread(
            target=lambda: out.update(r=asyncio.run(gw.call_tool(name, args))), daemon=True
        )
        t.start()
        threads.append((t, out))
        return out

    world = SimpleNamespace(
        gw=gw, broker=broker, store=store, call=call, background=background, app=app
    )
    yield world
    for req in broker.pending():  # never leave a commit thread waiting
        broker.reject(req.approval_id)
    for t, _ in threads:
        t.join(10)


@pytest.fixture
def page(stack, world):
    ctx = stack.browser.new_context(viewport={"width": 1440, "height": 900})
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.goto(stack.base + "/")
    yield p
    ctx.close()
    assert errors == []


def shot(stack, page, name):
    out = Path(os.environ.get("UI_SHOTS") or stack.tmp)
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / f"{name}.png"), full_page=True)


def expect(locator):
    return sync_api.expect(locator)


def first_story_status(page):
    return page.get_by_test_id("story").first.get_by_test_id("story-status")


def shop_for(world, query, sku):
    """What a real agent does first: search, read the saved addresses, add to the cart."""
    world.call("search_products", query=query)
    world.call("get_saved_addresses")
    world.call("add_to_cart", sku=sku, qty=1)


def review_card(world, page):
    shop_for(world, "laptop", "LAP-1201")
    out = world.background("checkout", **CHECKOUT)
    card = page.get_by_test_id("approval-card")
    expect(card).to_be_visible(timeout=10_000)
    return card, out


# (a) approve -> sign -> signature -> executed, linked by action_id
def test_approve_executes_and_the_story_shows_it(stack, world, page):
    card, out = review_card(world, page)
    expect(card.get_by_test_id("card-total")).to_have_text("₹54,999.00")
    expect(card.get_by_text("Checkout page")).to_be_visible()
    shot(stack, page, "a-card-1440")
    card.get_by_role("button", name="Approve", exact=True).click()
    expect(page.get_by_test_id("approval-card")).to_have_count(0, timeout=10_000)
    status = first_story_status(page)
    expect(status).to_contain_text("REVIEW")
    expect(status).to_contain_text("Order confirmed by the merchant", timeout=10_000)
    assert out["r"]["status"] == "executed" and len(world.store.orders) == 1
    page.get_by_test_id("story").first.get_by_role("button").first.click()
    expect(page.get_by_text("You approved (signed by the approver)")).to_be_visible()
    expect(page.get_by_text("Payment settlement not independently verified").first).to_be_visible()
    shot(stack, page, "a-executed-1440")


# (b) reject -> not approved, nothing executed
def test_reject_executes_nothing(stack, world, page):
    card, out = review_card(world, page)
    card.get_by_role("button", name="Reject").click()
    expect(page.get_by_test_id("approval-card")).to_have_count(0, timeout=10_000)
    expect(first_story_status(page)).to_contain_text("Not executed", timeout=10_000)
    assert out["r"]["status"] == "not_approved" and world.store.orders == []


# (d) DENY offers no approve control; (f) untrusted text renders as literal text
def test_deny_story_has_no_approve_and_untrusted_text_is_inert(stack, world, page):
    world.call("send_email", to="someone@example.com", subject="hi", body=XSS)
    status = first_story_status(page)
    expect(status).to_contain_text("DENY", timeout=10_000)
    expect(status).to_contain_text("Not executed")
    expect(
        page.get_by_text("Blocked by IntentLock: this action cannot be approved")
    ).to_be_visible()
    expect(page.get_by_role("button", name="Approve", exact=True)).to_have_count(0)
    page.get_by_test_id("story").first.get_by_role("button").first.click()
    expect(page.get_by_text("onerror", exact=False).first).to_be_visible()
    assert page.evaluate("window.__xss") is None
    assert page.locator("img[src=x]").count() == 0
    shot(stack, page, "d-deny-1440")


# (e) + (g2) a lost merchant answer: ALLOW and "Outcome unknown", side by side, never green
def test_unknown_outcome_is_shown_separately_from_the_policy(stack, world, page, monkeypatch):
    monkeypatch.setattr(
        world.gw.connector, "execute",
        lambda *a, **k: ExecutionResult(
            executed=False, uncertain=True, reasons=[make_reason("EXECUTION_FAILED", "lost")]
        ),
    )  # fmt: skip
    shop_for(world, "mouse", "MS-1001")
    assert world.call("checkout", **CHECKOUT)["status"] == "unknown"
    status = first_story_status(page)
    expect(status).to_contain_text("ALLOW", timeout=10_000)
    expect(status).to_contain_text("Outcome unknown")
    expect(status).not_to_contain_text("Executed")
    expect(status).not_to_contain_text("Order confirmed")
    shot(stack, page, "e-unknown-1440")


# (i) phone width: no horizontal overflow, Approve reachable
def test_phone_width_card_fits_and_approve_is_reachable(stack, world):
    ctx = stack.browser.new_context(viewport={"width": 375, "height": 800})
    p = ctx.new_page()
    p.goto(stack.base + "/")
    card, _ = review_card(world, p)
    approve = card.get_by_role("button", name="Approve", exact=True)
    approve.scroll_into_view_if_needed()
    expect(approve).to_be_enabled()
    assert p.evaluate("document.documentElement.scrollWidth") <= 375
    shot(stack, p, "i-card-375")
    ctx.close()


# (j) keyboard: Tab reaches Approve and Enter on it approves; Enter elsewhere never does
def test_keyboard_approve_needs_focus_on_the_button(stack, world, page):
    card, out = review_card(world, page)
    page.keyboard.press("Enter")
    time.sleep(0.5)
    assert world.broker.pending(), "Enter on the page must not approve"
    approve = card.get_by_role("button", name="Approve", exact=True)
    for _ in range(80):
        page.keyboard.press("Tab")
        if approve.evaluate("b => b === document.activeElement"):
            break
    else:
        pytest.fail("Approve is not reachable with Tab")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("approval-card")).to_have_count(0, timeout=10_000)


# (g) gateway down: notice, stale data, a pending card can't be acted on
def test_gateway_down_marks_everything_stale_and_disables_actions(stack, world, page):
    card, _ = review_card(world, page)
    expect(card.get_by_role("button", name="Approve", exact=True)).to_be_enabled()
    stack.gate.app = down
    expect(page.get_by_test_id("connection")).to_have_text("Gateway unavailable", timeout=15_000)
    expect(page.get_by_text("Gateway unavailable: live verification is off")).to_be_visible()
    expect(card.get_by_role("button", name="Approve", exact=True)).to_be_disabled()
    expect(card.get_by_role("button", name="Reject")).to_be_disabled()
    expect(card.get_by_text("reconnect to act on this")).to_be_visible()
    expect(page.get_by_text("stale: not live")).to_be_visible()
    shot(stack, page, "g-down-1440")
    stack.gate.app = world.app
    expect(page.get_by_test_id("connection")).to_have_text("Live", timeout=15_000)


# (h) no session -> the empty state
def test_no_session_shows_the_empty_state(stack, world, page):
    world.gw.end_session()
    page.reload()
    page.get_by_role("button", name="Session", exact=True).click()
    expect(
        page.get_by_text("Waiting for the agent").or_(page.get_by_text("No session yet"))
    ).to_be_visible()
    expect(page.get_by_text("Nothing waiting for you")).to_be_visible()


def test_screens_for_review(stack, world):
    """Screenshots of the main screens at every width, for visual review."""
    if not os.environ.get("UI_SHOTS"):
        pytest.skip("set UI_SHOTS=<dir> to capture review screenshots")
    world.call("send_email", to="someone@example.com", subject="hi", body="hello")
    shop_for(world, "laptop", "LAP-1201")
    world.background("checkout", **CHECKOUT)
    for w in (1440, 1280, 1024, 768, 375):
        ctx = stack.browser.new_context(viewport={"width": w, "height": 900})
        p = ctx.new_page()
        p.goto(stack.base + "/")
        expect(p.get_by_test_id("approval-card")).to_be_visible(timeout=10_000)
        shot(stack, p, f"session-{w}")
        for tab in ("Mandate", "Results"):
            p.get_by_role("button", name=tab, exact=True).click()
            time.sleep(0.4)
            shot(stack, p, f"{tab.lower()}-{w}")
        ctx.close()
