"""Streaming data-quality accumulators (bounded memory, one observation per cell)."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from decimal import Decimal

from data_pipeline.parsing import Parsed, decimal_to_str, has_suspect_encoding
from data_pipeline.schemas import SourceSpec
from data_pipeline.work_id import WorkIdParse

MAX_EXAMPLES = 10


class ColumnProfile:
    """Counts for one column of one source."""

    def __init__(self, name: str, kind: str, track: int = 0) -> None:
        self.name = name
        self.kind = kind
        self.track = track
        self.total = 0
        self.empty = 0
        self.placeholder = 0          # N/A, -, null ... (treated as missing)
        self.invalid = 0              # present but unparseable (dates / amounts)
        self.cleaned = 0              # text changed by whitespace normalisation
        self.suspect_encoding = 0
        self.notes: Counter[str] = Counter()
        self.values: Counter[str] | None = Counter() if track else None
        self.untracked = 0            # values not counted because the distinct-value cap was hit
        self.first_value: str | None = None
        self.examples: dict[str, list[tuple[int, str]]] = {}
        # amounts
        self.n_amounts = 0
        self.negatives = 0
        self.zeros = 0
        self.amount_sum = Decimal(0)
        self.min_amount: Decimal | None = None
        self.max_amount: Decimal | None = None
        # dates
        self.n_dates = 0
        self.min_date: dt.date | None = None
        self.max_date: dt.date | None = None
        self.too_early = 0
        self.too_late = 0

    def _example(self, kind: str, row_no: int, raw: object) -> None:
        bucket = self.examples.setdefault(kind, [])
        if len(bucket) < MAX_EXAMPLES:
            bucket.append((row_no, str(raw)[:120]))

    def _observe_missing(self, parsed: Parsed) -> None:
        if "placeholder" in parsed.notes:
            self.placeholder += 1
        else:
            self.empty += 1

    def observe_text(self, row_no: int, raw: object, cleaned: str | None, missing: str | None) -> None:
        self.total += 1
        if missing == "empty" or (missing is None and cleaned is None):
            self.empty += 1
            return
        if missing == "placeholder":
            self.placeholder += 1
            return
        assert cleaned is not None
        if self.first_value is None:
            self.first_value = cleaned
        if isinstance(raw, str) and raw != cleaned:
            self.cleaned += 1
        if not cleaned.isascii() and has_suspect_encoding(cleaned):  # mojibake is never pure ASCII
            self.suspect_encoding += 1
            self._example("suspect_encoding", row_no, cleaned)
        if self.values is not None:
            if cleaned in self.values or len(self.values) < self.track:
                self.values[cleaned] += 1
            else:
                self.untracked += 1

    def observe_amount(self, row_no: int, raw: object, parsed: Parsed) -> None:
        self.total += 1
        if parsed.status == "missing":
            self._observe_missing(parsed)
            return
        if parsed.status == "invalid":
            self.invalid += 1
            self.notes["invalid:" + ",".join(parsed.notes)] += 1
            self._example("invalid", row_no, raw)
            return
        value = parsed.value
        assert isinstance(value, Decimal)
        for note in parsed.notes:
            self.notes[note] += 1
        self.n_amounts += 1
        if value < 0:
            self.negatives += 1
            self._example("negative", row_no, raw)
        elif value == 0:
            self.zeros += 1
        self.amount_sum += value
        if self.min_amount is None or value < self.min_amount:
            self.min_amount = value
        if self.max_amount is None or value > self.max_amount:
            self.max_amount = value
        if self.first_value is None:
            self.first_value = decimal_to_str(value)

    def observe_date(self, row_no: int, raw: object, parsed: Parsed, lo: dt.date, hi: dt.date) -> None:
        self.total += 1
        if parsed.status == "missing":
            self._observe_missing(parsed)
            return
        if parsed.status == "invalid":
            self.invalid += 1
            self.notes["invalid:" + ",".join(parsed.notes)] += 1
            self._example("invalid", row_no, raw)
            return
        day = parsed.value
        assert isinstance(day, dt.date)
        for note in parsed.notes:
            self.notes[note] += 1
        self.n_dates += 1
        if self.min_date is None or day < self.min_date:
            self.min_date = day
        if self.max_date is None or day > self.max_date:
            self.max_date = day
        if day < lo:
            self.too_early += 1
            self._example("before_scheme_start", row_no, raw)
        elif day > hi:
            self.too_late += 1
            self._example("after_as_of_date", row_no, raw)
        if self.first_value is None:
            self.first_value = day.isoformat()

    @property
    def missing(self) -> int:
        return self.empty + self.placeholder

    @property
    def present(self) -> int:
        return self.total - self.missing


class IdProfile:
    """Work ID parsing outcomes for one source.

    ``malformed`` counts genuinely unparseable values only.  A value that fails to parse
    but reads as the ``NA-...`` unkeyed-recommendation convention (see
    :func:`data_pipeline.work_id.is_unkeyed_na`) is counted in ``na_unkeyed`` instead, kept
    entirely separate so the report never conflates "we could not read this ID" with "this
    work legitimately has none yet". Rows judged to be summary/footer rows never reach
    :meth:`observe` at all - they are counted by :class:`~data_pipeline.stats.SummaryRowStats`.
    """

    def __init__(self) -> None:
        self.total = 0
        self.valid = 0
        self.malformed = 0
        self.na_unkeyed = 0
        self.with_warnings = 0
        self.reasons: Counter[str] = Counter()
        self.warnings: Counter[str] = Counter()
        self.prefixes: Counter[str] = Counter()
        self.financial_years: Counter[str] = Counter()
        self.mp_codes: set[str] = set()
        self.examples: list[tuple[int, str, str, str]] = []  # (row, raw, reason, detail) - malformed only
        self.na_examples: list[tuple[int, str]] = []          # (row, raw) - na_unkeyed only

    def observe(self, row_no: int, raw: object, parsed: WorkIdParse, *, na_unkeyed: bool = False) -> None:
        self.total += 1
        if parsed.ok:
            self.valid += 1
            self.prefixes[parsed.prefix or ""] += 1
            self.financial_years[parsed.financial_year or ""] += 1
            self.mp_codes.add(parsed.mp_code or "")
            if parsed.warnings:
                self.with_warnings += 1
                self.warnings.update(parsed.warnings)
        elif na_unkeyed:
            self.na_unkeyed += 1
            if len(self.na_examples) < 20:
                self.na_examples.append((row_no, str(raw)[:120]))
        else:
            self.malformed += 1
            self.reasons[parsed.error or "unknown"] += 1
            if len(self.examples) < 20:
                self.examples.append((row_no, str(raw)[:120], parsed.error or "", parsed.detail))


class SourceProfile:
    """Everything measured while streaming one workbook."""

    def __init__(self, spec: SourceSpec) -> None:
        self.spec = spec
        self.key = spec.key
        self.columns: dict[str, ColumnProfile] = {
            c.name: ColumnProfile(c.name, c.kind, c.track) for c in spec.columns if c.kind != "work_id"
        }
        self.id = IdProfile()
        self.rows_read = 0
        self.blank_rows = 0
        self.data_rows = 0
        self.limit_hit = False
        self.exact_duplicate_rows = 0
        self.duplicate_examples: list[int] = []   # source rows of repeated records
        self.summary_rows_excluded = 0            # footer/summary rows found for this source (cheap + aggregate)
        self.distinct_ids = 0                     # unique valid Work IDs (keyed sources)
        self.layout = None                        # SheetLayout, set by the reader
        self.fingerprint: dict[str, object] = {}
        self.extra: dict[str, object] = {}
