"""Per-field trust labels for tool results (SPEC §7.1; D-028, D-029).

Applied by the proxy before any result reaches the agent (invariant 9). Labels follow a field's
exact position ("results[].sku"), never just its key name, so a nested `id` or `sku` inside
untrusted content can't pass as a merchant fact. Anything not explicitly trusted is UNTRUSTED:
unknown tools, unknown or nested fields, error text, identifiers used as dict keys.

Cart and order echoes (`add_to_cart`, `remove_from_cart`, commit results) repeat the agent's own
choices, so they are recorded but are never a source for any identifier (D-029). Likewise email ids
(set by whoever writes the email) and seller-written titles supply no identifiers, and a product
`url` supplies only its URL token (D-030).
The agent also gets the labels inline as `_trust`; security never depends on it reading them.
"""

import copy
import re
from dataclasses import dataclass
from typing import Any

from intentlock.models import TrustLabel

MERCHANT_FACTS = ("sku", "title", "price_paise", "seller_id", "seller_country", "stock", "url")
TRUSTED_SHAPES: dict[str, frozenset[str]] = {
    "search_products": frozenset(f"results[].{k}" for k in MERCHANT_FACTS),
    "get_product": frozenset({*MERCHANT_FACTS, "recurring.amount_paise", "recurring.interval"}),
    "read_email": frozenset({"id"}),
    "read_inbox": frozenset({"emails[].id"}),
}
SENSITIVE_TOOLS = frozenset({"get_saved_addresses", "get_order_history", "get_contacts"})
CART_TOOLS = frozenset({"add_to_cart", "remove_from_cart"})
SCHEMA_KEY = re.compile(r"^[a-z_][a-z0-9_]*$")  # ordinary field names; anything else is content


@dataclass(frozen=True)
class LabeledField:
    field_path: str
    label: TrustLabel
    raw: str
    is_text: bool  # numbers (prices, stock, qty) are never searched for identifier tokens
    sources: str = "all"  # which identifiers this field may supply: "all", "url" or "none"


def source_policy(tool: str, path: str, label: TrustLabel) -> str:
    """Trusted fields only vouch for the identifiers they are about (D-030). Untrusted fields keep
    every token, so taint is as wide as possible."""
    if tool in CART_TOOLS:
        return "none"  # the agent's own choices echoed back
    if label != TrustLabel.TRUSTED_API:
        return "all"
    key = shape(path).rsplit(".", 1)[-1]
    if tool in ("read_email", "read_inbox") and key == "id":
        return "none"  # chosen by whoever wrote the email
    if key == "title":
        return "none"  # seller-written text
    if key == "url":
        return "url"
    return "all"


def shape(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)


def field_label(tool: str, path: str) -> TrustLabel:
    if tool in SENSITIVE_TOOLS:
        return TrustLabel.SENSITIVE
    if tool in CART_TOOLS:
        return TrustLabel.TRUSTED_API  # displayed as merchant data, but never a source
    if shape(path) in TRUSTED_SHAPES.get(tool, frozenset()):
        return TrustLabel.TRUSTED_API
    return TrustLabel.UNTRUSTED  # deny by default


def _walk(value: Any, path: str):
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{k}" if path else str(k)
            if not SCHEMA_KEY.match(str(k)):
                yield f"{child}#key", str(k), True  # identifier-looking keys are content too
            yield from _walk(v, child)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    elif value is not None and not isinstance(value, bool):
        yield path, value, False


def label_fields(tool: str, result: dict[str, Any], *, error: bool = False) -> list[LabeledField]:
    if error:
        return [LabeledField("error", TrustLabel.UNTRUSTED, str(result.get("error", "")), True)]
    out = []
    for path, value, is_key in _walk(result, ""):
        label = TrustLabel.UNTRUSTED if is_key else field_label(tool, path)
        policy = source_policy(tool, path, label)
        out.append(LabeledField(path, label, str(value), isinstance(value, str), policy))
    return out


def annotate(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    """A copy of `result` where every object lists its non-trusted fields under `_trust`."""

    def visit(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out, marks = {}, {}
            for k, v in node.items():
                if k == "_trust":
                    continue
                child = f"{path}.{k}" if path else str(k)
                out[k] = visit(v, child)
                if v is not None and not isinstance(v, (dict, list)):
                    label = field_label(tool, child)
                    if label != TrustLabel.TRUSTED_API:
                        marks[k] = label.value
            if marks:
                out["_trust"] = marks
            return out
        if isinstance(node, list):
            return [visit(v, f"{path}[{i}]") for i, v in enumerate(node)]
        return node

    return visit(copy.deepcopy(result), "")
