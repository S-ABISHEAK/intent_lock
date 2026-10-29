"""Ed25519 signing of approval requests. Lives in approver/ only: the gateway never imports it and
never holds the private key (CLAUDE.md invariant 7)."""

from nacl.signing import SigningKey

from intentlock.canonical import canonical
from intentlock.models import Approval, ApprovalRequest


def sign_request(req: ApprovalRequest, key: SigningKey) -> Approval:
    signature = key.sign(canonical(req).encode("utf-8")).signature.hex()
    return Approval(**req.model_dump(), signature=signature)
