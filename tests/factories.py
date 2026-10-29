"""Shared builders for pipeline tests."""

from intentlock.canonical import compute_quote_hash
from intentlock.models import ActionProposal, Mandate, Quote, QuoteLine, RecurringTerm

NOW = 1_000_000


def make_mandate(**overrides) -> Mandate:
    data = dict(
        mandate_id="m1",
        user_text="Find a laptop under ₹60,000 all-in from an Indian seller.",
        goal="buy laptop",
        allowed_actions=["search", "read", "add_to_cart", "checkout"],
        budget_total_paise=6_000_000,
        auto_approve_under_paise=0,
        allowed_seller_countries=["IN"],
        allow_recurring=False,
        require_refundable=False,
        max_quantity=1,
        allowed_recipients=[],
        grants=[],
        clarifications_needed=[],
        policy_version=1,
        user_confirmed=True,
        expires_at=NOW + 3600,
    )
    data.update(overrides)
    return Mandate(**data)


def make_line(**overrides) -> QuoteLine:
    data = dict(
        sku="LAP-1201",
        title="Asus Vivobook 15",
        qty=1,
        unit_paise=5_499_900,
        seller_id="SEL-IN01",
        seller_country="IN",
        is_addon=False,
    )
    data.update(overrides)
    return QuoteLine(**data)


def make_quote(lines=None, recurring=None, fees=0, tax=0, total=None, **overrides) -> Quote:
    lines = lines if lines is not None else [make_line()]
    if total is None:
        total = sum(line.unit_paise * line.qty for line in lines) + fees + tax
    data = dict(
        quote_id="Q-test",
        cart_id="c1",
        lines=lines,
        fees_paise=fees,
        tax_paise=tax,
        total_paise=total,
        recurring=recurring or [],
        shipping_address_id="ADDR-0001",
        checkout_url="http://localhost:8001/checkout/c1",
        quote_hash="",
    )
    data.update(overrides)
    q = Quote(**data)
    return q.model_copy(update={"quote_hash": compute_quote_hash(q)})


def monthly(sku="PP-1001", amount=29_900) -> RecurringTerm:
    return RecurringTerm(sku=sku, amount_paise=amount, interval="month")


def make_proposal(tool="checkout", **args) -> ActionProposal:
    if tool == "checkout" and not args:
        args = {"cart_id": "c1", "shipping_address_id": "ADDR-0001"}
    return ActionProposal(action_id="a1", session_id="s1", tool=tool, args=args, ts=NOW)
