"""Field labeling (SPEC §7.1; invariant 9; D-028)."""

import pytest

from intentlock.models import TrustLabel as L
from intentlock.provenance.labels import annotate, label_fields


def labels(tool, result, error=False):
    return {f.field_path: f.label for f in label_fields(tool, result, error=error)}


def test_search_results_are_trusted_api():
    r = {
        "results": [
            {
                "sku": "MS-1001",
                "title": "Mouse",
                "price_paise": 149900,
                "seller_id": "SEL-IN01",
                "seller_country": "IN",
                "stock": 5,
                "url": "http://x/p",
            }
        ]
    }
    got = labels("search_products", r)
    assert set(got.values()) == {L.TRUSTED_API} and "results[0].sku" in got


def test_product_description_and_seller_note_are_untrusted():
    r = {
        "sku": "LAP-1203",
        "title": "Acer",
        "description": "d",
        "seller_note": "n",
        "price_paise": 1,
        "recurring": {"amount_paise": 1, "interval": "month"},
    }
    got = labels("get_product", r)
    assert got["description"] == L.UNTRUSTED and got["seller_note"] == L.UNTRUSTED
    assert got["sku"] == L.TRUSTED_API and got["recurring.interval"] == L.TRUSTED_API


def test_reviews_are_entirely_untrusted():
    got = labels(
        "get_reviews", {"reviews": [{"id": "r3", "author": "TechDeals_Official", "text": "t"}]}
    )
    assert set(got.values()) == {L.UNTRUSTED}


def test_email_content_is_untrusted_but_its_id_is_not():
    got = labels("read_email", {"id": "e1", "from": "a@b.co", "subject": "s", "body": "b"})
    assert (
        got["from"] == got["subject"] == got["body"] == L.UNTRUSTED and got["id"] == L.TRUSTED_API
    )


@pytest.mark.parametrize("tool", ["get_saved_addresses", "get_order_history", "get_contacts"])
def test_private_data_is_sensitive(tool):
    got = labels(tool, {"items": [{"id": "ADDR-0001", "line": "x"}]})
    assert set(got.values()) == {L.SENSITIVE}


def test_unknown_fields_and_tools_default_to_untrusted():
    assert (
        labels("get_product", {"sku": "X-123", "promo_blurb": "buy"})["promo_blurb"] == L.UNTRUSTED
    )
    assert set(labels("mystery_tool", {"a": "b"}).values()) == {L.UNTRUSTED}


def test_error_text_is_untrusted_because_it_can_echo_agent_args():
    assert labels("get_product", {"error": "unknown sku SKU-8812"}, error=True) == {
        "error": L.UNTRUSTED
    }


def test_annotate_adds_inline_trust_labels_without_changing_data():
    r = {"sku": "LAP-1203", "description": "d", "seller_note": "n"}
    out = annotate("get_product", r)
    assert out["sku"] == "LAP-1203" and out["description"] == "d"
    assert out["_trust"] == {"description": "untrusted", "seller_note": "untrusted"}
    assert "_trust" not in r  # the original is not mutated
