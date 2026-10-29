"""Hands a signed approval from the UI (or CLI / eval harness) to the commit that is waiting for it
(SPEC §10). The broker only transports; the connector verifies everything before execution."""

import threading
from dataclasses import dataclass, field

from intentlock.models import Approval, ApprovalRequest


@dataclass
class _Pending:
    request: ApprovalRequest
    done: threading.Event = field(default_factory=threading.Event)
    approval: Approval | None = None


class ApprovalBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    def open(self, request: ApprovalRequest) -> None:
        with self._lock:
            self._pending[request.approval_id] = _Pending(request)

    def pending(self) -> list[ApprovalRequest]:
        with self._lock:
            return [p.request for p in self._pending.values() if not p.done.is_set()]

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            p = self._pending.get(approval_id)
            return p.request if p else None

    def submit(self, approval_id: str, approval: Approval) -> bool:
        with self._lock:
            p = self._pending.get(approval_id)
            if p is None or p.done.is_set() or approval.approval_id != approval_id:
                return False
            p.approval = approval
            p.done.set()
            return True

    def reject(self, approval_id: str) -> bool:
        with self._lock:
            p = self._pending.get(approval_id)
            if p is None or p.done.is_set():
                return False
            p.done.set()
            return True

    def wait(self, approval_id: str, timeout: float) -> Approval | None:
        with self._lock:
            p = self._pending.get(approval_id)
        if p is None:
            return None
        p.done.wait(max(0.0, timeout))
        with self._lock:
            self._pending.pop(approval_id, None)
        return p.approval
