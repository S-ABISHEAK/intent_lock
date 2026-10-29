"""Token extraction, taint rule and lookup propagation (SPEC §7.2, §7.4, §7.4.1; D-028).

Tokens are the identifiers taint matches on: SKUs, emails, URLs (host + path), seller and address
ids, and account-like numbers of 8+ digits. Text is normalized first so look-alike or invisible
characters can't split an identifier: NFKC (full-width forms), format characters removed
(zero-width, bidi overrides), dash look-alikes folded to "-". Prices are never tokens.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from intentlock.models import ProvenanceRecord, Reason, TrustLabel
from intentlock.policy.hard_rules import split_recipients
from intentlock.policy.reasons import make_reason
from intentlock.provenance.labels import label_fields

if TYPE_CHECKING:
    from intentlock.policy.context import DecisionContext
    from intentlock.provenance.ledger import Ledger

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣"), "-")

SKU_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,4}-\d{3,6})(?!\d)")
ID_RE = re.compile(r"(?<![A-Za-z0-9])((?:SEL|ADDR)-[A-Za-z0-9]+)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
ACCOUNT_RE = re.compile(r"(?<!\d)\d{8,}(?!\d)")
MIN_TOKEN_LEN = 4


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _url_token(url: str) -> str | None:
    url = url.rstrip(".,;:!?)]}")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return f"raw:{url.lower()}"
    host = (parts.hostname or "").lower()
    if not host:
        return None
    if "\\" in url or parts.username or parts.password:
        return f"raw:{url.lower()}"  # browsers and urlsplit disagree on these: never matches
    if port not in (None, 80, 443):
        host = f"{host}:{port}"
    return f"{host}{parts.path.lower().rstrip('/')}"


def extract_tokens(text: str) -> list[str]:
    text = clean_text(text)
    found: set[str] = set()
    for m in URL_RE.finditer(text):
        tok = _url_token(m.group(0))
        if tok:
            found.add(tok)
    found.update(m.group(0).lower() for m in EMAIL_RE.finditer(text))
    found.update(m.group(1).upper() for m in SKU_RE.finditer(text))
    found.update(m.group(1).upper() for m in ID_RE.finditer(text))
    found.update(m.group(0) for m in ACCOUNT_RE.finditer(text))
    return sorted(t for t in found if len(t) >= MIN_TOKEN_LEN)


def normalize_value(value: str) -> str:
    """A sensitive argument value in the same form tokens are stored in."""
    text = clean_text(str(value)).strip()
    if EMAIL_RE.fullmatch(text):
        return text.lower()
    if URL_RE.fullmatch(text):
        return _url_token(text) or text.lower()
    return text.upper()


# -- taint rule (§7.4) --------------------------------------------------------------------------

ECHO_SUFFIX = "#echo"  # a lookup repeated an arg nobody chose: clean, but not a choice (D-030)
CANONICAL_FIELDS = ("shipping_address_id", ".sku", ".seller_id", "id")
CLEAN_LABELS = frozenset({TrustLabel.USER, TrustLabel.TRUSTED_API, TrustLabel.SENSITIVE})
TAINT_LABELS = frozenset({TrustLabel.UNTRUSTED, TrustLabel.DERIVED_UNTRUSTED})


@dataclass(frozen=True)
class Verdict:
    status: Literal["clean", "tainted", "unknown"]
    sources: tuple[str, ...] = ()  # untrusted / derived record ids that contain the token


class TaintIndex:
    """Snapshot of one session's ledger: token -> the records that contain it."""

    def __init__(self, records: list[ProvenanceRecord]) -> None:
        self.by_id = {r.record_id: r for r in records}
        self.by_token: dict[str, list[ProvenanceRecord]] = {}
        for r in records:
            for t in r.tokens:
                self.by_token.setdefault(t, []).append(r)

    def classify(self, token: str) -> Verdict:
        records = self.by_token.get(token, [])
        if any(r.label in CLEAN_LABELS for r in records):
            return Verdict("clean")
        tainted = tuple(r.record_id for r in records if r.label in TAINT_LABELS)
        return Verdict("tainted", tainted) if tainted else Verdict("unknown")

    def chosen_by_user_or_merchant(self, token: str) -> bool:
        """D-014/D-029: a clean source that can justify *choosing* an item: the user's words, or a
        merchant search/product record (laundered tokens were already split out as derived)."""
        return any(
            r.label == TrustLabel.USER
            or (
                r.label == TrustLabel.TRUSTED_API
                and r.tool in ("search_products", "get_product")
                and not r.field_path.endswith(ECHO_SUFFIX)
            )
            for r in self.by_token.get(token, [])
        )

    def explain(self, token: str) -> str:
        """Readable source chain, e.g. 'get_reviews#1 reviews[0].text -> get_product#2 sku'."""
        chains = []
        for rid in self.classify(token).sources:
            chains.append(" -> ".join(self._chain(rid, set())))
        return "; ".join(chains)

    def _chain(self, rid: str, seen: set[str]) -> list[str]:
        r = self.by_id.get(rid)
        if r is None or rid in seen:
            return []
        seen.add(rid)
        earlier = [step for src in r.derived_from for step in self._chain(src, seen)]
        return [*earlier, f"{r.tool}#{r.call_seq} {r.field_path}"]


def _arg_tokens(args: Any) -> set[str]:
    if isinstance(args, dict):
        return {t for v in args.values() for t in _arg_tokens(v)}
    if isinstance(args, list):
        return {t for v in args for t in _arg_tokens(v)}
    if isinstance(args, str):
        return set(extract_tokens(args))
    return set()


def label_call(
    tool: str,
    args: dict[str, Any],
    result: dict[str, Any],
    index: TaintIndex,
    *,
    session_id: str,
    call_seq: int,
    ts: int,
    error: bool = False,
) -> list[ProvenanceRecord]:
    """Label one forwarded call's result with §7.4.1 propagation.

    Any identifier in the call's args that is tainted or of unknown provenance is suspicious; every
    occurrence of it in the result is stored as DERIVED_UNTRUSTED (with its source chain), split
    out of the field's normal record. Everything else keeps its §7.1 label.
    """
    suspicious: dict[str, tuple[str, ...]] = {}
    unchosen: set[str] = set()  # clean args (private data, …) that nobody chose as a product
    for tok in _arg_tokens(args):
        v = index.classify(tok)
        if v.status != "clean":
            suspicious[tok] = v.sources
        elif not index.chosen_by_user_or_merchant(tok):
            unchosen.add(tok)
    out: list[ProvenanceRecord] = []
    for i, f in enumerate(label_fields(tool, result, error=error)):
        toks = extract_tokens(f.raw) if f.is_text and f.sources != "none" else []
        if f.sources == "url":
            toks = [t for t in toks if "/" in t or "." in t]  # URL tokens, not ids inside the path
        rid = f"{session_id[:12]}:{call_seq}:{i}"
        echoed = [t for t in toks if t in unchosen]
        kept = [t for t in toks if t not in suspicious and t not in unchosen]
        derived = [t for t in toks if t in suspicious]
        common = dict(session_id=session_id, tool=tool, field_path=f.field_path, raw=f.raw, ts=ts,
                      call_seq=call_seq)  # fmt: skip
        out.append(ProvenanceRecord(record_id=rid, label=f.label, tokens=kept, **common))
        if echoed:  # still clean for §7.4, but not a reason to choose the item (D-030)
            out.append(
                ProvenanceRecord(
                    record_id=f"{rid}:e", label=f.label, tokens=echoed,
                    **{**common, "field_path": f.field_path + ECHO_SUFFIX},
                )
            )  # fmt: skip
        if derived:
            sources = sorted({s for t in derived for s in suspicious[t]})
            out.append(
                ProvenanceRecord(
                    record_id=f"{rid}:d", label=TrustLabel.DERIVED_UNTRUSTED, tokens=derived,
                    derived_from=sources, **common,
                )
            )  # fmt: skip
    return out


def user_records(session_id: str, user_text: str, *, ts: int) -> list[ProvenanceRecord]:
    """The user's own words are a clean source (SPEC §7.1, first row)."""
    return [
        ProvenanceRecord(
            record_id=f"{session_id[:12]}:0:user", session_id=session_id, tool="user",
            label=TrustLabel.USER, field_path="mandate.user_text", raw=user_text,
            tokens=extract_tokens(user_text), ts=ts, call_seq=0,
        )
    ]  # fmt: skip


# -- pipeline step 6 (§7.3, §7.4) -----------------------------------------------------------------
def sensitive_values(ctx: "DecisionContext") -> list[tuple[str, str]]:
    """(field, value) pairs that must have clean provenance. Checkout reads the authoritative
    quote, never the agent's args."""
    tool, args = ctx.proposal.tool, ctx.proposal.args
    if tool == "checkout":
        if ctx.quote is None:
            raise ValueError("the taint step needs the authoritative quote for checkout")
        out = []
        for i, line in enumerate(ctx.quote.lines):
            out += [(f"lines[{i}].sku", line.sku), (f"lines[{i}].seller_id", line.seller_id)]
        return [*out, ("shipping_address_id", ctx.quote.shipping_address_id)]
    if tool in ("send_email", "share_cart"):
        out = [("to", r) for r in split_recipients(str(args["to"]))]
        if tool == "send_email":
            for part in ("subject", "body"):
                text = clean_text(str(args.get(part, "")))
                out += [(f"{part}.url", m.group(0)) for m in URL_RE.finditer(text)]
                out += [(f"{part}.email", m.group(0)) for m in EMAIL_RE.finditer(text)]
        return out
    if tool in ("delete_saved_address", "cancel_order"):
        return [("id", str(args["id"]))]
    return []


def make_taint_step(ledger: "Ledger"):
    """Step 6: REVIEW `TAINTED_ARG` / `UNKNOWN_PROVENANCE` for any sensitive value without a clean
    source in this session's ledger. Runs only in the `intentlock` config (SPEC §6)."""

    def step(ctx: "DecisionContext") -> list[Reason]:
        index = ledger.index(ctx.proposal.session_id)
        reasons, seen = [], set()
        for field, raw in sensitive_values(ctx):
            value = normalize_value(raw)
            if field.endswith(CANONICAL_FIELDS) and value != raw:
                reasons.append(
                    make_reason("UNKNOWN_PROVENANCE", f"{field}={raw!r} is not canonical")
                )
                continue
            if value in seen:
                continue
            seen.add(value)
            verdict = index.classify(value)
            if verdict.status == "tainted":
                detail = (
                    f"{field}={value} sources={list(verdict.sources)} via {index.explain(value)}"
                )
                reasons.append(make_reason("TAINTED_ARG", detail))
            elif verdict.status == "unknown":
                reasons.append(make_reason("UNKNOWN_PROVENANCE", f"{field}={value}"))
        return reasons

    return step
