"""Dependency-free value parsers and normalisers for the MPLADS pipeline.

Every parser is *total*: it never raises on bad input.  It returns a
:class:`Parsed` result whose ``status`` is ``"ok"``, ``"missing"`` or
``"invalid"`` so callers can count and report problems instead of silently
dropping rows.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Strings that mean "no value" in the exported workbooks (compared case-folded).
MISSING_TOKENS = frozenset(
    {"", "n/a", "na", "nil", "null", "none", "nan", "#n/a", "-", "--", "\u2014", "not available"}
)

# The scheme (MPLADS) started in December 1993; earlier dates are implausible.
MPLADS_START = dt.date(1993, 12, 23)

_ZERO_WIDTH = {ord(c): None for c in "\u200b\u200c\u200d\u2060\ufeff"}
_WS_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[\W_]+")
_SUSPECT_ENCODING_RE = re.compile("\ufffd|\u00c3.|\u00e2\u20ac")
_HONORIFICS = frozenset(
    {"shri", "shree", "sri", "smt", "shrimati", "dr", "prof", "kumari", "adv", "ms", "mr", "mrs"}
)


@dataclass(frozen=True, slots=True)
class Parsed:
    """Outcome of parsing one cell."""

    value: Decimal | dt.date | None
    status: str  # "ok" | "missing" | "invalid"
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# --------------------------------------------------------------------------- text
_MAX_TOKEN_LEN = max(len(token) for token in MISSING_TOKENS)


def normalize_text(value: object) -> tuple[str | None, str | None]:
    """Single pass over a cell: ``(cleaned_text, missing_kind)``.

    ``missing_kind`` is ``"empty"``, ``"placeholder"`` (e.g. ``N/A``) or ``None``.  Whitespace is
    collapsed, zero-width characters are removed, case is preserved.
    """
    if value is None:
        return None, "empty"
    if isinstance(value, str):
        text = value if value.isascii() else value.translate(_ZERO_WIDTH)
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    text = " ".join(text.split())
    if not text:
        return None, "empty"
    if len(text) <= _MAX_TOKEN_LEN and text.casefold() in MISSING_TOKENS:
        return None, "placeholder"
    return text, None


def classify_missing(value: object) -> str | None:
    """Return ``"empty"``, ``"placeholder"`` (e.g. ``N/A``) or ``None`` (real value)."""
    if value is None:
        return "empty"
    if isinstance(value, str):
        text = value.translate(_ZERO_WIDTH).strip()
        if not text:
            return "empty"
        if text.casefold() in MISSING_TOKENS:
            return "placeholder"
    return None


def clean_text(value: object) -> str | None:
    """Whitespace-normalised text; ``None`` for missing/placeholder values.

    Case is preserved on purpose - source values stay recognisable.
    """
    return normalize_text(value)[0]


def fold_text(value: str | None) -> str:
    """Comparison key: case-folded, punctuation/whitespace collapsed."""
    if not value:
        return ""
    return _NON_WORD_RE.sub(" ", value.casefold()).strip()


def fold_person_name(value: str | None) -> str:
    """Like :func:`fold_text` but drops leading honorifics (Shri, Smt, Dr ...).

    Used only for diagnostic overlap counts, never to rewrite output data.
    """
    tokens = fold_text(value).split()
    while tokens and tokens[0] in _HONORIFICS:
        tokens.pop(0)
    return " ".join(tokens)


def has_suspect_encoding(text: str) -> bool:
    """True for replacement characters or typical UTF-8-read-as-cp1252 mojibake."""
    return _SUSPECT_ENCODING_RE.search(text) is not None


# ------------------------------------------------------------------------ amounts
_CURRENCY_RE = re.compile(r"(?:\u20b9|rs\.?|inr\.?)", re.IGNORECASE)
_PLAIN_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII)
_WESTERN_RE = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", re.ASCII)
_INDIAN_RE = re.compile(r"[+-]?\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?", re.ASCII)
_TWO_PLACES = Decimal("0.01")
_FLOAT_NOISE = Decimal("0.000001")


_MAX_ABS_AMOUNT = Decimal("1e15")  # rupees; anything larger is a corrupt cell, not money
_MIN_EXPONENT = -12                # more than 12 decimal places is not a rupee amount


def _accept(dec: Decimal, notes: list[str]) -> Parsed:
    """Final guard for every amount path: finite, plausible magnitude, sane precision."""
    if not dec.is_finite():
        return Parsed(None, "invalid", ("non_finite",))
    if abs(dec) >= _MAX_ABS_AMOUNT:
        return Parsed(None, "invalid", ("magnitude_out_of_range",))
    exponent = dec.as_tuple().exponent
    if exponent < _MIN_EXPONENT:
        return Parsed(None, "invalid", ("excessive_precision",))
    if exponent < -2:
        notes.append("more_than_2_decimals")
    return Parsed(dec, "ok", tuple(notes))


def _from_float(value: float) -> Parsed:
    dec = Decimal(repr(value))  # shortest repr: 154773472.11 stays 154773472.11
    if abs(dec) >= _MAX_ABS_AMOUNT:
        return Parsed(None, "invalid", ("magnitude_out_of_range",))
    if dec.as_tuple().exponent < -2:
        rounded = dec.quantize(_TWO_PLACES)
        if abs(dec - rounded) <= _FLOAT_NOISE:
            return Parsed(rounded, "ok", ("float_noise_rounded",))
        return Parsed(dec, "ok", ("more_than_2_decimals",))
    return Parsed(dec, "ok")


def parse_amount(value: object) -> Parsed:
    """Parse a rupee amount into an exact :class:`~decimal.Decimal`.

    Accepts numbers and strings such as ``"1,50,000"``, ``"₹ 5,00,000.50"``,
    ``"(1,000)"`` (accounting negative).  Negative values are *valid* here -
    the quality checks count them separately.  Placeholders (``N/A``, ``-``)
    are ``missing``; anything else unparseable is ``invalid``.
    """
    if value is None:
        return Parsed(None, "missing")
    if isinstance(value, bool):
        return Parsed(None, "invalid", ("boolean",))
    if isinstance(value, int):
        return _accept(Decimal(value), [])
    if isinstance(value, float):
        if not math.isfinite(value):
            return Parsed(None, "invalid", ("non_finite",))
        return _from_float(value)
    if isinstance(value, Decimal):
        return _accept(value, [])
    if not isinstance(value, str):
        return Parsed(None, "invalid", ("unsupported_type",))
    if value.isascii() and value.isdigit():  # fast path: "448127"
        return _accept(Decimal(value), [])

    text = value.translate(_ZERO_WIDTH).strip()
    if not text:
        return Parsed(None, "missing")
    if text.casefold() in MISSING_TOKENS:
        return Parsed(None, "missing", ("placeholder",))

    notes: list[str] = []
    without_currency = _CURRENCY_RE.sub("", text).strip()
    if without_currency != text:
        notes.append("currency_symbol")
    text = without_currency

    negative = False
    if text.startswith("(") and text.endswith(")"):
        text, negative = text[1:-1].strip(), True
        notes.append("accounting_negative")

    if _PLAIN_RE.fullmatch(text):
        digits = text
    elif _WESTERN_RE.fullmatch(text) or _INDIAN_RE.fullmatch(text):
        digits = text.replace(",", "")
        notes.append("thousands_separator")
    else:
        return Parsed(None, "invalid", ("unparseable",))

    try:
        dec = Decimal(digits)
    except InvalidOperation:
        return Parsed(None, "invalid", ("unparseable",))
    return _accept(-dec if negative else dec, notes)


def decimal_to_str(value: Decimal) -> str:
    """Plain decimal text (no exponent, no trailing zeros): ``497185``, ``1234.5``."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


# --------------------------------------------------------------------------- dates
_MONTHS: dict[str, int] = {}
for _n, _name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"),
    start=1,
):
    _MONTHS[_name] = _n
    _MONTHS[_name[:3]] = _n
_MONTHS["sept"] = 9

_STANDARD_DATE_RE = re.compile(r"\d{2}-[A-Za-z]{3}-\d{4}", re.ASCII)  # 08-Jul-2024 (observed format)
_FAST_DATE_RE = re.compile(r"(\d{2})-([A-Za-z]{3})-(\d{4})", re.ASCII)
_DMY_TEXT_RE = re.compile(r"(\d{1,2})[\s\-/.,]+([A-Za-z]{3,9})\.?[\s\-/.,]+(\d{4})", re.ASCII)
_MDY_TEXT_RE = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", re.ASCII)
_ISO_RE = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?",
    re.ASCII,
)
_DMY_NUM_RE = re.compile(r"(\d{1,2})([/\-.])(\d{1,2})\2(\d{4})", re.ASCII)
_EXCEL_EPOCH = dt.date(1899, 12, 30)
_MAX_EXCEL_SERIAL = 2_958_465  # 9999-12-31


def _build_date(year: int, month: int, day: int, notes: tuple[str, ...]) -> Parsed:
    try:
        return Parsed(dt.date(year, month, day), "ok", notes)
    except ValueError:
        return Parsed(None, "invalid", ("impossible_calendar_date",))


def parse_date(value: object) -> Parsed:
    """Parse a date cell.

    The workbooks use ``DD-Mon-YYYY`` text (e.g. ``08-Jul-2024``).  Real
    ``datetime`` cells, ISO dates, ``dd/mm/yyyy`` (day-first assumed) and Excel
    serial numbers are also accepted; non-standard text formats are flagged
    with the ``alt_format`` note so they show up in the quality report.
    """
    if value is None:
        return Parsed(None, "missing")
    if isinstance(value, bool):
        return Parsed(None, "invalid", ("boolean",))
    if isinstance(value, dt.datetime):
        notes = () if value.time() == dt.time(0, 0) else ("time_component_dropped",)
        return Parsed(value.date(), "ok", notes)
    if isinstance(value, dt.date):
        return Parsed(value, "ok")
    if isinstance(value, dt.time):
        return Parsed(None, "invalid", ("time_only",))
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return Parsed(None, "invalid", ("non_finite",))
        if 1 <= value <= _MAX_EXCEL_SERIAL:
            return Parsed(_EXCEL_EPOCH + dt.timedelta(days=int(value)), "ok", ("excel_serial",))
        return Parsed(None, "invalid", ("number_not_a_date",))
    if not isinstance(value, str):
        return Parsed(None, "invalid", ("unsupported_type",))

    fast = _FAST_DATE_RE.fullmatch(value)  # fast path for the observed DD-Mon-YYYY format
    if fast:
        month = _MONTHS.get(fast.group(2).casefold())
        if month is not None:
            return _build_date(int(fast.group(3)), month, int(fast.group(1)), ())

    text = _WS_RE.sub(" ", value.translate(_ZERO_WIDTH)).strip()
    if not text:
        return Parsed(None, "missing")
    if text.casefold() in MISSING_TOKENS:
        return Parsed(None, "missing", ("placeholder",))

    match = _DMY_TEXT_RE.fullmatch(text)
    if match:
        month = _MONTHS.get(match.group(2).casefold())
        if month is None:
            return Parsed(None, "invalid", ("unknown_month_name",))
        notes = () if _STANDARD_DATE_RE.fullmatch(text) else ("alt_format",)
        return _build_date(int(match.group(3)), month, int(match.group(1)), notes)
    match = _ISO_RE.fullmatch(text)
    if match:
        return _build_date(int(match.group(1)), int(match.group(2)), int(match.group(3)), ("alt_format",))
    match = _DMY_NUM_RE.fullmatch(text)
    if match:
        return _build_date(
            int(match.group(4)), int(match.group(3)), int(match.group(1)),
            ("alt_format", "numeric_day_first_assumed"),
        )
    match = _MDY_TEXT_RE.fullmatch(text)
    if match:
        month = _MONTHS.get(match.group(1).casefold())
        if month is None:
            return Parsed(None, "invalid", ("unknown_month_name",))
        return _build_date(int(match.group(3)), month, int(match.group(2)), ("alt_format",))
    return Parsed(None, "invalid", ("unrecognised_format",))


def financial_year_label(day: dt.date) -> str:
    """Indian financial year (1 April - 31 March) as ``"2024-2025"``."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{start + 1}"
