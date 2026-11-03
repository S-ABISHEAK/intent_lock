"""Layer V cases are valid before any model sees them (D-059).

For every case, a faithful reading of its page (the page oracle, no model) triggers exactly the
expected screenshot code (attacks) or none (clean twins); without step 8 every purchase would go
through with no card (so the screenshot check is the deciding control); with step 8 driven by the
faithful reading, attacks get a card and twins don't.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eval.layer_v import build
from eval.run_g import facts_from_page
from intentlock.connector.shop_connector import ShopConnector
from intentlock.models import ActionProposal, Mandate
from intentlock.policy.pipeline import decide
from intentlock.policy.rule_of_two import SessionFlags
from intentlock.verify.two_channel import compare, make_two_channel_step
from shop.app import create_app
from shop.pages import render
from shop.store import Store

ROOT = Path(__file__).resolve().parent.parent
BASE = Mandate.model_validate_json((ROOT / "eval/tasks/demo_mandate.json").read_text())
HOME = "12 Lake View Road, Chennai 600001, IN"


def setup(c):
    store = Store()
    for item in c["cart"]:
        store.add_to_cart(item["sku"], item["qty"])
    store.page_variant = c["variant"]
    client = TestClient(create_app(store, "k"), base_url="http://shop.test")
    conn = ShopConnector("http://shop.test", api_key="k", client=client)
    quote = conn.get_quote("c1", "ADDR-0001")
    mandate = BASE.model_copy(update=c["mandate"])
    return store, conn, quote, mandate


def run(c, conn, mandate, extra):
    p = ActionProposal(
        action_id="a", session_id="s", tool="checkout",
        args={"cart_id": "c1", "shipping_address_id": "ADDR-0001"}, ts=1,
    )  # fmt: skip
    return decide(
        p, mandate, "intentlock", connector=conn, now=1,
        requested_skus=frozenset(c["requested_skus"]), flags=SessionFlags(),
        extra_steps={6: lambda ctx: [], **extra}, features=frozenset(extra),
    )  # fmt: skip


@pytest.mark.parametrize("c", build.CASES_SPEC, ids=lambda c: c["id"])
def test_case_is_valid(c):
    store, conn, quote, mandate = setup(c)
    page = store.checkout_page("c1")
    codes = [r.code for r in compare(facts_from_page(page), quote, mandate, address_line=HOME)]
    if c["attack"]:
        assert c["expect_code"] in codes, codes
        assert render(page) != render({**page, "variant": {}})  # the variant is visible
    else:
        assert codes == []

    silent = run(c, conn, mandate, {})
    assert silent.outcome == "ALLOW", [r.code for r in silent.reasons]

    reader = lambda q: facts_from_page(store.checkout_page("c1"))  # noqa: E731
    step = make_two_channel_step(reader, {}, address_of=lambda _id: HOME)
    checked = run(c, conn, mandate, {8: step})
    if c["attack"]:  # a card, or DENY for a hard-rule fact such as non-refundable (SPEC §6 step 8)
        assert checked.outcome in ("REVIEW", "DENY")
    else:
        assert checked.outcome == "ALLOW"


def test_counts_and_unique_ids():
    ids = [c["id"] for c in build.CASES_SPEC]
    assert len(ids) == len(set(ids)) == 54
    assert sum(c["attack"] for c in build.CASES_SPEC) == 36


def test_locked_file_matches_the_builder():
    if not build.LOCK.exists():
        pytest.skip("not locked yet")
    assert [json.loads(x) for x in build.text().splitlines()] == build.load()


def test_a_faithful_reading_never_trips_the_dom_cross_check_on_real_renders():
    """Every page through the real offline render: the DOM cross-check (D-057) must stay quiet for
    a faithful reading, or the clean twins would false-alarm once Muse reads them."""
    pytest.importorskip("playwright.sync_api")
    from intentlock.verify.dom_check import dom_disagreements
    from tests.servers import serve

    bad = []
    for c in build.CASES_SPEC:
        store, _, _, _ = setup(c)
        with serve(create_app(store, "k")) as url:
            store.public_url = url
            conn = ShopConnector(url, api_key="k")
            quote = conn.get_quote("c1", "ADDR-0001")
            _, dom = conn.render_checkout_evidence(quote, timeout_s=10)
        reasons = dom_disagreements(facts_from_page(store.checkout_page("c1")), dom)
        if reasons:
            bad.append((c["id"], reasons[0].detail))
    assert bad == []
