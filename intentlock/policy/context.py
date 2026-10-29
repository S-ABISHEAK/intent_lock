"""DecisionContext: everything a pipeline step may read (SPEC §6).

Steps are pure functions `(DecisionContext) -> list[Reason]`. The proposal here has already been
sanitized: only the tool's identifier args remain, so agent-supplied money cannot reach a step
(CLAUDE.md invariant 3).
"""

from dataclasses import dataclass

from intentlock.models import ActionProposal, Mandate, Quote


@dataclass(frozen=True)
class DecisionContext:
    proposal: ActionProposal
    mandate: Mandate | None  # None only where the config skips mandate steps
    config: str
    now: int
    quote: Quote | None = None
    # SKUs the agent explicitly added with add_to_cart; supplied by the proxy (D-014)
    requested_skus: frozenset[str] = frozenset()
