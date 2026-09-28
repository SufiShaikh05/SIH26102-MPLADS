"""Build ``work_features_v0.csv`` from data_pipeline's already-cleaned ``works_master.csv``
and ``expenditure_by_work.csv``.

This module only reads those two files - it never touches ``data_pipeline/``, the raw
workbooks, or any of the frozen Work-ID / footer-detection / expenditure-aggregation logic.
Everything here is a left join plus straightforward derived arithmetic; nothing here is a
machine-learning model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from data_pipeline.parsing import classify_missing
from data_pipeline.sinks import to_cell
from feature_engineering import feature_definitions as fd
from feature_engineering import profiling

log = logging.getLogger("feature_engineering.build_features")


class FeatureBuildError(RuntimeError):
    """The input CSVs don't match the contract this module expects from data_pipeline."""


# ---------------------------------------------------------------------------------- loading
def load_csv_as_dicts(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Returns ``(header, rows)``. The header comes from the CSV reader directly (not from
    inspecting the first data row), so a legitimately empty file - a header with zero data
    rows, e.g. no expenditure recorded against anything yet - is not treated as an error."""
    if not path.is_file():
        raise FeatureBuildError(f"required input file not found: {path}")
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        header = list(reader.fieldnames or [])
    return header, rows


def _require_columns(header: list[str], required: tuple[str, ...], path: Path) -> None:
    missing = [c for c in required if c not in header]
    if missing:
        raise FeatureBuildError(f"{path}: missing required column(s) {missing}; found {header}")


def _index_by_work_id(rows: list[dict[str, str]], path: Path) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    duplicates: list[str] = []
    for row in rows:
        work_id = row.get("work_id")
        if not work_id:
            continue
        if work_id in index:
            duplicates.append(work_id)
        else:
            index[work_id] = row
    if duplicates:
        sample = ", ".join(duplicates[:10])
        raise FeatureBuildError(
            f"{path}: work_id is not unique ({len(duplicates)} duplicate id(s), e.g. {sample}) - "
            "this file is expected to have at most one row per work_id"
        )
    return index


# --------------------------------------------------------------------------- value parsing
# The inputs are already-cleaned data_pipeline output (plain decimal text, ISO dates, "1"/"0"
# flags) - these are thin, literal readers of that exact contract, not general-purpose
# parsers. A value that doesn't fit is a genuine contract break, so it raises rather than
# silently reinterpreting.
def _text_or_none(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def _decimal_or_none(value: str | None, *, field_name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise FeatureBuildError(f"{field_name}: {value!r} is not a valid decimal amount") from error


def _int_or_none(value: str | None, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as error:
        raise FeatureBuildError(f"{field_name}: {value!r} is not a valid integer") from error


def _date_or_none(value: str | None, *, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise FeatureBuildError(f"{field_name}: {value!r} is not a valid ISO date") from error


def _flag(value: str | None) -> bool:
    return value == "1"


# ------------------------------------------------------------------------------ derivations
def safe_ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """None on a missing or zero denominator - never a ZeroDivisionError, never a fabricated 0."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return (numerator / denominator).quantize(fd.RATIO_QUANT)


def day_diff(later: date | None, earlier: date | None) -> int | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).days


def clean_description(text: str | None) -> str | None:
    """Conservative, deterministic cleanup: collapse whitespace, blank a placeholder-only
    value. Wording, case, and every place/person/project term are left exactly as written."""
    if classify_missing(text) is not None:
        return None
    return " ".join(text.split())


_INCOMPLETE_MARKER = "incomplete"


def classify_work_status(status: str | None) -> tuple[bool, bool, bool]:
    """(is_completed, is_partially_completed, is_sanctioned_only) - mutually exclusive.
    All False when the work has no work_status (never sanctioned). See the profiling report
    for the exact distinct status values seen and how each one was classified."""
    if not status:
        return False, False, False
    text = status.casefold()
    if _INCOMPLETE_MARKER in text:   # "incomplete" contains "complet" - never read as completion
        return False, False, True
    has_complete = "complet" in text
    has_partial = "partial" in text
    if has_complete and has_partial:
        return False, True, False
    if has_complete:
        return True, False, False
    return False, False, True


# ------------------------------------------------------------------------------- row building
def build_row(master: dict[str, str], exp: dict[str, str] | None, snapshot: date) -> dict[str, object]:
    """One work's feature values as native Python types (Decimal/int/date/bool/str/None) -
    peer-group columns are added afterwards, in a separate pass over every row."""
    row: dict[str, object] = {}

    # Group 1 - identifiers / context
    row["work_id"] = master["work_id"]
    for name in ("state", "constituency", "mp_name", "ida", "work_category", "work_status",
                 "id_mp_code", "id_financial_year"):
        row[name] = _text_or_none(master.get(name))
    row["in_recommended"] = _flag(master.get("in_recommended"))
    row["in_sanctioned"] = _flag(master.get("in_sanctioned"))
    row["in_completed"] = _flag(master.get("in_completed"))

    # Group 2 - financial
    recommended_amount = _decimal_or_none(master.get("recommended_amount"), field_name="recommended_amount")
    sanction_amount = _decimal_or_none(master.get("sanction_amount"), field_name="sanction_amount")
    row["recommended_amount"] = recommended_amount
    row["sanction_amount"] = sanction_amount
    row["recommendation_to_sanction_amount_ratio"] = safe_ratio(sanction_amount, recommended_amount)

    total_disbursed = _decimal_or_none(exp.get("total_disbursed_all_rows") if exp else None, field_name="total_disbursed_all_rows")
    success_amount = _decimal_or_none(exp.get("success_amount") if exp else None, field_name="success_amount")
    in_progress_amount = _decimal_or_none(exp.get("in_progress_amount") if exp else None, field_name="in_progress_amount")
    exact_duplicate_amount = _decimal_or_none(exp.get("exact_duplicate_amount") if exp else None, field_name="exact_duplicate_amount")
    deduplicated_amount = _decimal_or_none(exp.get("deduplicated_disbursed_amount") if exp else None, field_name="deduplicated_disbursed_amount")
    row["total_disbursed_all_rows"] = total_disbursed
    row["success_amount"] = success_amount
    row["in_progress_amount"] = in_progress_amount
    row["exact_duplicate_amount"] = exact_duplicate_amount
    row["deduplicated_disbursed_amount"] = deduplicated_amount
    row["success_utilization_ratio"] = safe_ratio(success_amount, sanction_amount)
    row["gross_utilization_ratio"] = safe_ratio(total_disbursed, sanction_amount)
    row["deduplicated_utilization_ratio"] = safe_ratio(deduplicated_amount, sanction_amount)
    row["remaining_sanction_amount"] = (
        sanction_amount - deduplicated_amount if sanction_amount is not None and deduplicated_amount is not None else None
    )
    row["in_progress_ratio"] = safe_ratio(in_progress_amount, total_disbursed)
    row["duplicate_amount_ratio"] = safe_ratio(exact_duplicate_amount, total_disbursed)

    # Group 3 - time (Group 5's completion_date is parsed below and reused here)
    recommended_date = _date_or_none(master.get("recommended_date"), field_name="recommended_date")
    sanction_date = _date_or_none(master.get("sanction_date"), field_name="sanction_date")
    completion_date = _date_or_none(master.get("completion_date"), field_name="completion_date")
    first_payment = _date_or_none(exp.get("first_expenditure_date") if exp else None, field_name="first_expenditure_date")
    last_payment = _date_or_none(exp.get("last_expenditure_date") if exp else None, field_name="last_expenditure_date")
    row["recommendation_to_sanction_days"] = day_diff(sanction_date, recommended_date)
    row["sanction_to_first_payment_days"] = day_diff(first_payment, sanction_date)
    row["sanction_to_last_payment_days"] = day_diff(last_payment, sanction_date)
    row["sanction_to_completion_days"] = day_diff(completion_date, sanction_date)
    row["days_since_sanction"] = day_diff(snapshot, sanction_date)
    row["days_since_last_payment"] = day_diff(snapshot, last_payment)

    # Group 4 - transaction behavior
    payment_count = _int_or_none(exp.get("payment_count_all_rows") if exp else None, field_name="payment_count_all_rows")
    row["payment_count_all_rows"] = payment_count
    row["deduplicated_payment_count"] = _int_or_none(exp.get("deduplicated_payment_count") if exp else None, field_name="deduplicated_payment_count")
    row["vendor_count"] = _int_or_none(exp.get("vendor_count") if exp else None, field_name="vendor_count")
    row["duplicate_record_count"] = _int_or_none(exp.get("duplicate_record_count") if exp else None, field_name="duplicate_record_count")
    row["duplicate_ratio"] = _decimal_or_none(exp.get("duplicate_ratio") if exp else None, field_name="duplicate_ratio")
    span = day_diff(last_payment, first_payment)
    row["payment_span_days"] = span
    single_payment = payment_count == 1
    row["single_payment_work"] = bool(single_payment)
    if span is not None and payment_count is not None and payment_count > 1:
        row["average_payment_interval_days"] = (Decimal(span) / Decimal(payment_count - 1)).quantize(fd.RATIO_QUANT)
    else:
        row["average_payment_interval_days"] = None   # never 0 for a single payment - see single_payment_work

    # Group 5 - completion / execution
    is_completed, is_partially_completed, is_sanctioned_only = classify_work_status(master.get("work_status"))
    row["is_completed"] = is_completed
    row["is_partially_completed"] = is_partially_completed
    row["is_sanctioned_only"] = is_sanctioned_only and row["in_sanctioned"]
    row["completion_date"] = completion_date
    row["completed_amount_disbursed"] = _decimal_or_none(master.get("completed_amount_disbursed"), field_name="completed_amount_disbursed")

    # Group 6 - text preparation
    cleaned = clean_description(master.get("work_description"))
    row["cleaned_work_description"] = cleaned
    row["description_length_chars"] = len(cleaned) if cleaned is not None else None
    row["description_token_count"] = len(cleaned.split()) if cleaned is not None else None

    return row


# ------------------------------------------------------------------------- peer-group pass
def compute_peer_features(rows: list[dict[str, object]]) -> None:
    """Adds every ``peer_*`` column to each row in place - a second pass, because a peer
    statistic needs the whole population's values before any single row's can be filled in.

    Each group's median is computed exactly once (not once per member): with N rows split
    into groups, total work is O(N log N), not O(group_size^2) per group.
    """
    for key, group_cols in fd.PEER_GROUPINGS:
        groups: dict[tuple, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            group_key = tuple(row.get(c) for c in group_cols)
            groups[group_key].append(row)

        group_stats: dict[tuple, tuple[int, bool, Decimal | None, Decimal | None, object]] = {}
        for group_key, members in groups.items():
            count = len(members)
            sufficient = count >= fd.MIN_PEER_GROUP_SIZE and all(k is not None for k in group_key)
            if sufficient:
                median_sanction = _median_or_none(m["sanction_amount"] for m in members if m["sanction_amount"] is not None)
                median_disbursed = _median_or_none(m[fd.PEER_DISBURSED_FIELD] for m in members if m[fd.PEER_DISBURSED_FIELD] is not None)
                median_payments = _median_int_or_none(m[fd.PEER_PAYMENT_COUNT_FIELD] for m in members if m[fd.PEER_PAYMENT_COUNT_FIELD] is not None)
            else:
                median_sanction = median_disbursed = median_payments = None
            group_stats[group_key] = (count, sufficient, median_sanction, median_disbursed, median_payments)

        for row in rows:
            group_key = tuple(row.get(c) for c in group_cols)
            count, sufficient, median_sanction, median_disbursed, median_payments = group_stats[group_key]
            row[f"peer_count_by_{key}"] = count if all(k is not None for k in group_key) else None
            row[f"peer_group_sufficient_by_{key}"] = sufficient
            row[f"peer_median_sanction_amount_by_{key}"] = median_sanction
            row[f"peer_median_disbursed_amount_by_{key}"] = median_disbursed
            row[f"peer_median_payment_count_by_{key}"] = median_payments


def _median_or_none(values) -> Decimal | None:
    values = list(values)
    return statistics.median(values) if values else None


def _median_int_or_none(values) -> int | None:
    values = list(values)
    if not values:
        return None
    result = statistics.median(values)
    return result if isinstance(result, int) else result   # a float .5 midpoint is kept as-is, not rounded


# --------------------------------------------------------------------------------- rendering
def render_row(row: dict[str, object]) -> dict[str, str]:
    return {name: to_cell(row.get(name)) for name in fd.OUTPUT_COLUMNS}


def write_features_csv(rendered_rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(fd.OUTPUT_COLUMNS)
        for row in rendered_rows:
            writer.writerow([row.get(name, "") for name in fd.OUTPUT_COLUMNS])


def select_demo_work_ids(work_ids: list[str], existing_demo_master: Path | None, size: int, seed: str) -> set[str]:
    """Reuse the SAME demo cohort data_pipeline already published (so every demo CSV lines up
    on the same work_ids) when it's available; otherwise fall back to an independent,
    deterministic seeded-hash sample so the demo file is still reproducible on its own."""
    if existing_demo_master is not None and existing_demo_master.is_file():
        with open(existing_demo_master, newline="", encoding="utf-8") as handle:
            selected = {row["work_id"] for row in csv.DictReader(handle) if row.get("work_id")}
        chosen = [w for w in work_ids if w in selected]
        if chosen:
            return set(chosen)
    ranked = sorted(work_ids, key=lambda w: hashlib.blake2b(f"{seed}|{w}".encode("utf-8"), digest_size=8).digest())
    return set(ranked[:size])


# --------------------------------------------------------------------------------- orchestration
@dataclass(frozen=True)
class FeatureBuildConfig:
    processed_dir: Path
    demo_dir: Path
    docs_dir: Path
    snapshot_date: date = date.fromisoformat(fd.DEFAULT_SNAPSHOT_DATE)
    demo_size: int = fd.DEMO_SIZE
    demo_seed: str = fd.DEMO_SEED


@dataclass
class FeatureBuildResult:
    rows: int
    demo_rows: int
    parsed_rows: list[dict[str, object]] = field(repr=False)
    master_rows: list[dict[str, str]] = field(repr=False)
    expenditure_rows: list[dict[str, str]] = field(repr=False)


def run(cfg: FeatureBuildConfig) -> FeatureBuildResult:
    log.info("snapshot date for this run: %s (documented default unless overridden)", cfg.snapshot_date.isoformat())
    master_path = cfg.processed_dir / "works_master.csv"
    expenditure_path = cfg.processed_dir / "expenditure_by_work.csv"

    master_rows_header, master_rows = load_csv_as_dicts(master_path)
    _require_columns(master_rows_header, fd.MASTER_REQUIRED_COLUMNS, master_path)
    master_index = _index_by_work_id(master_rows, master_path)  # validates work_id uniqueness

    expenditure_header, expenditure_rows = load_csv_as_dicts(expenditure_path)
    _require_columns(expenditure_header, fd.EXPENDITURE_REQUIRED_COLUMNS, expenditure_path)
    expenditure_index = _index_by_work_id(expenditure_rows, expenditure_path)

    parsed = [
        build_row(master_index[work_id], expenditure_index.get(work_id), cfg.snapshot_date)
        for work_id in sorted(master_index)   # deterministic order, independent of file order
    ]
    compute_peer_features(parsed)
    rendered = [render_row(row) for row in parsed]

    cfg.processed_dir.mkdir(parents=True, exist_ok=True)
    write_features_csv(rendered, cfg.processed_dir / "work_features_v0.csv")

    demo_master = cfg.demo_dir / "works_master_demo.csv"
    demo_ids = select_demo_work_ids(list(master_index), demo_master if demo_master.is_file() else None, cfg.demo_size, cfg.demo_seed)
    demo_rendered = [row for row in rendered if row["work_id"] in demo_ids]
    write_features_csv(demo_rendered, cfg.demo_dir / "work_features_v0_demo.csv")

    cfg.docs_dir.mkdir(parents=True, exist_ok=True)
    report = profiling.render_report(parsed, master_rows, expenditure_rows, cfg)
    (cfg.docs_dir / "FEATURE_PROFILING_REPORT.md").write_text(report, encoding="utf-8", newline="\n")

    log.info("wrote %s feature row(s), %s demo row(s)", len(rendered), len(demo_rendered))
    return FeatureBuildResult(len(rendered), len(demo_rendered), parsed, master_rows, expenditure_rows)


def parse_args(argv: list[str] | None = None) -> FeatureBuildConfig:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build work_features_v0.csv from data_pipeline's cleaned CSV output.")
    parser.add_argument("--processed-dir", type=Path, default=root / "data" / "processed")
    parser.add_argument("--demo-dir", type=Path, default=root / "data" / "demo")
    parser.add_argument("--docs-dir", type=Path, default=root / "docs")
    parser.add_argument("--snapshot-date", type=date.fromisoformat, default=None,
                         help=f"YYYY-MM-DD; defaults to the documented {fd.DEFAULT_SNAPSHOT_DATE} (never the machine clock)")
    parser.add_argument("--demo-size", type=int, default=fd.DEMO_SIZE)
    parser.add_argument("--demo-seed", default=fd.DEMO_SEED)
    args = parser.parse_args(argv)
    return FeatureBuildConfig(
        processed_dir=args.processed_dir, demo_dir=args.demo_dir, docs_dir=args.docs_dir,
        snapshot_date=args.snapshot_date or date.fromisoformat(fd.DEFAULT_SNAPSHOT_DATE),
        demo_size=args.demo_size, demo_seed=args.demo_seed,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = parse_args(argv)
    try:
        run(cfg)
    except FeatureBuildError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
