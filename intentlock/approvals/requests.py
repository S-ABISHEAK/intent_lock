"""Unsigned approval requests and their binding (SPEC §5 Approval, §10; D-022).

A request binds mandate, policy version, session, tool and the exact thing being approved: the
authoritative quote hash for checkout, or the hash of the tool's identifier args otherwise.
"""

import secrets
import uuid
from dataclasses import dataclass

from intentlock.canonical import canonical_hash
from intentlock.models import ActionProposal, ApprovalRequest, Decision, Mandate
from intentlock.policy.tools import sanitize_args

APPROVAL_TTL = 120  # seconds (SPEC §5, CLAUDE.md invariant 6)


@dataclass(frozen=True)
class Binding:
    mandate_id: str
    policy_version: int
    session_id: str
    action_tool: str
    quote_hash: str


def action_hash(proposal: ActionProposal) -> str:
    """Hash of tool + identifier args. Money and any other extra args are excluded."""
    args = sanitize_args(proposal.tool, proposal.args)
    return canonical_hash({"tool": proposal.tool, "args": args})


def bound_hash(proposal: ActionProposal, decision: Decision) -> str:
    if proposal.tool == "checkout":
        if not decision.quote_hash:
            raise ValueError("a checkout approval needs the decided quote hash")
        return decision.quote_hash
    return action_hash(proposal)


def binding_for(
    mandate: Mandate, session_id: str, proposal: ActionProposal, decision: Decision
) -> Binding:
    return Binding(
        mandate_id=mandate.mandate_id,
        policy_version=mandate.policy_version,
        session_id=session_id,
        action_tool=proposal.tool,
        quote_hash=bound_hash(proposal, decision),
    )


def new_request(
    mandate: Mandate, session_id: str, proposal: ActionProposal, decision: Decision, *, now: int
) -> ApprovalRequest:
    if decision.outcome != "REVIEW":
        raise ValueError("only REVIEW decisions get an approval request")
    b = binding_for(mandate, session_id, proposal, decision)
    return ApprovalRequest(
        approval_id=uuid.uuid4().hex,
        nonce=secrets.token_hex(16),
        mandate_id=b.mandate_id,
        policy_version=b.policy_version,
        session_id=b.session_id,
        action_tool=b.action_tool,
        quote_hash=b.quote_hash,
        expires_at=now + APPROVAL_TTL,
    )
