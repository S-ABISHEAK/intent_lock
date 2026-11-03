"""Cross-check of Muse's screenshot reading against the page's own rendered text (D-057).

The checkout page is merchant-controlled; text on it can be aimed at the vision model. The same
offline browser render that takes the screenshot also gives the page's visible text (hidden
elements excluded). Python checks that Muse's reading is consistent with that text. Neither
reading is an authority: a contradiction only escalates to REVIEW `VISION_DOM_DISAGREE`.
"""

import re

from intentlock.models import PageFacts, Reason
from intentlock.policy.reasons import make_reason
from intentlock.verify.two_channel import address_matches

AMOUNT_RE = re.compile(r"₹\s?(\d[\d,]*)(?:\.(\d{1,2}))?")
RECURRING_RE = re.compile(
    r"recurring|subscription|subscribe|per month|/\s?month|monthly|renews?|auto-?renew", re.I
)
NON_REFUNDABLE_RE = re.compile(r"non-?refundable|all sales final|no returns|not returnable", re.I)


def amounts_paise(text: str) -> set[int]:
    out = set()
    for whole, frac in AMOUNT_RE.findall(text):
        out.add(int(whole.replace(",", "")) * 100 + int((frac or "0").ljust(2, "0")))
    return out


def dom_disagreements(facts: PageFacts, dom_text: str) -> list[Reason]:
    problems = []
    total = facts.displayed_total_paise
    if total is not None and total not in amounts_paise(dom_text):
        problems.append(f"total {total} is not an amount on the page")
    if not facts.recurring_text.strip() and RECURRING_RE.search(dom_text):
        problems.append("the page mentions a subscription or recurring charge the reading missed")
    if facts.refund_policy == "refundable" and NON_REFUNDABLE_RE.search(dom_text):
        problems.append("the page says non-refundable but the reading says refundable")
    shown = facts.delivery_address.strip()
    if shown and not address_matches(dom_text, shown):
        problems.append("the reading's delivery address is not on the page")
    if not problems:
        return []
    return [make_reason("VISION_DOM_DISAGREE", "; ".join(problems))]
