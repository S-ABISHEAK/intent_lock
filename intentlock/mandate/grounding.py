"""Deterministic span grounding (SPEC §9; CLAUDE.md invariant 10).

Every widening permission needs a grant whose span is verbatim in the user's own request, sits in
one clause that names the permission, is not a question, and is not refused or retracted anywhere
(D-033, D-036). Skipping approval is stricter still: the clause must be one of a few fixed sentence
shapes (D-037). Typed answers to clarification questions follow the request after `ANSWERS_MARK`;
they can narrow the mandate (budget, countries, refusals) but never widen it.

Requested add-ons (D-031, D-032) count only through a `requested_addon` grant whose SKU is a whole
token of the request, stated affirmatively in a sentence that is not a question, with no sentence
naming it negated. This is an allow-list: anything unclear is not granted and becomes a question.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from intentlock.models import Grant, Mandate
from intentlock.policy.tools import COMMIT_TOOLS
from intentlock.provenance.taint import clean_text, extract_tokens, normalize_value

ADDON_GRANT = "requested_addon"
# The compiler appends the user's typed answers after this line (D-037).
ANSWERS_MARK = "\n\n[answers]\n"
_APOSTROPHES = dict.fromkeys(map(ord, "’‘ʼ´′`"), "'")
# Refusal words (D-036, D-037). Verbs like remove/cancel/delete are refusals except for the grant
# that is about exactly that verb (deleting an address, cancelling an order).
NEG_CORE = (
    r"n't\b|\b(dont|cant|wont|cannot|no|not|none|nothing|never|nor|without|except|excluding"
    r"|exclude|skip|avoid|refrain|stop|hate|dislike|ignore|later|myself|refuse|decline|nahi"
    r"|nahin|mat|na|nope|nah|off|wait|disable[ds]?|disabling|prohibit(s|ed)?|forbid(s|den)?"
    r"|ban(s|ned)?)\b|\bw/o\b"
)
NEG_VERBS = r"\b(remove|removing|cancel|cancelling|canceling|drop|delete)\b"
NEGATION = re.compile(f"{NEG_CORE}|{NEG_VERBS}", re.IGNORECASE)
# Taking back what was said: revokes every widening permission in the request.
RETRACTION = re.compile(
    r"\b(scratch that|never ?mind|forget (it|that|about it|all that)|on second thought"
    r"|actually,? no|nope|nah|cancel that|ignore that|disregard (that|this|all that)"
    r"|i take (it|that) back|(i )?changed my mind)\b",
    re.IGNORECASE,
)
AFFIRMATIVE = re.compile(
    r"\b(add|adding|include|including|with|plus|want|wants|also|get|buy|need|yes)\b",
    re.IGNORECASE,
)
# A sentence ends at punctuation followed by space or the end, so "asha@example.com" stays whole.
SENTENCE_END = re.compile(r"([.;!?]+(?=\s|$)|\n+)")
CLAUSE_END = re.compile(r":(?=\s|$)|,\s+|\s+but\s+", re.IGNORECASE)


def _prep(text: str) -> str:
    return clean_text(text).translate(_APOSTROPHES)


def normalize_text(text: str) -> str:
    return " ".join(_prep(text).lower().split())


def span_in_text(span: str, user_text: str) -> bool:
    span_n = normalize_text(span)
    return bool(span_n) and span_n in normalize_text(user_text)


def split_answers(user_text: str) -> tuple[str, str]:
    """(the user's request, their typed answers)."""
    request, _, answers = user_text.partition(ANSWERS_MARK)
    return request, answers


def _sentences(text: str) -> Iterator[tuple[str, bool]]:
    """Each sentence with whether it is a question."""
    parts = SENTENCE_END.split(_prep(text))
    for i in range(0, len(parts), 2):
        end = parts[i + 1] if i + 1 < len(parts) else ""
        if parts[i].strip():
            yield parts[i], "?" in end


def _units(text: str) -> list[tuple[str, bool]]:
    """Each clause with whether its sentence is a question."""
    return [
        (c.strip(), q) for s, q in _sentences(text) for c in CLAUSE_END.split(s) if c and c.strip()
    ]


def clauses(text: str) -> list[str]:
    return [c for c, _ in _units(text)]


def _retracted(request: str) -> bool:
    return bool(RETRACTION.search(_prep(request)))


def addon_granted(sku: str, span: str, user_text: str) -> bool:
    request, _ = split_answers(user_text)
    if sku not in extract_tokens(request) or sku not in extract_tokens(span):
        return False
    if not span_in_text(span, request) or _retracted(request):
        return False
    if any(NEGATION.search(s) for s, _ in _sentences(user_text) if sku in extract_tokens(s)):
        return False
    naming = [(s, q) for s, q in _sentences(request) if sku in extract_tokens(s)]
    if any(q for _, q in naming):
        return False
    return any(AFFIRMATIVE.search(s) for s, _ in naming)


def granted_addons(mandate: Mandate | None) -> frozenset[str]:
    if mandate is None:
        return frozenset()
    return frozenset(
        normalize_value(g.value)
        for g in mandate.grants
        if g.field == ADDON_GRANT
        and addon_granted(normalize_value(g.value), g.span, mandate.user_text)
    )


# ================================ the full validator (SPEC §9; D-033, D-036, D-037) ===========

BASE_ACTIONS = ("search", "read")
CART_ACTIONS = ("add_to_cart",)  # moves no money; allowed alongside any purchase
_MULT = {"k": 1_000, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
         "crore": 10_000_000, "cr": 10_000_000}  # fmt: skip
_FOREIGN = r"us\$|s\$|\$|€|£|¥|usd|sgd|eur|gbp|jpy|npr|aed"
_FOREIGN_UNIT = r"dollars?|euros?|pounds?|yen|usd|sgd|eur|gbp|jpy|npr|aed"
MONEY_RE = re.compile(
    rf"(?<![\w-])(?P<pre>₹|rs\.?|inr|{_FOREIGN})?\s*"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<mult>k|thousand|lakhs?|lac|crore|cr)?\b"
    rf"(?:\s*(?P<unit>paise|paisa|rupees?|rs|inr|{_FOREIGN_UNIT})\b)?",
    re.IGNORECASE,
)
_FOREIGN_RE = re.compile(rf"^(?:{_FOREIGN}|{_FOREIGN_UNIT})$", re.IGNORECASE)
# "without asking" / "don't ask me" are approval wording, not refusals, unless they go on to
# "...ask me to buy / whether / if / for it / about it" (then they are a refusal).
APPROVAL_PHRASE = re.compile(
    r"\b(without (asking|approval|confirmation|checking|permission)( me)?( first)?"
    r"|without (my|your) (approval|confirmation|permission)"
    r"|no need to ask( me)?( first)?|(do not|don't|dont) (need to )?ask( me)?( first)?)\b"
    r"(?!\s+(me\s+)?(to|whether|if|for|about|from|on)\b)",
    re.IGNORECASE,
)
AUTO_APPROVE = re.compile(r"\bauto-?approve[sd]?\b", re.IGNORECASE)
ASK_BEFORE_OVER = re.compile(
    r"\bask me (first )?(before|for)\b.*\b(over|above|more than)\b", re.IGNORECASE
)
# The only sentence shapes that can skip approval (D-037), matched against the whole clause after
# §9.2 normalization. `items` is checked for refusals; nothing may follow the amount except the
# approval wording itself.
_AMT = r"(?:inr\s*)?\d+(?:\.\d+)?\s*(?:k|thousand|lakhs?|lac|crore|cr)?(?:\s*(?:rupees?|paise|paisa|inr))?"  # noqa: E501
_ITEM_WORD = r"(?!(?:under|below|over|above|up|less|more|than|within|or|and|if|but)\b)\w[\w'/-]*"
_ITEMS = rf"(?P<items>(?:{_ITEM_WORD}\s+){{0,5}}?)"
_UNDER = r"(?:under|below|up ?to|less than|within)"
_ASKING = (
    r"without (?:asking|approval|confirmation|checking|permission)(?: me)?(?: first)?"
    r"|without (?:my|your) (?:approval|confirmation|permission)"
)
_BUYING = r"(?:buying|purchasing|ordering|getting|you buy)"
_SHAPES = (
    rf"(?:you can |you may |feel free to |go ahead and |just )?(?:buy|purchase|order|get)\s+"
    rf"{_ITEMS}{_UNDER}\s+{_AMT}\s+(?:{_ASKING})",
    rf"(?:you can |you may )?auto-?approve\s+{_ITEMS}{_UNDER}\s+{_AMT}",
    rf"ask me (?:first )?before {_BUYING}\s+{_ITEMS}(?:over|above|more than)\s+{_AMT}",
    rf"(?:(?:there is |there's )?no need to|you (?:don't|dont|do not) need to|don't|dont|do not)"
    rf" ask(?: me)?(?: first)?\s+(?:before {_BUYING}|for)\s+{_ITEMS}{_UNDER}\s+{_AMT}",
)
AUTO_SHAPES = tuple(
    re.compile(rf"(?:(?:and|also|please|then|so|ok|okay|plus)\s+)*(?:{s})(?:\s+(?:please|first))?")
    for s in _SHAPES
)
BUDGET_WORD = re.compile(
    r"\b(budget|under|below|within|max|maximum|at most|up ?to|less than|no more than"
    r"|not more than|all-in|ceiling|cap|limit)\b",
    re.IGNORECASE,
)
# "no more than ₹X" states a budget; it is not a refusal of the amount.
_LIMIT_NEGATION = re.compile(
    r"\b(no|not|never)\s+(more|higher|greater|over|above|exceeding)\b(\s+than)?", re.IGNORECASE
)
CONCEPTS = {
    "checkout": (
        r"\b(buy|buying|purchase|purchasing|order|ordering|checkout|check out|pay|get me|get it)\b"
    ),
    "send_email": r"\b(e-?mail|mail|send|message|write to)\b",
    "share_cart": r"\b(share|send|e-?mail|mail)\b",
    "delete_saved_address": r"\b(delete|remove|forget)\b",
    "cancel_order": r"\bcancel\b",
    "recurring": (
        r"\b(subscriptions?|subscribe|recurring|monthly|yearly|annual|plans?|renews?|auto-?renew)\b"
    ),
}  # fmt: skip
# Granting recurring needs a subscription noun and a permission word in the same clause (D-037);
# any clause that names the broader concept and refuses it still revokes.
RECURRING_GRANT = (
    r"(?=.*\b(subscriptions?|subscribe|recurring|auto-?renew\w*|renews?|memberships?"
    r"|(monthly|yearly|annual|phone|data|mobile|protection|service)\s+(\w+\s+)?plans?)\b)"
    r"(?=.*\b(get|buy|subscribe|sign( me)? up|want|like|ok(ay)?|fine|allow(ed)?|you can|you may"
    r"|go ahead|include|add|yes|happy)\b)"
)
OWN_VERBS = {"delete_saved_address": ("delete", "remove", "removing"), "cancel_order": ("cancel",)}
COUNTRY_NAMES = {
    "IN": r"india|indian",
    "SG": r"singapore|singaporean",
    "US": r"united states|americans?|america|(?-i:U\.?S\.?A?)",  # case-sensitive "US" only
}  # fmt: skip
_SELLER = r"(?:sellers?|vendors?|merchants?|shops?|stores?|retailers?|brands?|ones)"
KNOWN_COUNTRY_WORDS = {
    "india", "indian", "singapore", "singaporean", "us", "usa", "american", "america",
    "the", "local", "trusted", "good", "verified", "other",
}  # fmt: skip
COUNTRY_HINT = re.compile(r"\b([A-Z][a-z]+)\s+sellers?\b|\bsellers?\s+(?:from|in)\s+([A-Z][a-z]+)")
REFUND_WORDS = re.compile(r"\b(refundable|returnable|easy returns?|money[- ]back)\b", re.IGNORECASE)
_QTY_WORDS = {"one": 1, "single": 1, "a single": 1, "two": 2, "three": 3, "four": 4, "five": 5}
QTY_RE = re.compile(
    r"\b(\d+|one|a single|single|two|three|four|five)\s+(units?|items?|pieces?|pcs|laptops?"
    r"|mice|mouses?|phones?)\b",
    re.IGNORECASE,
)


def _money(text: str) -> Iterator[tuple[int, int | None]]:
    """(start, paise) for every amount; paise is None for a foreign currency (§9.3, D-037)."""
    for m in MONEY_RE.finditer(text):
        try:
            number = Decimal(m.group("num").replace(",", ""))
        except InvalidOperation:
            continue
        pre, unit = (m.group("pre") or "").lower(), (m.group("unit") or "").lower()
        if _FOREIGN_RE.match(pre) or _FOREIGN_RE.match(unit):
            yield m.start(), None
            continue
        paise = number * _MULT.get((m.group("mult") or "").lower(), 1)
        if unit not in ("paise", "paisa"):
            paise *= 100
        if paise >= 1 and paise == paise.to_integral_value():
            yield m.start(), int(paise)


def parse_money_paise(text: str) -> list[int]:
    """Every rupee amount in the text as integer paise (§9.3): ₹2,000 / rs 2000 / 2k / 2 thousand /
    1.5 lakh / ₹1,00,000 / 50,000 paise. Digits inside identifiers (LAP-1201) are not amounts, and
    foreign currencies are never read as rupees."""
    return sorted({p for _, p in _money(clean_text(text)) if p is not None})


def foreign_money(text: str) -> bool:
    return any(p is None for _, p in _money(clean_text(text)))


def _normalize_9_2(text: str) -> str:
    """§9.2: lowercase, collapse whitespace, ₹/rs/inr as one token, no commas inside numbers."""
    t = _prep(text).lower()
    t = re.sub(r"(₹|\brs\.?|\binr\b)\s*", " inr ", t)
    t = re.sub(r"(?<=\d),(?=\d)", "", t)
    return " ".join(t.split())


def _span_in(span: str, user_text: str) -> bool:
    s = _normalize_9_2(span)
    return bool(s) and s in _normalize_9_2(user_text)


def _span_unit(span: str, request: str) -> tuple[str, bool] | None:
    """The single clause of the request that contains the whole span (None if it crosses)."""
    s = _normalize_9_2(span)
    for c, q in _units(request):
        if s and s in _normalize_9_2(c):
            return c, q
    return None


def refuses(clause: str, *, keep: tuple[str, ...] = ()) -> bool:
    """A refusal in this clause, ignoring genuine approval wording and the grant's own verbs."""
    text = APPROVAL_PHRASE.sub(" ", clause)
    for verb in keep:
        text = re.sub(rf"\b{verb}\b", " ", text, flags=re.IGNORECASE)
    return bool(NEGATION.search(text))


def _concept_refused(concept: str, user_text: str, keep: tuple[str, ...] = ()) -> bool:
    return any(
        re.search(concept, c, re.IGNORECASE) and refuses(c, keep=keep) for c in clauses(user_text)
    )


def auto_shape(clause: str) -> re.Match | None:
    c = _normalize_9_2(clause)
    return next((m for rx in AUTO_SHAPES if (m := rx.fullmatch(c))), None)


def _auto_refused(clause: str) -> bool:
    shape = auto_shape(clause)
    if shape:
        return bool(NEGATION.search(shape.group("items")))
    approval = APPROVAL_PHRASE.search(clause) or AUTO_APPROVE.search(clause)
    return bool(approval or ASK_BEFORE_OVER.search(clause)) and refuses(
        AUTO_APPROVE.sub(" ", clause)
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


def _check_clause_grant(grant, request, full, concept, keep=(), grant_rx=None):
    """None if valid, else the readback code. The span must sit in one clause of the request, that
    clause must name the permission, not be a question, and nothing anywhere may refuse it."""
    if grant is None:
        return "GRANT_MISSING_SPAN"
    unit = _span_unit(grant.span, request) if _span_in(grant.span, request) else None
    if unit is None:
        return "GRANT_SPAN_NOT_IN_TEXT"
    clause, question = unit
    if not re.search(concept, clause, re.IGNORECASE):
        return "GRANT_KEYWORD_MISSING"
    if refuses(clause, keep=keep) or _concept_refused(concept, full, keep):
        return "GRANT_NEGATED"
    if grant_rx and not re.search(grant_rx, clause, re.IGNORECASE):
        return "GRANT_KEYWORD_MISSING"
    return "GRANT_IN_QUESTION" if question else None


def _check_auto(grant, request, full, amount):
    if grant is None:
        return "GRANT_MISSING_SPAN"
    unit = _span_unit(grant.span, request) if _span_in(grant.span, request) else None
    if unit is None:
        return "GRANT_SPAN_NOT_IN_TEXT"
    clause, question = unit
    if parse_money_paise(clause) != [amount] or grant.value.strip() != str(amount):
        return "GRANT_NUMBER_MISMATCH"
    if not auto_shape(clause):
        return "GRANT_KEYWORD_MISSING"  # only the fixed shapes skip approval (D-037)
    if any(_auto_refused(c) for c in clauses(full)):
        return "GRANT_NEGATED"
    return "GRANT_IN_QUESTION" if question else None


def _sends_to(clause: str, address: str) -> bool:
    """The address is the direct target of a send/share verb, not a possessive or a mention."""
    a = re.escape(address)
    end = r"(?!['\w@-])"
    rx = (
        rf"\b(?:send|share|e-?mail|mail|forward)\b"
        rf"(?:\s+(?!(?:along|together|plus|and|or|as)\b)[\w'-]+){{0,4}}?\s+(?:to|with)\s+{a}{end}"
        rf"|\b(?:e-?mail|mail|cc)\s+{a}{end}"
    )
    return bool(re.search(rx, clause.lower()))


def _check_recipient(grant, request, full, address):
    if grant is None:
        return "GRANT_MISSING_SPAN"
    if not _span_in(grant.span, request):
        return "GRANT_SPAN_NOT_IN_TEXT"
    addr = normalize_value(address)
    if any(refuses(c) for c in clauses(full) if addr in extract_tokens(c)):
        return "GRANT_NEGATED"
    unit = _span_unit(grant.span, request)
    if unit is None or not _sends_to(unit[0], addr):
        return "GRANT_KEYWORD_MISSING"
    return "GRANT_IN_QUESTION" if unit[1] else None


def _named(clause: str, seller_context: bool) -> set[str]:
    out = set()
    for code, name in COUNTRY_NAMES.items():
        if seller_context:
            rx = (
                rf"\b(?:{name})(?:-based)?\s+{_SELLER}\b"
                rf"|\b{_SELLER}\s+(?:from|in|based in)\s+(?:the\s+)?(?:{name})\b"
                rf"|\b(?:buy|order|purchase|get|source|ship(?:s|ped|ping)?)\s+(?:it\s+|them\s+"
                rf"|one\s+)?from\s+(?:an?\s+|the\s+)?(?:{name})\b"
            )
        else:
            rx = rf"\b(?:{name})s?\b"
        if re.search(rx, clause, re.IGNORECASE):
            out.add(code)
    return out


def _countries(request: str, answers: str) -> tuple[set[str], set[str]]:
    """(allowed, refused). The request names a country only as a seller (D-037); a typed answer
    may name it bare. A refusal of any mention removes it."""
    pos, neg = set(), set()
    for text, context in ((request, True), (answers, False)):
        for c in clauses(text):
            if refuses(c):
                neg |= _named(c, seller_context=False)
            else:
                pos |= _named(c, seller_context=context)
    return pos - neg, neg


def _unknown_country_hint(user_text: str) -> bool:
    for m in COUNTRY_HINT.finditer(_prep(user_text)):
        word = (m.group(1) or m.group(2) or "").lower()
        if word and word not in KNOWN_COUNTRY_WORDS:
            return True
    return False


def _budget_amounts(clause: str) -> list[int]:
    """Rupee amounts in the clause that are not themselves refused ("not ₹90,000")."""
    text, out = clean_text(clause), []
    for start, paise in _money(text):
        before = _LIMIT_NEGATION.sub(" ", text[:start]).split()[-3:]
        if paise is not None and not NEGATION.search(" ".join(before)):
            out.append(paise)
    return out


def _stated_budgets(request: str, answers: str) -> list[int]:
    """The smallest unrefused amount of each budget clause of the request (skip-approval shapes
    are not budgets), and of each typed answer."""
    out: set[int] = set()
    for c in clauses(request):
        if BUDGET_WORD.search(c) and not auto_shape(c) and (amounts := _budget_amounts(c)):
            out.add(min(amounts))
    for c in clauses(answers):
        if amounts := _budget_amounts(c):
            out.add(min(amounts))
    return sorted(out)


def _stated_quantity(user_text: str) -> int | None:
    found = []
    for c in clauses(user_text):
        for m in QTY_RE.finditer(c):
            word = m.group(1).lower()
            found.append(int(word) if word.isdigit() else _QTY_WORDS[word])
    return min(found) if found else None


def _stated_refundable(user_text: str) -> bool:
    return any(REFUND_WORDS.search(c) and not refuses(c) for c in clauses(user_text))


def ground(m: Mandate) -> GroundingReport:
    """Every widening field needs a valid grant or is reverted to the baseline (SPEC §9 rules 1-4);
    narrowing fields are accepted (rule 5) unless the draft drops or loosens something the user
    stated, which becomes a clarification (D-033, D-036); clarifications block confirmation.
    Widening comes only from the request; answers can only narrow; a retraction revokes (D-037)."""
    text, issues, kept, questions = m.user_text, [], [], list(m.clarifications_needed)
    request, answers = split_answers(text)
    retracted = _retracted(request)

    def find(field_name: str, value: str | None = None) -> Grant | None:
        for g in m.grants:
            if g.field == field_name and (
                value is None or g.value.strip().lower() == value.lower()
            ):
                return g
        return None

    def note(field_name: str, code: str, detail: str = "") -> None:
        issues.append(Issue(field_name, code, detail))

    def ask(question: str, field_name: str, detail: str = "") -> None:
        questions.append(question)
        note(field_name, "CLARIFICATION_NEEDED", detail)

    def gate(code: str | None) -> str | None:
        return code or ("GRANT_RETRACTED" if retracted else None)

    known = {f"allowed_actions:{a}" for a in COMMIT_TOOLS} | {
        "auto_approve_under_paise", "allow_recurring", "allowed_recipients", ADDON_GRANT,
    }  # fmt: skip
    for g in m.grants:
        if g.field not in known:
            note(g.field, "GRANT_UNKNOWN", g.span)

    commits: list[str] = []
    for a in m.allowed_actions:
        if a in BASE_ACTIONS or a in CART_ACTIONS:
            continue
        if a not in COMMIT_TOOLS:
            note(f"allowed_actions:{a}", "GRANT_UNKNOWN", a)
            continue
        g = find(f"allowed_actions:{a}")
        code = gate(_check_clause_grant(g, request, text, CONCEPTS[a], OWN_VERBS.get(a, ())))
        if code:
            note(f"allowed_actions:{a}", code, g.span if g else "")
        else:
            commits.append(a)
            kept.append(g)
    cart = "add_to_cart" in m.allowed_actions or "checkout" in commits  # a purchase needs a cart
    actions = [*BASE_ACTIONS, *(["add_to_cart"] if cart else []), *commits]

    auto = m.auto_approve_under_paise
    if auto > 0:
        g = find("auto_approve_under_paise")
        code = gate(_check_auto(g, request, text, auto))
        if code:
            note("auto_approve_under_paise", code, g.span if g else "")
            auto = 0
        else:
            kept.append(g)

    recurring = m.allow_recurring
    if recurring:
        g = find("allow_recurring")
        code = gate(
            _check_clause_grant(g, request, text, CONCEPTS["recurring"], grant_rx=RECURRING_GRANT)
        )
        if code:
            note("allow_recurring", code, g.span if g else "")
            recurring = False
        else:
            kept.append(g)

    recipients = []
    for r in m.allowed_recipients:
        g = find("allowed_recipients", r)
        code = gate(_check_recipient(g, request, text, r))
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
                code = "GRANT_RETRACTED" if retracted else "GRANT_NEGATED"
                note(f"requested_addon:{g.value}", code, g.span)

    # seller countries: never wider than what the user named, never named-and-refused
    countries = m.allowed_seller_countries
    allowed, refused = _countries(request, answers)
    answered = _countries("", answers)[0]
    if answered:  # the user's typed answer settles which countries they meant
        allowed = answered
    if allowed:
        if countries is None:
            countries = sorted(allowed)
            note("allowed_seller_countries", "GRANT_SPAN_NOT_IN_TEXT", "restored to what you said")
        else:
            for c in countries:
                if c.upper() not in allowed:
                    note(f"allowed_seller_countries:{c}", "GRANT_SPAN_NOT_IN_TEXT", c)
            countries = [c for c in countries if c.upper() in allowed]
            if not countries:
                ask("Which seller countries are allowed?", "allowed_seller_countries")
    elif refused:
        if countries is None:
            countries = []
            ask("Which seller countries are allowed?", "allowed_seller_countries")
        else:
            countries = [c for c in countries if c.upper() not in refused]
    if countries and len(countries) > 1 and not answered:  # one stray mention must not widen
        ask(f"Which seller countries are allowed? I read: {', '.join(countries)}.",
            "allowed_seller_countries")  # fmt: skip
    if _unknown_country_hint(text) and not answered:
        ask(
            "Which seller countries do you mean? I only know IN, SG and US.",
            "allowed_seller_countries",
        )

    # budget, refundability, quantity: the draft may not drop or loosen what the user stated
    if foreign_money(text):
        ask("Please give every amount in rupees (₹); I can't use other currencies.",
            "budget_total_paise")  # fmt: skip
    stated = _stated_budgets(request, answers)
    if m.budget_total_paise is not None and (not stated or m.budget_total_paise > stated[0]):
        rupees = m.budget_total_paise // 100
        question = f"Your request doesn't state a budget of ₹{rupees:,}; what is it?"
        ask(question, "budget_total_paise", str(m.budget_total_paise))
    elif m.budget_total_paise is None and stated:
        ask(f"Your request mentions ₹{stated[-1] // 100:,}; is that your total budget?",
            "budget_total_paise")  # fmt: skip
    if _stated_refundable(text) and not m.require_refundable:
        ask("You asked for refundable purchases; keep that rule?", "require_refundable")
    qty = _stated_quantity(text)
    if qty is not None and (m.max_quantity is None or m.max_quantity > qty):
        ask(f"You asked for {qty}; should that be the maximum quantity?", "max_quantity")

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
