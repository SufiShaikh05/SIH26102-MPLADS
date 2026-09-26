"""Stream each workbook once: normalise, classify, profile, and hand rows to disk.

Every row is judged before it becomes a record:

* A footer/summary row (see :mod:`data_pipeline.footer_detection`) is written to
  ``summary_rows.csv`` and excluded from every analytical output - it never
  reaches a column profile, a staging table, or the malformed-ID log.  A row
  is judged this way on its own content only; lacking a Work ID is never, by
  itself, one of the signals.
* A Recommended row whose Work ID reads as the ``NA-...`` unkeyed convention
  (:func:`data_pipeline.work_id.is_unkeyed_na`) is written to
  ``unkeyed_recommendations.csv`` instead of ``malformed_ids.csv``: a
  legitimate record category (not yet assigned an ID), kept apart from both
  footers and genuine malformed IDs.
* Everything else proceeds as before:

  * Recommended / Sanctioned / Completed -> SQLite staging table (one per source)
  * Expenditure -> ``expenditure_transactions.csv`` + SQLite ``txn`` table
  * Allocation / Calamity -> small normalised CSVs

Recommended / Sanctioned / Completed stage only ID-valid candidate rows
straight into their SQLite table, as before, so that table is exactly the
input ``merge.build_master`` reads. Expenditure, Allocation and Calamity
can no longer write their final CSV during the streaming pass, because the
aggregate footer check (signal 3 - a row's amount exactly equals half the
column's total) needs the *whole* column total first. Those three now stage
every surviving candidate row into a narrow SQLite table; once the post-pass
aggregate check has removed at most one more row, a second, cheap pass over
that table (in ``source_row`` order, its primary key) writes the real
output. Every staging table is fully resolved - footers removed - before any
final output is written from it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from data_pipeline import footer_detection, staging
from data_pipeline.excel_io import SourceReader
from data_pipeline.footer_detection import FooterVerdict
from data_pipeline.parsing import (
    Parsed,
    classify_missing,
    decimal_to_str,
    fold_text,
    normalize_text,
    parse_amount,
    parse_date,
)
from data_pipeline.profiling import SourceProfile
from data_pipeline.schemas import SourceSpec
from data_pipeline.sinks import CsvSink
from data_pipeline.stats import PipelineStats
from data_pipeline.work_id import WorkIdParse, is_unkeyed_na, parse_work_id

log = logging.getLogger("mplads.ingest")

BATCH_SIZE = 5_000

MALFORMED_COLUMNS = ["source", "source_row", "column", "raw_value", "reason", "detail"]
UNKEYED_COLUMNS = ["source", "source_row", "raw_value", "state", "mp_name", "constituency", "recommended_amount"]
SUMMARY_COLUMNS = ["source", "source_row", "reason", "representative_values"]
TXN_COLUMNS = [
    "work_id", "state", "work", "ida", "mp_name", "constituency", "expenditure_date",
    "vendor_name", "payment_status", "fund_disbursed_amount", "source_row", "duplicate_record",
]
# expenditure_candidates: every field a transaction row needs, minus source_row/duplicate_record
# (the candidate table's own primary key and flag column), plus vendor_key for aggregation.
EXPENDITURE_CANDIDATE_COLUMNS = [
    "work_id", "state", "work", "ida", "mp_name", "constituency", "expenditure_date",
    "vendor_name", "payment_status", "fund_disbursed_amount", "vendor_key",
]
TXN_TABLE_COLUMNS = ["source_row", "work_id", "amount", "expenditure_date", "vendor_key", "payment_status", "duplicate"]


@dataclass
class IngestContext:
    lo: dt.date                 # earliest plausible date
    hi: dt.date                 # "as of" date: later dates are flagged
    limit_rows: int | None      # smoke-test option: stop after N data rows per workbook
    progress_every: int
    malformed: CsvSink
    summary: CsvSink
    unkeyed: CsvSink
    stats: PipelineStats


def is_blank(record: Mapping[str, object]) -> bool:
    return all(classify_missing(value) == "empty" for value in record.values())


def _work_column_name(spec: SourceSpec) -> str | None:
    """The spec's ``work``/``work_id`` column name, or ``None`` for an unkeyed source
    (Allocation, Calamity)."""
    return next((c.name for c in spec.columns if c.kind in ("work", "work_id")), None)


@dataclass
class RowEvaluation:
    """Everything computed for one row, before any of it touches a profile or a sink."""

    clean: dict[str, str | None]
    missing: dict[str, str | None] = field(default_factory=dict)   # text/sr_no/work columns
    parsed_fields: dict[str, Parsed] = field(default_factory=dict)  # date/amount columns
    id_parse: WorkIdParse | None = None
    footer: FooterVerdict = footer_detection.NOT_FOOTER
    na_unkeyed: bool = False
    signature: int = 0


def evaluate_row(spec: SourceSpec, record: Mapping[str, object]) -> RowEvaluation:
    """Normalise every column and judge the row - footer, unkeyed, or genuine data.

    Work-ID parsing only runs once the row has already cleared the footer check: a
    footer row's Work cell is never even attempted, so it can never contaminate
    ``malformed_ids.csv`` (or, for Recommended, ``unkeyed_recommendations.csv``).
    """
    clean: dict[str, str | None] = {}
    missing: dict[str, str | None] = {}
    parsed_fields: dict[str, Parsed] = {}
    work_col = _work_column_name(spec)

    for col in spec.columns:
        raw = record.get(col.name)
        if col.name == work_col:
            if col.kind == "work_id":
                clean["work_id"] = None  # filled in below, once we know this isn't a footer
            else:
                text, kind_missing = normalize_text(raw)
                clean["work"] = text            # provisional: the raw cell, whitespace-normalised
                missing["work"] = kind_missing  # replaced below if the ID parses
            continue
        if col.kind == "date":
            parsed = parse_date(raw)
            parsed_fields[col.name] = parsed
            clean[col.name] = parsed.value.isoformat() if parsed.ok else None
        elif col.kind == "amount":
            parsed = parse_amount(raw)
            parsed_fields[col.name] = parsed
            clean[col.name] = decimal_to_str(parsed.value) if parsed.ok else None
        else:
            cleaned, kind_missing = normalize_text(raw)
            clean[col.name] = cleaned
            missing[col.name] = kind_missing

    footer = footer_detection.classify_cheap(spec.key, record, clean, parsed_fields)
    if footer:
        return RowEvaluation(clean, missing, parsed_fields, None, footer, False, 0)

    id_parse: WorkIdParse | None = None
    na_unkeyed = False
    if work_col is not None:
        work_kind = next(c.kind for c in spec.columns if c.name == work_col)
        raw_work = record.get(work_col)
        id_parse = parse_work_id(raw_work, bare=(work_kind == "work_id"))
        clean["work_id"] = id_parse.work_id
        if id_parse.ok:
            if work_kind == "work":
                remainder = id_parse.remainder or None
                clean["work"] = remainder
                missing["work"] = None if remainder else "empty"
        else:
            na_unkeyed = spec.key == "recommended" and is_unkeyed_na(raw_work)

    signature = _build_signature(spec, record, clean, parsed_fields, id_parse, work_col)
    return RowEvaluation(clean, missing, parsed_fields, id_parse, footer, na_unkeyed, signature)


def _build_signature(
    spec: SourceSpec,
    record: Mapping[str, object],
    clean: Mapping[str, str | None],
    parsed_fields: Mapping[str, Parsed],
    id_parse: WorkIdParse | None,
    work_col: str | None,
) -> int:
    """Hash of the normalised row content (Sr. No. excluded) - spots exact duplicate rows.
    Invalid values contribute their raw text so two different bad values are never confused.
    """
    parts: list[str] = []
    for col in spec.columns:
        raw = record.get(col.name)
        if col.name == work_col:
            assert id_parse is not None
            parts.append(id_parse.work_id if id_parse.ok else "!" + str(raw))
            if col.kind == "work":
                parts.append(clean.get("work") or "")
            continue
        if col.kind in ("date", "amount"):
            parsed = parsed_fields[col.name]
            value = clean.get(col.name)
            parts.append(value or ("!" + str(raw) if parsed.status == "invalid" else ""))
        elif col.kind != "sr_no":
            parts.append(clean.get(col.name) or "")
    digest = hashlib.blake2b("\x1f".join(parts).encode("utf-8", "replace"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _observe_row(
    spec: SourceSpec, record: Mapping[str, object], row_no: int, ev: RowEvaluation, profile: SourceProfile, ctx: IngestContext
) -> None:
    """Feed a NON-footer row's already-computed values into the column/ID profiles - the
    same observations the original single-pass code made inline, just replayed from
    pre-computed values so a footer row's numbers can never reach these counters."""
    work_col = _work_column_name(spec)
    for col in spec.columns:
        raw = record.get(col.name)
        if col.name == work_col:
            assert ev.id_parse is not None
            profile.id.observe(row_no, raw, ev.id_parse, na_unkeyed=ev.na_unkeyed)
            if col.kind == "work":
                remainder = ev.clean.get("work")
                profile.columns[col.name].observe_text(row_no, remainder, remainder, ev.missing.get("work"))
            continue
        if col.kind == "date":
            profile.columns[col.name].observe_date(row_no, raw, ev.parsed_fields[col.name], ctx.lo, ctx.hi)
        elif col.kind == "amount":
            profile.columns[col.name].observe_amount(row_no, raw, ev.parsed_fields[col.name])
        else:
            profile.columns[col.name].observe_text(row_no, raw, ev.clean.get(col.name), ev.missing.get(col.name))


def _log_malformed(ctx: IngestContext, spec: SourceSpec, profile: SourceProfile, row_no: int, parsed: WorkIdParse) -> None:
    column = _work_column_name(spec)
    header = profile.layout.header_for.get(column, column) if profile.layout and column else column
    ctx.malformed.write(
        {
            "source": spec.key,
            "source_row": row_no,
            "column": header,
            "raw_value": parsed.raw[:500],
            "reason": parsed.error,
            "detail": parsed.detail,
        }
    )


def _log_unkeyed(ctx: IngestContext, spec: SourceSpec, row_no: int, record: Mapping[str, object], ev: RowEvaluation) -> None:
    work_col = _work_column_name(spec)
    ctx.unkeyed.write(
        {
            "source": spec.key,
            "source_row": row_no,
            "raw_value": str(record.get(work_col))[:300] if work_col else "",
            "state": ev.clean.get("state"),
            "mp_name": ev.clean.get("mp_name"),
            "constituency": ev.clean.get("constituency"),
            "recommended_amount": ev.clean.get("recommended_amount"),
        }
    )
    ctx.stats.unkeyed.record(spec.key, row_no)


def _log_summary_row(ctx: IngestContext, source_key: str, row_no: int, verdict: FooterVerdict, record: Mapping[str, object]) -> None:
    """Write one excluded footer/summary row to the audit CSV and the report stats.

    ``record`` is either the raw reader row (a cheap-signal footer, judged before any
    staging) or a staged row fetched back from a staging table (an aggregate-check
    match) - :func:`~data_pipeline.parsing.parse_amount` reads either representation.
    """
    ctx.summary.write(
        {
            "source": source_key,
            "source_row": row_no,
            "reason": verdict.reason,
            "representative_values": footer_detection.representative_values(source_key, record),
        }
    )
    amount = None
    amount_col = footer_detection.PRIMARY_AMOUNT.get(source_key)
    if amount_col is not None:
        parsed = parse_amount(record.get(amount_col))
        if parsed.ok:
            amount = parsed.value
    assert verdict.reason is not None
    ctx.stats.summary_rows.record(source_key, verdict.reason, row_no, amount)


def _exclude_as_summary_row(profile: SourceProfile, ctx: IngestContext, source_key: str, row_no: int, verdict: FooterVerdict, record: Mapping[str, object]) -> None:
    _log_summary_row(ctx, source_key, row_no, verdict, record)
    profile.data_rows -= 1
    profile.summary_rows_excluded += 1


def _progress(ctx: IngestContext, profile: SourceProfile, started: float) -> None:
    if profile.data_rows % ctx.progress_every == 0:
        log.info("%s: %s data rows read (%.0fs)", profile.key, f"{profile.data_rows:,}", time.perf_counter() - started)


def _next_data_row(profile: SourceProfile, record: Mapping[str, object], ctx: IngestContext) -> str:
    """Bookkeeping shared by all ingesters: returns ``"blank"``, ``"stop"`` or ``"data"``."""
    profile.rows_read += 1
    if is_blank(record):
        profile.blank_rows += 1
        return "blank"
    if ctx.limit_rows is not None and profile.data_rows >= ctx.limit_rows:
        profile.limit_hit = True
        return "stop"
    profile.data_rows += 1
    return "data"


def _apply_aggregate_footer_check(conn: sqlite3.Connection, table: str, source_key: str, profile: SourceProfile, ctx: IngestContext) -> None:
    """Signal 3 (post-pass): remove at most one row from ``table`` whose primary amount
    equals half the column's running total, and log it to ``summary_rows.csv``.

    Also recomputes that amount column's authoritative sum/count/min/max/negatives/zeros
    when a row is found, since it had already been counted by :func:`_observe_row` before
    anyone could know it was a footer.
    """
    amount_col = footer_detection.PRIMARY_AMOUNT.get(source_key)
    col_profile = profile.columns.get(amount_col) if amount_col else None
    if amount_col is None or col_profile is None:
        return
    total = col_profile.amount_sum
    match = footer_detection.find_aggregate_match(conn.execute(f"SELECT source_row, {amount_col} FROM {table}"), total)
    if match is None:
        return

    record = staging.fetch_row(conn, table, match)
    assert record is not None
    staging.delete_row(conn, table, match)
    verdict = FooterVerdict(
        True, "invalid_record_shape",
        f"{amount_col}={record.get(amount_col)!r} equals the total of every other {source_key} row",
    )
    _exclude_as_summary_row(profile, ctx, source_key, match, verdict, record)
    if "work_id" in record:  # this source calls profile.id.observe() for every candidate row
        profile.id.total -= 1
        work_id = record.get("work_id")
        if work_id is not None:
            profile.id.valid -= 1
            prefix, _, financial_year, _ = work_id.split("/")
            for counter, key in ((profile.id.prefixes, prefix), (profile.id.financial_years, financial_year)):
                counter[key] -= 1
                if counter[key] <= 0:
                    del counter[key]

    fresh = footer_detection.scan_amount_column(conn.execute(f"SELECT {amount_col} FROM {table}"))
    col_profile.total -= 1
    col_profile.amount_sum, col_profile.n_amounts = fresh.sum, fresh.count
    col_profile.negatives, col_profile.zeros = fresh.negatives, fresh.zeros
    col_profile.min_amount, col_profile.max_amount = fresh.min, fresh.max


def ingest_work_source(
    spec: SourceSpec, path: Path, conn: sqlite3.Connection, profile: SourceProfile, ctx: IngestContext
) -> None:
    """Recommended / Sanctioned / Completed -> staging table keyed by canonical Work ID."""
    stored = list(spec.stored_columns)
    columns = ["source_row", "work_id", *stored]
    staging.create_work_table(conn, spec)
    seen_rows: set[int] = set()
    batch: list[tuple[object, ...]] = []
    started = time.perf_counter()
    with SourceReader(spec, path) as reader:
        profile.layout = reader.layout
        for row_no, record in reader.rows():
            state = _next_data_row(profile, record, ctx)
            if state == "blank":
                continue
            if state == "stop":
                break
            ev = evaluate_row(spec, record)
            if ev.footer:
                _exclude_as_summary_row(profile, ctx, spec.key, row_no, ev.footer, record)
                continue
            if ev.signature in seen_rows:
                profile.exact_duplicate_rows += 1
                if len(profile.duplicate_examples) < 10:
                    profile.duplicate_examples.append(row_no)
            else:
                seen_rows.add(ev.signature)
            _observe_row(spec, record, row_no, ev, profile, ctx)
            assert ev.id_parse is not None
            if not ev.id_parse.ok:
                if ev.na_unkeyed:
                    _log_unkeyed(ctx, spec, row_no, record, ev)
                else:
                    _log_malformed(ctx, spec, profile, row_no, ev.id_parse)
            else:
                batch.append((row_no, ev.id_parse.work_id, *(ev.clean[c] for c in stored)))
                if len(batch) >= BATCH_SIZE:
                    staging.insert_rows(conn, spec.key, columns, batch)
                    batch.clear()
            _progress(ctx, profile, started)
    if batch:
        staging.insert_rows(conn, spec.key, columns, batch)
    conn.commit()
    staging.index_work_table(conn, spec.key)

    _apply_aggregate_footer_check(conn, spec.key, spec.key, profile, ctx)
    profile.distinct_ids = conn.execute(f"SELECT COUNT(DISTINCT work_id) FROM {spec.key}").fetchone()[0]

    log.info(
        "%s: done - %s data rows, %s valid IDs (%s distinct), %s malformed, %s unkeyed, %s summary rows excluded (%.0fs)",
        spec.key, f"{profile.data_rows:,}", f"{profile.id.valid:,}", f"{profile.distinct_ids:,}",
        f"{profile.id.malformed:,}", f"{profile.id.na_unkeyed:,}", f"{profile.summary_rows_excluded:,}",
        time.perf_counter() - started,
    )


def ingest_expenditure(
    spec: SourceSpec, path: Path, conn: sqlite3.Connection, profile: SourceProfile, ctx: IngestContext, sink: CsvSink
) -> None:
    """Expenditure: stage every surviving candidate row, run the aggregate footer check once
    the whole column total is known, then emit ``expenditure_transactions.csv`` and the
    ``txn`` aggregation table from what's left (see module docstring for why)."""
    staging.create_candidate_table(conn, "expenditure_candidates", EXPENDITURE_CANDIDATE_COLUMNS)
    seen_rows: set[int] = set()
    batch: list[tuple[object, ...]] = []
    started = time.perf_counter()
    with SourceReader(spec, path) as reader:
        profile.layout = reader.layout
        for row_no, record in reader.rows():
            state = _next_data_row(profile, record, ctx)
            if state == "blank":
                continue
            if state == "stop":
                break
            ev = evaluate_row(spec, record)
            if ev.footer:
                _exclude_as_summary_row(profile, ctx, spec.key, row_no, ev.footer, record)
                continue
            duplicate = 0
            if ev.signature in seen_rows:
                duplicate = 1
                profile.exact_duplicate_rows += 1
                if len(profile.duplicate_examples) < 10:
                    profile.duplicate_examples.append(row_no)
            else:
                seen_rows.add(ev.signature)
            _observe_row(spec, record, row_no, ev, profile, ctx)
            assert ev.id_parse is not None
            if not ev.id_parse.ok:
                _log_malformed(ctx, spec, profile, row_no, ev.id_parse)

            batch.append(
                (
                    row_no, duplicate, ev.clean["work_id"], ev.clean["state"], ev.clean["work"], ev.clean["ida"],
                    ev.clean["mp_name"], ev.clean["constituency"], ev.clean["expenditure_date"], ev.clean["vendor_name"],
                    ev.clean["payment_status"], ev.clean["fund_disbursed_amount"], fold_text(ev.clean["vendor_name"]) or None,
                )
            )
            if len(batch) >= BATCH_SIZE:
                staging.insert_rows(conn, "expenditure_candidates", ["source_row", "duplicate_record", *EXPENDITURE_CANDIDATE_COLUMNS], batch)
                batch.clear()
            _progress(ctx, profile, started)
    if batch:
        staging.insert_rows(conn, "expenditure_candidates", ["source_row", "duplicate_record", *EXPENDITURE_CANDIDATE_COLUMNS], batch)
    conn.commit()

    _apply_aggregate_footer_check(conn, "expenditure_candidates", spec.key, profile, ctx)

    staging.create_txn_table(conn)
    status_stats: dict[str, list] = {}
    txn_batch: list[tuple[object, ...]] = []
    cursor = conn.execute("SELECT * FROM expenditure_candidates ORDER BY source_row")
    names = [d[0] for d in cursor.description]
    for row in cursor:
        record = dict(zip(names, row))
        sink.write(record)

        status = record["payment_status"] or "(missing)"
        entry = status_stats.setdefault(status, [0, Decimal(0)])
        entry[0] += 1
        if record["fund_disbursed_amount"] is not None:
            entry[1] += Decimal(record["fund_disbursed_amount"])

        if record["work_id"] is not None:
            txn_batch.append(
                (row[0], record["work_id"], record["fund_disbursed_amount"], record["expenditure_date"],
                 record["vendor_key"], record["payment_status"], record["duplicate_record"])
            )
            if len(txn_batch) >= BATCH_SIZE:
                staging.insert_rows(conn, "txn", TXN_TABLE_COLUMNS, txn_batch)
                txn_batch.clear()
    if txn_batch:
        staging.insert_rows(conn, "txn", TXN_TABLE_COLUMNS, txn_batch)
    conn.commit()
    staging.index_txn_table(conn)
    profile.extra["payment_status_amounts"] = {k: (v[0], v[1]) for k, v in status_stats.items()}

    log.info(
        "expenditure: done - %s data rows, %s valid IDs, %s malformed, %s summary rows excluded (%.0fs)",
        f"{profile.data_rows:,}", f"{profile.id.valid:,}", f"{profile.id.malformed:,}",
        f"{profile.summary_rows_excluded:,}", time.perf_counter() - started,
    )


def ingest_reference(
    spec: SourceSpec, path: Path, conn: sqlite3.Connection, profile: SourceProfile, ctx: IngestContext, sink: CsvSink
) -> None:
    """Allocation / Calamity: stage every surviving candidate row (both are small, but the
    aggregate footer check still needs the whole column total before anything is written),
    then emit the normalised CSV from what's left."""
    table = f"{spec.key}_candidates"
    stored = list(spec.stored_columns)
    staging.create_candidate_table(conn, table, stored)
    seen_rows: set[int] = set()
    batch: list[tuple[object, ...]] = []
    started = time.perf_counter()
    with SourceReader(spec, path) as reader:
        profile.layout = reader.layout
        for row_no, record in reader.rows():
            state = _next_data_row(profile, record, ctx)
            if state == "blank":
                continue
            if state == "stop":
                break
            ev = evaluate_row(spec, record)
            if ev.footer:
                _exclude_as_summary_row(profile, ctx, spec.key, row_no, ev.footer, record)
                continue
            duplicate = 0
            if ev.signature in seen_rows:
                duplicate = 1
                profile.exact_duplicate_rows += 1
                if len(profile.duplicate_examples) < 10:
                    profile.duplicate_examples.append(row_no)
            else:
                seen_rows.add(ev.signature)
            _observe_row(spec, record, row_no, ev, profile, ctx)
            batch.append((row_no, duplicate, *(ev.clean[c] for c in stored)))
            if len(batch) >= BATCH_SIZE:
                staging.insert_rows(conn, table, ["source_row", "duplicate_record", *stored], batch)
                batch.clear()
    if batch:
        staging.insert_rows(conn, table, ["source_row", "duplicate_record", *stored], batch)
    conn.commit()

    _apply_aggregate_footer_check(conn, table, spec.key, profile, ctx)

    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY source_row")
    names = [d[0] for d in cursor.description]
    for row in cursor:
        sink.write(dict(zip(names, row)))

    log.info(
        "%s: done - %s data rows, %s summary rows excluded (%.0fs)",
        spec.key, f"{profile.data_rows:,}", f"{profile.summary_rows_excluded:,}", time.perf_counter() - started,
    )
