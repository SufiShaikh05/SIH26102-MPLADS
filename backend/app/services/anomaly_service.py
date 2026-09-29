"""Serves the anomaly / review-priority records from ``work_anomalies_v1.csv``.

The anomaly engine is developed separately, so this module is deliberately tolerant:

* column ORDER does not matter and extra columns are accepted (kept as raw text
  under ``extra`` for single-record endpoints, never in list responses);
* a contract column that is absent is filled from ``work_features_v0.csv`` when the
  feature matrix has it (identity and metric columns only - scores, labels and
  explanations are never invented);
* the file may not exist yet: the API then reports ``data_kind = "none"`` and returns
  empty results - it never fabricates scores;
* the file is re-read automatically when it changes on disk, so a new engine run shows
  up without restarting the server.

Everything is kept in memory in a pre-sorted list (highest review priority first).
"""

from __future__ import annotations

import logging
import math
import threading
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from operator import attrgetter
from pathlib import Path
from typing import Any

from ..config import Settings
from .data_service import (
    DataUnavailableError,
    WorkStore,
    build_plan,
    col,
    read_csv,
    to_cat,
    to_float,
    to_int,
    to_text,
    to_work_id,
)

log = logging.getLogger("uvicorn.error.mplads")

DEMO_MARKER = "DEMO FIXTURE"


class InvalidQueryError(ValueError):
    """A query parameter is well-formed but not acceptable (mapped to HTTP 422)."""


@dataclass(slots=True)
class AnomalyRow:
    """One record of work_anomalies_v1.csv. ``fallback`` names the feature-matrix column
    used only when the CSV has no such column at all."""

    work_id: str | None = col(to_work_id)
    state: str | None = col(to_cat, fallback="state")
    constituency: str | None = col(to_cat, fallback="constituency")
    mp_name: str | None = col(to_cat, fallback="mp_name")
    ida: str | None = col(to_cat, fallback="ida")
    work_category: str | None = col(to_cat, fallback="work_category")
    work_status: str | None = col(to_cat, fallback="work_status")
    sanction_amount: float | None = col(to_float, fallback="sanction_amount")
    total_disbursed_all_rows: float | None = col(to_float, fallback="total_disbursed_all_rows")
    deduplicated_disbursed_amount: float | None = col(to_float, fallback="deduplicated_disbursed_amount")
    success_utilization_ratio: float | None = col(to_float, fallback="success_utilization_ratio")
    days_since_sanction: int | None = col(to_int, fallback="days_since_sanction")
    days_since_last_payment: int | None = col(to_int, fallback="days_since_last_payment")
    payment_count: int | None = col(to_int, "payment_count_all_rows", fallback="payment_count_all_rows")
    vendor_count: int | None = col(to_int, fallback="vendor_count")
    duplicate_ratio: float | None = col(to_float, fallback="duplicate_ratio")
    review_priority_score: float | None = col(to_float)
    review_priority_label: str | None = col(to_cat)
    signal_count: int | None = col(to_int)
    top_signal_1: str | None = col(to_text)
    top_signal_2: str | None = col(to_text)
    top_signal_3: str | None = col(to_text)
    explanation_text: str | None = col(to_text)
    extra: tuple[str | None, ...] = ()  # values of the non-contract columns, in file order


CONTRACT_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(AnomalyRow) if "conv" in f.metadata
)
REQUIRED_COLUMNS = ("work_id", "review_priority_score")

# field -> "num" | "text"; only these can be used in ?sort=
SORT_FIELDS: dict[str, str] = {
    "work_id": "text",
    "state": "text",
    "constituency": "text",
    "mp_name": "text",
    "ida": "text",
    "work_category": "text",
    "work_status": "text",
    "review_priority_score": "num",
    "signal_count": "num",
    "sanction_amount": "num",
    "total_disbursed_all_rows": "num",
    "deduplicated_disbursed_amount": "num",
    "success_utilization_ratio": "num",
    "days_since_sanction": "num",
    "days_since_last_payment": "num",
    "payment_count": "num",
    "vendor_count": "num",
    "duplicate_ratio": "num",
}
DEFAULT_SORT: tuple[tuple[str, bool], ...] = (("review_priority_score", True),)


def parse_sort(raw: str | None) -> tuple[tuple[str, bool], ...]:
    """``sort=-review_priority_score,state`` -> (("review_priority_score", True), ("state", False)).

    A leading ``-`` means descending; ``field:asc`` / ``field:desc`` is accepted too."""
    if raw is None or not raw.strip():
        return DEFAULT_SORT
    keys: list[tuple[str, bool]] = []
    for token in raw.split(","):
        token = token.strip()
        descending = token.startswith("-")
        name = token[1:] if descending else token
        if ":" in name:
            if descending:
                raise InvalidQueryError(
                    f"sort '{token}': use either a leading '-' or a ':asc'/':desc' suffix, not both."
                )
            name, _, direction = name.partition(":")
            direction = direction.strip().lower()
            if direction not in {"asc", "desc"}:
                raise InvalidQueryError(f"sort '{token}': direction must be 'asc' or 'desc'.")
            descending = direction == "desc"
        name = name.strip()
        if name not in SORT_FIELDS:
            raise InvalidQueryError(
                f"Unknown sort field '{name}'. Allowed: {', '.join(SORT_FIELDS)}."
            )
        keys.append((name, descending))
    if len(keys) > 3:
        raise InvalidQueryError("sort accepts at most 3 fields.")
    return tuple(keys)


def sort_rows(rows: Sequence[AnomalyRow], keys: Sequence[tuple[str, bool]]) -> list[AnomalyRow]:
    """Stable multi-key sort; missing values always go last; ties fall back to work_id."""
    ordered = sorted(rows, key=lambda r: r.work_id or "")
    for name, descending in reversed(keys):
        getter = attrgetter(name)
        present = [r for r in ordered if getter(r) is not None]
        missing = [r for r in ordered if getter(r) is None]
        if SORT_FIELDS[name] == "text":
            present.sort(key=lambda r: getter(r).casefold(), reverse=descending)
        else:
            present.sort(key=getter, reverse=descending)
        ordered = present + missing
    return ordered


def row_as_dict(row: AnomalyRow) -> dict[str, Any]:
    return {name: getattr(row, name) for name in CONTRACT_FIELDS}


# --------------------------------------------------------------------------- loaded data
@dataclass(frozen=True)
class AnomalyData:
    """Immutable snapshot of one load of the anomaly file (swapped atomically on reload)."""

    kind: str = "none"  # engine | demo_fixture | none
    file: str | None = None
    signature: tuple | None = None
    ordered: tuple[AnomalyRow, ...] = ()
    by_id: dict[str, AnomalyRow] = field(default_factory=dict)
    extra_columns: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()
    label_counts: tuple[tuple[str, int], ...] = ()
    candidate_count: int = 0
    candidates_by_state: dict[str, int] = field(default_factory=dict)
    candidates_by_category: dict[str, int] = field(default_factory=dict)
    label_lookup: dict[str, str] = field(default_factory=dict)
    state_lookup: dict[str, str] = field(default_factory=dict)
    category_lookup: dict[str, str] = field(default_factory=dict)
    rule: str | None = None
    warnings: tuple[str, ...] = ()
    message: str | None = None
    loaded_at: datetime | None = None

    @property
    def available(self) -> bool:
        return self.kind != "none"

    def info(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "kind": self.kind,
            "file": self.file,
            "records": len(self.ordered),
            "review_candidate_rule": self.rule,
            "missing_columns": list(self.missing_columns),
            "message": self.message,
        }


class AnomalyStore:
    def __init__(self, settings: Settings, works: WorkStore) -> None:
        self._settings = settings
        self._works = works
        self._lock = threading.Lock()
        self._data: AnomalyData | None = None

    # ---- locating / refreshing --------------------------------------------------
    def _locate(self) -> tuple[Path, str] | None:
        real = self._settings.anomalies_path
        if real.is_file():
            return real, "engine"  # real engine output always wins over the demo fixture
        demo = self._settings.demo_anomalies_path
        if self._settings.use_demo_anomalies and demo.is_file():
            return demo, "demo_fixture"
        return None

    def data(self) -> AnomalyData:
        """Current data, re-read first if the file appeared, changed or disappeared."""
        located = self._locate()
        signature = None
        if located is not None:
            stat = located[0].stat()
            signature = (str(located[0]), stat.st_mtime_ns, stat.st_size, located[1])
        current = self._data
        if current is not None and current.signature == signature:
            return current
        with self._lock:
            current = self._data
            if current is None or current.signature != signature:
                current = self._data = self._reload(located, signature, current)
        return current

    def _reload(
        self,
        located: tuple[Path, str] | None,
        signature: tuple | None,
        previous: AnomalyData | None,
    ) -> AnomalyData:
        if located is None:
            return AnomalyData(
                kind="none",
                file=None,
                signature=None,
                message=(
                    f"{self._settings.anomalies_path.name} not found in "
                    f"{self._settings.display(self._settings.processed_dir)}. Anomaly endpoints "
                    "return no rows until the anomaly engine writes it "
                    "(MPLADS_USE_DEMO_ANOMALIES=1 serves a clearly-marked demo fixture instead)."
                ),
            )
        path, kind = located
        try:
            data = self._load(path, kind, signature)
        except Exception as exc:  # a half-written or malformed file must not take the API down
            log.warning("Could not load %s: %s", path.name, exc)
            message = f"Could not read {path.name}: {exc}"
            if previous is not None and previous.available:
                return replace(previous, signature=signature, message=f"{message} Serving the previous load.")
            return AnomalyData(kind="none", file=path.name, signature=signature, message=message)
        log.info("Anomaly data loaded (%s): %d records from %s", kind, len(data.ordered), path.name)
        return data

    # ---- parsing ------------------------------------------------------------------
    def _load(self, path: Path, kind: str, signature: tuple | None) -> AnomalyData:
        header, reader = read_csv(path)
        plan = build_plan(AnomalyRow, header)
        absent_required = [name for name in REQUIRED_COLUMNS if name in plan.missing]
        if absent_required:
            raise ValueError(f"missing required column(s): {', '.join(absent_required)}")

        # Absent contract columns that the feature matrix can supply: (position, field, feature attribute).
        contract = [spec for spec in fields(AnomalyRow) if "conv" in spec.metadata]
        fallbacks = [
            (position, spec.name, spec.metadata["fallback"])
            for position, spec in enumerate(contract)
            if spec.name in plan.missing and spec.metadata["fallback"]
        ]
        works: WorkStore | None = None
        if fallbacks:
            try:
                self._works.ensure_loaded()
                works = self._works
            except DataUnavailableError:
                works = None

        rows: dict[str, AnomalyRow] = {}
        skipped_blank = duplicates = 0
        for raw in reader:
            if not raw:
                continue
            values = plan.values(raw)
            work_id = values[0]
            if not work_id:
                skipped_blank += 1
                continue
            if work_id in rows:
                duplicates += 1
                continue
            if fallbacks and works is not None:
                work = works.get(work_id)
                if work is not None:
                    for position, _name, attribute in fallbacks:
                        values[position] = getattr(work, attribute)
            extra = tuple((raw[i].strip() or None) if i < len(raw) else None for i in plan.extra_indices)
            rows[work_id] = AnomalyRow(*values, extra=extra)

        ordered = tuple(sort_rows(list(rows.values()), DEFAULT_SORT))

        # ---- review-candidate rule ----------------------------------------------------
        # Precedence: explicit labels > explicit signal-count override > the default score rule.
        # review_priority_score is a required column (REQUIRED_COLUMNS above), so it is always
        # present whenever any anomaly data is loaded at all - unlike signal_count, which is only
        # "expected".
        settings = self._settings
        if settings.review_candidate_labels:
            wanted = {label.casefold() for label in settings.review_candidate_labels}
            rule = "review_priority_label in [" + ", ".join(settings.review_candidate_labels) + "]"

            def is_candidate(r: AnomalyRow) -> bool:
                return r.review_priority_label is not None and r.review_priority_label.casefold() in wanted

        elif settings.review_candidate_min_signals is not None:
            minimum = settings.review_candidate_min_signals
            rule = f"signal_count >= {minimum}"

            def is_candidate(r: AnomalyRow) -> bool:
                return r.signal_count is not None and r.signal_count >= minimum

        elif settings.review_candidate_min_score is not None:
            minimum = settings.review_candidate_min_score
            display = int(minimum) if minimum == int(minimum) else minimum
            rule = f"review_priority_score >= {display}"

            def is_candidate(r: AnomalyRow) -> bool:
                return r.review_priority_score is not None and r.review_priority_score >= minimum

        else:
            rule = "every record in the anomaly file"

            def is_candidate(r: AnomalyRow) -> bool:
                return True

        by_state: dict[str, int] = defaultdict(int)
        by_category: dict[str, int] = defaultdict(int)
        label_scores: dict[str, list[float]] = defaultdict(list)
        label_totals: Counter[str] = Counter()
        candidates = 0
        for r in ordered:
            if r.review_priority_label:
                label_totals[r.review_priority_label] += 1
                if r.review_priority_score is not None:
                    label_scores[r.review_priority_label].append(r.review_priority_score)
            if is_candidate(r):
                candidates += 1
                if r.state:
                    by_state[r.state] += 1
                if r.work_category:
                    by_category[r.work_category] += 1

        def mean_score(label: str) -> float:
            scores = label_scores.get(label)
            return sum(scores) / len(scores) if scores else -math.inf

        # Highest mean review_priority_score first: severity order without knowing the vocabulary.
        label_counts = tuple(
            (label, label_totals[label])
            for label in sorted(label_totals, key=lambda name: (-mean_score(name), name.casefold()))
        )

        warnings: list[str] = []
        if kind == "demo_fixture":
            warnings.append(
                f"{DEMO_MARKER}: anomaly data is placeholder content for frontend development, "
                "not engine output. Scores, labels and explanations are NOT real."
            )
        if skipped_blank:
            warnings.append(f"{skipped_blank} row(s) without work_id skipped in {path.name}.")
        if duplicates:
            warnings.append(f"{duplicates} duplicate work_id row(s) ignored in {path.name} (first row kept).")
        filled_from_features = {name for _, name, _ in fallbacks} if works is not None else set()
        still_missing = sorted(set(plan.missing) - filled_from_features)
        if still_missing:
            warnings.append(f"{path.name} has no column(s): {', '.join(still_missing)} (returned as null).")

        return AnomalyData(
            kind=kind,
            file=path.name,
            signature=signature,
            ordered=ordered,
            by_id=rows,
            extra_columns=plan.extra_names,
            missing_columns=tuple(sorted(plan.missing)),
            label_counts=label_counts,
            candidate_count=candidates,
            candidates_by_state=dict(by_state),
            candidates_by_category=dict(by_category),
            label_lookup={v.casefold(): v for v in label_totals},
            state_lookup={v.casefold(): v for v in {r.state for r in ordered if r.state}},
            category_lookup={v.casefold(): v for v in {r.work_category for r in ordered if r.work_category}},
            rule=rule,
            warnings=tuple(warnings),
            message=DEMO_MARKER + ": placeholder data, not engine output." if kind == "demo_fixture" else None,
            loaded_at=datetime.now(timezone.utc).replace(microsecond=0),
        )

    # ---- queries ----------------------------------------------------------------------
    def lookup(self, work_id: str) -> tuple[AnomalyRow | None, AnomalyData]:
        data = self.data()
        return data.by_id.get(work_id), data

    def query(
        self,
        *,
        labels: Sequence[str] | None,
        states: Sequence[str] | None,
        categories: Sequence[str] | None,
        min_score: float | None,
        max_score: float | None,
        sort: Sequence[tuple[str, bool]],
        page: int,
        page_size: int,
    ) -> tuple[list[AnomalyRow], int, AnomalyData]:
        data = self.data()
        wanted_labels = _resolve(labels, data.label_lookup)
        wanted_states = _resolve(states, data.state_lookup)
        wanted_categories = _resolve(categories, data.category_lookup)

        rows: Sequence[AnomalyRow] = data.ordered
        filtered = (
            wanted_labels is not None or wanted_states is not None or wanted_categories is not None
            or min_score is not None or max_score is not None
        )
        if filtered:

            def keep(r: AnomalyRow) -> bool:
                if wanted_labels is not None and r.review_priority_label not in wanted_labels:
                    return False
                if wanted_states is not None and r.state not in wanted_states:
                    return False
                if wanted_categories is not None and r.work_category not in wanted_categories:
                    return False
                if min_score is not None or max_score is not None:
                    score = r.review_priority_score
                    if score is None:
                        return False
                    if min_score is not None and score < min_score:
                        return False
                    if max_score is not None and score > max_score:
                        return False
                return True

            rows = [r for r in rows if keep(r)]

        if tuple(sort) != DEFAULT_SORT:  # data.ordered is already in the default order
            rows = sort_rows(rows, sort)

        start = (page - 1) * page_size
        return list(rows[start : start + page_size]), len(rows), data


def _resolve(requested: Sequence[str] | None, lookup: dict[str, str]) -> set[str] | None:
    """Case-insensitive filter values -> the exact values present in the data.

    None  -> no filter requested (blank values are ignored)
    set() -> a filter was requested but matches nothing (so the result is empty)
    """
    cleaned = [value.strip().casefold() for value in (requested or []) if value and value.strip()]
    if not cleaned:
        return None
    return {lookup[value] for value in cleaned if value in lookup}
