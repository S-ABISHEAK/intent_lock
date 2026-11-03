"""Requirements in the user's words that IntentLock cannot enforce (D-058).

The mandate has fields for budget, sellers, subscriptions, refundability, quantity, add-ons and
recipients. Product specifications, processors, screen sizes, colours and brands are not checked
against the quote, so they are named in the readback ("Not enforced …") instead of being dropped
silently. Deterministic, information only: nothing here narrows or widens the mandate.
"""

import re

PATTERNS = [
    r"\b\d+(?:\.\d+)?\s?(?:GB|TB)\s?(?:RAM|SSD|HDD|storage|memory|DDR\d)\b",
    r"\b\d+(?:\.\d+)?\s?(?:inch(?:es)?\b|in\.|\")",
    r"\b(?:core\s?)?i[3579]\b",
    r"\bryzen\s?\d\b",
    r"\b(?:black|white|silver|grey|gray|blue|red|green|gold|pink)\b",
    r"\b(?:asus|lenovo|hp|dell|acer|apple|macbook|samsung|logitech|microsoft|msi|sony|xiaomi"
    r"|redmi|oneplus|zebronics|boat)\b",
]
_RX = re.compile("|".join(f"(?:{p})" for p in PATTERNS), re.IGNORECASE)


def unenforced_phrases(user_text: str) -> list[str]:
    """Each unenforceable requirement as the user wrote it, once, in order of appearance."""
    out, seen = [], set()
    for m in _RX.finditer(user_text):
        phrase = m.group(0).strip()
        if phrase.lower() not in seen:
            seen.add(phrase.lower())
            out.append(phrase)
    return out
