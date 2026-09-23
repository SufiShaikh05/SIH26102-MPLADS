"""Expected workbook layouts, taken from DATA_SPIKE_REPORT.txt.

Headers are matched after :func:`normalize_header`, so ``"RECOMMENDED AMOUNT   ( ₹ )"``
in the file matches ``"Recommended Amount (₹)"`` here.  Only columns seen in the
spike are declared - nothing is invented.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_TRAILING_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*$")
_HEADER_JUNK_RE = re.compile(r"[^\w]+")


def normalize_header(value: object) -> str:
    """Case / spacing / punctuation / currency-notation insensitive header key.

    A trailing parenthesised group is dropped, so ``"RECOMMENDED AMOUNT   ( \u20b9 )"``,
    ``"Recommended Amount (Rs.)"`` and ``"Recommended Amount"`` all become
    ``"recommended amount"``.
    """
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = _TRAILING_PARENS_RE.sub("", text)
    return _HEADER_JUNK_RE.sub(" ", text).strip()


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str                   # canonical snake_case name used downstream
    aliases: tuple[str, ...]    # accepted header spellings
    kind: str                   # sr_no | text | work | work_id | date | amount
    required: bool = True
    track: int = 0              # max distinct values profiled for consistency checks (0 = off)
    store: bool = True          # carried into staging / processed output


@dataclass(frozen=True, slots=True)
class SourceSpec:
    key: str
    file_name: str
    title: str
    columns: tuple[ColumnSpec, ...]
    keyed: bool                 # rows carry a Work ID

    @property
    def stored_columns(self) -> tuple[str, ...]:
        """Canonical columns persisted per row (``work_id`` and ``sr_no`` handled separately)."""
        return tuple(c.name for c in self.columns if c.store and c.kind not in ("work_id", "sr_no"))

    def column(self, name: str) -> ColumnSpec:
        return next(c for c in self.columns if c.name == name)


_MP = ("Hon'ble Members of Parliament", "Hon'ble Members of Parliaments", "Honble Members of Parliament")


def _sr() -> ColumnSpec:
    return ColumnSpec("sr_no", ("Sr. No.", "Sr No", "S. No.", "S.No."), "sr_no", required=False, store=False)


def _text(name: str, *aliases: str, track: int = 0, required: bool = True, store: bool = True) -> ColumnSpec:
    return ColumnSpec(name, aliases, "text", required, track, store)


def _date(name: str, *aliases: str) -> ColumnSpec:
    return ColumnSpec(name, aliases, "date")


def _amount(name: str, *aliases: str) -> ColumnSpec:
    return ColumnSpec(name, aliases, "amount")


def _work(*aliases: str) -> ColumnSpec:
    return ColumnSpec("work", aliases, "work", True, 20_000)


RECOMMENDED = SourceSpec(
    key="recommended",
    file_name="Works Recommended.xlsx",
    title="Works Recommended",
    keyed=True,
    columns=(
        _sr(),
        _text("work_category", "Work category", track=500),
        _work("WORK", "Work"),
        _text("state", "State", track=500),
        _text("ida", "IDA", track=50_000),
        _text("mp_name", *_MP, track=50_000),
        _text("constituency", "Constituency", track=50_000),
        _text("work_description", "Work description"),
        _date("recommended_date", "Recommended date"),
        _amount("recommended_amount", "Recommended Amount", "Recommended Amount (₹)"),
        _date("sanction_date", "Sanction Date"),
    ),
)

SANCTIONED = SourceSpec(
    key="sanctioned",
    file_name="Works Sanctioned.xlsx",
    title="Works Sanctioned",
    keyed=True,
    columns=(
        _sr(),
        _text("work_category", "Work category", track=500),
        _work("Work", "WORK"),
        _text("state", "State", track=500),
        _text("ida", "IDA", track=50_000),
        _text("mp_name", *_MP, track=50_000),
        _text("constituency", "Constituency", track=50_000),
        _text("work_description", "Work description"),
        _date("recommended_date", "Recommended date"),
        _date("sanction_date", "Sanction Date"),
        _amount("sanction_amount", "Sanction Amount", "Sanction Amount (₹)"),
        _text("work_status", "Work Status", track=500),
    ),
)

COMPLETED = SourceSpec(
    key="completed",
    file_name="Works Completed.xlsx",
    title="Works Completed",
    keyed=True,
    columns=(
        _sr(),
        _text("work_category", "Work Category", track=500),
        _work("Work", "WORK"),
        _text("state", "State", track=500),
        _text("ida", "IDA", track=50_000),
        _text("work_description", "Work Description"),
        _text("mp_name", *_MP, track=50_000),
        _text("constituency", "Constituency", track=50_000),
        _text("image", "Image", track=50, required=False, store=False),
        _date("completion_date", "Completion Date"),
        _amount("completed_amount_disbursed", "Amount Disbursed", "Amount Disbursed (₹)"),
    ),
)

EXPENDITURE = SourceSpec(
    key="expenditure",
    file_name="Expenditure on Completed and On-going Works as on Date.xlsx",
    title="Expenditure on Completed and On-going Works as on Date",
    keyed=True,
    columns=(
        _sr(),
        _text("state", "State", track=500),
        _text("work", "Work", track=20_000),
        ColumnSpec("work_id", ("Work ID",), "work_id"),
        _text("ida", "IDA", track=50_000),
        _text("mp_name", *_MP, track=50_000),
        _text("constituency", "Constituency", track=50_000),
        _date("expenditure_date", "Expenditure Date"),
        _text("vendor_name", "Vendor Name", track=500_000),
        _text("payment_status", "Payment Status", track=500),
        _amount("fund_disbursed_amount", "Fund Disbursed Amount", "Fund Disbursed Amount (₹)"),
    ),
)

ALLOCATION = SourceSpec(
    key="allocation",
    file_name="Allocated Limit for Honble MPs.xlsx",
    title="Allocated Limit for Hon'ble MPs",
    keyed=False,
    columns=(
        _sr(),
        _text("state", "State", track=500),
        _text("mp_name", *_MP, track=50_000),
        _text("constituency", "Constituency", track=50_000),
        _amount("allocated_amount", "Allocated AMOUNT", "Allocated AMOUNT (₹)"),
    ),
)

CALAMITY = SourceSpec(
    key="calamity",
    file_name="Amount consented for Calamity.xlsx",
    title="Amount consented for Calamity",
    keyed=False,
    columns=(
        _sr(),
        _text("calamity_type", "Calamity Type", track=500),
        _text("calamity_name", "Calamity Name", track=5_000),
        _text("mp_name", *_MP, track=50_000),
        _date("consent_date", "Date of Consent"),
        _amount("consent_amount", "Consent Amount", "Consent Amount (₹)"),
    ),
)

SOURCES: dict[str, SourceSpec] = {
    s.key: s for s in (RECOMMENDED, SANCTIONED, COMPLETED, EXPENDITURE, ALLOCATION, CALAMITY)
}
WORK_SOURCES = ("recommended", "sanctioned", "completed")
