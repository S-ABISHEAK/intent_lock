"""Two-channel checkout check (SPEC §11, pipeline step 8): the page the user would see vs the quote.
Escalation only; DENY only for the hard-rule fact NON_REFUNDABLE."""

import threading
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from intentlock.models import PageFacts, PageLine
from intentlock.policy.pipeline import decide
from intentlock.policy.reasons import RANK, outcome_of
from intentlock.policy.rule_of_two import SessionFlags
from intentlock.verify.two_channel import compare, make_two_channel_step
from tests.factories import NOW, make_line, make_mandate, make_proposal, make_quote, monthly
from tests.test_pipeline import FakeConnector

LAPTOP = make_line()  # Asus Vivobook 15, ₹54,999
ADDON = make_line(sku="XC-4999", title="Express Care", unit_paise=499_900, is_addon=True)


def facts(**kw):
    data = dict(
        displayed_total_paise=5_499_900,
        line_items=[PageLine(title="Asus Vivobook 15", price_paise=5_499_900)],
        recurring_text="",
        refund_policy="refundable",
        seller_name="Mumbai Gadgets",
    )
    data.update(kw)
    return PageFacts(**data)


def codes(reasons):
    return sorted(r.code for r in reasons)


# -- compare: one rule per code -------------------------------------------------------------------
def test_a_matching_page_raises_nothing():
    assert compare(facts(), make_quote(), make_mandate(require_refundable=True)) == []


def test_a_displayed_total_that_differs_by_one_paisa_is_a_mismatch():
    for shown in (5_499_899, 5_499_901, None):
        out = compare(facts(displayed_total_paise=shown), make_quote(), make_mandate())
        assert codes(out) == ["PRICE_DISPLAY_MISMATCH"], shown


def test_a_line_missing_from_the_page_is_hidden():
    q = make_quote([LAPTOP, ADDON])
    out = compare(facts(displayed_total_paise=q.total_paise), q, make_mandate())
    assert codes(out) == ["HIDDEN_LINE_ITEM"]


def test_an_extra_line_on_the_page_is_hidden_too():
    page = facts(line_items=[*facts().line_items, PageLine(title="Gift Wrap", price_paise=0)])
    assert codes(compare(page, make_quote(), make_mandate())) == ["HIDDEN_LINE_ITEM"]


@pytest.mark.parametrize(
    "title,match",
    [("Asus Vivobook 15", True), ("ASUS VivoBook 15", True), ("Asus Vivobook 15 ", True),
     ("Asus Vivobook 14", True), ("Asus Vivobook 15 Pro OLED", False), ("Acer Aspire 5", False)],
)  # fmt: skip
def test_titles_match_fuzzily_at_0_85(title, match):
    page = facts(line_items=[PageLine(title=title, price_paise=5_499_900)])
    assert ("HIDDEN_LINE_ITEM" not in codes(compare(page, make_quote(), make_mandate()))) is match


def test_a_preselected_addon_is_flagged():
    q = make_quote([LAPTOP, ADDON])
    page = facts(
        displayed_total_paise=q.total_paise,
        line_items=[
            PageLine(title="Asus Vivobook 15", price_paise=5_499_900),
            PageLine(title="Express Care", price_paise=499_900, preselected=True),
        ],
    )
    assert codes(compare(page, q, make_mandate())) == ["PRESELECTED_ADDON"]


def test_p3_shape_raises_display_mismatch_and_preselected():
    q = make_quote([make_line(sku="LAP-1204", title="HP Pavilion 14", unit_paise=5_899_900), ADDON])
    page = facts(
        displayed_total_paise=5_899_900,
        line_items=[
            PageLine(title="HP Pavilion 14", price_paise=5_899_900),
            PageLine(title="Express Care", price_paise=499_900, preselected=True),
        ],
    )
    assert codes(compare(page, q, make_mandate())) == [
        "PRESELECTED_ADDON",
        "PRICE_DISPLAY_MISMATCH",
    ]


@pytest.mark.parametrize("on_page,in_quote", [(True, False), (False, True)])
def test_recurring_on_one_side_only_is_a_mismatch(on_page, in_quote):
    q = make_quote(recurring=[monthly()] if in_quote else None)
    page = facts(recurring_text="₹299 / month, renews automatically" if on_page else "")
    assert codes(compare(page, q, make_mandate())) == ["RECURRING_MISMATCH"]


def test_recurring_on_both_sides_is_fine():
    page = facts(recurring_text="₹299 / month")
    assert compare(page, make_quote(recurring=[monthly()]), make_mandate()) == []


@pytest.mark.parametrize(
    "policy,required,expected",
    [("non_refundable", True, ["NON_REFUNDABLE"]), ("non_refundable", False, []),
     ("unknown", True, ["REFUND_POLICY_UNKNOWN"]), ("unknown", False, []),
     ("refundable", True, [])],
)  # fmt: skip
def test_refund_policy(policy, required, expected):
    out = compare(
        facts(refund_policy=policy), make_quote(), make_mandate(require_refundable=required)
    )
    assert codes(out) == expected


def test_only_non_refundable_denies():
    q = make_quote([LAPTOP, ADDON])
    page = facts(displayed_total_paise=1, refund_policy="non_refundable", recurring_text="monthly")
    out = compare(page, q, make_mandate(require_refundable=True))
    assert {outcome_of(r) for r in out if r.code != "NON_REFUNDABLE"} == {"REVIEW"}
    assert [outcome_of(r) for r in out if r.code == "NON_REFUNDABLE"] == ["DENY"]
    assert all(r.source == "two_channel" for r in out)


# -- the step: failures escalate, cache by quote hash, checkout only ------------------------------
def ctx(quote=None, tool="checkout", mandate=None):
    from intentlock.policy.context import DecisionContext

    proposal = (
        make_proposal(tool) if tool == "checkout" else make_proposal(tool, to="a@example.com")
    )
    return DecisionContext(
        proposal=proposal, mandate=mandate or make_mandate(), config="intentlock", now=NOW,
        quote=quote if quote is not None else make_quote(),
    )  # fmt: skip


def test_a_failing_reader_escalates_to_two_channel_unavailable():
    def broken(quote):
        raise RuntimeError("playwright crashed")

    assert codes(make_two_channel_step(broken, {})(ctx())) == ["TWO_CHANNEL_UNAVAILABLE"]


def test_a_slow_reader_times_out_to_two_channel_unavailable():
    release = threading.Event()

    def slow(quote):
        release.wait(5)
        return facts()

    start = time.monotonic()
    out = make_two_channel_step(slow, {}, timeout_s=0.2)(ctx())
    release.set()
    assert codes(out) == ["TWO_CHANNEL_UNAVAILABLE"] and time.monotonic() - start < 2


def test_results_are_cached_by_quote_hash_and_failures_are_not():
    calls = []

    def reader(quote):
        calls.append(quote.quote_hash)
        if len(calls) == 1:
            raise RuntimeError("flaky")
        return facts()

    cache = {}
    step = make_two_channel_step(reader, cache)
    assert codes(step(ctx())) == ["TWO_CHANNEL_UNAVAILABLE"]
    assert step(ctx()) == [] and step(ctx()) == []
    assert len(calls) == 2 and make_quote().quote_hash in cache


def test_only_checkout_is_checked():
    called = []
    step = make_two_channel_step(lambda q: called.append(q) or facts(), {})
    assert step(ctx(tool="send_email")) == [] and called == []


def test_the_reader_gets_the_authoritative_quote():
    seen = []
    q = make_quote()
    make_two_channel_step(lambda quote: seen.append(quote) or facts(), {})(ctx(quote=q))
    assert seen == [q]


# -- §17 test 1 with the real step 8 ------------------------------------------------------------
@settings(max_examples=80, deadline=None)
@given(
    st.one_of(st.none(), st.integers(min_value=0, max_value=10**7)),
    st.lists(st.tuples(st.text(max_size=20), st.booleans()), max_size=3),
    st.sampled_from(["refundable", "non_refundable", "unknown"]),
    st.booleans(),
    st.booleans(),
    st.booleans(),
)
def test_property_real_two_channel_step_never_lowers_an_outcome(
    shown, lines, policy, required, cheap, fails
):
    q = make_quote([make_line(unit_paise=100)])
    page = PageFacts(
        displayed_total_paise=shown,
        line_items=[PageLine(title=t, price_paise=100, preselected=p) for t, p in lines],
        recurring_text="",
        refund_policy=policy,
        seller_name="",
    )

    def reader(quote):
        if fails:
            raise RuntimeError("down")
        return page

    m = make_mandate(auto_approve_under_paise=10**9 if cheap else 0, require_refundable=required)
    common = dict(
        connector=FakeConnector(q), now=NOW, flags=SessionFlags(), features=frozenset({8})
    )
    step8 = make_two_channel_step(reader, {})
    real = decide(make_proposal(), m, "intentlock", extra_steps={8: step8}, **common)
    noop = decide(make_proposal(), m, "intentlock", extra_steps={8: lambda c: []}, **common)
    assert RANK[real.outcome] >= RANK[noop.outcome]
    if real.outcome == "DENY" and noop.outcome != "DENY":
        assert {r.code for r in real.reasons if r.source == "two_channel"} >= {"NON_REFUNDABLE"}


def test_a_reader_returning_nothing_escalates():
    assert codes(make_two_channel_step(lambda q: None, {})(ctx())) == ["TWO_CHANNEL_UNAVAILABLE"]


@pytest.mark.parametrize(
    "shown", ["Gift Wrap × 1", "Gift Wrap x 1", "Gift Wrap (x1)", "Gift Wrap ×1"]
)
def test_a_quantity_suffix_on_the_page_title_still_matches(shown):
    """Live Muse reads titles as "Asus Vivobook 15 × 1" (Nov 2 two-channel-check)."""
    q = make_quote([make_line(sku="GW-0799", title="Gift Wrap", unit_paise=79_900, is_addon=True)])
    page = facts(
        displayed_total_paise=79_900, line_items=[PageLine(title=shown, price_paise=79_900)]
    )
    assert compare(page, q, make_mandate()) == []


def test_a_different_quantity_word_is_not_stripped_into_a_match():
    q = make_quote([make_line(sku="GW-0799", title="Gift Wrap", unit_paise=79_900, is_addon=True)])
    page = facts(
        displayed_total_paise=79_900,
        line_items=[PageLine(title="Gift Wrap Deluxe Bundle", price_paise=79_900)],
    )
    assert codes(compare(page, q, make_mandate())) == ["HIDDEN_LINE_ITEM"]
