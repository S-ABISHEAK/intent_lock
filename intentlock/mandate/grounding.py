"""Deterministic span grounding (SPEC §9; CLAUDE.md invariant 10).

Today: requested add-on grants (D-031, D-032). An add-on counts as requested only through a grant
`{"field": "requested_addon", "value": SKU, "span": ...}` where, deterministically:
- the span is a normalized substring of the user's text and contains the SKU as a whole token;
- the SKU is a whole token of the user's text (so "PP-10012" never grants PP-1001);
- some sentence of the user's text that names the SKU says so affirmatively (add, with, include,
  plus, want, also, ...), and no sentence that names it contains a negation.
The user's sentences decide, not the span the compiler chose to quote, so quoting "GW-0799" out of
"No gift wrap (GW-0799)" grants nothing. This is an allow-list: anything unclear is not granted and
belongs in `clarifications_needed`. The full validator arrives with the compiler (Nov 1).
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from intentlock.models import Grant, Mandate
from intentlock.policy.tools import COMMIT_TOOLS
from intentlock.provenance.taint import clean_text, extract_tokens, normalize_value

ADDON_GRANT = "requested_addon"
_APOSTROPHES = dict.fromkeys(map(ord, "\u2019\u2018\u02bc\u00b4\u2032`"), "'")
NEGATION = re.compile(
    r"n't\b|\b(no|not|none|nothing|never|nor|without|except|excluding|exclude|skip|avoid|remove"
    r"|removing|cancel|decline|drop|refuse|nahi|nahin|mat)\b|\bw/o\b",
    re.IGNORECASE,
)
AFFIRMATIVE = re.compile(
    r"\b(add|adding|include|including|with|plus|want|wants|also|get|buy|need|yes)\b",
    re.IGNORECASE,
)
# A sentence ends at punctuation followed by space or the end, so "asha@example.com" stays whole.
SENTENCE_END = re.compile(r"[.;!?]+(?=\s|$)|\n+")


def normalize_text(text: str) -> str:
    return " ".join(clean_text(text).translate(_APOSTROPHES).lower().split())


def span_in_text(span: str, user_text: str) -> bool:
    span_n = normalize_text(span)
    return bool(span_n) and span_n in normalize_text(user_text)


def _sentences_naming(sku: str, user_text: str) -> list[str]:
    text = clean_text(user_text).translate(_APOSTROPHES)
    return [s for s in SENTENCE_END.split(text) if sku in extract_tokens(s)]


def addon_granted(sku: str, span: str, user_text: str) -> bool:
    if sku not in extract_tokens(user_text) or sku not in extract_tokens(span):
        return False
    if not span_in_text(span, user_text):
        return False
    sentences = _sentences_naming(sku, user_text)
    if any(NEGATION.search(s) for s in sentences):
        return False
    return any(AFFIRMATIVE.search(s) for s in sentences)


def granted_addons(mandate: Mandate | None) -> frozenset[str]:
    if mandate is None:
        return frozenset()
    return frozenset(
        normalize_value(g.value)
        for g in mandate.grants
        if g.field == ADDON_GRANT
        and addon_granted(normalize_value(g.value), g.span, mandate.user_text)
    )


# ================================ the full validator (SPEC §9, D-033) ===========================


BASE_ACTIONS = ("search", "read")
CART_ACTIONS = ("add_to_cart",)  # moves no money; allowed alongside any purchase
_MULT = {"k": 1_000, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
         "crore": 10_000_000, "cr": 10_000_000}  # fmt: skip
MONEY_RE = re.compile(
    r"(?<![\w-])(?:\u20b9|rs\.?|inr)?\s*(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(k|thousand|lakhs?|lac|crore|cr)?\b",
    re.IGNORECASE,
)
NEGATION_CORE = re.compile(
    r"n't\b|\b(no|not|none|never|nor|without|nahi|nahin|mat)\b|\bw/o\b", re.IGNORECASE
)
APPROVAL_PHRASES = re.compile(
    r"\b(without (asking|approval|confirmation|checking|permission)( me)?|no need to ask"
    r"|(don'?t|do not) (ask|check with) me)\b",
    re.IGNORECASE,
)
ACTION_WORDS = {
    "checkout": r"\b(buy|buying|purchase|order|checkout|check out|pay|get me|get it)\b",
    "send_email": r"\b(e-?mail|mail|send|message|write to)\b",
    "share_cart": r"\b(share|send|e-?mail|mail)\b",
    "delete_saved_address": r"\b(delete|remove|forget)\b",
    "cancel_order": r"\bcancel\b",
}
AUTO_WORDS = re.compile(
    r"without (asking|approval|confirmation|checking)|(don'?t|do not|no need to) ask|automatically"
    r"|auto-?approve|go ahead|\b(under|below|less than|up ?to|over|above|before)\b",
    re.IGNORECASE,
)
RECURRING_WORDS = re.compile(
    r"\b(subscriptions?|subscribe|recurring|monthly|yearly|annual|plan|renews?|auto-?renew)\b",
    re.IGNORECASE,
)
COUNTRIES = {
    "IN": re.compile(r"\b(india|indian)\b", re.IGNORECASE),
    "SG": re.compile(r"\b(singapore|singaporean)\b", re.IGNORECASE),
    "US": re.compile(r"\bUS\b|\b(usa|united states|america|american)\b", re.IGNORECASE),
}


def parse_money_paise(text: str) -> list[int]:
    """Every amount in the text as integer paise (§9.3): ₹2,000 / rs 2000 / 2k / 2 thousand /
    1.5 lakh / ₹1,00,000. Digits inside identifiers (LAP-1201) are not amounts."""
    out = set()
    for m in MONEY_RE.finditer(clean_text(text)):
        try:
            rupees = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            continue
        mult = _MULT.get((m.group(2) or "").lower(), 1)
        paise = rupees * mult * 100
        if paise >= 1 and paise == paise.to_integral_value():
            out.add(int(paise))
    return sorted(out)


def _normalize_9_2(text: str) -> str:
    """§9.2: lowercase, collapse whitespace, ₹/rs/inr as one token, no commas inside numbers."""
    t = clean_text(text).translate(_APOSTROPHES).lower()
    t = re.sub(r"(₹|\brs\.?|\binr\b)\s*", " inr ", t)
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    return " ".join(t.split())


def _span_in(span: str, user_text: str) -> bool:
    s = _normalize_9_2(span)
    return bool(s) and s in _normalize_9_2(user_text)


def _span_sentences(span: str, user_text: str) -> list[str]:
    s = _normalize_9_2(span)
    sentences = [x for x in SENTENCE_END.split(user_text) if s in _normalize_9_2(x)]
    return sentences or [user_text]  # a span across sentences: judge the whole text


def _negated(span: str, user_text: str) -> bool:
    return any(
        NEGATION_CORE.search(APPROVAL_PHRASES.sub(" ", clean_text(s).translate(_APOSTROPHES)))
        for s in _span_sentences(span, user_text)
    )


@dataclass(frozen=True)
class Issue:
    field: str
    code: str
    detail: str


@dataclass
class GroundingReport:
    mandate: Mandate
    issues: list[Issue] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)


def _check(grant: Grant | None, user_text: str, *, words=None, negation=True, number=None):
    """None if the grant is valid, else the readback code that reverts it."""
    if grant is None:
        return "GRANT_MISSING_SPAN"
    if not _span_in(grant.span, user_text):
        return "GRANT_SPAN_NOT_IN_TEXT"
    if number is not None and number not in parse_money_paise(grant.span):
        return "GRANT_NUMBER_MISMATCH"
    if words is not None and not re.search(words, clean_text(grant.span).translate(_APOSTROPHES),
                                           re.IGNORECASE):  # fmt: skip
        return "GRANT_KEYWORD_MISSING"
    if negation and _negated(grant.span, user_text):
        return "GRANT_NEGATED"
    return None


def ground(m: Mandate) -> GroundingReport:
    """Every widening field needs a valid grant or is reverted to the baseline (SPEC §9 rules 1-4);
    narrowing fields are accepted (rule 5) except a budget the user never stated, which becomes a
    clarification (D-033); clarifications block confirmation (rule 6)."""
    text, issues, kept, questions = m.user_text, [], [], list(m.clarifications_needed)

    def find(field_name: str, value: str | None = None) -> Grant | None:
        for g in m.grants:
            if g.field == field_name and (
                value is None or g.value.strip().lower() == value.lower()
            ):
                return g
        return None

    def note(field_name: str, code: str, detail: str = "") -> None:
        issues.append(Issue(field_name, code, detail))

    known = {f"allowed_actions:{a}" for a in COMMIT_TOOLS} | {
        "auto_approve_under_paise", "allow_recurring", "allowed_recipients", ADDON_GRANT,
    }  # fmt: skip
    for g in m.grants:
        if g.field not in known:
            note(g.field, "GRANT_UNKNOWN", g.span)

    # rule 1-4: actions
    commits: list[str] = []
    for a in m.allowed_actions:
        if a in BASE_ACTIONS or a in CART_ACTIONS:
            continue
        if a not in COMMIT_TOOLS:
            note(f"allowed_actions:{a}", "GRANT_UNKNOWN", a)
            continue
        g = find(f"allowed_actions:{a}")
        code = _check(g, text, words=ACTION_WORDS[a])
        if code:
            note(f"allowed_actions:{a}", code, g.span if g else "")
        else:
            commits.append(a)
            kept.append(g)
    cart = "add_to_cart" in m.allowed_actions or "checkout" in commits  # a purchase needs a cart
    actions = [*BASE_ACTIONS, *(["add_to_cart"] if cart else []), *commits]

    # auto-approve limit: number must match, approval wording, no refusal check ("don't ask me")
    auto = m.auto_approve_under_paise
    if auto > 0:
        g = find("auto_approve_under_paise")
        code = _check(g, text, words=AUTO_WORDS.pattern, negation=False, number=auto)
        if g is not None and code is None and g.value.strip() != str(auto):
            code = "GRANT_NUMBER_MISMATCH"
        if code:
            note("auto_approve_under_paise", code, g.span if g else "")
            auto = 0
        else:
            kept.append(g)

    recurring = m.allow_recurring
    if recurring:
        g = find("allow_recurring")
        code = _check(g, text, words=RECURRING_WORDS.pattern)
        if code:
            note("allow_recurring", code, g.span if g else "")
            recurring = False
        else:
            kept.append(g)

    recipients = []
    for r in m.allowed_recipients:
        g = find("allowed_recipients", r)
        code = _check(g, text)
        if code is None and normalize_value(r) not in extract_tokens(g.span):
            code = "GRANT_KEYWORD_MISSING"
        if code:
            note(f"allowed_recipients:{r}", code, g.span if g else "")
        else:
            recipients.append(r)
            kept.append(g)

    for g in m.grants:
        if g.field == ADDON_GRANT:
            if addon_granted(normalize_value(g.value), g.span, text):
                kept.append(g)
            else:
                note(f"requested_addon:{g.value}", "GRANT_NEGATED", g.span)

    # narrowing: seller countries (trim to what the user named, if they named any)
    countries = m.allowed_seller_countries
    if countries is not None:
        named = {c for c, rx in COUNTRIES.items() if rx.search(text)}
        if named:
            trimmed = [c for c in countries if c.upper() in named]
            for c in countries:
                if c.upper() not in named:
                    note(f"allowed_seller_countries:{c}", "GRANT_SPAN_NOT_IN_TEXT", c)
            if not trimmed and countries:
                questions.append("Which seller countries are allowed?")
                note("allowed_seller_countries", "CLARIFICATION_NEEDED", "")
            countries = trimmed

    # narrowing: budget must be an amount the user actually said
    if m.budget_total_paise is not None and m.budget_total_paise not in parse_money_paise(text):
        rupees = m.budget_total_paise // 100
        questions.append(f"Your request doesn't state a budget of ₹{rupees:,}; what is it?")
        note("budget_total_paise", "CLARIFICATION_NEEDED", str(m.budget_total_paise))

    seen, grants = set(), []
    for g in kept:
        key = (g.field, g.value, g.span)
        if key not in seen:
            seen.add(key)
            grants.append(g)
    out = m.model_copy(
        update={
            "allowed_actions": actions,
            "auto_approve_under_paise": auto,
            "allow_recurring": recurring,
            "allowed_recipients": recipients,
            "allowed_seller_countries": countries,
            "grants": grants,
            "clarifications_needed": list(dict.fromkeys(questions)),
        }
    )
    return GroundingReport(out, issues, out.clarifications_needed)
