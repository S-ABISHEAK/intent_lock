"""Decision pipeline for commit tools (SPEC §6, §8; D-005, D-011). Steps 1-5 live here.

Steps are pure `(DecisionContext) -> list[Reason]`. Precedence is DENY > REVIEW > ALLOW. Only
deterministic steps (1-5) can lead to ALLOW; steps 6-9 are passed in as `extra_steps` and can only
add REVIEW/DENY reasons. Any exception anywhere becomes DENY `PIPELINE_ERROR` plus a report to
`error_sink` (the `error` receipt from Oct 30). A step missing from `extra_steps` also fails closed
unless `allow_missing_steps` is set, so a config can never silently run without a safety check.
"""

import logging
from collections.abc import Callable, Mapping

from intentlock.connector.shop_connector import ConnectorError
from intentlock.models import ActionProposal, Decision, Mandate, Reason
from intentlock.policy.context import DecisionContext
from intentlock.policy.hard_rules import hard_rules
from intentlock.policy.reasons import make_reason, outcome_of, worst

log = logging.getLogger(__name__)

StepFn = Callable[[DecisionContext], list[Reason]]
ErrorSink = Callable[[ActionProposal, Exception], None]

# Identifier args per commit tool. Anything else the agent sends (price, total...) is dropped.
TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "checkout": ("cart_id", "shipping_address_id"),
    "send_email": ("to", "subject", "body"),
    "share_cart": ("cart_id", "to"),
    "delete_saved_address": ("id",),
    "cancel_order": ("id",),
}
FREE_TEXT_ARGS = ("subject", "body")  # may be empty; all other args must be non-blank
COMMIT_TOOLS = tuple(TOOL_ARGS)

STEP_SETS: dict[str, tuple[int, ...]] = {
    "none": (1, 3),
    "r2_session": (1, 2, 3, 4, 5, 7, 8, 9),
    "intentlock": (1, 2, 3, 4, 5, 6, 8, 9),
}
MODEL_ASSISTED_STEPS = (6, 7, 8, 9)


def sanitize_args(tool: str, args: Mapping[str, object]) -> dict[str, object]:
    return {k: args[k] for k in TOOL_ARGS[tool] if k in args}


def step_schema(proposal: ActionProposal) -> list[Reason]:
    if proposal.tool not in TOOL_ARGS:
        return [make_reason("UNKNOWN_TOOL", proposal.tool)]
    bad = []
    for key in TOOL_ARGS[proposal.tool]:
        value = proposal.args.get(key)
        blank_ok = key in FREE_TEXT_ARGS
        if not isinstance(value, str) or (not blank_ok and not value.strip()):
            bad.append(key)
    return [make_reason("SCHEMA_INVALID", ",".join(bad))] if bad else []


def step_scope(ctx: DecisionContext) -> list[Reason]:
    m = ctx.mandate
    if m is None:
        return [make_reason("MANDATE_UNCONFIRMED", "no mandate")]
    out = []
    if not m.user_confirmed:
        out.append(make_reason("MANDATE_UNCONFIRMED"))
    if ctx.now >= m.expires_at:
        out.append(make_reason("MANDATE_EXPIRED"))
    if ctx.proposal.tool not in m.allowed_actions:
        out.append(make_reason("ACTION_NOT_IN_MANDATE", ctx.proposal.tool))
    return out


def step_fetch(ctx: DecisionContext, connector) -> tuple[DecisionContext, list[Reason]]:
    """Step 3: the authoritative quote (checkout) or target resolution (destructive tools)."""
    tool, args = ctx.proposal.tool, ctx.proposal.args
    try:
        if tool == "checkout":
            quote = connector.get_quote(args["cart_id"], args["shipping_address_id"])
            return DecisionContext(**{**ctx.__dict__, "quote": quote}), []
        if tool == "delete_saved_address":
            connector.resolve_target("address", args["id"])
        elif tool == "cancel_order":
            connector.resolve_target("order", args["id"])
    except ConnectorError as exc:
        return ctx, [make_reason("QUOTE_UNAVAILABLE", str(exc))]
    return ctx, []


def step_approval(ctx: DecisionContext) -> list[Reason]:
    """Step 5: approval the mandate itself requires."""
    if ctx.mandate is None:
        raise ValueError("mandate approval needs a mandate")
    if ctx.proposal.tool == "checkout":
        if ctx.quote is None:
            raise ValueError("mandate approval needs the authoritative quote")
        if ctx.quote.total_paise >= ctx.mandate.auto_approve_under_paise:
            return [make_reason("APPROVAL_REQUIRED", f"total {ctx.quote.total_paise}")]
        return []
    return [make_reason("APPROVAL_REQUIRED", ctx.proposal.tool)]  # comms / destructive


def _decision(proposal, config, reasons, quote) -> Decision:
    outcome = worst([outcome_of(r) for r in reasons])
    if outcome == "ALLOW" and config != "none":
        reasons = [make_reason("ALLOWED_IN_MANDATE")]
    return Decision(
        action_id=proposal.action_id,
        outcome=outcome,
        reasons=reasons,
        quote_hash=quote.quote_hash if quote else None,
        config=config if config in STEP_SETS else "intentlock",
    )


def _run_steps(proposal, mandate, config, connector, now, requested_skus, extra, allow_missing):
    if config not in STEP_SETS:
        raise ValueError(f"unknown config {config!r}")
    steps = STEP_SETS[config]

    reasons = step_schema(proposal)
    if reasons:
        return _decision(proposal, config, reasons, None)
    ctx = DecisionContext(
        proposal=proposal.model_copy(update={"args": sanitize_args(proposal.tool, proposal.args)}),
        mandate=mandate,
        config=config,
        now=now,
        requested_skus=frozenset(requested_skus),
    )
    if 2 in steps and (reasons := step_scope(ctx)):
        return _decision(proposal, config, reasons, None)
    ctx, reasons = step_fetch(ctx, connector)
    if reasons:
        return _decision(proposal, config, reasons, ctx.quote)
    if 4 in steps and (reasons := hard_rules(ctx)):
        return _decision(proposal, config, reasons, ctx.quote)

    reasons = step_approval(ctx) if 5 in steps else []
    for n in MODEL_ASSISTED_STEPS:
        if n not in steps:
            continue
        step = extra.get(n)
        if step is None:
            if allow_missing:
                continue
            raise RuntimeError(
                f"pipeline step {n} is required by config {config} but not installed"
            )
        produced = step(ctx)
        if any(outcome_of(r) == "ALLOW" for r in produced):
            raise ValueError(f"step {n} tried to ALLOW; model-assisted steps can only escalate")
        reasons = [*reasons, *produced]
    return _decision(proposal, config, reasons, ctx.quote)


def decide(
    proposal: ActionProposal,
    mandate: Mandate | None,
    config: str,
    *,
    connector,
    now: int,
    requested_skus=frozenset(),
    extra_steps: Mapping[int, StepFn] | None = None,
    error_sink: ErrorSink | None = None,
    allow_missing_steps: bool = False,
) -> Decision:
    try:
        return _run_steps(
            proposal,
            mandate,
            config,
            connector,
            now,
            requested_skus,
            extra_steps or {},
            allow_missing_steps,
        )
    except Exception as exc:  # fail closed (CLAUDE.md invariant 2): never let an error become ALLOW
        if error_sink is not None:
            try:
                error_sink(proposal, exc)
            except Exception:
                log.exception("error sink failed while reporting a pipeline error")
        return Decision(
            action_id=proposal.action_id,
            outcome="DENY",
            reasons=[make_reason("PIPELINE_ERROR", type(exc).__name__)],
            quote_hash=None,
            config=config if config in STEP_SETS else "intentlock",
        )
