"""D-058 (second review 1.2, the honest core): requirements IntentLock cannot enforce are named in
the readback instead of being silently dropped. Information only: the mandate never changes."""

from intentlock.mandate.compiler import readback
from intentlock.mandate.grounding import ground
from intentlock.mandate.unenforced import unenforced_phrases
from tests.factories import DEMO_TEXT, grounded_mandate


def test_the_demo_request_has_nothing_unenforced():
    assert unenforced_phrases(DEMO_TEXT) == []


def test_specs_processors_screens_colours_and_brands_are_found():
    text = (
        "Buy a Lenovo laptop with 16 GB RAM and a 512GB SSD, an i5 or Ryzen 5, 15.6 inch screen, "
        "in black, under ₹60,000."
    )
    got = unenforced_phrases(text)
    for want in ("16 GB RAM", "512GB SSD", "i5", "Ryzen 5", "15.6 inch", "black", "Lenovo"):
        assert any(want.lower() == g.lower() for g in got), (want, got)


def test_enforced_things_are_not_flagged():
    for text in ("under ₹60,000", "Indian seller", "no subscriptions", "must be refundable",
                 "just one", "share my cart with asha@example.com"):  # fmt: skip
        assert unenforced_phrases(text) == [], text


def test_each_phrase_is_listed_once_in_order():
    assert unenforced_phrases("16 GB RAM, 16 GB RAM, Lenovo") == ["16 GB RAM", "Lenovo"]


def test_readback_names_them_and_the_mandate_is_unchanged():
    text = "Laptop with 16 GB RAM, Lenovo, under ₹60,000, Indian seller, refundable."
    m = grounded_mandate(user_text=text, grants=[])
    report = ground(m)
    lines = [x for x in readback(report) if x["kind"] == "not_enforced"]
    assert [x["span"] for x in lines] == ["16 GB RAM", "Lenovo"]
    assert all("Not enforced" in x["text"] for x in lines)
    assert ground(m).mandate == report.mandate  # information only


def test_cli_shows_a_mark_for_them():
    from intentlock.cli import format_readback

    line = {"kind": "not_enforced", "text": "Not enforced: '16 GB RAM'", "span": "16 GB RAM"}
    out = format_readback([{**line, "field": None}])
    assert out.startswith("~ ")
