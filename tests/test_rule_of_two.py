"""Rule-of-Two session rule (SPEC §8, pipeline step 7) and optional steps 8-9 (D-021)."""

import pytest

from intentlock.policy.pipeline import decide, steps_for
from intentlock.policy.reasons import make_reason
from intentlock.policy.rule_of_two import SessionFlags
from tests.factories import NOW, make_line, make_mandate, make_proposal, make_quote
from tests.test_pipeline import FakeConnector

CLEAN = SessionFlags()
NOOP = {6: lambda c: [], 8: lambda c: [], 9: lambda c: []}
CHEAP = make_quote([make_line(unit_paise=149_900)])
MANDATE = make_mandate(
    auto_approve_under_paise=200_000,
    allowed_recipients=["a@example.com"],
    allowed_actions=["checkout", "send_email"],
)


def go(config, flags=CLEAN, proposal=None, extra=None, features=frozenset(), mandate=MANDATE):
    return decide(
        proposal or make_proposal(),
        mandate,
        config,
        connector=FakeConnector(CHEAP),
        now=NOW,
        flags=flags,
        extra_steps=extra if extra is not None else NOOP,
        features=features,
    )


def codes(d):
    return [r.code for r in d.reasons]


EMAIL = make_proposal("send_email", to="a@example.com", subject="s", body="b")
UNTRUSTED = SessionFlags().after_tool("get_reviews")
SENSITIVE = SessionFlags().after_tool("get_saved_addresses")
BOTH = UNTRUSTED.after_tool("get_saved_addresses")


# -- flags -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool", ["get_product", "get_reviews", "read_inbox", "read_email", "fetch_page"]
)
def test_untrusted_tools_set_flag_a(tool):
    f = SessionFlags().after_tool(tool)
    assert f.untrusted_input and not f.sensitive_access


@pytest.mark.parametrize("tool", ["get_saved_addresses", "get_order_history", "get_contacts"])
def test_sensitive_tools_set_flag_b(tool):
    f = SessionFlags().after_tool(tool)
    assert f.sensitive_access and not f.untrusted_input


@pytest.mark.parametrize("tool", ["search_products", "add_to_cart", "remove_from_cart"])
def test_trusted_tools_set_nothing(tool):
    assert SessionFlags().after_tool(tool) == SessionFlags()


def test_flags_are_sticky():
    assert BOTH.after_tool("search_products") == BOTH


# -- r2_session ------------------------------------------------------------------------------
def test_benign_purchase_with_no_untrusted_reads_is_allowed_in_r2_session():
    d = go("r2_session")
    assert d.outcome == "ALLOW"


def test_checkout_after_reading_reviews_is_rule_of_two_in_r2_session():
    d = go("r2_session", UNTRUSTED)  # checkout implies B, the action is C
    assert d.outcome == "REVIEW" and codes(d) == ["RULE_OF_TWO"]


def test_headline_case_same_session_intentlock_allows_r2_interrupts():
    assert go("intentlock", UNTRUSTED).outcome == "ALLOW"
    assert go("r2_session", UNTRUSTED).outcome == "REVIEW"


def test_email_needs_b_from_a_sensitive_read():
    assert "RULE_OF_TWO" not in codes(go("r2_session", UNTRUSTED, EMAIL))
    assert "RULE_OF_TWO" in codes(go("r2_session", BOTH, EMAIL))


def test_sensitive_only_is_not_rule_of_two():
    assert go("r2_session", SENSITIVE).outcome == "ALLOW"


@pytest.mark.parametrize("config", ["intentlock", "none"])
def test_rule_of_two_never_fires_outside_r2_session(config):
    assert "RULE_OF_TWO" not in codes(go(config, BOTH))


def test_r2_session_without_flags_fails_closed():
    d = go("r2_session", flags=None)
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


def test_step_7_cannot_be_replaced_by_an_extra_step():
    d = go("r2_session", UNTRUSTED, extra={**NOOP, 7: lambda c: []})
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


# -- optional steps 8-9 (D-021) ---------------------------------------------------------------
def test_step_sets():
    assert steps_for("none", frozenset({8, 9})) == (1, 3)
    assert steps_for("r2_session", frozenset()) == (1, 2, 3, 4, 5, 7)
    assert steps_for("intentlock", frozenset()) == (1, 2, 3, 4, 5, 6)
    assert steps_for("r2_session", frozenset({8, 9})) == (1, 2, 3, 4, 5, 7, 8, 9)
    assert steps_for("intentlock", frozenset({8})) == (1, 2, 3, 4, 5, 6, 8)


@pytest.mark.parametrize("config", ["r2_session", "intentlock"])
def test_enabled_feature_runs_equally_in_both_guarded_configs(config):
    flagging = {**NOOP, 8: lambda c: [make_reason("PRESELECTED_ADDON")]}
    assert "PRESELECTED_ADDON" not in codes(go(config, extra=flagging))
    assert "PRESELECTED_ADDON" in codes(go(config, extra=flagging, features=frozenset({8})))


def test_enabled_feature_without_its_step_fails_closed():
    d = go("intentlock", extra={6: lambda c: []}, features=frozenset({9}))
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


@pytest.mark.parametrize("bad", [frozenset({6}), frozenset({7}), frozenset({10})])
def test_only_steps_8_and_9_are_features(bad):
    d = go("intentlock", features=bad)
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


def test_intentlock_still_requires_the_taint_step():
    d = go("intentlock", extra={})
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]
