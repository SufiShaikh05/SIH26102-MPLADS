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
        "expenditure_date TEXT, vendor_key TEXT, duplicate INTEGER NOT NULL)"
    )


def index_txn_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX idx_txn_work ON txn(work_id)")
    conn.commit()


def insert_rows(conn: sqlite3.Connection, table: str, columns: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", rows)
