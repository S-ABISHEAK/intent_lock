"""SQLite per run, stdlib `sqlite3`, explicit transactions (SPEC §4; CLAUDE.md "Style").

Each operation opens its own connection, so threads never share one. `tx()` starts with
BEGIN IMMEDIATE, which takes the write lock up front: concurrent writers serialize instead of
racing, which is what the receipt chain and the single-use nonce insert rely on (SPEC §6.2).
"""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    seq INTEGER PRIMARY KEY,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS used_nonces (
    nonce TEXT PRIMARY KEY,
    ts INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        # Switching a brand-new file to WAL can report "locked" immediately when several handles
        # create it at once (the busy handler doesn't cover that step), so retry briefly.
        for attempt in range(50):
            conn = self.connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or attempt == 49:
                    raise
            finally:
                conn.close()
            time.sleep(0.01 * (attempt + 1))

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn = self.connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()
