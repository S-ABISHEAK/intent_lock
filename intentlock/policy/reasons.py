"""Registry of reason codes (docs/REASON_CODES.md is the human-readable copy).

A `Reason` carries only code, detail and source, so the outcome of a code lives here. An unknown
code raises KeyError, which the pipeline turns into DENY `PIPELINE_ERROR` (fail closed).
"""

from typing import Literal

from intentlock.models import Reason

Outcome = Literal["ALLOW", "REVIEW", "DENY"]
Source = Literal["rule", "taint", "r2", "two_channel", "promptguard"]

# code -> (outcome, source)
REGISTRY: dict[str, tuple[Outcome, Source]] = {
    "SCHEMA_INVALID": ("DENY", "rule"),
    "UNKNOWN_TOOL": ("DENY", "rule"),
    "MANDATE_UNCONFIRMED": ("DENY", "rule"),
    "MANDATE_EXPIRED": ("DENY", "rule"),
    "ACTION_NOT_IN_MANDATE": ("DENY", "rule"),
    "QUOTE_UNAVAILABLE": ("DENY", "rule"),
    "BUDGET_EXCEEDED": ("DENY", "rule"),
    "RECURRING_NOT_ALLOWED": ("DENY", "rule"),
    "SELLER_NOT_ALLOWED": ("DENY", "rule"),
    "QUANTITY_EXCEEDED": ("DENY", "rule"),
    "UNREQUESTED_ADDON": ("DENY", "rule"),
    "RECIPIENT_NOT_ALLOWED": ("DENY", "rule"),
    "APPROVAL_REQUIRED": ("REVIEW", "rule"),
    "TAINTED_ARG": ("REVIEW", "taint"),
    "UNKNOWN_PROVENANCE": ("REVIEW", "taint"),
    "RULE_OF_TWO": ("REVIEW", "r2"),
    "PRICE_DISPLAY_MISMATCH": ("REVIEW", "two_channel"),
    "HIDDEN_LINE_ITEM": ("REVIEW", "two_channel"),
    "PRESELECTED_ADDON": ("REVIEW", "two_channel"),
    "RECURRING_MISMATCH": ("REVIEW", "two_channel"),
    "REFUND_POLICY_UNKNOWN": ("REVIEW", "two_channel"),
    "NON_REFUNDABLE": ("DENY", "two_channel"),
    "TWO_CHANNEL_UNAVAILABLE": ("REVIEW", "two_channel"),
    "INJECTION_SUSPECTED": ("REVIEW", "promptguard"),
    "PIPELINE_ERROR": ("DENY", "rule"),
    "QUOTE_CHANGED": ("DENY", "rule"),
    "APPROVAL_INVALID_SIGNATURE": ("DENY", "rule"),
    "APPROVAL_EXPIRED": ("DENY", "rule"),
    "APPROVAL_MISMATCH": ("DENY", "rule"),
    "APPROVAL_REPLAYED": ("DENY", "rule"),
    "ALLOWED_IN_MANDATE": ("ALLOW", "rule"),
}

RANK: dict[str, int] = {"ALLOW": 0, "REVIEW": 1, "DENY": 2}


def make_reason(code: str, detail: str = "") -> Reason:
    _, source = REGISTRY[code]
    return Reason(code=code, detail=detail, source=source)


def outcome_of(reason: Reason) -> Outcome:
    return REGISTRY[reason.code][0]


def worst(outcomes: list[str]) -> Outcome:
    """DENY > REVIEW > ALLOW. An empty list is ALLOW only for the caller to interpret."""
    top = max(outcomes, key=RANK.__getitem__, default="ALLOW")
    return top  # type: ignore[return-value]
