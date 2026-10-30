"""Deterministic span grounding (SPEC §9; CLAUDE.md invariant 10).

Today: requested add-on grants (D-031). An add-on counts as requested only through a grant
`{"field": "requested_addon", "value": SKU, "span": ...}` whose span is a (normalized) substring of
the user's text, contains that SKU, and contains no negation: "No gift wrap (GW-0799)" never asks
for gift wrap. The full validator for every widening field arrives with the compiler (Nov 1).
"""

import re

from intentlock.models import Mandate
from intentlock.provenance.taint import clean_text, extract_tokens, normalize_value

ADDON_GRANT = "requested_addon"
NEGATION = re.compile(
    r"\b(no|not|don'?t|do\s+not|doesn'?t|without|never|except|excluding|exclude|skip|avoid|nor)\b",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    return " ".join(clean_text(text).lower().split())


def span_in_text(span: str, user_text: str) -> bool:
    span_n = normalize_text(span)
    return bool(span_n) and span_n in normalize_text(user_text)


def granted_addons(mandate: Mandate | None) -> frozenset[str]:
    if mandate is None:
        return frozenset()
    out = set()
    for grant in mandate.grants:
        if grant.field != ADDON_GRANT:
            continue
        sku = normalize_value(grant.value)
        if not span_in_text(grant.span, mandate.user_text):
            continue
        if sku not in extract_tokens(grant.span) or NEGATION.search(clean_text(grant.span)):
            continue
        out.add(sku)
    return frozenset(out)
