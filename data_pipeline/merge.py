"""Merge Recommended / Sanctioned / Completed into one row per canonical Work ID.

Rules (repeated in the generated data dictionary):

* Rows are joined on the canonical Work ID only - never on Sr. No.
* A Work ID repeated inside one source keeps its *first* row (lowest source
  row); every repeat is logged in ``work_id_duplicates.csv`` as ``identical``
  or ``conflicting`` together with the fields that differ.
* Descriptive fields (category, work, state, IDA, MP, constituency,
  description): first non-empty value in the order Sanctioned > Recommended >
  Completed.  ``sanction_date`` prefers Sanctioned, ``recommended_date``
  prefers Recommended.  Amount / status / completion fields have one source each.
* If two sources hold *different* non-empty values for a field, the preferred
  value is used **and** the disagreement is written to ``work_conflicts.csv``.
  Differences in case / spacing / punctuation only are counted separately.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import heapq
import itertools
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from operator import itemgetter

from data_pipeline.aggregate import iter_expenditure_aggregates
from data_pipeline.parsing import financial_year_label, fold_person_name, fold_text
from data_pipeline.schemas import SOURCES
from data_pipeline.sinks import CsvSink
from data_pipeline.stats import PipelineStats

MASTER_COLUMNS = [
    "work_id", "id_mp_code", "id_financial_year",
    "work_category", "work", "state", "ida", "mp_name", "constituency", "work_description",
    "recommended_date", "recommended_amount", "sanction_date", "sanction_amount", "work_status",
    "completion_date", "completed_amount_disbursed",
    "in_recommended", "in_sanctioned", "in_completed",
    "recommended_source_row", "sanctioned_source_row", "completed_source_row",
    "recommended_row_count", "sanctioned_row_count", "completed_row_count",
    "conflict_fields",
]
CONFLICT_COLUMNS = [
    "work_id", "field", "chosen_source", "chosen_value",
    "recommended_value", "sanctioned_value", "completed_value",
]
DUPLICATE_COLUMNS = ["source", "work_id", "source_row", "kept_source_row", "relation", "differing_fields"]
UNMATCHED_COLUMNS = ["issue", "work_id"]

DESCRIPTIVE_FIELDS = ("work_category", "work", "state", "ida", "mp_name", "constituency", "work_description")
DESCRIPTIVE_PRIORITY = ("sanctioned", "recommended", "completed")

TAG_LABELS = {
    "R": "Recommended",
    "S": "Sanctioned",
    "C": "Completed",
    "E": "Expenditure",
    "M": "Master (Recommended + Sanctioned + Completed)",
}
# (from, to, issue name, write the IDs to join_unmatched_ids.csv?)
# "from -> to" asks: is every ID in `from` also present in `to`?  The first five are
# relationships the recommend -> sanction -> complete -> pay flow implies; the last three
# only describe coverage (pending / in-progress works legitimately have no next stage).
CONTAINMENT = (
    ("S", "R", "sanctioned_not_in_recommended", True),
    ("C", "S", "completed_not_in_sanctioned", True),
    ("E", "S", "expenditure_not_in_sanctioned", True),
    ("E", "M", "expenditure_not_in_master", True),
    ("C", "E", "completed_without_expenditure", True),
    ("R", "S", "recommended_not_in_sanctioned", False),
    ("S", "C", "sanctioned_not_completed", False),
    ("S", "E", "sanctioned_without_expenditure", False),
)
EXTRA_ISSUE = "recommended_with_sanction_date_not_in_sanctioned"

# name -> (group, description)
CHECK_INFO = {
    "sanction_before_recommendation": ("Date order", "sanction_date earlier than recommended_date"),
    "completion_before_sanction": ("Date order", "completion_date earlier than sanction_date"),
    "completion_before_recommendation": ("Date order", "completion_date earlier than recommended_date"),
    "expenditure_before_recommendation": ("Date order", "first_expenditure_date earlier than recommended_date"),
    "expenditure_before_sanction": ("Date order", "first_expenditure_date earlier than sanction_date"),
    "completed_amount_exceeds_sanction": ("Amounts", "completed_amount_disbursed greater than sanction_amount"),
    "expenditure_total_exceeds_sanction": (
        "Amounts", "expenditure total_disbursed (all payment statuses) greater than sanction_amount"),
    "sanction_amount_differs_from_recommended": (
        "Informational", "sanction_amount differs from recommended_amount (partial sanction is possible)"),
    "sanction_amount_exceeds_recommended": ("Informational", "sanction_amount greater than recommended_amount"),
    "id_fy_differs_from_sanction_fy": (
        "Informational", "financial year inside the Work ID differs from the financial year of sanction_date"),
}

_EXACT_SUFFIXES = ("_date", "_amount", "_disbursed")


# ------------------------------------------------------------------ comparison helpers
def values_equal(field: str, a: str | None, b: str | None) -> bool:
    """Compare two normalised values.

    Text fields ignore case, spacing and punctuation; dates and amounts must
    match exactly (so ``-5`` never equals ``5``).
    """
    a, b = a or None, b or None
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a == b:
        return True
    if field.endswith(_EXACT_SUFFIXES):
        return False
    return fold_text(a) == fold_text(b)


@dataclass(frozen=True, slots=True)
class DuplicateFinding:
    row: Mapping[str, object]
    relation: str                       # "identical" | "conflicting"
    differing_fields: tuple[str, ...]


def resolve_duplicates(
    rows: Sequence[Mapping[str, object]], fields: Sequence[str]
) -> tuple[Mapping[str, object], list[DuplicateFinding]]:
    """Keep the first occurrence (lowest ``source_row``); classify every later row against it."""
    ordered = sorted(rows, key=itemgetter("source_row"))
    kept = ordered[0]
    findings = []
    for row in ordered[1:]:
        differing = tuple(f for f in fields if not values_equal(f, kept.get(f), row.get(f)))  # type: ignore[arg-type]
        findings.append(DuplicateFinding(row, "conflicting" if differing else "identical", differing))
    return kept, findings


# ------------------------------------------------------------------------ input streams
def _staged_groups(conn: sqlite3.Connection, table: str, tag: str) -> Iterator[tuple[str, str, list[dict]]]:
    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY work_id, source_row")
    names = [d[0] for d in cursor.description]
    for work_id, rows in itertools.groupby(cursor, key=itemgetter(1)):  # column 1 = work_id
        yield work_id, tag, [dict(zip(names, row)) for row in rows]


def _aggregate_groups(conn: sqlite3.Connection, tag: str) -> Iterator[tuple[str, str, list[dict]]]:
    for aggregate in iter_expenditure_aggregates(conn):
        yield aggregate["work_id"], tag, [aggregate]


# ------------------------------------------------------------------------- merge step
@dataclass
class MergeSinks:
    master: CsvSink
    conflicts: CsvSink
    duplicates: CsvSink
    unmatched: CsvSink


def _pick(
    field: str,
    candidates: list[tuple[str, str]],
    work_id: str,
    stats: PipelineStats,
    sinks: MergeSinks,
    conflict_fields: list[str],
) -> str | None:
    """Return the preferred value; log a conflict if any other source disagrees."""
    if not candidates:
        return None
    source, value = candidates[0]
    others = candidates[1:]
    if any(not values_equal(field, value, other) for _, other in others):
        by_source = dict(candidates)
        record = {
            "work_id": work_id,
            "field": field,
            "chosen_source": source,
            "chosen_value": value,
            "recommended_value": by_source.get("recommended"),
            "sanctioned_value": by_source.get("sanctioned"),
            "completed_value": by_source.get("completed"),
        }
        sinks.conflicts.write(record)
        stats.conflicts.by_field[field] += 1
        conflict_fields.append(field)
        samples = stats.conflicts.samples[field]
        if len(samples) < 5:
            samples.append(record)
    elif any(other != value for _, other in others):
        stats.conflicts.format_only[field] += 1
    return value


def _build_master_row(
    work_id: str,
    kept: dict[str, Mapping[str, object] | None],
    counts: dict[str, int],
    stats: PipelineStats,
    sinks: MergeSinks,
) -> dict[str, object]:
    rec, san, com = kept["recommended"], kept["sanctioned"], kept["completed"]
    _, mp_code, financial_year, _ = work_id.split("/")
    row: dict[str, object] = {"work_id": work_id, "id_mp_code": mp_code, "id_financial_year": financial_year}
    conflict_fields: list[str] = []

    def candidates(field: str, order: Sequence[str]) -> list[tuple[str, str]]:
        found = []
        for source in order:
            record = kept[source]
            value = record.get(field) if record else None
            if value:
                found.append((source, value))
        return found  # type: ignore[return-value]

    for field in DESCRIPTIVE_FIELDS:
        row[field] = _pick(field, candidates(field, DESCRIPTIVE_PRIORITY), work_id, stats, sinks, conflict_fields)
    row["recommended_date"] = _pick(
        "recommended_date", candidates("recommended_date", ("recommended", "sanctioned")),
        work_id, stats, sinks, conflict_fields,
    )
    row["sanction_date"] = _pick(
        "sanction_date", candidates("sanction_date", ("sanctioned", "recommended")),
        work_id, stats, sinks, conflict_fields,
    )
    row["recommended_amount"] = rec["recommended_amount"] if rec else None
    row["sanction_amount"] = san["sanction_amount"] if san else None
    row["work_status"] = san["work_status"] if san else None
    row["completion_date"] = com["completion_date"] if com else None
    row["completed_amount_disbursed"] = com["completed_amount_disbursed"] if com else None
    row["in_recommended"], row["in_sanctioned"], row["in_completed"] = (
        rec is not None, san is not None, com is not None,
    )
    row["recommended_source_row"] = rec["source_row"] if rec else None
    row["sanctioned_source_row"] = san["source_row"] if san else None
    row["completed_source_row"] = com["source_row"] if com else None
    row["recommended_row_count"] = counts["recommended"]
    row["sanctioned_row_count"] = counts["sanctioned"]
    row["completed_row_count"] = counts["completed"]
    row["conflict_fields"] = ";".join(conflict_fields)
    if conflict_fields:
        stats.conflicts.works_with_conflicts += 1
    return row


def _run_checks(work_id: str, row: Mapping[str, object], exp: Mapping[str, object] | None, stats: PipelineStats) -> None:
    """Cross-field checks on the merged row.  Each check only runs when its inputs exist."""
    record = stats.checks.record
    rd, sd, cd = row["recommended_date"], row["sanction_date"], row["completion_date"]
    ra, sa, ca = (
        Decimal(v) if v else None  # type: ignore[arg-type]
        for v in (row["recommended_amount"], row["sanction_amount"], row["completed_amount_disbursed"])
    )
    first = exp["first_expenditure_date"] if exp else None
    if rd and sd:
        record("sanction_before_recommendation", sd < rd, work_id)  # type: ignore[operator]
    if sd and cd:
        record("completion_before_sanction", cd < sd, work_id)  # type: ignore[operator]
    if rd and cd:
        record("completion_before_recommendation", cd < rd, work_id)  # type: ignore[operator]
    if first and rd:
        record("expenditure_before_recommendation", first < rd, work_id)  # type: ignore[operator]
    if first and sd:
        record("expenditure_before_sanction", first < sd, work_id)  # type: ignore[operator]
    if sa is not None and ca is not None:
        record("completed_amount_exceeds_sanction", ca > sa, work_id)
    if sa is not None and exp:
        record("expenditure_total_exceeds_sanction", exp["total_disbursed"] > sa, work_id)  # type: ignore[operator]
    if ra is not None and sa is not None:
        record("sanction_amount_differs_from_recommended", sa != ra, work_id)
        record("sanction_amount_exceeds_recommended", sa > ra, work_id)
    if sd:
        sanction_fy = financial_year_label(dt.date.fromisoformat(sd))  # type: ignore[arg-type]
        record("id_fy_differs_from_sanction_fy", sanction_fy != row["id_financial_year"], work_id)


def _demo_key(seed: str, work_id: str) -> int:
    digest = hashlib.blake2b(f"{seed}|{work_id}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def build_master(
    conn: sqlite3.Connection,
    sinks: MergeSinks,
    stats: PipelineStats,
    *,
    demo_size: int,
    demo_seed: str,
) -> set[str]:
    """Stream the four sorted inputs, write the master rows, fill the join/conflict stats.

    Returns the deterministic demo sample: the ``demo_size`` Work IDs (present in the
    master *and* in the expenditure data) with the smallest seeded hash.
    """
    streams = [
        _staged_groups(conn, "recommended", "R"),
        _staged_groups(conn, "sanctioned", "S"),
        _staged_groups(conn, "completed", "C"),
        _aggregate_groups(conn, "E"),
    ]
    merged = heapq.merge(*streams, key=itemgetter(0))
    join, diagnostics = stats.join, stats.diagnostics
    demo_heap: list[tuple[int, str]] = []  # max-heap on hash via negation -> bounded memory

    for work_id, group in itertools.groupby(merged, key=itemgetter(0)):
        by_tag = {tag: rows for _, tag, rows in group}
        rec_rows, san_rows, com_rows = by_tag.get("R", []), by_tag.get("S", []), by_tag.get("C", [])
        exp = by_tag["E"][0] if "E" in by_tag else None
        has = {"R": bool(rec_rows), "S": bool(san_rows), "C": bool(com_rows), "E": exp is not None}
        has["M"] = has["R"] or has["S"] or has["C"]

        # --- join analysis -------------------------------------------------------
        join.union_ids += 1
        join.patterns[(has["R"], has["S"], has["C"], has["E"])] += 1
        for tag in "RSCE":
            if has[tag]:
                join.unique[tag] += 1
        for a, b, issue, write in CONTAINMENT:
            if not has[a]:
                continue
            join.pair_total[(a, b)] += 1
            if has[b]:
                join.pair_match[(a, b)] += 1
            else:
                join.flag(issue, work_id)
                if write:
                    sinks.unmatched.write({"issue": issue, "work_id": work_id})
        if rec_rows and rec_rows[0].get("sanction_date") and not san_rows:
            join.flag(EXTRA_ISSUE, work_id)
            sinks.unmatched.write({"issue": EXTRA_ISSUE, "work_id": work_id})

        if not has["M"]:
            continue  # expenditure-only ID: reported as unmatched, no master row

        # --- duplicates inside a source ------------------------------------------
        kept: dict[str, Mapping[str, object] | None] = {}
        counts: dict[str, int] = {}
        for source, rows in (("recommended", rec_rows), ("sanctioned", san_rows), ("completed", com_rows)):
            counts[source] = len(rows)
            if not rows:
                kept[source] = None
                continue
            first_row, findings = resolve_duplicates(rows, SOURCES[source].stored_columns)
            kept[source] = first_row
            if findings:
                stats.duplicates.ids_with_duplicates[source] += 1
            for finding in findings:
                stats.duplicates.extra_rows[source] += 1
                (stats.duplicates.identical if finding.relation == "identical" else stats.duplicates.conflicting)[
                    source
                ] += 1
                record = {
                    "source": source,
                    "work_id": work_id,
                    "source_row": finding.row["source_row"],
                    "kept_source_row": first_row["source_row"],
                    "relation": finding.relation,
                    "differing_fields": ";".join(finding.differing_fields),
                }
                sinks.duplicates.write(record)
                samples = stats.duplicates.samples[source]
                if finding.relation == "conflicting" and len(samples) < 5:
                    samples.append(record)

        # --- master row + checks -------------------------------------------------
        row = _build_master_row(work_id, kept, counts, stats, sinks)
        sinks.master.write(row)
        join.master_rows += 1
        _run_checks(work_id, row, exp, stats)

        status = kept["sanctioned"].get("work_status") if kept["sanctioned"] else None
        key = status or ("(no sanctioned record)" if not kept["sanctioned"] else "(status missing)")
        entry = diagnostics.status_xtab.setdefault(key, [0, 0, 0])
        entry[0] += 1
        entry[1] += has["C"]
        entry[2] += has["E"]
        if row["mp_name"]:
            name_key = fold_person_name(row["mp_name"])  # type: ignore[arg-type]
            diagnostics.names_by_code[row["id_mp_code"]].add(name_key)  # type: ignore[index]
            diagnostics.codes_by_name[name_key].add(row["id_mp_code"])  # type: ignore[arg-type]

        if has["E"] and demo_size > 0:
            item = (-_demo_key(demo_seed, work_id), work_id)
            if len(demo_heap) < demo_size:
                heapq.heappush(demo_heap, item)
            elif item > demo_heap[0]:
                heapq.heapreplace(demo_heap, item)

    return {work_id for _, work_id in demo_heap}
