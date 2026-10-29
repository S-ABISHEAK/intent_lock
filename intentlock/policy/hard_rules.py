"""Deterministic hard rules on the authoritative quote (SPEC §6.1, pipeline step 4).

Pure `(DecisionContext) -> list[Reason]`; every reason is a DENY. Money comes only from `ctx.quote`,
which the connector fetched (CLAUDE.md invariant 3). Misuse (no quote or mandate) raises instead of
returning an empty list, so the pipeline fails closed rather than passing.
"""

import re

from intentlock.models import Mandate, Quote, Reason
from intentlock.policy.context import DecisionContext
from intentlock.policy.reasons import make_reason

COMMS_TOOLS = ("send_email", "share_cart")


def split_recipients(to: str) -> list[str]:
    """Every address in a `to` field, lowercased. Any separator or display-name form is split."""
    tokens = (t.strip("<>\"'") for t in re.split(r"[,;\s]+", to.strip()))
    return [t.lower() for t in tokens if t]


def _quote_rules(ctx: DecisionContext, mandate: Mandate, quote: Quote) -> list[Reason]:
    out: list[Reason] = []
    if mandate.budget_total_paise is not None and quote.total_paise > mandate.budget_total_paise:
        out.append(
            make_reason(
                "BUDGET_EXCEEDED",
                f"total {quote.total_paise} > budget {mandate.budget_total_paise}",
            )
        )
    if quote.recurring and not mandate.allow_recurring:
        out.append(make_reason("RECURRING_NOT_ALLOWED", ",".join(r.sku for r in quote.recurring)))
    if mandate.allowed_seller_countries is not None:
        allowed = {c.upper() for c in mandate.allowed_seller_countries}
        bad = [line.sku for line in quote.lines if line.seller_country.upper() not in allowed]
        if bad:
            out.append(make_reason("SELLER_NOT_ALLOWED", ",".join(bad)))
    if mandate.max_quantity is not None:
        bad = [line.sku for line in quote.lines if line.qty > mandate.max_quantity]
        if bad:
            out.append(make_reason("QUANTITY_EXCEEDED", ",".join(bad)))
    unrequested = [
        line.sku for line in quote.lines if line.is_addon and line.sku not in ctx.requested_skus
    ]
    if unrequested:
        out.append(make_reason("UNREQUESTED_ADDON", ",".join(unrequested)))
    return out


def _recipient_rules(ctx: DecisionContext, mandate: Mandate) -> list[Reason]:
    allowed = {r.lower() for r in mandate.allowed_recipients}
    bad = [r for r in split_recipients(str(ctx.proposal.args["to"])) if r not in allowed]
    return [make_reason("RECIPIENT_NOT_ALLOWED", ",".join(bad))] if bad else []


def hard_rules(ctx: DecisionContext) -> list[Reason]:
    if ctx.mandate is None:
        raise ValueError("hard rules need a mandate")
    tool = ctx.proposal.tool
    if tool == "checkout":
        if ctx.quote is None:
            raise ValueError("hard rules need the authoritative quote for checkout")
        return _quote_rules(ctx, ctx.mandate, ctx.quote)
    if tool in COMMS_TOOLS:
        return _recipient_rules(ctx, ctx.mandate)
    return []
