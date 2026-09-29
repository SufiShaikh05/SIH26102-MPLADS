"""CSV-backed data access for the dashboard API.

Three processed files are read **once** into compact in-memory structures:

* ``work_features_v0.csv``    one row per work - the backbone of the API
* ``works_master.csv``        work-type text and recommended/sanction dates
* ``expenditure_by_work.csv`` first / last payment dates

Only the columns the API serves are kept (60 of the 63 feature columns, 4 of the master
columns, 3 of the expenditure columns), values are converted to real types once, and repeated
text (state, IDA, ...) is interned, so 81k works fit in a few hundred MB at most.
Nothing here knows about FastAPI.

The module also holds the small CSV helpers shared with ``anomaly_service``.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import logging
import math
import re
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import Settings

log = logging.getLogger("uvicorn.error.mplads")


class DataUnavailableError(RuntimeError):
    """A required processed file is missing or unusable (mapped to HTTP 503)."""


# --------------------------------------------------------------------------- work ids
# PREFIX/MPCODE/YYYY-YYYY/SERIAL, e.g. WS/MP005/2024-2025/145074. The middle segments
# are matched leniently on purpose: a 422 must only mean "this cannot be a Work ID".
WORK_ID_PATTERN = re.compile(r"[A-Z0-9_-]+/[A-Z0-9_-]+/\d{4}-\d{4}/[A-Z0-9]+")
WORK_ID_EXAMPLE = "WS/MP005/2024-2025/145074"


def normalize_work_id(raw: str) -> str:
    """Same normalisation the pipeline applies: drop whitespace, upper-case."""
    return re.sub(r"\s+", "", raw or "").upper()


def is_valid_work_id(work_id: str) -> bool:
    return WORK_ID_PATTERN.fullmatch(work_id) is not None


# --------------------------------------------------------------------------- converters
def to_text(value: str) -> str | None:
    value = value.strip()
    return value or None


def to_work_id(value: str) -> str | None:
    """Work IDs are normalised the way the pipeline does it, so a stray space or a
    lower-case letter in any CSV can never break a join."""
    return normalize_work_id(value) or None


def to_cat(value: str) -> str | None:
    """Text that repeats a lot (state, IDA, label): interned so rows share one object."""
    value = value.strip()
    return sys.intern(value) if value else None


def to_float(value: str) -> float | None:
    """Blank / 'nan' / 'inf' / junk -> None (JSON cannot carry NaN or Infinity)."""
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def to_int(value: str) -> int | None:
    number = to_float(value)
    return None if number is None else int(round(number))


_TRUE = frozenset({"1", "1.0", "true", "t", "yes", "y"})
_FALSE = frozenset({"0", "0.0", "false", "f", "no", "n"})


def to_flag(value: str) -> bool | None:
    token = value.strip().lower()
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    return None


def to_date(value: str) -> date | None:
    value = value.strip()
    if len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def cached(convert: Callable[[str], Any], limit: int = 50_000) -> Callable[[str], Any]:
    """Memoise a converter for low-cardinality columns (peer medians, dates)."""
    memo: dict[str, Any] = {}

    def wrapper(value: str) -> Any:
        try:
            return memo[value]
        except KeyError:
            result = convert(value)
            if len(memo) < limit:
                memo[value] = result
            return result

    return wrapper


_peer_int = cached(to_int)
_peer_num = cached(to_float)
_cached_date = cached(to_date)


# --------------------------------------------------------------------------- csv helpers
def col(convert: Callable[[str], Any], *aliases: str, fallback: str | None = None) -> Any:
    """Declare a CSV-backed dataclass field: how to convert it, alias names, and (for
    anomaly rows) which feature-matrix attribute fills it when the column is absent."""
    return field(
        default=None,
        metadata={"conv": convert, "aliases": aliases, "fallback": fallback},
    )


def read_csv(path: Path) -> tuple[list[str], Iterator[list[str]]]:
    """Read a CSV (UTF-8, with or without BOM; Windows cp1252 as a fallback).

    The whole file is decoded up front so an encoding problem surfaces immediately
    instead of half-way through a load.
    """
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = [name.strip() for name in next(reader)]
    except StopIteration:
        return [], iter(())
    return header, reader


@dataclass(frozen=True)
class ColumnPlan:
    """Which CSV column feeds which dataclass field (built once per file)."""

    names: tuple[str, ...]
    pairs: tuple[tuple[int | None, Callable[[str], Any]], ...]
    missing: frozenset[str]
    extra_indices: tuple[int, ...]
    extra_names: tuple[str, ...]

    def values(self, row: list[str]) -> list[Any]:
        size = len(row)
        return [
            convert(row[i]) if i is not None and i < size else None
            for i, convert in self.pairs
        ]


def build_plan(cls: type, header: list[str]) -> ColumnPlan:
    """Match dataclass fields to CSV columns by name (case-insensitive, any order)."""
    lookup: dict[str, int] = {}
    for position, name in enumerate(header):
        lookup.setdefault(name.strip().lower(), position)

    names: list[str] = []
    pairs: list[tuple[int | None, Callable[[str], Any]]] = []
    missing: set[str] = set()
    used: set[int] = set()
    for spec in dataclasses.fields(cls):
        if "conv" not in spec.metadata:
            continue
        index = None
        for candidate in (spec.name, *spec.metadata["aliases"]):
            if candidate in lookup:
                index = lookup[candidate]
                break
        if index is None:
            missing.add(spec.name)
        else:
            used.add(index)
        names.append(spec.name)
        pairs.append((index, spec.metadata["conv"]))

    extras = [(i, n) for i, n in enumerate(header) if n and i not in used]
    return ColumnPlan(
        names=tuple(names),
        pairs=tuple(pairs),
        missing=frozenset(missing),
        extra_indices=tuple(i for i, _ in extras),
        extra_names=tuple(n for _, n in extras),
    )


# --------------------------------------------------------------------------- row types
@dataclass(slots=True)
class WorkRow:
    """A work as described by work_features_v0.csv (only what the API serves)."""

    # identity / context
    work_id: str | None = col(to_work_id)
    state: str | None = col(to_cat)
    constituency: str | None = col(to_cat)
    mp_name: str | None = col(to_cat)
    ida: str | None = col(to_cat)
    work_category: str | None = col(to_cat)
    work_status: str | None = col(to_cat)
    id_mp_code: str | None = col(to_cat)
    id_financial_year: str | None = col(to_cat)
    # source presence / stage flags
    in_recommended: bool | None = col(to_flag)
    in_sanctioned: bool | None = col(to_flag)
    in_completed: bool | None = col(to_flag)
    is_completed: bool | None = col(to_flag)
    is_partially_completed: bool | None = col(to_flag)
    is_sanctioned_only: bool | None = col(to_flag)
    single_payment_work: bool | None = col(to_flag)
    # amounts (rupees)
    recommended_amount: float | None = col(to_float)
    sanction_amount: float | None = col(to_float)
    total_disbursed_all_rows: float | None = col(to_float)
    success_amount: float | None = col(to_float)
    in_progress_amount: float | None = col(to_float)
    exact_duplicate_amount: float | None = col(to_float)
    deduplicated_disbursed_amount: float | None = col(to_float)
    remaining_sanction_amount: float | None = col(to_float)
    completed_amount_disbursed: float | None = col(to_float)
    # ratios
    success_utilization_ratio: float | None = col(to_float)
    gross_utilization_ratio: float | None = col(to_float)
    deduplicated_utilization_ratio: float | None = col(to_float)
    in_progress_ratio: float | None = col(to_float)
    duplicate_amount_ratio: float | None = col(to_float)
    duplicate_ratio: float | None = col(to_float)
    # timing
    recommendation_to_sanction_days: int | None = col(to_int)
    sanction_to_first_payment_days: int | None = col(to_int)
    sanction_to_last_payment_days: int | None = col(to_int)
    sanction_to_completion_days: int | None = col(to_int)
    days_since_sanction: int | None = col(to_int)
    days_since_last_payment: int | None = col(to_int)
    payment_span_days: int | None = col(to_int)
    average_payment_interval_days: float | None = col(to_float)
    completion_date: date | None = col(_cached_date)
    # payment / vendor counts
    payment_count_all_rows: int | None = col(to_int, "payment_count")
    deduplicated_payment_count: int | None = col(to_int)
    vendor_count: int | None = col(to_int)
    duplicate_record_count: int | None = col(to_int)
    cleaned_work_description: str | None = col(to_text)
    # peer groups (>= 20 works, otherwise the medians are blank upstream)
    peer_count_by_work_category: int | None = col(_peer_int)
    peer_median_sanction_amount_by_work_category: float | None = col(_peer_num)
    peer_median_disbursed_amount_by_work_category: float | None = col(_peer_num)
    peer_median_payment_count_by_work_category: float | None = col(_peer_num)
    peer_group_sufficient_by_work_category: bool | None = col(to_flag)
    peer_count_by_state: int | None = col(_peer_int)
    peer_median_sanction_amount_by_state: float | None = col(_peer_num)
    peer_median_disbursed_amount_by_state: float | None = col(_peer_num)
    peer_median_payment_count_by_state: float | None = col(_peer_num)
    peer_group_sufficient_by_state: bool | None = col(to_flag)
    peer_count_by_state_work_category: int | None = col(_peer_int)
    peer_median_sanction_amount_by_state_work_category: float | None = col(_peer_num)
    peer_median_disbursed_amount_by_state_work_category: float | None = col(_peer_num)
    peer_median_payment_count_by_state_work_category: float | None = col(_peer_num)
    peer_group_sufficient_by_state_work_category: bool | None = col(to_flag)


@dataclass(slots=True)
class MasterRow:
    """The few works_master.csv columns that work_features_v0.csv does not carry."""

    work_id: str | None = col(to_work_id)
    work: str | None = col(to_cat)
    recommended_date: date | None = col(_cached_date)
    sanction_date: date | None = col(_cached_date)


@dataclass(slots=True)
class PaymentDatesRow:
    """First / last payment dates from expenditure_by_work.csv."""

    work_id: str | None = col(to_work_id)
    first_expenditure_date: date | None = col(_cached_date)
    last_expenditure_date: date | None = col(_cached_date)


# Columns without which the summary would be wrong; a missing one is a loud error.
REQUIRED_FEATURE_COLUMNS = (
    "work_id",
    "state",
    "work_category",
    "in_sanctioned",
    "in_completed",
    "payment_count_all_rows",
)


@dataclass(frozen=True)
class WorkAggregates:
    total_works: int
    sanctioned_works: int
    completed_works: int
    works_with_expenditure: int
    total_expenditure_transactions: int
    state_counts: dict[str, int]
    category_counts: dict[str, int]
    status_counts: dict[str, int]


# --------------------------------------------------------------------------- the store
class WorkStore:
    """Loads the works once (lazily, thread-safe) and serves lookups + aggregates."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._loaded = False
        self._rows: dict[str, WorkRow] = {}
        self._master: dict[str, MasterRow] = {}
        self._payments: dict[str, PaymentDatesRow] = {}
        self._aggregates: WorkAggregates | None = None
        self._snapshot_date: date | None = None
        self._warnings: list[str] = []
        self._loaded_at: datetime | None = None

    # ---- loading ------------------------------------------------------------
    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if not self._loaded:  # a failed load is retried on the next request
                self._load()
                self._loaded = True

    def _load(self) -> None:
        settings = self._settings
        warnings: list[str] = []

        rows, aggregates, duplicates = self._load_features(settings.features_path)
        if duplicates:
            warnings.append(
                f"{duplicates} duplicate work_id row(s) ignored in {settings.features_path.name} (first row kept)."
            )

        master: dict[str, MasterRow] = {}
        if settings.works_master_path.is_file():
            master = self._load_keyed(settings.works_master_path, MasterRow)
        else:
            warnings.append(
                f"{settings.works_master_path.name} not found: work details will have no work type "
                "and no recommended/sanction dates."
            )

        payments: dict[str, PaymentDatesRow] = {}
        if settings.expenditure_by_work_path.is_file():
            payments = self._load_keyed(settings.expenditure_by_work_path, PaymentDatesRow)
        else:
            warnings.append(
                f"{settings.expenditure_by_work_path.name} not found: first/last payment dates will be null."
            )

        snapshot = settings.snapshot_date or self._derive_snapshot(rows, master)
        if snapshot is None:
            warnings.append(
                "snapshot_date could not be derived (needs days_since_sanction plus either "
                "works_master.csv sanction_date or completion data); set MPLADS_SNAPSHOT_DATE."
            )

        self._rows, self._master, self._payments = rows, master, payments
        self._aggregates, self._snapshot_date, self._warnings = aggregates, snapshot, warnings
        self._loaded_at = datetime.now(timezone.utc).replace(microsecond=0)
        log.info(
            "MPLADS data loaded: %d works, %d master rows, %d payment-date rows, snapshot %s",
            len(rows), len(master), len(payments), snapshot,
        )

    def _load_features(self, path: Path) -> tuple[dict[str, WorkRow], WorkAggregates, int]:
        if not path.is_file():
            raise DataUnavailableError(
                f"{self._settings.display(path)} not found. Build it first "
                "(feature_engineering/build_features.py) or set MPLADS_PROCESSED_DIR."
            )
        header, reader = read_csv(path)
        plan = build_plan(WorkRow, header)
        absent = [name for name in REQUIRED_FEATURE_COLUMNS if name in plan.missing]
        if absent:
            raise DataUnavailableError(
                f"{path.name} is missing required column(s): {', '.join(absent)}."
            )

        rows: dict[str, WorkRow] = {}
        states: Counter[str] = Counter()
        categories: Counter[str] = Counter()
        statuses: Counter[str] = Counter()
        sanctioned = completed = with_expenditure = transactions = duplicates = 0

        for raw in reader:
            if not raw:
                continue
            row = WorkRow(*plan.values(raw))
            if not row.work_id:
                continue
            if row.work_id in rows:
                duplicates += 1
                continue
            rows[row.work_id] = row

            sanctioned += bool(row.in_sanctioned)
            completed += bool(row.in_completed)
            if row.payment_count_all_rows:
                with_expenditure += 1
                transactions += row.payment_count_all_rows
            if row.state:
                states[row.state] += 1
            if row.work_category:
                categories[row.work_category] += 1
            if row.work_status:
                statuses[row.work_status] += 1

        aggregates = WorkAggregates(
            total_works=len(rows),
            sanctioned_works=sanctioned,
            completed_works=completed,
            works_with_expenditure=with_expenditure,
            total_expenditure_transactions=transactions,
            state_counts=dict(states),
            category_counts=dict(categories),
            status_counts=dict(statuses),
        )
        return rows, aggregates, duplicates

    @staticmethod
    def _load_keyed(path: Path, cls: type) -> dict[str, Any]:
        header, reader = read_csv(path)
        plan = build_plan(cls, header)
        result: dict[str, Any] = {}
        for raw in reader:
            if not raw:
                continue
            row = cls(*plan.values(raw))
            if row.work_id and row.work_id not in result:
                result[row.work_id] = row
        return result

    @staticmethod
    def _derive_snapshot(rows: dict[str, WorkRow], master: dict[str, MasterRow]) -> date | None:
        """days_since_sanction = snapshot - sanction_date, so snapshot = sanction_date + days.

        The sanction date comes from works_master.csv when available, otherwise it is
        recovered as completion_date - sanction_to_completion_days (completed works only).
        Every work gives the same answer; the most common value wins."""
        counts: Counter[date] = Counter()
        for work_id, work in rows.items():
            if work.days_since_sanction is None:
                continue
            master_row = master.get(work_id)
            sanction = master_row.sanction_date if master_row else None
            if sanction is None and work.completion_date and work.sanction_to_completion_days is not None:
                sanction = work.completion_date - timedelta(days=work.sanction_to_completion_days)
            if sanction is not None:
                counts[sanction + timedelta(days=work.days_since_sanction)] += 1
        return counts.most_common(1)[0][0] if counts else None

    # ---- accessors ------------------------------------------------------------
    def get(self, work_id: str) -> WorkRow | None:
        self.ensure_loaded()
        return self._rows.get(work_id)

    def aggregates(self) -> WorkAggregates:
        self.ensure_loaded()
        assert self._aggregates is not None
        return self._aggregates

    @property
    def snapshot_date(self) -> date | None:
        self.ensure_loaded()
        return self._snapshot_date

    @property
    def warnings(self) -> list[str]:
        self.ensure_loaded()
        return list(self._warnings)

    @property
    def loaded_at(self) -> datetime:
        self.ensure_loaded()
        assert self._loaded_at is not None
        return self._loaded_at

    def master_row(self, work_id: str) -> MasterRow | None:
        self.ensure_loaded()
        return self._master.get(work_id)

    def payment_dates(self, work_id: str) -> PaymentDatesRow | None:
        self.ensure_loaded()
        return self._payments.get(work_id)

    # ---- work detail ------------------------------------------------------------
    def detail(self, work_id: str) -> dict[str, Any] | None:
        """Grouped, compact description of one work (everything except the review record)."""
        work = self.get(work_id)
        if work is None:
            return None
        master = self._master.get(work_id)
        paid = self._payments.get(work_id)

        def peers(suffix: str) -> dict[str, Any]:
            return {
                "peer_count": getattr(work, f"peer_count_by_{suffix}"),
                "sufficient": getattr(work, f"peer_group_sufficient_by_{suffix}"),
                "median_sanction_amount": getattr(work, f"peer_median_sanction_amount_by_{suffix}"),
                "median_disbursed_amount": getattr(work, f"peer_median_disbursed_amount_by_{suffix}"),
                "median_payment_count": getattr(work, f"peer_median_payment_count_by_{suffix}"),
            }

        return {
            "work_id": work.work_id,
            "snapshot_date": self._snapshot_date,
            "identity": {
                "work_id": work.work_id,
                "work": master.work if master else None,
                "description": work.cleaned_work_description,
                "state": work.state,
                "constituency": work.constituency,
                "mp_name": work.mp_name,
                "ida": work.ida,
                "work_category": work.work_category,
                "work_status": work.work_status,
                "mp_code": work.id_mp_code,
                "financial_year": work.id_financial_year,
            },
            "flags": {
                "in_recommended": work.in_recommended,
                "in_sanctioned": work.in_sanctioned,
                "in_completed": work.in_completed,
                "is_completed": work.is_completed,
                "is_partially_completed": work.is_partially_completed,
                "is_sanctioned_only": work.is_sanctioned_only,
            },
            "amounts": {
                "recommended_amount": work.recommended_amount,
                "sanction_amount": work.sanction_amount,
                "total_disbursed_all_rows": work.total_disbursed_all_rows,
                "success_amount": work.success_amount,
                "in_progress_amount": work.in_progress_amount,
                "exact_duplicate_amount": work.exact_duplicate_amount,
                "deduplicated_disbursed_amount": work.deduplicated_disbursed_amount,
                "remaining_sanction_amount": work.remaining_sanction_amount,
                "completed_amount_disbursed": work.completed_amount_disbursed,
            },
            "utilization": {
                "success_utilization_ratio": work.success_utilization_ratio,
                "gross_utilization_ratio": work.gross_utilization_ratio,
                "deduplicated_utilization_ratio": work.deduplicated_utilization_ratio,
                "in_progress_ratio": work.in_progress_ratio,
            },
            "payments": {
                "payment_count": work.payment_count_all_rows,
                "deduplicated_payment_count": work.deduplicated_payment_count,
                "vendor_count": work.vendor_count,
                "duplicate_record_count": work.duplicate_record_count,
                "duplicate_ratio": work.duplicate_ratio,
                "duplicate_amount_ratio": work.duplicate_amount_ratio,
                "single_payment_work": work.single_payment_work,
                "payment_span_days": work.payment_span_days,
                "average_payment_interval_days": work.average_payment_interval_days,
            },
            "timeline": {
                "recommended_date": master.recommended_date if master else None,
                "sanction_date": master.sanction_date if master else None,
                "first_payment_date": paid.first_expenditure_date if paid else None,
                "last_payment_date": paid.last_expenditure_date if paid else None,
                "completion_date": work.completion_date,
                "recommendation_to_sanction_days": work.recommendation_to_sanction_days,
                "sanction_to_first_payment_days": work.sanction_to_first_payment_days,
                "sanction_to_last_payment_days": work.sanction_to_last_payment_days,
                "sanction_to_completion_days": work.sanction_to_completion_days,
                "days_since_sanction": work.days_since_sanction,
                "days_since_last_payment": work.days_since_last_payment,
            },
            "peers": {
                "work_category": peers("work_category"),
                "state": peers("state"),
                "state_work_category": peers("state_work_category"),
            },
        }
