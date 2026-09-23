"""Work ID extraction, validation and normalisation.

Recommended / Sanctioned / Completed workbooks embed the ID at the start of the
``WORK`` cell::

    WS/ MP620/2024-2025/133166-Construction of buildings for community ...

The Expenditure workbook has a separate ``Work ID`` column holding only the ID.
Both go through :func:`parse_work_id`, which returns the canonical form
``WS/MP620/2024-2025/133166`` (upper-case, no spaces) plus the text that
followed the ID.  Values that do not fit the structure are *not* repaired by
guesswork: the result carries an ``error`` code so the caller can log the row.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from data_pipeline.parsing import MISSING_TOKENS

_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
_TRANSLATE: dict[int, str | None] = {ord(c): "-" for c in _DASHES}
_TRANSLATE.update({ord(c): None for c in "\u200b\u200c\u200d\u2060\ufeff"})
_WS_RE = re.compile(r"\s+")

# ASCII-only on purpose: \d would also accept Arabic-Indic and other Unicode digits.
_PREFIX_RE = re.compile(r"[A-Z]{1,4}", re.ASCII)
_MP_CODE_RE = re.compile(r"[A-Z]{1,4}[0-9]{1,7}", re.ASCII)
_FY_RE = re.compile(r"([0-9]{4})-([0-9]{4})", re.ASCII)
_SERIAL_RE = re.compile(r"[0-9]+", re.ASCII)
CANONICAL_RE = re.compile(r"[A-Z]{1,4}/[A-Z]{1,4}[0-9]{1,7}/[0-9]{4}-[0-9]{4}/[0-9]{1,12}", re.ASCII)

MAX_SERIAL_DIGITS = 12

# Error codes (WorkIdParse.error) in the order they are checked.
ERROR_CODES = (
    "empty",
    "placeholder",
    "numeric_value",
    "wrong_segment_count",
    "bad_prefix",
    "bad_mp_code",
    "bad_financial_year",
    "bad_serial",
    "serial_not_terminated",
)


@dataclass(frozen=True, slots=True)
class WorkIdParse:
    """Result of parsing one Work / Work ID cell."""

    raw: str
    work_id: str | None = None          # canonical ID, None when malformed
    remainder: str = ""                 # text after the ID (work type), whitespace-normalised
    prefix: str | None = None
    mp_code: str | None = None          # 2nd segment, e.g. MP620
    financial_year: str | None = None   # 3rd segment, e.g. 2024-2025
    serial: str | None = None           # 4th segment, e.g. 133166
    warnings: tuple[str, ...] = ()      # accepted-but-normalised quirks
    error: str | None = None            # one of ERROR_CODES when malformed
    detail: str = ""                    # short human-readable context for the log

    @property
    def ok(self) -> bool:
        return self.work_id is not None


def _fail(raw: str, error: str, detail: str = "") -> WorkIdParse:
    return WorkIdParse(raw=raw, error=error, detail=detail[:120])


def parse_work_id(value: object, *, bare: bool = False) -> WorkIdParse:
    """Extract and normalise the Work ID at the start of ``value``.

    ``bare=True`` is for a dedicated Work ID column: text after the ID is then
    reported as the ``trailing_text_after_id`` warning instead of being
    silently accepted.  The Sr. No. column is never consulted.
    """
    if value is None:
        return _fail("", "empty")
    if isinstance(value, (bool, int, float)):
        return _fail(str(value), "numeric_value", "Work IDs are structured text, not numbers")
    raw = value if isinstance(value, str) else str(value)

    text = unicodedata.normalize("NFKC", raw).translate(_TRANSLATE)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return _fail(raw, "empty")
    if text.casefold() in MISSING_TOKENS:
        return _fail(raw, "placeholder")

    parts = text.split("/", 3)  # the description after the ID may itself contain "/"
    if len(parts) < 4:
        return _fail(raw, "wrong_segment_count", f"found {len(parts)} '/'-separated segment(s): {text[:60]}")

    warnings: list[str] = []
    head = parts[:3]
    if any(p != p.strip() or " " in p.strip() for p in head):
        warnings.append("whitespace_normalized")
    prefix_raw, mp_raw, fy_raw = (p.strip().replace(" ", "") for p in head)
    prefix, mp_code = prefix_raw.upper(), mp_raw.upper()
    if prefix != prefix_raw or mp_code != mp_raw:
        warnings.append("case_normalized")

    if not _PREFIX_RE.fullmatch(prefix):
        return _fail(raw, "bad_prefix", prefix_raw)
    if not _MP_CODE_RE.fullmatch(mp_code):
        return _fail(raw, "bad_mp_code", mp_raw)
    fy_match = _FY_RE.fullmatch(fy_raw)
    if not fy_match:
        return _fail(raw, "bad_financial_year", fy_raw)
    if int(fy_match.group(2)) != int(fy_match.group(1)) + 1:
        warnings.append("financial_year_not_consecutive")

    tail = parts[3].strip()
    serial_match = _SERIAL_RE.match(tail)
    if not serial_match or len(serial_match.group(0)) > MAX_SERIAL_DIGITS:
        return _fail(raw, "bad_serial", tail[:40])
    serial = serial_match.group(0)
    rest = tail[serial_match.end():]
    if rest and rest[0] not in " -:":
        return _fail(raw, "serial_not_terminated", tail[:40])
    remainder = rest.lstrip(" -:").strip()
    if bare and remainder:
        warnings.append("trailing_text_after_id")

    return WorkIdParse(
        raw=raw,
        work_id=f"{prefix}/{mp_code}/{fy_raw}/{serial}",
        remainder=remainder,
        prefix=prefix,
        mp_code=mp_code,
        financial_year=fy_raw,
        serial=serial,
        warnings=tuple(warnings),
    )


def extract_work_id(work_value: object) -> WorkIdParse:
    """ID embedded at the start of a ``WORK`` / ``Work`` string."""
    return parse_work_id(work_value, bare=False)


def normalize_work_id(value: object) -> str | None:
    """Canonical ID for a bare Work ID value, or ``None`` when malformed."""
    return parse_work_id(value, bare=True).work_id


def is_canonical_work_id(value: object) -> bool:
    return isinstance(value, str) and CANONICAL_RE.fullmatch(value) is not None
