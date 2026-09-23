"""Containers for everything the reports need (no I/O in here)."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from data_pipeline.parsing import decimal_to_str
from data_pipeline.profiling import SourceProfile
from data_pipeline.sinks import OutputProfile

SAMPLE_LIMIT = 10


@dataclass
class RunInfo:
    started_utc: str = ""
    finished_utc: str = ""
    elapsed_seconds: float = 0.0
    python: str = ""
    openpyxl: str = ""
    platform: str = ""
    as_of: dt.date | None = None
    limit_rows: int | None = None
    raw_dir: str = ""
    output_root: str = ""
    demo_size: int = 0
    demo_seed: str = ""
    raw_unchanged: bool | None = None
    peak_memory_mib: dict[str, float | None] = field(default_factory=dict)


@dataclass
class JoinStats:
    unique: Counter = field(default_factory=Counter)          # tag -> unique canonical IDs
    patterns: Counter = field(default_factory=Counter)        # (R, S, C, E) booleans -> IDs
    pair_total: Counter = field(default_factory=Counter)      # (A, B) -> IDs in A
    pair_match: Counter = field(default_factory=Counter)      # (A, B) -> IDs in A that are also in B
    issue_counts: Counter = field(default_factory=Counter)
    issue_samples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    union_ids: int = 0
    master_rows: int = 0

    def flag(self, issue: str, work_id: str) -> None:
        self.issue_counts[issue] += 1
        if len(self.issue_samples[issue]) < SAMPLE_LIMIT:
            self.issue_samples[issue].append(work_id)


@dataclass
class ConflictStats:
    by_field: Counter = field(default_factory=Counter)        # true value conflicts
    format_only: Counter = field(default_factory=Counter)     # differ only by case/space/punctuation
    samples: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    works_with_conflicts: int = 0


@dataclass
class DuplicateStats:
    ids_with_duplicates: Counter = field(default_factory=Counter)   # source -> IDs appearing more than once
    extra_rows: Counter = field(default_factory=Counter)            # source -> rows beyond the first
    identical: Counter = field(default_factory=Counter)             # source -> extra rows identical to the kept row
    conflicting: Counter = field(default_factory=Counter)           # source -> extra rows that differ
    samples: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))


@dataclass
class CheckStats:
    evaluated: Counter = field(default_factory=Counter)
    hits: Counter = field(default_factory=Counter)
    samples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def record(self, name: str, hit: bool, work_id: str) -> None:
        self.evaluated[name] += 1
        if hit:
            self.hits[name] += 1
            if len(self.samples[name]) < SAMPLE_LIMIT:
                self.samples[name].append(work_id)


@dataclass
class DiagnosticStats:
    status_xtab: dict[str, list[int]] = field(default_factory=dict)      # status -> [works, completed, expenditure]
    names_by_code: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    codes_by_name: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


@dataclass
class ExpenditureStats:
    works: int = 0
    payments: int = 0
    max_payments: int = 0
    max_payments_work: str = ""
    total: Decimal = Decimal(0)
    histogram: Counter = field(default_factory=Counter)

    BUCKETS = ("1", "2", "3-5", "6-10", "11-20", "21+")

    def observe(self, agg: dict) -> None:
        count = agg["payment_count"]
        self.works += 1
        self.payments += count
        self.total += agg["total_disbursed"]
        if count > self.max_payments:
            self.max_payments, self.max_payments_work = count, agg["work_id"]
        if count <= 2:
            bucket = str(count)
        elif count <= 5:
            bucket = "3-5"
        elif count <= 10:
            bucket = "6-10"
        elif count <= 20:
            bucket = "11-20"
        else:
            bucket = "21+"
        self.histogram[bucket] += 1


@dataclass
class PipelineStats:
    run: RunInfo = field(default_factory=RunInfo)
    profiles: dict[str, SourceProfile] = field(default_factory=dict)
    outputs: dict[str, OutputProfile] = field(default_factory=dict)
    join: JoinStats = field(default_factory=JoinStats)
    conflicts: ConflictStats = field(default_factory=ConflictStats)
    duplicates: DuplicateStats = field(default_factory=DuplicateStats)
    checks: CheckStats = field(default_factory=CheckStats)
    diagnostics: DiagnosticStats = field(default_factory=DiagnosticStats)
    expenditure: ExpenditureStats = field(default_factory=ExpenditureStats)
    demo: dict[str, int] = field(default_factory=dict)
    malformed_logged: int = 0

    def to_summary(self) -> dict:
        """JSON-serialisable digest (counts only - no giant counters)."""
        sources = {}
        for key, p in self.profiles.items():
            layout = p.layout
            sources[key] = {
                "file": p.spec.file_name,
                **p.fingerprint,
                "sheet": layout.sheet_name if layout else None,
                "header_row": layout.header_row if layout else None,
                "rows_read": p.rows_read,
                "blank_rows": p.blank_rows,
                "data_rows": p.data_rows,
                "truncated_by_limit": p.limit_hit,
                "exact_duplicate_rows": p.exact_duplicate_rows,
                "work_ids": {
                    "valid": p.id.valid,
                    "malformed": p.id.malformed,
                    "distinct_valid": p.distinct_ids,
                    "with_warnings": p.id.with_warnings,
                    "malformed_reasons": dict(p.id.reasons),
                    "warnings": dict(p.id.warnings),
                }
                if p.spec.keyed
                else None,
                "columns": {
                    name: {
                        "rows": c.total,
                        "empty": c.empty,
                        "placeholder": c.placeholder,
                        "invalid": c.invalid,
                        "negative": c.negatives,
                        "before_scheme_start": c.too_early,
                        "after_as_of_date": c.too_late,
                    }
                    for name, c in p.columns.items()
                },
            }
        j = self.join
        return {
            "run": {
                "started_utc": self.run.started_utc,
                "finished_utc": self.run.finished_utc,
                "elapsed_seconds": round(self.run.elapsed_seconds, 1),
                "python": self.run.python,
                "openpyxl": self.run.openpyxl,
                "platform": self.run.platform,
                "as_of": self.run.as_of.isoformat() if self.run.as_of else None,
                "limit_rows": self.run.limit_rows,
                "raw_files_unchanged": self.run.raw_unchanged,
                "peak_memory_mib": self.run.peak_memory_mib,
            },
            "sources": sources,
            "join": {
                "unique_ids": dict(j.unique),
                "union_ids": j.union_ids,
                "master_rows": j.master_rows,
                "presence_patterns_RSCE": {
                    "".join("1" if flag else "0" for flag in pattern): n for pattern, n in sorted(j.patterns.items())
                },
                "containment": {
                    f"{a}->{b}": {"ids": j.pair_total[(a, b)], "matched": j.pair_match[(a, b)]}
                    for (a, b) in sorted(j.pair_total)
                },
                "issues": dict(j.issue_counts),
            },
            "duplicates": {
                "ids_with_duplicates": dict(self.duplicates.ids_with_duplicates),
                "extra_rows": dict(self.duplicates.extra_rows),
                "identical": dict(self.duplicates.identical),
                "conflicting": dict(self.duplicates.conflicting),
            },
            "conflicts": {
                "works_with_conflicts": self.conflicts.works_with_conflicts,
                "by_field": dict(self.conflicts.by_field),
                "format_only_by_field": dict(self.conflicts.format_only),
            },
            "checks": {
                name: {"evaluated": self.checks.evaluated[name], "hits": self.checks.hits[name]}
                for name in sorted(self.checks.evaluated)
            },
            "expenditure": {
                "works_with_payments": self.expenditure.works,
                "payments": self.expenditure.payments,
                "total_disbursed_all_statuses": decimal_to_str(self.expenditure.total),
                "max_payments_for_one_work": self.expenditure.max_payments,
                "payments_per_work_histogram": dict(self.expenditure.histogram),
            },
            "outputs": {name: {"rows": o.rows, "columns": len(o.columns)} for name, o in self.outputs.items()},
            "demo_rows": self.demo,
            "malformed_ids_logged": self.malformed_logged,
        }
