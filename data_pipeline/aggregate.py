"""Aggregate expenditure transactions by canonical Work ID (streaming, sorted by ID)."""

from __future__ import annotations

import itertools
import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from operator import itemgetter

from data_pipeline.sinks import CsvSink
from data_pipeline.stats import ExpenditureStats

AGG_COLUMNS = [
    "work_id",
    "total_disbursed",
    "payment_count",
    "first_expenditure_date",
    "last_expenditure_date",
    "vendor_count",
    "amount_missing_count",
    "date_missing_count",
    "negative_amount_count",
    "duplicate_record_count",
]


def iter_expenditure_aggregates(conn: sqlite3.Connection) -> Iterator[dict]:
    """Yield one aggregate dict per Work ID, in ascending Work ID order.

    * ``total_disbursed``: exact decimal sum of every valid amount, all payment
      statuses, duplicates and negatives included (counts of each are exposed
      so the figure can be judged, not trusted blindly)
    * ``payment_count``: every payment row for the ID
    * ``vendor_count``: distinct vendor names after case/punctuation folding
    * first/last date: earliest/latest *valid* expenditure date
    """
    cursor = conn.execute(
        "SELECT work_id, amount, expenditure_date, vendor_key, duplicate FROM txn ORDER BY work_id, source_row"
    )
    for work_id, rows in itertools.groupby(cursor, key=itemgetter(0)):
        total = Decimal(0)
        count = amount_missing = date_missing = negatives = duplicates = 0
        first: str | None = None
        last: str | None = None
        vendors: set[str] = set()
        for _, amount, day, vendor_key, duplicate in rows:
            count += 1
            duplicates += duplicate
            if amount is None:
                amount_missing += 1
            else:
                value = Decimal(amount)
                total += value
                if value < 0:
                    negatives += 1
            if day is None:
                date_missing += 1
            else:
                if first is None or day < first:  # ISO dates compare correctly as text
                    first = day
                if last is None or day > last:
                    last = day
            if vendor_key:
                vendors.add(vendor_key)
        yield {
            "work_id": work_id,
            "total_disbursed": total,
            "payment_count": count,
            "first_expenditure_date": first,
            "last_expenditure_date": last,
            "vendor_count": len(vendors),
            "amount_missing_count": amount_missing,
            "date_missing_count": date_missing,
            "negative_amount_count": negatives,
            "duplicate_record_count": duplicates,
        }


def write_expenditure_by_work(conn: sqlite3.Connection, sink: CsvSink, stats: ExpenditureStats) -> None:
    for aggregate in iter_expenditure_aggregates(conn):
        sink.write(aggregate)
        stats.observe(aggregate)
