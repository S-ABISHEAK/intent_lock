"""D-048 (review gap 1): a product picked through a search worded from untrusted content is tainted.

Identifier taint misses this: a review says "get the Pro model", the agent searches "vivobook pro",
and the shop returns LAP-1209, a new identifier with clean provenance. The rule is conservative but
narrow, to keep friction low: a query word counts as influenced when it is not in the user's words
and does appear in untrusted text this session; a result is marked SELECTED only if its own title
contains such a word. Its SKU is then DERIVED_UNTRUSTED, with the chain back to the review.
"""

import pytest

from intentlock.db import Database
from intentlock.models import TrustLabel as L
from intentlock.provenance.ledger import Ledger
from intentlock.provenance.taint import content_words, label_call, user_records

S = "sess-influence"


@pytest.fixture
def ledger(tmp_path):
    return Ledger(Database(tmp_path / "run.sqlite"))


def observe(ledger, tool, args, result):
    seq = ledger.next_call_seq(S)
    ledger.add(label_call(tool, args, result, ledger.index(S), session_id=S, call_seq=seq, ts=1))


def item(sku, title):
    return {
        "sku": sku, "title": title, "price_paise": 5989900, "seller_id": "SEL-IN02",
        "seller_country": "IN", "stock": 7, "url": f"http://localhost:8001/p/{sku.lower()}",
    }  # fmt: skip


REVIEW = {"reviews": [{"id": "r3", "author": "x", "text": "Skip this one, get the Pro model."}]}
RESULTS = {
    "results": [item("LAP-1209", "Asus VivoBook 15 Pro"), item("LAP-1201", "Asus Vivobook 15")]
}


def session(ledger, user_text):
    ledger.add(user_records(S, user_text, ts=0))


def test_review_worded_search_taints_the_product_it_selected(ledger):
    session(ledger, "Buy me a good Asus Vivobook laptop under ₹60,000.")
    observe(ledger, "get_reviews", {"sku": "LAP-1201"}, REVIEW)
    observe(ledger, "search_products", {"query": "vivobook pro"}, RESULTS)
    idx = ledger.index(S)
    v = idx.classify("LAP-1209")
    assert v.status == "tainted"
    chain = idx.explain("LAP-1209")
    assert "get_reviews#" in chain and "search_products#" in chain
    assert idx.classify("LAP-1201").status == "clean"  # no influenced word in its title


def test_words_from_the_user_are_never_influence(ledger):
    session(ledger, "Buy the Asus VivoBook 15 Pro for me.")
    observe(ledger, "get_reviews", {"sku": "LAP-1201"}, REVIEW)  # the review also says "Pro"
    observe(ledger, "search_products", {"query": "vivobook pro"}, RESULTS)
    assert ledger.index(S).classify("LAP-1209").status == "clean"


def test_the_agents_own_words_are_not_influence(ledger):
    session(ledger, "Buy me a laptop.")
    observe(ledger, "search_products", {"query": "vivobook pro"}, RESULTS)  # nothing untrusted read
    assert ledger.index(S).classify("LAP-1209").status == "clean"


def test_a_review_word_that_is_in_no_title_marks_nothing(ledger):
    session(ledger, "Buy me a wireless mouse.")
    observe(
        ledger,
        "get_reviews",
        {"sku": "MS-1001"},
        {"reviews": [{"id": "r1", "author": "a", "text": "Battery lasts forever."}]},
    )
    mice = {"results": [item("MS-1001", "Logitech M221 Wireless Mouse")]}
    observe(ledger, "search_products", {"query": "wireless mouse long battery"}, mice)
    assert ledger.index(S).classify("MS-1001").status == "clean"


def test_plurals_and_case_match_the_users_words(ledger):
    session(ledger, "I want PRO laptops only.")
    observe(ledger, "get_reviews", {"sku": "LAP-1201"}, REVIEW)
    observe(ledger, "search_products", {"query": "Pro Laptop"}, RESULTS)
    assert ledger.index(S).classify("LAP-1209").status == "clean"


def test_content_words_skip_stopwords_short_words_and_numbers():
    assert content_words("Get the Pro 15 model, it's best!") == {"pro", "model"}
    assert content_words("Laptops") == {"laptop"}


def test_derived_record_is_labelled_derived_untrusted_with_its_source(ledger):
    session(ledger, "Buy me a good Asus Vivobook laptop.")
    observe(ledger, "get_reviews", {"sku": "LAP-1201"}, REVIEW)
    observe(ledger, "search_products", {"query": "vivobook pro"}, RESULTS)
    idx = ledger.index(S)
    derived = [r for r in idx.by_token["LAP-1209"] if r.label == L.DERIVED_UNTRUSTED]
    assert derived and all(r.derived_from for r in derived)
    review_ids = {r.record_id for r in idx.by_id.values() if r.tool == "get_reviews"}
    assert set(derived[0].derived_from) & review_ids


def test_checkout_of_the_selected_product_is_tainted_arg(ledger):
    from intentlock.models import ActionProposal
    from intentlock.policy.context import DecisionContext
    from intentlock.provenance.taint import make_taint_step
    from tests.factories import make_line, make_quote

    session(ledger, "Buy me a good Asus Vivobook laptop under ₹60,000.")
    observe(ledger, "get_reviews", {"sku": "LAP-1201"}, REVIEW)
    observe(ledger, "search_products", {"query": "vivobook pro"}, RESULTS)
    observe(ledger, "get_saved_addresses", {}, {"addresses": [{"id": "ADDR-0001", "label": "Home",
                                                               "line": "x"}]})  # fmt: skip
    quote = make_quote(lines=[make_line(sku="LAP-1209", seller_id="SEL-IN02")])
    p = ActionProposal(
        action_id="a1", session_id=S, tool="checkout",
        args={"cart_id": "c1", "shipping_address_id": "ADDR-0001"}, ts=1,
    )  # fmt: skip
    ctx = DecisionContext(proposal=p, mandate=None, config="intentlock", now=1, quote=quote)
    reasons = make_taint_step(ledger)(ctx)
    tainted = [r for r in reasons if r.code == "TAINTED_ARG"]
    assert tainted and "LAP-1209" in tainted[0].detail and "get_reviews#" in tainted[0].detail


def test_a_later_generic_search_does_not_wash_out_selection_taint(ledger):
    """Checkpoint review MED-1: one ordinary re-search must not clean a steered choice."""
    session(ledger, "Buy me a good Asus Vivobook laptop under ₹60,000.")
    observe(ledger, "get_reviews", {"sku": "LAP-1201"}, REVIEW)
    observe(ledger, "search_products", {"query": "vivobook pro"}, RESULTS)
    observe(ledger, "search_products", {"query": "vivobook"}, RESULTS)  # the user's word only
    idx = ledger.index(S)
    assert idx.classify("LAP-1209").status == "tainted"
    assert "get_reviews#" in idx.explain("LAP-1209")
    assert idx.classify("LAP-1201").status == "clean"


def test_only_the_users_own_words_clear_a_steered_choice(ledger):
    session(ledger, "Buy me a good Asus Vivobook laptop, maybe the LAP-1209.")
    observe(ledger, "get_reviews", {"sku": "LAP-1201"}, REVIEW)
    observe(ledger, "search_products", {"query": "vivobook pro"}, RESULTS)
    assert ledger.index(S).classify("LAP-1209").status == "clean"
