"""D-050 (review gap 2): item condition is part of what the user asked for.

"Buy the Asus Vivobook 15" was fulfilled with "Asus Vivobook 15 (Refurbished)" in a live run on
Nov 2. A refurbished line is never bought silently: in the guarded configs it goes to a card,
whatever the price, and the card shows the condition. It can only escalate (REVIEW), never ALLOW.
"""

import json

import pytest
from fastapi.testclient import TestClient

from intentlock.connector.shop_connector import ShopConnector
from intentlock.models import QuoteLine
from intentlock.policy.pipeline import decide
from intentlock.policy.rule_of_two import SessionFlags
from shop.app import create_app
from shop.store import Store
from tests.factories import grounded_mandate, make_proposal

KEY = "cond-key"
BASE = "http://shop.test"
CATALOG = json.loads(open("shop/data/catalog.json", encoding="utf-8").read())["products"]


def connector(*skus):
    s = Store()
    for sku in skus:
        s.add_to_cart(sku)
    return ShopConnector(BASE, api_key=KEY, client=TestClient(create_app(s, KEY), base_url=BASE))


def test_every_product_has_a_condition_and_it_matches_its_text():
    for p in CATALOG:
        assert p["condition"] in ("new", "refurbished"), p["sku"]
        said = "refurbished" in (p["title"] + p["description"]).lower()
        assert (p["condition"] == "refurbished") == said, p["sku"]


def test_quote_lines_carry_the_condition():
    q = connector("MS-1004", "MS-1001").get_quote("c1", "ADDR-0001")
    assert {ln.sku: ln.condition for ln in q.lines} == {"MS-1004": "refurbished", "MS-1001": "new"}
    assert QuoteLine.model_fields["condition"].default == "new"


def run(config, *skus):
    c = connector(*skus)
    return decide(
        make_proposal("checkout"), grounded_mandate(), config, connector=c, now=1,
        requested_skus=frozenset(), flags=SessionFlags(), extra_steps={6: lambda ctx: []},
    )  # fmt: skip


@pytest.mark.parametrize("config", ["r2_session", "intentlock"])
def test_a_refurbished_item_under_the_auto_approve_limit_still_gets_a_card(config):
    d = run(config, "MS-1004")  # ₹ under the ₹2,000 limit: would otherwise be bought silently
    assert d.outcome == "REVIEW"
    assert [r.code for r in d.reasons if r.code == "REFURBISHED_ITEM"] == ["REFURBISHED_ITEM"]
    assert "MS-1004" in next(r.detail for r in d.reasons if r.code == "REFURBISHED_ITEM")


@pytest.mark.parametrize("config", ["r2_session", "intentlock"])
def test_a_new_item_under_the_limit_is_still_allowed(config):
    assert run(config, "MS-1001").outcome == "ALLOW"


def test_none_config_is_unchanged():
    assert run("none", "MS-1004").outcome == "ALLOW"


def test_a_hard_rule_deny_stays_deny():
    d = run("intentlock", "LAP-1202", "LAP-1206")  # over the ₹60,000 budget
    assert d.outcome == "DENY"
