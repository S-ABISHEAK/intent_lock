"""Prompt Guard 2 injection signal (SPEC §12, pipeline step 9; D-021, D-034). Escalation only.

Every UNTRUSTED (or derived) record is scored when it is ingested, before the agent sees it, and
the score is stored on the record as integer per-mille (no floats in hashed objects, D-009). At
decision time the step looks at untrusted records that fed a sensitive value of this action, or
arrived in the last 5 tool calls; any score >= 0.5 gives REVIEW `INJECTION_SUSPECTED`. If the model
can't be loaded the feature is switched off with a recorded warning; a missing model never ALLOWs.
"""

import logging
import os
from collections.abc import Callable, Iterable

from intentlock.models import ProvenanceRecord, Reason
from intentlock.policy.reasons import make_reason
from intentlock.provenance.ledger import Ledger
from intentlock.provenance.taint import TAINT_LABELS, normalize_value, sensitive_values

log = logging.getLogger(__name__)

MODEL_ID = "meta-llama/Llama-Prompt-Guard-2-86M"
THRESHOLD = 500  # per-mille: a score of 0.5 (SPEC §12)
RECENT_CALLS = 5
CHUNK = 1000  # characters; well under the model's 512-token window even for dense scripts
Scorer = Callable[[str], int]
last_load_error: str | None = None  # why the model didn't load (token redacted), for diagnostics


def load_hf_scorer(token: str | None = None) -> Scorer | None:
    """The real model on CPU via transformers, or None if it isn't installed or access is gated."""
    global last_load_error
    try:
        from transformers import pipeline  # optional extra: .[promptguard]
    except ImportError as exc:
        last_load_error = f"transformers not installed ({exc})"
        return None
    try:
        clf = pipeline("text-classification", model=MODEL_ID, token=token or None)
    except Exception as exc:  # gated, offline, wrong token: the feature stays off, it never ALLOWs
        message = f"{type(exc).__name__}: {exc}"
        if token:
            message = message.replace(token, "<HF_TOKEN>")
        last_load_error = message[:600]
        log.warning("Prompt Guard 2 could not be loaded; step 9 is off")
        return None

    def score(text: str) -> int:
        best = 0
        for i in range(0, max(len(text), 1), CHUNK):  # long texts: score each chunk, keep the max
            out = clf(text[i : i + CHUNK], truncation=True)[0]
            label, p = str(out["label"]).lower(), float(out["score"])
            malicious = p if ("malicious" in label or label.endswith("1")) else 1.0 - p
            best = max(best, round(malicious * 1000))
        return min(max(best, 0), 1000)

    return score


def resolve_promptguard(
    features: frozenset[int], loader: Callable[[], Scorer | None] | None = None
) -> tuple[frozenset[int], Scorer | None, str]:
    """(features, scorer, status). Status is recorded per session so results say what ran."""
    if 9 not in features:
        return features, None, "off"
    load = loader or (lambda: load_hf_scorer(os.environ.get("HF_TOKEN")))
    scorer = load()
    if scorer is None:
        log.warning("PROMPT_GUARD is on but the model is unavailable; running without step 9")
        return features - {9}, None, "unavailable"
    return features, scorer, "on"


def score_records(records: Iterable[ProvenanceRecord], scorer: Scorer) -> list[ProvenanceRecord]:
    cache: dict[str, int | None] = {}
    out = []
    for r in records:
        if r.label in TAINT_LABELS and r.raw.strip():
            if r.raw not in cache:
                try:
                    cache[r.raw] = min(max(int(scorer(r.raw)), 0), 1000)
                except Exception:
                    log.exception("Prompt Guard scoring failed; record left unscored")
                    cache[r.raw] = None
            r = r.model_copy(update={"pg_score_permille": cache[r.raw]})
        out.append(r)
    return out


def make_promptguard_step(ledger: Ledger):
    def step(ctx) -> list[Reason]:
        index = ledger.index(ctx.proposal.session_id)
        records = list(index.by_id.values())
        if not records:
            return []
        latest = max(r.call_seq for r in records)
        feeding = {
            rid
            for _, raw in sensitive_values(ctx)
            for rid in index.untrusted_sources(normalize_value(raw))
        }
        reasons, seen = [], set()
        for r in sorted(records, key=lambda x: (x.call_seq, x.record_id)):
            if r.label not in TAINT_LABELS or not r.raw.strip():
                continue
            in_scope = r.record_id in feeding or r.call_seq > latest - RECENT_CALLS
            if in_scope and r.pg_score_permille is None and r.raw not in seen:
                seen.add(r.raw)  # the scorer failed on it: escalate rather than silently pass
                reasons.append(make_reason("INJECTION_SUSPECTED", f"{r.record_id} unscored"))
                continue
            if r.pg_score_permille is None:
                continue
            if in_scope and r.pg_score_permille >= THRESHOLD and r.raw not in seen:
                seen.add(r.raw)
                detail = (
                    f"{r.record_id} score={r.pg_score_permille}/1000 {r.tool}#{r.call_seq} "
                    f"{r.field_path}"
                )
                reasons.append(make_reason("INJECTION_SUSPECTED", detail))
        return reasons

    return step
