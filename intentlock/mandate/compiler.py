"""Mandate compiler (SPEC §9): Muse drafts, the deterministic validator decides (invariant 10).

Muse fills a restricted `MandateDraft` that has no ids, no user text, no expiry and no confirmation
flag: code sets those, so a model can never confirm or extend its own mandate. The draft then goes
through `grounding.ground`, which reverts every widening permission that isn't backed by a verbatim,
unrefused, keyword-and-number-matching span of the user's words. The result is always unconfirmed;
only the user's click in the readback confirms it.
"""

import uuid

from pydantic import BaseModel, ConfigDict, ValidationError

from intentlock.llm.client import LLMClient, LLMError
from intentlock.mandate.grounding import ADDON_GRANT, ANSWERS_MARK, GroundingReport, ground
from intentlock.models import EpochSeconds, Grant, Mandate, Paise

MANDATE_TTL = 24 * 3600

INSTRUCTION = """You turn a shopper's request into a spending mandate for a shopping agent.

Baseline (what the agent may do with no permission from the user): search and read only; every
purchase needs approval (auto_approve_under_paise 0); no subscriptions; no recipients; no budget.

Money is integer paise (₹1 = 100 paise). Allowed actions: search, read, add_to_cart, checkout,
send_email, share_cart, delete_saved_address, cancel_order.

Every field that grants more than the baseline must include a grants[] entry whose span is copied
verbatim from the user text:
- allowed_actions:<action> (value = the action) for each action beyond search/read/add_to_cart
- auto_approve_under_paise (value = the paise amount as a string)
- allow_recurring (value "true")
- allowed_recipients (value = one email address; one grant per address)
- requested_addon (value = an add-on SKU the user explicitly asked for)
Narrowing fields (budget_total_paise, require_refundable, allowed_seller_countries as ISO codes,
max_quantity) need no grant, but use only amounts and countries the user actually stated.
If something is unclear, add it to clarifications_needed instead of guessing."""


class MandateDraft(BaseModel):
    """What the model may propose. No mandate_id, user_text, policy_version, user_confirmed or
    expires_at: those are set by code (D-033)."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    allowed_actions: list[str]
    budget_total_paise: Paise | None
    auto_approve_under_paise: Paise = 0
    allowed_seller_countries: list[str] | None
    allow_recurring: bool = False
    require_refundable: bool = False
    max_quantity: int | None
    allowed_recipients: list[str] = []
    grants: list[Grant]
    clarifications_needed: list[str]


class CompileError(RuntimeError):
    """No mandate could be drafted; the user must try again or write it differently."""


def compile_mandate(
    user_text: str,
    llm: LLMClient,
    *,
    now: EpochSeconds,
    model: str | None = None,
    policy_version: int = 1,
    answers: list[dict] | None = None,
) -> GroundingReport:
    """`answers` are the user's replies to earlier clarification questions. The model sees both
    question and answer as context, but only the user's own typed answers are added to the text the
    mandate is grounded against, one per line after `ANSWERS_MARK`: a model-written question is
    never treated as the user's words, and answers can narrow the mandate but never widen it."""
    answers = [a for a in (answers or []) if str(a.get("answer", "")).strip()]
    typed = [" ".join(str(a["answer"]).split()) for a in answers]
    grounding_text = user_text + (ANSWERS_MARK + "\n".join(typed) if typed else "")
    messages = [
        {"role": "system", "content": INSTRUCTION},
        {"role": "user", "content": user_text},
    ]
    if answers:
        qa = "\n".join(f"Q: {a.get('question', '')}\nA: {a['answer']}" for a in answers)
        messages.append({"role": "user", "content": f"My answers to your questions:\n{qa}"})
    try:
        draft = llm.structured(messages, MandateDraft, model=model)
        mandate = Mandate(
            mandate_id=uuid.uuid4().hex,
            user_text=grounding_text,
            policy_version=policy_version,
            user_confirmed=False,
            expires_at=now + MANDATE_TTL,
            **draft.model_dump(),
        )
    except (LLMError, ValidationError) as exc:
        raise CompileError(f"could not draft a mandate: {type(exc).__name__}") from None
    return ground(mandate)


def _rupees(paise: int) -> str:
    return f"₹{paise // 100:,}" + (f".{paise % 100:02d}" if paise % 100 else "")


def _span(m: Mandate, field: str) -> str | None:
    for g in m.grants:
        if g.field == field:
            return g.span
    return None


def readback(report: GroundingReport) -> list[dict]:
    """Plain-language bullets for the user to confirm (SPEC §9 readback). Each widening bullet
    quotes the span that grants it; reverted permissions and open questions are listed."""
    m, out = report.mandate, []

    def add(kind: str, text: str, span: str | None = None, field: str | None = None) -> None:
        out.append({"kind": kind, "text": text, "span": span, "field": field})

    for action in m.allowed_actions:
        if action not in ("search", "read", "add_to_cart"):
            f = f"allowed_actions:{action}"
            add("grant", f"May {action.replace('_', ' ')}", _span(m, f), f)
    if m.auto_approve_under_paise:
        add(
            "grant",
            f"Purchases under {_rupees(m.auto_approve_under_paise)} go through without asking you",
            _span(m, "auto_approve_under_paise"),
            "auto_approve_under_paise",
        )
    else:
        add("limit", "Every purchase needs your approval")
    if m.budget_total_paise is not None:
        add(
            "limit",
            f"Total ceiling {_rupees(m.budget_total_paise)} including fees, tax and add-ons",
        )
    if m.allowed_seller_countries is not None:
        countries = ", ".join(m.allowed_seller_countries) or "none (see the question below)"
        add("limit", f"Only sellers in: {countries}")
    if m.allow_recurring:
        add(
            "grant",
            "May agree to recurring charges",
            _span(m, "allow_recurring"),
            "allow_recurring",
        )
    else:
        add("limit", "No subscriptions or recurring charges")
    if m.require_refundable:
        add("limit", "Only refundable purchases")
    if m.max_quantity is not None:
        add("limit", f"At most {m.max_quantity} of any item")
    for r in m.allowed_recipients:
        span = next(
            (g.span for g in m.grants if g.field == "allowed_recipients" and g.value == r), None
        )
        add("grant", f"May send to {r}", span, f"allowed_recipients:{r}")
    if not m.allowed_recipients:
        add("limit", "May not email or share anything with anyone")
    for g in m.grants:
        if g.field == ADDON_GRANT:
            add("grant", f"Add-on {g.value} is requested", g.span, f"{ADDON_GRANT}:{g.value}")
    for issue in report.issues:
        if issue.code != "CLARIFICATION_NEEDED":
            add(
                "not_granted",
                f"Not granted: {issue.field} ({issue.code}) - ask the user",
                issue.detail,
            )
    for q in report.clarifications:
        add("question", q)
    return out


def narrow(mandate: dict, field: str) -> dict:
    """The mandate (as JSON) without one widening permission the user declined in the readback
    (D-037). `field` is a readback grant's `field`. Only ever narrows, so it still grounds."""
    m = {**mandate, "grants": list(mandate.get("grants", []))}
    name, _, value = field.partition(":")

    def drop(grant_field: str, grant_value: str | None = None) -> None:
        m["grants"] = [
            g
            for g in m["grants"]
            if not (
                g["field"] == grant_field and (grant_value is None or g["value"] == grant_value)
            )
        ]

    if name == "allowed_actions":
        m["allowed_actions"] = [a for a in m["allowed_actions"] if a != value]
        drop(field)
    elif name == "auto_approve_under_paise":
        m["auto_approve_under_paise"] = 0
        drop(name)
    elif name == "allow_recurring":
        m["allow_recurring"] = False
        drop(name)
    elif name == "allowed_recipients":
        m["allowed_recipients"] = [r for r in m["allowed_recipients"] if r != value]
        drop(name, value)
    elif name == ADDON_GRANT:
        drop(name, value)
    else:
        raise ValueError(f"not a widening permission: {field}")
    return m
