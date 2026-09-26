"""Aggregate expenditure transactions by canonical Work ID (streaming, sorted by ID).

Exact duplicate transaction rows (``duplicate_record = 1`` - see ``ingest.py``) are never
dropped here, or anywhere else: the raw workbook carries no transaction identifier, so an
identical row could genuinely be a second, real payment rather than a duplicate-entry error.
Instead, every total below is produced twice - once over every row, once with duplicates
excluded - so a reader can judge the difference themselves rather than have it decided for
them. See ``data_pipeline/reports.py`` section 6.4 for how this is written up.
"""

from __future__ import annotations

import itertools
import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from operator import itemgetter

from data_pipeline.sinks import CsvSink
from data_pipeline.stats import ExpenditureStats

# Exact payment_status text used by the real MPLADS export (see DATA_SPIKE_REPORT.txt);
# matched with a plain `==` - not folded or fuzzy - so an unexpected spelling is simply
# not counted in either bucket rather than silently merged into one.
SUCCESS_STATUS = "Payment Success"
IN_PROGRESS_STATUS = "Payment In-Progress"
DUPLICATE_RATIO_PLACES = Decimal("0.0001")

AGG_COLUMNS = [
    "work_id",
    "total_disbursed_all_rows",
    "total_disbursed",                  # backward-compatible alias of total_disbursed_all_rows
    "success_amount",
    "in_progress_amount",
    "exact_duplicate_amount",
    "deduplicated_disbursed_amount",
    "payment_count_all_rows",
    "payment_count",                    # backward-compatible alias of payment_count_all_rows
    "deduplicated_payment_count",
    "duplicate_record_count",
    "duplicate_ratio",
    "first_expenditure_date",
    "last_expenditure_date",
    "vendor_count",
    "amount_missing_count",
    "date_missing_count",
    "negative_amount_count",
]


def iter_expenditure_aggregates(conn: sqlite3.Connection) -> Iterator[dict]:
    """Yield one aggregate dict per Work ID, in ascending Work ID order.

    * ``total_disbursed_all_rows`` (alias: ``total_disbursed``): exact decimal sum of every
      valid amount - all payment statuses, exact duplicates and negatives included, nothing
      pre-filtered. This is the field's original, unchanged meaning; the new name only makes
      that meaning explicit now that narrower totals sit alongside it.
    * ``success_amount`` / ``in_progress_amount``: the same "every row" sum, restricted to
      rows whose ``payment_status`` is exactly ``Payment Success`` / ``Payment In-Progress``.
      A row with any other status (or a missing one) counts toward neither.
    * ``exact_duplicate_amount`` / ``deduplicated_disbursed_amount``: the total split purely by
      ``duplicate_record``, regardless of payment status - the two always add back up to
      ``total_disbursed_all_rows``.
    * ``payment_count_all_rows`` (alias: ``payment_count``): every payment row for the ID,
      including ones with a missing amount. ``deduplicated_payment_count`` is the same count
      restricted to ``duplicate_record = 0``; ``duplicate_ratio`` is
      ``duplicate_record_count / payment_count_all_rows``, rounded to 4 decimal places (0 when
      there are no payments).
    * ``vendor_count``: distinct vendor names after case/punctuation folding.
    * first/last date: earliest/latest *valid* expenditure date.
    """
    cursor = conn.execute(
        "SELECT work_id, amount, expenditure_date, vendor_key, payment_status, duplicate "
        "FROM txn ORDER BY work_id, source_row"
    )
    for work_id, rows in itertools.groupby(cursor, key=itemgetter(0)):
        total = Decimal(0)
        success_total = Decimal(0)
        in_progress_total = Decimal(0)
        duplicate_total = Decimal(0)
        deduplicated_total = Decimal(0)
        count = amount_missing = date_missing = negatives = 0
        duplicate_count = deduplicated_count = 0
        first: str | None = None
        last: str | None = None
        vendors: set[str] = set()
        for _, amount, day, vendor_key, payment_status, duplicate in rows:
            count += 1
            if duplicate:
                duplicate_count += 1
            else:
                deduplicated_count += 1
            if amount is None:
                amount_missing += 1
            else:
                value = Decimal(amount)
                total += value
                if payment_status == SUCCESS_STATUS:
                    success_total += value
                elif payment_status == IN_PROGRESS_STATUS:
                    in_progress_total += value
                if duplicate:
                    duplicate_total += value
                else:
                    deduplicated_total += value
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
        duplicate_ratio = (
            (Decimal(duplicate_count) / Decimal(count)).quantize(DUPLICATE_RATIO_PLACES) if count else Decimal(0)
        )
        yield {
            "work_id": work_id,
            "total_disbursed_all_rows": total,
            "total_disbursed": total,
            "success_amount": success_total,
            "in_progress_amount": in_progress_total,
            "exact_duplicate_amount": duplicate_total,
            "deduplicated_disbursed_amount": deduplicated_total,
            "payment_count_all_rows": count,
            "payment_count": count,
            "deduplicated_payment_count": deduplicated_count,
            "duplicate_record_count": duplicate_count,
            "duplicate_ratio": duplicate_ratio,
            "first_expenditure_date": first,
            "last_expenditure_date": last,
            "vendor_count": len(vendors),
            "amount_missing_count": amount_missing,
            "date_missing_count": date_missing,
            "negative_amount_count": negatives,
        }


def write_expenditure_by_work(conn: sqlite3.Connection, sink: CsvSink, stats: ExpenditureStats) -> None:
    for aggregate in iter_expenditure_aggregates(conn):
        sink.write(aggregate)
        stats.observe(aggregate)
