"""Hash-chained receipts: append and verify (SPEC §5, §17 test 7; CLAUDE.md invariant 11).

row_hash = sha256(canonical({seq, prev_hash, kind, payload, ts})), prev_hash of seq 0 is GENESIS.
Payloads go through `canonical()`, so a float or non-JSON value raises before anything is written.
Known limit: a chain can't reveal that its newest rows were deleted; anchor `verify_chain().head`
somewhere else (UI, eval results) to detect that.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_args

from intentlock.canonical import canonical, canonical_hash
from intentlock.db import Database
from intentlock.models import Receipt

GENESIS = "0" * 64
KINDS = get_args(Receipt.model_fields["kind"].annotation)


def compute_row_hash(seq: int, prev_hash: str, kind: str, payload: dict, ts: int) -> str:
    return canonical_hash(
        {"seq": seq, "prev_hash": prev_hash, "kind": kind, "payload": payload, "ts": ts}
    )


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    head: str
    bad_seq: int | None = None


def verify_chain(rows: list[Receipt]) -> ChainCheck:
    prev = GENESIS
    for expected_seq, r in enumerate(rows):
        bad = (
            r.seq != expected_seq
            or r.prev_hash != prev
            or r.row_hash != compute_row_hash(r.seq, r.prev_hash, r.kind, r.payload, r.ts)
        )
        if bad:
            return ChainCheck(ok=False, head=prev, bad_seq=expected_seq)
        prev = r.row_hash
    return ChainCheck(ok=True, head=prev)


class ReceiptLog:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock

    def append(self, kind: str, payload: dict[str, Any]) -> Receipt:
        if kind not in KINDS:
            raise ValueError(f"unknown receipt kind {kind!r}")
        body = canonical(payload)  # raises on floats before the transaction starts
        ts = int(self.clock())
        with self.db.tx() as conn:
            last = conn.execute("SELECT seq, row_hash FROM receipts ORDER BY seq DESC LIMIT 1")
            row = last.fetchone()
            seq, prev = (row[0] + 1, row[1]) if row else (0, GENESIS)
            row_hash = compute_row_hash(seq, prev, kind, payload, ts)
            conn.execute(
                "INSERT INTO receipts (seq, prev_hash, row_hash, kind, payload, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (seq, prev, row_hash, kind, body, ts),
            )
        return Receipt(
            seq=seq, prev_hash=prev, row_hash=row_hash, kind=kind, payload=payload, ts=ts
        )

    def all(self) -> list[Receipt]:
        rows = self.db.query(
            "SELECT seq, prev_hash, row_hash, kind, payload, ts FROM receipts ORDER BY seq"
        )
        return [
            Receipt(seq=s, prev_hash=p, row_hash=h, kind=k, payload=json.loads(b), ts=t)
            for s, p, h, k, b, t in rows
        ]
