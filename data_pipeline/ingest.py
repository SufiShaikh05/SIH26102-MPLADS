"""Stream each workbook once: normalise, profile, and hand rows to disk.

* Recommended / Sanctioned / Completed -> SQLite staging tables (one per source)
* Expenditure -> ``expenditure_transactions.csv`` + SQLite ``txn`` table
* Allocation / Calamity -> small normalised CSVs

Rows whose Work ID cannot be parsed are never dropped silently: they are
written to ``malformed_ids.csv`` with the reason and the original text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from data_pipeline import staging
from data_pipeline.excel_io import SourceReader
from data_pipeline.parsing import (
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
from data_pipeline.work_id import WorkIdParse, parse_work_id

log = logging.getLogger("mplads.ingest")

BATCH_SIZE = 5_000

MALFORMED_COLUMNS = ["source", "source_row", "column", "raw_value", "reason", "detail"]
TXN_COLUMNS = [
    "work_id", "state", "work", "ida", "mp_name", "constituency", "expenditure_date",
    "vendor_name", "payment_status", "fund_disbursed_amount", "source_row", "duplicate_record",
]


@dataclass
class IngestContext:
    lo: dt.date                 # earliest plausible date
    hi: dt.date                 # "as of" date: later dates are flagged
    limit_rows: int | None      # smoke-test option: stop after N data rows per workbook
    progress_every: int
    malformed: CsvSink


def is_blank(record: Mapping[str, object]) -> bool:
    return all(classify_missing(value) == "empty" for value in record.values())


def normalize_row(
    spec: SourceSpec,
    record: Mapping[str, object],
    row_no: int,
    profile: SourceProfile,
    ctx: IngestContext,
) -> tuple[dict[str, str | None], WorkIdParse | None, int]:
    """Normalise one row, feed the profile, return ``(clean, id_parse, row_signature)``.

    ``row_signature`` hashes the normalised content (Sr. No. excluded) and is
    used to spot exact duplicate rows.  Invalid values contribute their raw
    text so two different bad values are never mistaken for duplicates.
    """
    clean: dict[str, str | None] = {}
    id_parse: WorkIdParse | None = None
    signature: list[str] = []
    for col in spec.columns:
        raw = record.get(col.name)
        kind = col.kind
        if kind in ("work", "work_id"):
            id_parse = parse_work_id(raw, bare=(kind == "work_id"))
            profile.id.observe(row_no, raw, id_parse)
            clean["work_id"] = id_parse.work_id
            signature.append(id_parse.work_id if id_parse.ok else "!" + str(raw))
            if kind == "work":
                if id_parse.ok:
                    remainder = id_parse.remainder or None
                    missing = None if remainder else "empty"
                else:
                    # ID unreadable: judge the cell itself, so a malformed ID is not counted as missing text
                    remainder, missing = normalize_text(raw)
                profile.columns[col.name].observe_text(row_no, remainder, remainder, missing)
                clean[col.name] = remainder
                signature.append(remainder or "")
        elif kind == "date":
            parsed = parse_date(raw)
            profile.columns[col.name].observe_date(row_no, raw, parsed, ctx.lo, ctx.hi)
            value = parsed.value.isoformat() if parsed.ok else None
            clean[col.name] = value
            signature.append(value or ("!" + str(raw) if parsed.status == "invalid" else ""))
        elif kind == "amount":
            parsed = parse_amount(raw)
            profile.columns[col.name].observe_amount(row_no, raw, parsed)
            value = decimal_to_str(parsed.value) if parsed.ok else None  # type: ignore[arg-type]
            clean[col.name] = value
            signature.append(value or ("!" + str(raw) if parsed.status == "invalid" else ""))
        else:  # text, sr_no
            cleaned, missing = normalize_text(raw)
            profile.columns[col.name].observe_text(row_no, raw, cleaned, missing)
            clean[col.name] = cleaned
            if kind != "sr_no":
                signature.append(cleaned or "")
    digest = hashlib.blake2b("\x1f".join(signature).encode("utf-8", "replace"), digest_size=8).digest()
    return clean, id_parse, int.from_bytes(digest, "big")


def _id_column(spec: SourceSpec) -> str:
    return next(c.name for c in spec.columns if c.kind in ("work", "work_id"))


def _log_malformed(ctx: IngestContext, spec: SourceSpec, profile: SourceProfile, row_no: int, parsed: WorkIdParse) -> None:
    column = _id_column(spec)
    header = profile.layout.header_for.get(column, column) if profile.layout else column
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


def ingest_work_source(
    spec: SourceSpec, path: Path, conn: sqlite3.Connection, profile: SourceProfile, ctx: IngestContext
) -> None:
    """Recommended / Sanctioned / Completed -> staging table keyed by canonical Work ID."""
    stored = list(spec.stored_columns)
    columns = ["source_row", "work_id", *stored]
    staging.create_work_table(conn, spec)
    seen_ids: set[str] = set()
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
            clean, id_parse, signature = normalize_row(spec, record, row_no, profile, ctx)
            if signature in seen_rows:
                profile.exact_duplicate_rows += 1
                if len(profile.duplicate_examples) < 10:
                    profile.duplicate_examples.append(row_no)
            else:
                seen_rows.add(signature)
            assert id_parse is not None
            if not id_parse.ok:
                _log_malformed(ctx, spec, profile, row_no, id_parse)
            else:
                seen_ids.add(id_parse.work_id)  # type: ignore[arg-type]
                batch.append((row_no, id_parse.work_id, *(clean[c] for c in stored)))
                if len(batch) >= BATCH_SIZE:
                    staging.insert_rows(conn, spec.key, columns, batch)
                    batch.clear()
            _progress(ctx, profile, started)
    if batch:
        staging.insert_rows(conn, spec.key, columns, batch)
    conn.commit()
    staging.index_work_table(conn, spec.key)
    profile.distinct_ids = len(seen_ids)
    log.info(
        "%s: done - %s data rows, %s valid IDs (%s distinct), %s malformed (%.0fs)",
        spec.key, f"{profile.data_rows:,}", f"{profile.id.valid:,}", f"{len(seen_ids):,}",
        f"{profile.id.malformed:,}", time.perf_counter() - started,
    )


def ingest_expenditure(
    spec: SourceSpec,
    path: Path,
    conn: sqlite3.Connection,
    profile: SourceProfile,
    ctx: IngestContext,
    sink: CsvSink,
) -> None:
    """One transactions CSV row per payment record; valid-ID rows also go to ``txn`` for aggregation."""
    staging.create_txn_table(conn)
    seen_rows: set[int] = set()
    status_stats: dict[str, list] = {}
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
            clean, id_parse, signature = normalize_row(spec, record, row_no, profile, ctx)
            assert id_parse is not None
            duplicate = 0
            if signature in seen_rows:
                duplicate = 1
                profile.exact_duplicate_rows += 1
                if len(profile.duplicate_examples) < 10:
                    profile.duplicate_examples.append(row_no)
            else:
                seen_rows.add(signature)
            if not id_parse.ok:
                _log_malformed(ctx, spec, profile, row_no, id_parse)
            sink.write({**clean, "source_row": row_no, "duplicate_record": duplicate})

            status = clean["payment_status"] or "(missing)"
            entry = status_stats.setdefault(status, [0, Decimal(0)])
            entry[0] += 1
            if clean["fund_disbursed_amount"] is not None:
                entry[1] += Decimal(clean["fund_disbursed_amount"])

            if id_parse.ok:
                batch.append(
                    (
                        row_no,
                        id_parse.work_id,
                        clean["fund_disbursed_amount"],
                        clean["expenditure_date"],
                        fold_text(clean["vendor_name"]) or None,
                        duplicate,
                    )
                )
                if len(batch) >= BATCH_SIZE:
                    staging.insert_rows(
                        conn, "txn",
                        ["source_row", "work_id", "amount", "expenditure_date", "vendor_key", "duplicate"],
                        batch,
                    )
                    batch.clear()
            _progress(ctx, profile, started)
    if batch:
        staging.insert_rows(
            conn, "txn",
            ["source_row", "work_id", "amount", "expenditure_date", "vendor_key", "duplicate"],
            batch,
        )
    conn.commit()
    staging.index_txn_table(conn)
    profile.extra["payment_status_amounts"] = {k: (v[0], v[1]) for k, v in status_stats.items()}
    log.info(
        "expenditure: done - %s data rows, %s valid IDs, %s malformed (%.0fs)",
        f"{profile.data_rows:,}", f"{profile.id.valid:,}", f"{profile.id.malformed:,}", time.perf_counter() - started,
    )


def ingest_reference(
    spec: SourceSpec, path: Path, profile: SourceProfile, ctx: IngestContext, sink: CsvSink
) -> None:
    """Allocation / Calamity: normalise and write straight to CSV (both are small)."""
    seen_rows: set[int] = set()
    started = time.perf_counter()
    with SourceReader(spec, path) as reader:
        profile.layout = reader.layout
        for row_no, record in reader.rows():
            state = _next_data_row(profile, record, ctx)
            if state == "blank":
                continue
            if state == "stop":
                break
            clean, _, signature = normalize_row(spec, record, row_no, profile, ctx)
            duplicate = 0
            if signature in seen_rows:
                duplicate = 1
                profile.exact_duplicate_rows += 1
                if len(profile.duplicate_examples) < 10:
                    profile.duplicate_examples.append(row_no)
            else:
                seen_rows.add(signature)
            sink.write({**clean, "source_row": row_no, "duplicate_record": duplicate})
    log.info("%s: done - %s data rows (%.0fs)", spec.key, f"{profile.data_rows:,}", time.perf_counter() - started)
