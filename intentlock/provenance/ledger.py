"""Provenance ledger (SPEC §5 ProvenanceRecord, §7; invariant 9).

Every labeled field of every result is written here, in one transaction, before the agent sees
the result. `index(session)` is the snapshot the taint rule and propagation read.
"""

import json

from intentlock.db import Database
from intentlock.models import ProvenanceRecord
from intentlock.provenance.taint import TaintIndex


class Ledger:
    def __init__(self, db: Database) -> None:
        self.db = db

    def next_call_seq(self, session_id: str) -> int:
        with self.db.tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO call_seq (session_id, last) VALUES (?, 0)", (session_id,)
            )
            conn.execute("UPDATE call_seq SET last = last + 1 WHERE session_id = ?", (session_id,))
            return conn.execute(
                "SELECT last FROM call_seq WHERE session_id = ?", (session_id,)
            ).fetchone()[0]

    def add(self, records: list[ProvenanceRecord]) -> None:
        with self.db.tx() as conn:
            conn.executemany(
                "INSERT INTO provenance (record_id, session_id, call_seq, tool, label, field_path,"
                " raw, tokens, derived_from, ts, pg_score_permille)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.record_id, r.session_id, r.call_seq, r.tool, r.label.value,
                        r.field_path, r.raw, json.dumps(r.tokens), json.dumps(r.derived_from),
                        r.ts, r.pg_score_permille,
                    )
                    for r in records
                ],
            )  # fmt: skip

    def records(self, session_id: str) -> list[ProvenanceRecord]:
        rows = self.db.query(
            "SELECT record_id, session_id, call_seq, tool, label, field_path, raw, tokens,"
            " derived_from, ts, pg_score_permille FROM provenance WHERE session_id = ?"
            " ORDER BY call_seq, record_id",
            (session_id,),
        )
        return [
            ProvenanceRecord(
                record_id=a, session_id=b, call_seq=c, tool=d, label=e, field_path=f, raw=g,
                tokens=json.loads(h), derived_from=json.loads(i), ts=j, pg_score_permille=k,
            )
            for a, b, c, d, e, f, g, h, i, j, k in rows
        ]  # fmt: skip

    def index(self, session_id: str) -> TaintIndex:
        return TaintIndex(self.records(session_id))
