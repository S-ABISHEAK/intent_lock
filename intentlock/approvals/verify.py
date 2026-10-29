"""Approval verification and single-use consume (SPEC §6.2 steps 2-3; CLAUDE.md invariant 6).

Only the approver's public key is used here. The expected binding comes from the gateway's own
records of the decision, never from the approval being checked.
"""

import sqlite3
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from intentlock.approvals.requests import APPROVAL_TTL, Binding
from intentlock.canonical import canonical
from intentlock.db import Database
from intentlock.models import Approval, ApprovalRequest, Reason
from intentlock.policy.reasons import make_reason


def load_verify_key(path: str | Path) -> VerifyKey:
    """The approver's public key (hex). The gateway never loads the private key."""
    return VerifyKey(bytes.fromhex(Path(path).read_text().strip()))


def _signature_ok(approval: Approval, verify_key: VerifyKey) -> bool:
    req = ApprovalRequest(**approval.model_dump(exclude={"signature"}))
    try:
        verify_key.verify(canonical(req).encode("utf-8"), bytes.fromhex(approval.signature))
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def verify_approval(
    approval: Approval, *, verify_key: VerifyKey, expected: Binding, now: int
) -> list[Reason]:
    if not _signature_ok(approval, verify_key):
        return [make_reason("APPROVAL_INVALID_SIGNATURE")]
    out: list[Reason] = []
    if now >= approval.expires_at:
        out.append(make_reason("APPROVAL_EXPIRED"))
    actual = Binding(
        approval.mandate_id,
        approval.policy_version,
        approval.session_id,
        approval.action_tool,
        approval.quote_hash,
    )
    mismatched = [
        f for f in expected.__dataclass_fields__ if getattr(actual, f) != getattr(expected, f)
    ]
    if approval.expires_at > now + APPROVAL_TTL:
        mismatched.append("expires_at window")
    if mismatched:
        out.append(make_reason("APPROVAL_MISMATCH", ",".join(mismatched)))
    return out


class NonceStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def consume(self, nonce: str, *, now: int) -> bool:
        """True exactly once per nonce; False on replay (UNIQUE violation)."""
        try:
            with self.db.tx() as conn:
                conn.execute("INSERT INTO used_nonces (nonce, ts) VALUES (?, ?)", (nonce, now))
        except sqlite3.IntegrityError:
            return False
        return True
