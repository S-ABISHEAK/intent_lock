"""Decision pipeline steps 1-5 and the combiner (SPEC §6, §8, §17 tests 1, 2, 4; D-005, D-011)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

import intentlock.policy.pipeline as pipeline
from intentlock.connector.shop_connector import ConnectorError
from intentlock.models import ActionProposal, Reason
from intentlock.policy.pipeline import STEP_SETS, decide, sanitize_args
from intentlock.policy.reasons import RANK, REGISTRY, make_reason, outcome_of, worst
from tests.factories import NOW, make_line, make_mandate, make_proposal, make_quote, monthly

CONFIGS = ["none", "r2_session", "intentlock"]
COMMIT_PROPOSALS = {
    "checkout": make_proposal(),
    "send_email": make_proposal("send_email", to="a@example.com", subject="s", body="b"),
    "share_cart": make_proposal("share_cart", cart_id="c1", to="a@example.com"),
    "delete_saved_address": make_proposal("delete_saved_address", id="ADDR-0001"),
    "cancel_order": make_proposal("cancel_order", id="ORD-5001"),
}


class FakeConnector:
    def __init__(self, quote=None, error=None, resolve_error=None):
        self.quote = quote if quote is not None else make_quote()
        self.error, self.resolve_error = error, resolve_error
        self.quote_calls, self.resolve_calls = [], []

    def get_quote(self, cart_id, shipping_address_id):
        self.quote_calls.append((cart_id, shipping_address_id))
        if self.error:
            raise self.error
        return self.quote

    def resolve_target(self, kind, target_id):
        self.resolve_calls.append((kind, target_id))
        if self.resolve_error:
            raise self.resolve_error
        return {"kind": kind, "id": target_id}


DEFAULT = object()  # lets a test pass mandate=None ("no mandate") explicitly


def run(proposal=None, mandate=DEFAULT, config="intentlock", connector=None, **kw):
    kw.setdefault("allow_missing_steps", True)  # steps 6-9 arrive Oct 31
    return decide(
        proposal or make_proposal(),
        make_mandate() if mandate is DEFAULT else mandate,
        config,
        connector=connector or FakeConnector(),
        now=NOW,
        **kw,
    )


def codes(d):
    return [r.code for r in d.reasons]


# -- step 1: schema ----------
@pytest.mark.parametrize("tool", ["add_to_cart", "drop_tables", "get_product", ""])
@pytest.mark.parametrize("config", CONFIGS)
def test_unknown_tool_is_denied_in_every_config(tool, config):
    c = FakeConnector()
    d = run(make_proposal(tool, x="1"), config=config, connector=c)
    assert d.outcome == "DENY" and codes(d) == ["UNKNOWN_TOOL"]
    assert c.quote_calls == [] and c.resolve_calls == []


@pytest.mark.parametrize(
    "tool,args",
    [
        ("checkout", {"cart_id": "c1"}),
        ("checkout", {"shipping_address_id": "ADDR-0001"}),
        ("checkout", {"cart_id": "", "shipping_address_id": "ADDR-0001"}),
        ("checkout", {"cart_id": "  ", "shipping_address_id": "ADDR-0001"}),
        ("checkout", {"cart_id": 1, "shipping_address_id": "ADDR-0001"}),
        ("checkout", {"cart_id": None, "shipping_address_id": "ADDR-0001"}),
        ("checkout", {}),
        ("send_email", {"to": "a@b.co", "subject": "s"}),
        ("share_cart", {"cart_id": "c1"}),
        ("delete_saved_address", {}),
        ("cancel_order", {"id": 7}),
    ],
)
def test_missing_or_malformed_args_are_denied(tool, args):
    d = run(ActionProposal(action_id="a", session_id="s", tool=tool, args=args, ts=NOW))
    assert d.outcome == "DENY" and codes(d) == ["SCHEMA_INVALID"]


def test_sanitize_args_keeps_only_the_tools_identifier_args():
    clean = sanitize_args(
        "checkout",
        {"cart_id": "c1", "shipping_address_id": "ADDR-0001", "total": 1, "price": 0.5, "x": "y"},
    )
    assert clean == {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}


# -- step 2: mandate scope ----------
def test_missing_mandate_is_denied():
    d = run(mandate=None)
    assert d.outcome == "DENY" and codes(d) == ["MANDATE_UNCONFIRMED"]


def test_unconfirmed_mandate_is_denied():
    d = run(mandate=make_mandate(user_confirmed=False))
    assert d.outcome == "DENY" and codes(d) == ["MANDATE_UNCONFIRMED"]


def test_mandate_expiry_boundary():
    last_ok = make_mandate(expires_at=NOW + 1)
    assert "MANDATE_EXPIRED" not in codes(run(mandate=last_ok))
    expired = make_mandate(expires_at=NOW)
    d = run(mandate=expired)
    assert d.outcome == "DENY" and codes(d) == ["MANDATE_EXPIRED"]


def test_tool_outside_allowed_actions_is_denied():
    d = run(mandate=make_mandate(allowed_actions=["search", "read"]))
    assert d.outcome == "DENY" and codes(d) == ["ACTION_NOT_IN_MANDATE"]


def test_all_scope_failures_are_reported_together_and_no_quote_is_fetched():
    c = FakeConnector()
    m = make_mandate(user_confirmed=False, expires_at=NOW, allowed_actions=["search"])
    d = run(mandate=m, connector=c)
    assert set(codes(d)) == {"MANDATE_UNCONFIRMED", "MANDATE_EXPIRED", "ACTION_NOT_IN_MANDATE"}
    assert c.quote_calls == []


def test_config_none_skips_mandate_scope():
    m = make_mandate(user_confirmed=False, allowed_actions=[])
    assert run(mandate=m, config="none").outcome == "ALLOW"
    assert run(mandate=None, config="none").outcome == "ALLOW"


# -- step 3: authoritative quote / targets ----------
@pytest.mark.parametrize("config", CONFIGS)
def test_quote_failure_is_denied(config):
    d = run(config=config, connector=FakeConnector(error=ConnectorError("shop down")))
    assert d.outcome == "DENY" and codes(d) == ["QUOTE_UNAVAILABLE"] and d.quote_hash is None


def test_decision_carries_the_quote_hash():
    q = make_quote()
    assert run(connector=FakeConnector(q)).quote_hash == q.quote_hash


def test_destructive_target_must_resolve():
    p = make_proposal("delete_saved_address", id="ADDR-9931")
    m = make_mandate(allowed_actions=["delete_saved_address"])
    c = FakeConnector(resolve_error=ConnectorError("404"))
    d = run(p, m, connector=c)
    assert d.outcome == "DENY" and codes(d) == ["QUOTE_UNAVAILABLE"]
    assert c.resolve_calls == [("address", "ADDR-9931")]


def test_cancel_order_resolves_an_order_target():
    c = FakeConnector()
    run(
        make_proposal("cancel_order", id="ORD-5001"),
        make_mandate(allowed_actions=["cancel_order"]),
        connector=c,
    )
    assert c.resolve_calls == [("order", "ORD-5001")]


def test_comms_tools_do_not_call_the_shop():
    c = FakeConnector()
    m = make_mandate(allowed_actions=["send_email"], allowed_recipients=["a@example.com"])
    run(COMMIT_PROPOSALS["send_email"], m, connector=c)
    assert c.quote_calls == [] and c.resolve_calls == []


# -- step 4: hard rules ----------
def test_hard_rule_violation_denies_and_skips_later_steps():
    q = make_quote([make_line(unit_paise=7_000_000)])
    d = run(connector=FakeConnector(q))
    assert d.outcome == "DENY" and codes(d) == ["BUDGET_EXCEEDED"]  # no APPROVAL_REQUIRED


def test_config_none_skips_hard_rules():
    q = make_quote([make_line(unit_paise=99_000_000, seller_country="US")], recurring=[monthly()])
    d = run(config="none", connector=FakeConnector(q))
    assert d.outcome == "ALLOW" and d.config == "none"


def test_requested_skus_reach_the_addon_rule():
    q = make_quote([make_line(), make_line(sku="XC-4999", unit_paise=1, is_addon=True)])
    assert codes(run(connector=FakeConnector(q))) == ["UNREQUESTED_ADDON"]
    d = run(connector=FakeConnector(q), requested_skus=frozenset({"XC-4999", "LAP-1201"}))
    assert "UNREQUESTED_ADDON" not in codes(d)


# -- step 5: mandate-required approval ------------------------------------------------------------
def test_default_mandate_requires_approval_for_every_checkout():
    d = run()
    assert d.outcome == "REVIEW" and codes(d) == ["APPROVAL_REQUIRED"]


def test_auto_approve_boundary_is_exclusive_of_the_limit():
    q = make_quote([make_line(unit_paise=200_000)])
    at = run(mandate=make_mandate(auto_approve_under_paise=200_000), connector=FakeConnector(q))
    assert at.outcome == "REVIEW" and codes(at) == ["APPROVAL_REQUIRED"]
    below = run(mandate=make_mandate(auto_approve_under_paise=200_001), connector=FakeConnector(q))
    assert below.outcome == "ALLOW" and codes(below) == ["ALLOWED_IN_MANDATE"]


@pytest.mark.parametrize(
    "tool", ["send_email", "share_cart", "delete_saved_address", "cancel_order"]
)
def test_comms_and_destructive_actions_always_need_approval(tool):
    m = make_mandate(
        allowed_actions=[tool], allowed_recipients=["a@example.com"], auto_approve_under_paise=10**9
    )
    d = run(COMMIT_PROPOSALS[tool], m)
    assert d.outcome == "REVIEW" and codes(d) == ["APPROVAL_REQUIRED"]


@pytest.mark.parametrize("config", ["r2_session", "intentlock"])
def test_step5_runs_in_both_guarded_configs_but_not_none(config):
    assert run(config=config).outcome == "REVIEW"
    assert run(config="none").outcome == "ALLOW"


def test_recipient_hard_rule_beats_approval():
    d = run(COMMIT_PROPOSALS["send_email"], make_mandate(allowed_actions=["send_email"]))
    assert d.outcome == "DENY" and codes(d) == ["RECIPIENT_NOT_ALLOWED"]


# -- config step sets (D-005, D-011) --------------------------------------------------------------
def test_step_sets_match_decisions():
    assert STEP_SETS["none"] == (1, 3)
    assert STEP_SETS["r2_session"] == (1, 2, 3, 4, 5, 7, 8, 9)
    assert STEP_SETS["intentlock"] == (1, 2, 3, 4, 5, 6, 8, 9)


def test_missing_model_assisted_step_fails_closed_by_default():
    q = make_quote([make_line(unit_paise=100)])
    m = make_mandate(auto_approve_under_paise=10**6)
    d = decide(make_proposal(), m, "intentlock", connector=FakeConnector(q), now=NOW)
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


def test_steps_outside_the_configs_set_are_not_called():
    called = []
    steps = {n: (lambda c, n=n: called.append(n) or []) for n in (6, 7, 8, 9)}
    q = make_quote([make_line(unit_paise=100)])
    m = make_mandate(auto_approve_under_paise=10**6)
    run(mandate=m, connector=FakeConnector(q), extra_steps=steps, config="intentlock")
    assert called == [6, 8, 9]
    called.clear()
    run(mandate=m, connector=FakeConnector(q), extra_steps=steps, config="r2_session")
    assert called == [7, 8, 9]
    called.clear()
    run(mandate=m, connector=FakeConnector(q), extra_steps=steps, config="none")
    assert called == []


# -- escalation only (invariant 5, §17 test 1) ----------
def allow_setup():
    q = make_quote([make_line(unit_paise=100)])
    return make_mandate(auto_approve_under_paise=10**6), FakeConnector(q)


def test_extra_steps_can_escalate_allow_to_review_and_review_to_deny():
    m, c = allow_setup()
    assert run(mandate=m, connector=c).outcome == "ALLOW"
    step = {6: lambda ctx: [make_reason("TAINTED_ARG", "sku")]}
    d = run(mandate=m, connector=c, extra_steps=step)
    assert d.outcome == "REVIEW" and "TAINTED_ARG" in codes(d)
    both = {6: step[6], 8: lambda ctx: [make_reason("NON_REFUNDABLE")]}
    assert run(mandate=m, connector=c, extra_steps=both).outcome == "DENY"


def test_extra_step_returning_nothing_never_lowers_a_review():
    steps = {n: (lambda c: []) for n in (6, 8, 9)}
    d = run(extra_steps=steps)  # default mandate -> REVIEW from step 5
    assert d.outcome == "REVIEW" and codes(d) == ["APPROVAL_REQUIRED"]


def test_extra_step_cannot_allow():
    m, c = allow_setup()
    d = run(
        mandate=m, connector=c, extra_steps={6: lambda ctx: [make_reason("ALLOWED_IN_MANDATE")]}
    )
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


REVIEW_CODES = [c for c, (o, _) in REGISTRY.items() if o == "REVIEW" and c != "APPROVAL_REQUIRED"]
DENY_CODES = ["NON_REFUNDABLE"]  # the only DENY a model-assisted step may emit (SPEC §6 step 8)
step_output = st.lists(st.sampled_from(REVIEW_CODES + DENY_CODES), max_size=3)


@given(st.fixed_dictionaries({6: step_output, 8: step_output, 9: step_output}), st.booleans())
def test_property_extra_steps_never_lower_the_baseline_outcome(outputs, guarded_baseline):
    m = make_mandate(auto_approve_under_paise=10**6 if not guarded_baseline else 0)
    q = make_quote([make_line(unit_paise=100)])
    baseline = run(mandate=m, connector=FakeConnector(q))
    steps = {n: (lambda ctx, o=o: [make_reason(c) for c in o]) for n, o in outputs.items()}
    result = run(mandate=m, connector=FakeConnector(q), extra_steps=steps)
    assert RANK[result.outcome] >= RANK[baseline.outcome]
    emitted = [c for o in outputs.values() for c in o]
    expected = worst([baseline.outcome] + [REGISTRY[c][0] for c in emitted])
    assert result.outcome == expected
    informational = {"ALLOWED_IN_MANDATE"}  # dropped when an outcome escalates
    assert set(codes(baseline)) - informational <= set(codes(result))


def test_worst_orders_deny_review_allow():
    assert worst(["ALLOW", "REVIEW"]) == "REVIEW"
    assert worst(["REVIEW", "DENY", "ALLOW"]) == "DENY"
    assert worst([]) == "ALLOW"


# -- fail closed (invariant 2, §17 test 4) ----------
@pytest.mark.parametrize("tool", list(COMMIT_PROPOSALS))
@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize(
    "victim", ["schema", "scope", "connector", "hard_rules", "approval", "extra"]
)
def test_any_exception_in_any_step_is_deny_with_an_error_report(tool, config, victim, monkeypatch):
    if victim == "scope" and config == "none":
        pytest.skip("step 2 does not run in config none")
    if victim in {"hard_rules", "approval"} and config == "none":
        pytest.skip("steps 4-5 do not run in config none")

    boom = RuntimeError("boom")

    def explode(*a, **k):
        raise boom

    extra = None
    conn = FakeConnector()
    if victim == "schema":
        monkeypatch.setattr(pipeline, "step_schema", explode)
    elif victim == "scope":
        monkeypatch.setattr(pipeline, "step_scope", explode)
    elif victim == "connector":
        conn.get_quote = explode
        conn.resolve_target = explode
    elif victim == "hard_rules":
        monkeypatch.setattr(pipeline, "hard_rules", explode)
    elif victim == "approval":
        monkeypatch.setattr(pipeline, "step_approval", explode)
    else:
        extra = {n: explode for n in (6, 7, 8, 9)}

    m = make_mandate(allowed_actions=[tool], allowed_recipients=["a@example.com"])
    reported = []
    d = run(
        COMMIT_PROPOSALS[tool],
        m,
        config,
        conn,
        extra_steps=extra,
        error_sink=lambda p, e: reported.append((p, e)),
    )

    if victim == "connector" and tool in {"send_email", "share_cart"}:
        return  # comms tools never call the shop, so there is nothing to explode
    if victim == "extra" and config == "none":
        return  # no extra steps run in config none
    assert d.outcome == "DENY" and "PIPELINE_ERROR" in codes(d)
    assert reported and reported[0][1] is boom and reported[0][0].tool == tool


def test_a_failing_error_sink_still_denies():
    def bad_sink(p, e):
        raise OSError("disk full")

    d = run(connector=FakeConnector(error=RuntimeError("x")), error_sink=bad_sink)
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


def test_unexpected_exception_from_connector_is_pipeline_error_not_quote_unavailable():
    d = run(connector=FakeConnector(error=KeyError("oops")))
    assert codes(d) == ["PIPELINE_ERROR"]


def test_unknown_reason_code_from_a_step_fails_closed():
    m, c = allow_setup()
    bad = {6: lambda ctx: [Reason(code="MADE_UP", source="rule")]}
    d = run(mandate=m, connector=c, extra_steps=bad)
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


def test_invalid_config_name_is_denied_not_crashed():
    d = run(config="allow_everything")
    assert d.outcome == "DENY" and codes(d) == ["PIPELINE_ERROR"]


# -- agent-supplied money is ignored (invariant 3, §17 test 2) ----------
MONEY_KEYS = [
    "price",
    "total",
    "amount",
    "Total",
    "AMOUNT",
    "total_paise",
    "price_paise",
    "grand_total",
]


@pytest.mark.parametrize("key", MONEY_KEYS)
@pytest.mark.parametrize("value", [1, 0, -5, 100.5, "1", None, 10**12])
def test_agent_money_args_cannot_change_a_decision(key, value):
    over = FakeConnector(make_quote([make_line(unit_paise=7_000_000)]))
    plain = run(connector=over)
    spoof = make_proposal(cart_id="c1", shipping_address_id="ADDR-0001", **{key: value})
    d = run(spoof, connector=FakeConnector(make_quote([make_line(unit_paise=7_000_000)])))
    assert d == plain and d.outcome == "DENY" and codes(d) == ["BUDGET_EXCEEDED"]


@pytest.mark.parametrize("key", ["price", "total", "amount"])
def test_a_cheap_claimed_price_does_not_unlock_auto_approval(key):
    big = make_quote([make_line(unit_paise=5_000_000)])
    m = make_mandate(auto_approve_under_paise=200_000)
    spoof = make_proposal(cart_id="c1", shipping_address_id="ADDR-0001", **{key: 100})
    d = run(spoof, m, connector=FakeConnector(big))
    assert d.outcome == "REVIEW" and codes(d) == ["APPROVAL_REQUIRED"]


def test_the_connector_is_only_given_identifiers():
    c = FakeConnector()
    run(make_proposal(cart_id="c1", shipping_address_id="ADDR-0001", total=1, price=2), connector=c)
    assert c.quote_calls == [("c1", "ADDR-0001")]


json_scalars = (
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=8)
)


@given(st.dictionaries(st.text(min_size=1, max_size=12), json_scalars, max_size=6))
def test_property_extra_args_of_any_kind_never_change_the_decision(extras):
    base_args = {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}
    extras = {k: v for k, v in extras.items() if k not in base_args}
    q = make_quote([make_line(unit_paise=5_000_000)])
    plain = run(make_proposal(**base_args), connector=FakeConnector(q))
    noisy = run(make_proposal(**base_args, **extras), connector=FakeConnector(q))
    assert plain == noisy
    assert outcome_of(noisy.reasons[0]) in {"REVIEW", "DENY", "ALLOW"}
