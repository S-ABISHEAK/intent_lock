"""Hash-chained receipts (SPEC §5 Receipt, §17 test 7; CLAUDE.md invariant 11)."""

import sqlite3
import threading

import pytest

from intentlock.db import Database
from intentlock.receipts import GENESIS, ReceiptLog, verify_chain


@pytest.fixture
def log(tmp_path):
    return ReceiptLog(Database(tmp_path / "run.sqlite"), clock=lambda: 1000)


def fill(log, n=5):
    for i in range(n):
        log.append("decision", {"i": i, "outcome": "DENY"})


def test_chain_links_from_genesis(log):
    fill(log, 3)
    rows = log.all()
    assert [r.seq for r in rows] == [0, 1, 2]
    assert rows[0].prev_hash == GENESIS
    assert rows[1].prev_hash == rows[0].row_hash and rows[2].prev_hash == rows[1].row_hash
    assert verify_chain(rows).ok


def test_empty_chain_verifies(log):
    assert verify_chain([]).ok and verify_chain([]).head == GENESIS


def test_head_hash_is_the_last_row_hash(log):
    fill(log, 3)
    assert verify_chain(log.all()).head == log.all()[-1].row_hash


@pytest.mark.parametrize("field", ["payload", "kind", "ts", "prev_hash", "row_hash"])
def test_tampering_with_any_field_of_any_row_breaks_verification(log, field):
    fill(log)
    for target in range(5):
        rows = log.all()
        r = rows[target]
        changed = {
            "payload": {**r.payload, "outcome": "ALLOW"},
            "kind": "execution",
            "ts": r.ts + 1,
            "prev_hash": "f" * 64,
            "row_hash": "e" * 64,
        }[field]
        rows[target] = r.model_copy(update={field: changed})
        result = verify_chain(rows)
        assert not result.ok and result.bad_seq == target


def test_deleting_or_reordering_rows_breaks_verification(log):
    fill(log)
    rows = log.all()
    assert not verify_chain(rows[:2] + rows[3:]).ok
    assert not verify_chain([rows[1], rows[0], *rows[2:]]).ok
    assert not verify_chain(rows[1:]).ok  # dropping the first row


def test_tail_truncation_is_detected_only_against_an_anchored_head(log):
    """Known limit: a chain alone can't see its newest rows removed; compare the head."""
    fill(log)
    rows = log.all()
    anchored = verify_chain(rows).head
    truncated = verify_chain(rows[:3])
    assert truncated.ok and truncated.head != anchored


def test_tampering_in_the_database_itself_is_detected(log, tmp_path):
    fill(log)
    with sqlite3.connect(tmp_path / "run.sqlite") as conn:
        conn.execute(
            "UPDATE receipts SET payload = ? WHERE seq = 2", ('{"i":2,"outcome":"ALLOW"}',)
        )
    assert verify_chain(log.all()).bad_seq == 2


def test_float_payload_is_rejected_and_nothing_is_written(log):
    with pytest.raises(TypeError):
        log.append("proposal", {"price": 1.5})
    assert log.all() == []


def test_unknown_kind_is_rejected(log):
    with pytest.raises(ValueError):
        log.append("gossip", {})


def test_concurrent_appends_keep_one_unbroken_chain(log):
    def worker(k):
        for i in range(10):
            log.append("tool_result", {"worker": k, "i": i})

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = log.all()
    assert len(rows) == 80 and [r.seq for r in rows] == list(range(80))
    assert verify_chain(rows).ok


def test_receipts_survive_reopening_the_database(tmp_path):
    db = Database(tmp_path / "run.sqlite")
    fill(ReceiptLog(db, clock=lambda: 1), 2)
    again = ReceiptLog(Database(tmp_path / "run.sqlite"), clock=lambda: 2)
    again.append("execution", {"ok": True})
    assert verify_chain(again.all()).ok and len(again.all()) == 3
