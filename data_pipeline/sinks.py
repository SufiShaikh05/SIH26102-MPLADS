"""CSV writers that also profile what they write (feeds the data dictionary)."""

from __future__ import annotations

import csv
import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path

from data_pipeline.parsing import decimal_to_str


def to_cell(value: object) -> str:
    """Render a value for CSV: ISO dates, plain decimals, 1/0 flags, empty for None."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, Decimal):
        return decimal_to_str(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


class OutputProfile:
    """Row count, missing count and first example per output column."""

    def __init__(self, name: str, path: Path, columns: Sequence[str]) -> None:
        self.name = name
        self.path = path
        self.columns = list(columns)
        self.rows = 0
        self.missing = dict.fromkeys(self.columns, 0)
        self.example: dict[str, str] = {}

    def observe(self, cells: Sequence[str]) -> None:
        self.rows += 1
        for column, cell in zip(self.columns, cells):
            if cell == "":
                self.missing[column] += 1
            elif column not in self.example:
                self.example[column] = cell


class CsvSink:
    """UTF-8 CSV with ``\\n`` line endings (identical bytes on every OS)."""

    def __init__(self, path: Path, columns: Sequence[str]) -> None:
        self.path = Path(path)
        self.columns = list(columns)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._handle, lineterminator="\n")
        self._writer.writerow(self.columns)
        self.profile = OutputProfile(self.path.stem, self.path, self.columns)

    def write(self, row: Mapping[str, object]) -> None:
        cells = [to_cell(row.get(column)) for column in self.columns]
        self._writer.writerow(cells)
        self.profile.observe(cells)

    @property
    def rows(self) -> int:
        return self.profile.rows

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> "CsvSink":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
