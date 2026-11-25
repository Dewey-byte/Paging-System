"""Thin, thread-safe wrapper around a SQLite connection."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    return datetime.now().strftime(TIMESTAMP_FORMAT)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], TIMESTAMP_FORMAT)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


class Database:
    """Serialises access to one SQLite connection.

    The connection runs in autocommit mode; multi-statement operations use
    :meth:`transaction`, which supports nesting (only the outermost level
    commits or rolls back).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")

    # ----------------------------------------------------------------- queries
    def execute(self, sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)

    def executescript(self, script: str) -> None:
        with self._lock:
            self.conn.executescript(script)

    def query(self, sql: str, params: Sequence[Any] | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] | dict = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        return row[0] if row is not None and row[0] is not None else default

    # ------------------------------------------------------------ transactions
    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        with self._lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self.conn.execute("COMMIT")

    # ----------------------------------------------------------------- helpers
    def insert(self, table: str, data: dict[str, Any]) -> int:
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        cur = self.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(data.values()))
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, data: dict[str, Any]) -> None:
        if not data:
            return
        assignments = ", ".join(f"{key} = ?" for key in data)
        self.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", [*data.values(), row_id])

    @property
    def user_version(self) -> int:
        return int(self.scalar("PRAGMA user_version", default=0))

    def integrity_check(self) -> str:
        return str(self.scalar("PRAGMA integrity_check", default="unknown"))

    def backup_to(self, destination: str | Path) -> None:
        """Consistent online copy of the database (safe while the app runs)."""
        dest = sqlite3.connect(str(destination))
        try:
            with self._lock:
                self.conn.backup(dest)
        finally:
            dest.close()

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
