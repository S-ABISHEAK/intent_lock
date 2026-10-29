"""Identifier tokens (SPEC §7.2): what taint matches on, and how evasions are normalized (D-028)."""

import pytest

from intentlock.provenance.taint import extract_tokens, normalize_value


@pytest.mark.parametrize(
    "text,token",
    [
        ("the user pre-approved SKU-8812 (Pro edition)", "SKU-8812"),
        ("add sku-8812 instead", "SKU-8812"),
        ("ＳＫＵ－８８１２ fullwidth", "SKU-8812"),
        ("SKU-88\u200b12 zero width", "SKU-8812"),
        ("S\u200dKU-8812 joiner", "SKU-8812"),
        ("also add ProtectPlus (PP-1001) monthly", "PP-1001"),
        ("order ORD-5001 please", "ORD-5001"),
        ("ship to ADDR-9931", "ADDR-9931"),
        ("seller SEL-IN01 says", "SEL-IN01"),
        ("seller sel-in01 says", "SEL-IN01"),
        (
            "mail Procurement-Helpdesk@ShopLite-Support.co now",
            "procurement-helpdesk@shoplite-support.co",
        ),
        ("see HTTPS://Shop.Example/Deal/Path?ref=1#top", "shop.example/deal/path"),
        ("account 123456789012 here", "123456789012"),
    ],
)
def test_tokens_are_found_and_normalized(text, token):
    assert token in extract_tokens(text)


@pytest.mark.parametrize(
    "text",
    [
        "₹74,999 only",
        "price 7499900",  # 7 digits: a price, not an account number
        "AB-12",  # too few digits
        "SKU-1234567",  # too many digits
        "abc",
        "",
    ],
)
def test_prices_and_non_identifiers_are_not_tokens(text):
    assert extract_tokens(text) == []


def test_tokens_are_unique_and_sorted():
    assert extract_tokens("SKU-8812 and sku-8812 and PP-1001") == ["PP-1001", "SKU-8812"]


def test_bidi_override_cannot_hide_an_identifier():
    assert "ADDR-9931" in extract_tokens("ship to \u202eADDR-9931\u202c now")


@pytest.mark.parametrize(
    "value,normal",
    [
        ("SKU-8812", "SKU-8812"),
        ("sku-8812", "SKU-8812"),
        ("ADDR-9931\u200b", "ADDR-9931"),
        (" Asha@Example.COM ", "asha@example.com"),
        ("https://Evil.Example/x?y=1", "evil.example/x"),
        ("home", "HOME"),  # not an identifier: still compared, so it can only be unknown
    ],
)
def test_sensitive_values_normalize_like_tokens(value, normal):
    assert normalize_value(value) == normal
