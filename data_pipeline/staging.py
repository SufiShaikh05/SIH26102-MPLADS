"""Disk-backed scratch store (stdlib ``sqlite3``) so joins never need all rows in RAM.

The staging database is disposable: it is rebuilt from the raw workbooks on
every run and deleted afterwards (unless ``--keep-staging`` is given).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from data_pipeline.schemas import SourceSpec


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = OFF")   # scratch data: speed over crash safety
    conn.execute("PRAGMA synchronous = OFF")
    return conn


def create_work_table(conn: sqlite3.Connection, spec: SourceSpec) -> None:
    columns = ", ".join(f"{name} TEXT" for name in spec.stored_columns)
    conn.execute(
        f"CREATE TABLE {spec.key} (source_row INTEGER PRIMARY KEY, work_id TEXT NOT NULL, {columns})"
    )


def index_work_table(conn: sqlite3.Connection, key: str) -> None:
    conn.execute(f"CREATE INDEX idx_{key}_work ON {key}(work_id, source_row)")
    conn.commit()


def create_txn_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE txn (source_row INTEGER PRIMARY KEY, work_id TEXT NOT NULL, amount TEXT, "
        "expenditure_date TEXT, vendor_key TEXT, payment_status TEXT, duplicate INTEGER NOT NULL)"
    )


def index_txn_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX idx_txn_work ON txn(work_id)")
    conn.commit()


def create_candidate_table(conn: sqlite3.Connection, name: str, text_columns: Sequence[str]) -> None:
    """A narrow staging table for rows awaiting the footer post-pass before final emission.

    Used for sources whose output can't be written until the whole column total is known
    (expenditure, allocation, calamity): every candidate row (footer or not, ID-valid or
    not) is inserted here first; after the post-pass aggregate check removes at most one
    footer row, the table is scanned once more, in ``source_row`` order, to write the real
    output. Always has ``source_row`` (primary key) and ``duplicate_record`` (0/1); every
    other column in ``text_columns`` is stored as TEXT (``NULL`` allowed).
    """
    columns = ", ".join(f"{c} TEXT" for c in text_columns)
    conn.execute(f"CREATE TABLE {name} (source_row INTEGER PRIMARY KEY, duplicate_record INTEGER NOT NULL, {columns})")


def insert_rows(conn: sqlite3.Connection, table: str, columns: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", rows)


def fetch_row(conn: sqlite3.Connection, table: str, source_row: int) -> dict[str, object] | None:
    """One row of a candidate/work table as a ``{column: value}`` dict, or ``None`` if absent."""
    cursor = conn.execute(f"SELECT * FROM {table} WHERE source_row = ?", (source_row,))
    row = cursor.fetchone()
    if row is None:
        return None
    names = [d[0] for d in cursor.description]
    return dict(zip(names, row))


def delete_row(conn: sqlite3.Connection, table: str, source_row: int) -> None:
    conn.execute(f"DELETE FROM {table} WHERE source_row = ?", (source_row,))
    conn.commit()
