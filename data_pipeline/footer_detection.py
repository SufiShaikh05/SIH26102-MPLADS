"""Detection of summary/footer rows mixed into MPLADS export sheets.

Government exports often carry a trailing "Total" / "Grand Total" row that was
never meant to be a data record. Its columns don't line up with the header (a
total gets typed into whatever cell was free when the sheet was built), so it
looks like corrupt data to the normal parsers - a huge number in a status
column, or in a date column. This module recognises such rows using several
independent signals, kept deliberately separate from ordinary "malformed
Work ID" handling:

* A row is judged on its OWN content (a total-like label, a number sitting in
  a field that should never be numeric, an aggregate value with no identity
  fields, or a value that exactly matches the running total of its column) -
  never merely because a Work ID is missing.
* A work recommended without an ID yet (the ``NA-...`` convention; see
  :func:`data_pipeline.work_id.is_unkeyed_na`) never trips any signal here: it
  has ordinary identity fields (state, MP, constituency) and no total-shaped
  content, so it is left for the caller to log as "unkeyed", not "footer".
* The "number where one should never be valid" signal is field-specific, not
  a blanket "numeric text is suspicious" rule: real MPLADS expenditure data
  uses bare numeric strings as vendor names/account-style identifiers, so
  ``vendor_name`` is excluded from that one check (see
  ``MISMATCH_TEXT_FIELDS``) - a numeric vendor name is ordinary data. It is
  still scanned for an actual total/grand-total label, and it still has to be
  empty for the "aggregate amount, no identity" signal, exactly like every
  other identity field.

Two layers:

* :func:`classify_cheap` runs per row, needs nothing but that row's own
  (already-normalised) values, and catches four of the five reasons.
* :func:`find_aggregate_match` runs once per source, after every candidate
  row's amount is known, and catches the fifth (a row's amount is exactly
  half of the column's total - equivalently, exactly the sum of every OTHER
  row, which is exactly what a single trailing grand-total row produces).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from data_pipeline.parsing import Parsed, decimal_to_str, parse_amount

REASONS = ("grand_total_label", "total_label", "footer_row", "summary_amount_only", "invalid_record_shape")

# Rows this small (or smaller) skip the aggregate check entirely: with 1-2 candidate
# rows, "one amount equals half the total" is common by pure coincidence (two works
# that happen to cost the same), not evidence of a footer.
MIN_ROWS_FOR_AGGREGATE_CHECK = 3

GRAND_TOTAL_RE = re.compile(r"^\s*grand[\s\-]*total\b", re.IGNORECASE)
TOTAL_LABEL_RE = re.compile(r"^\s*(sub[\s\-]?total|total)\b", re.IGNORECASE)

# Per source: which fields are scanned for a total/grand-total label, which non-amount
# fields must never hold a plain number, which date fields are checked for the same
# thing, which fields must ALL be empty for "aggregate value, no identity", and which
# single amount column represents "the" total for that source.  Free-text description
# fields are deliberately left out of LABEL_FIELDS: a genuine long sentence is far more
# likely to contain the word "total" in passing than a short identity field is.
LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "recommended": ("work_category", "state", "ida", "mp_name", "constituency", "work"),
    "sanctioned": ("work_category", "state", "ida", "mp_name", "constituency", "work", "work_status"),
    "completed": ("work_category", "state", "ida", "mp_name", "constituency", "work"),
    "expenditure": ("state", "ida", "mp_name", "constituency", "work", "vendor_name", "payment_status"),
    "allocation": ("state", "mp_name", "constituency"),
    "calamity": ("calamity_type", "calamity_name", "mp_name"),
}
# Fields checked for a plain number sitting where one should never be valid (signal 4). This is
# NOT simply LABEL_FIELDS: a vendor name is legitimately a bare numeric code or account-style
# identifier in real MPLADS data (seen in practice, e.g. vendor names logged as pure digit
# strings), so `vendor_name` must never trigger this check on its own - only a total/grand-total
# LABEL in that field (still checked above, via LABEL_FIELDS) or the other signals can flag it.
MISMATCH_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "recommended": ("work_category", "state", "ida", "mp_name", "constituency", "work"),
    "sanctioned": ("work_category", "state", "ida", "mp_name", "constituency", "work", "work_status"),
    "completed": ("work_category", "state", "ida", "mp_name", "constituency", "work"),
    "expenditure": ("state", "ida", "mp_name", "constituency", "work", "payment_status"),  # vendor_name excluded
    "allocation": ("state", "mp_name", "constituency"),
    "calamity": ("calamity_type", "calamity_name", "mp_name"),
}
DATE_FIELDS: dict[str, tuple[str, ...]] = {
    "recommended": ("recommended_date", "sanction_date"),
    "sanctioned": ("recommended_date", "sanction_date"),
    "completed": ("completion_date",),
    "expenditure": ("expenditure_date",),
    "allocation": (),
    "calamity": ("consent_date",),
}
IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "recommended": ("work_category", "state", "ida", "mp_name", "constituency", "work_description", "work"),
    "sanctioned": ("work_category", "state", "ida", "mp_name", "constituency", "work_description", "work"),
    "completed": ("work_category", "state", "ida", "mp_name", "constituency", "work_description", "work"),
    "expenditure": ("state", "ida", "mp_name", "constituency", "work", "vendor_name", "payment_status"),
    "allocation": ("state", "mp_name", "constituency"),
    "calamity": ("calamity_type", "calamity_name", "mp_name"),
}
PRIMARY_AMOUNT: dict[str, str] = {
    "recommended": "recommended_amount",
    "sanctioned": "sanction_amount",
    "completed": "completed_amount_disbursed",
    "expenditure": "fund_disbursed_amount",
    "allocation": "allocated_amount",
    "calamity": "consent_amount",
}
# Fields written into representative_values, in order, for a human reading summary_rows.csv.
REPRESENTATIVE_FIELDS: dict[str, tuple[str, ...]] = {
    key: tuple(dict.fromkeys((*LABEL_FIELDS[key], *DATE_FIELDS[key], PRIMARY_AMOUNT[key])))
    for key in LABEL_FIELDS
}


@dataclass(frozen=True, slots=True)
class FooterVerdict:
    """Outcome of judging one row.  ``reason`` is one of :data:`REASONS` iff ``is_footer``."""

    is_footer: bool
    reason: str | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.is_footer


NOT_FOOTER = FooterVerdict(False)


def classify_cheap(
    source_key: str,
    record: Mapping[str, object],
    clean: Mapping[str, str | None],
    parsed_fields: Mapping[str, Parsed],
) -> FooterVerdict:
    """Judge one row from its own content alone (signals 1, 2, 4 and 5 - not signal 3).

    ``clean`` holds the already-normalised value for every non-ID column (``None`` where
    parsing failed or the cell was empty); ``record`` holds the raw cell values, needed
    here only to re-check a date field's raw text against :func:`parse_amount`, since a
    failed date leaves ``clean`` as ``None`` and loses the original text.  ``parsed_fields``
    holds the :class:`~data_pipeline.parsing.Parsed` result for every date/amount column,
    keyed by column name, exactly as produced while building ``clean``.
    """
    for field in LABEL_FIELDS.get(source_key, ()):
        value = clean.get(field)
        if value and GRAND_TOTAL_RE.match(value):
            return FooterVerdict(True, "grand_total_label", f"{field}={value!r} reads as a grand-total label")
    for field in LABEL_FIELDS.get(source_key, ()):
        value = clean.get(field)
        if value and TOTAL_LABEL_RE.match(value):
            return FooterVerdict(True, "total_label", f"{field}={value!r} reads as a total/subtotal label")

    for field in MISMATCH_TEXT_FIELDS.get(source_key, ()):
        value = clean.get(field)
        if value is not None and parse_amount(value).ok:
            return FooterVerdict(True, "footer_row", f"{field}={value!r} is a plain number, never valid there")
    for field in DATE_FIELDS.get(source_key, ()):
        parsed = parsed_fields.get(field)
        if parsed is not None and parsed.status == "invalid":
            guess = parse_amount(record.get(field))
            if guess.ok:
                amount_text = decimal_to_str(guess.value)  # type: ignore[arg-type]
                return FooterVerdict(True, "footer_row", f"{field} holds {amount_text!r}, not a date")

    identity_fields = IDENTITY_FIELDS.get(source_key, ())
    primary = PRIMARY_AMOUNT.get(source_key)
    if primary and clean.get(primary) is not None and identity_fields and all(not clean.get(f) for f in identity_fields):
        return FooterVerdict(
            True, "summary_amount_only", f"{primary}={clean[primary]} present but every identity field is empty"
        )
    return NOT_FOOTER


def representative_values(source_key: str, record: Mapping[str, object], max_len: int = 50, cap: int = 320) -> str:
    """Compact ``field=value`` summary of a row for a human reading ``summary_rows.csv``."""
    parts = []
    for field in REPRESENTATIVE_FIELDS.get(source_key, ()):
        value = record.get(field)
        text = "" if value is None else str(value)
        if len(text) > max_len:
            text = text[: max_len - 3] + "..."
        parts.append(f"{field}={text}")
    joined = "; ".join(parts)
    return joined if len(joined) <= cap else joined[: cap - 3] + "..."


def find_aggregate_match(
    rows: Iterable[tuple[int, str | None]], total: Decimal, *, min_rows: int = MIN_ROWS_FOR_AGGREGATE_CHECK
) -> int | None:
    """Signal 3: a row whose amount exactly equals half the column's total.

    That is mathematically identical to "equals the sum of every other row" - exactly
    the signature of one trailing grand-total row - regardless of how many other rows
    there are.  ``rows`` is every candidate row's ``(source_row, amount_text)``, in any
    order; ``total`` is the column's sum over those same rows.  Returns the matching
    ``source_row``, or ``None`` when there is no unique match (zero, or more than one -
    a tie is genuinely ambiguous, so nothing is guessed) or too few rows to trust the
    arithmetic.
    """
    if total <= 0:
        return None
    half = total / 2
    matches: list[int] = []
    count = 0
    for source_row, amount_text in rows:
        count += 1
        if amount_text is not None and Decimal(amount_text) == half:
            matches.append(source_row)
    if count < min_rows or len(matches) != 1:
        return None
    return matches[0]


@dataclass(frozen=True, slots=True)
class AmountStats:
    count: int = 0
    sum: Decimal = Decimal(0)
    negatives: int = 0
    zeros: int = 0
    min: Decimal | None = None
    max: Decimal | None = None


def scan_amount_column(cursor: sqlite3.Cursor) -> AmountStats:
    """Authoritative sum/count/min/max/negatives/zeros for one already-clean amount column.

    ``cursor`` must yield ``(amount_text_or_none,)`` rows (a plain single-column SELECT);
    used to recompute a source's primary-amount statistics after a footer row found only
    by :func:`find_aggregate_match` is removed from its staging table.
    """
    count = negatives = zeros = 0
    total = Decimal(0)
    lo = hi = None
    for (text,) in cursor:
        if text is None:
            continue
        value = Decimal(text)
        count += 1
        total += value
        if value < 0:
            negatives += 1
        elif value == 0:
            zeros += 1
        if lo is None or value < lo:
            lo = value
        if hi is None or value > hi:
            hi = value
    return AmountStats(count, total, negatives, zeros, lo, hi)
