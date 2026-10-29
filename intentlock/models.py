"""Pydantic v2 data model (SPEC §5).

All money is integer paise and all times are UTC epoch seconds. Both are strict ints, so a float
(or a bool) is a validation error rather than a silent coercion (CLAUDE.md invariant 4). Unknown
fields are rejected, in line with deny-by-default (invariant 1).
"""

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Paise = Annotated[int, Field(strict=True, ge=0)]
EpochSeconds = Annotated[int, Field(strict=True, ge=0)]
StrictInt = Annotated[int, Field(strict=True)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrustLabel(str, Enum):  # noqa: UP042 (SPEC §5 spells it this way)
    USER = "user"  # user's own words
    TRUSTED_API = "trusted_api"  # merchant structured fields (id, price, seller_id, stock)
    UNTRUSTED = "untrusted"  # descriptions, reviews, seller msgs, emails, web pages
    SENSITIVE = "sensitive"  # private user data (addresses, order history); also trusted
    DERIVED_UNTRUSTED = "derived_untrusted"  # lookup result token laundered from untrusted (§7.4.1)


class Grant(_Model):
    field: str  # e.g. "auto_approve_under_paise", "allowed_actions:checkout"
    value: str
    span: str  # exact substring of user_text that grants it


class Mandate(_Model):
    mandate_id: str
    user_text: str  # verbatim original request
    goal: str
    allowed_actions: list[str]  # e.g. ["search","read","add_to_cart","checkout"]
    budget_total_paise: Paise | None  # all-in ceiling, incl. tax/fees/add-ons
    auto_approve_under_paise: Paise = 0  # 0 = every commit needs approval
    allowed_seller_countries: list[str] | None
    allow_recurring: bool = False
    require_refundable: bool = False
    max_quantity: Annotated[int, Field(strict=True, ge=1)] | None
    allowed_recipients: list[str] = []  # outbound comms; empty = none allowed
    grants: list[Grant]  # every widening permission + its grounding span
    clarifications_needed: list[str]
    policy_version: StrictInt
    user_confirmed: bool
    expires_at: EpochSeconds


class ProvenanceRecord(_Model):
    record_id: str
    session_id: str
    tool: str
    label: TrustLabel
    field_path: str  # e.g. "results[2].description"
    raw: str
    tokens: list[str]  # normalized identifiers extracted (§7.2)
    ts: EpochSeconds
    # D-028 additions: which forwarded call produced it, the records a derived_untrusted token
    # came from (§7.4.1 chain), and the Prompt Guard score in per-mille, filled on Nov 1 (D-009).
    call_seq: StrictInt = 0
    derived_from: list[str] = []
    pg_score_permille: Annotated[int, Field(strict=True, ge=0, le=1000)] | None = None


class ActionProposal(_Model):
    action_id: str
    session_id: str
    tool: str  # "checkout" | "send_email" | "share_cart" | "delete_saved_address" ...
    args: dict[str, Any]  # identifiers ONLY; any price/total fields are ignored
    ts: EpochSeconds


class QuoteLine(_Model):
    sku: str
    title: str
    qty: Annotated[int, Field(strict=True, ge=0)]
    unit_paise: Paise
    seller_id: str
    seller_country: str
    is_addon: bool = False


class RecurringTerm(_Model):
    sku: str
    amount_paise: Paise
    interval: str


class Quote(_Model):
    """Fetched by the connector from the merchant, never from the agent."""

    quote_id: str
    cart_id: str
    lines: list[QuoteLine]
    fees_paise: Paise
    tax_paise: Paise
    total_paise: Paise
    recurring: list[RecurringTerm]
    shipping_address_id: str
    checkout_url: str
    quote_hash: str  # sha256 of canonical(quote minus quote_hash)


class Reason(_Model):
    code: str  # from docs/REASON_CODES.md
    detail: str = ""
    source: Literal["rule", "taint", "r2", "two_channel", "promptguard"]


class Decision(_Model):
    action_id: str
    outcome: Literal["ALLOW", "REVIEW", "DENY"]
    reasons: list[Reason]
    quote_hash: str | None
    config: Literal["none", "r2_session", "intentlock"]


class ApprovalRequest(_Model):
    """The unsigned fields of an Approval (SPEC §10): what the gateway asks the user to approve."""

    approval_id: str
    nonce: str
    mandate_id: str
    policy_version: StrictInt
    session_id: str
    action_tool: str
    quote_hash: str  # the quote hash, or the action hash for non-checkout tools (D-022)
    expires_at: EpochSeconds


class Approval(_Model):
    approval_id: str
    nonce: str
    mandate_id: str
    policy_version: StrictInt
    session_id: str
    action_tool: str
    quote_hash: str
    expires_at: EpochSeconds  # now + 120 s
    signature: str  # Ed25519 over canonical(all fields above)


class Receipt(_Model):
    seq: StrictInt
    prev_hash: str
    row_hash: str
    kind: Literal[
        "mandate", "tool_result", "proposal", "decision", "approval", "execution", "error"
    ]
    payload: dict[str, Any]
    ts: EpochSeconds
