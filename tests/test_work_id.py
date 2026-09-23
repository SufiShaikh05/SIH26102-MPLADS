import pytest

from data_pipeline.work_id import extract_work_id, is_canonical_work_id, normalize_work_id, parse_work_id

REAL_STYLE = "WS/ MP620/2024-2025/133166-Construction of buildings for community cultural activities"
CANONICAL = "WS/MP620/2024-2025/133166"


def test_extracts_id_embedded_in_work_string():
    parsed = extract_work_id(REAL_STYLE)
    assert parsed.ok
    assert parsed.work_id == CANONICAL
    assert parsed.remainder == "Construction of buildings for community cultural activities"
    assert (parsed.prefix, parsed.mp_code, parsed.financial_year, parsed.serial) == ("WS", "MP620", "2024-2025", "133166")
    assert parsed.warnings == ("whitespace_normalized",)  # the space after "WS/" was removed, and reported


def test_second_example_from_the_spike():
    parsed = extract_work_id("WS/ MP620/2025-2026/133167-Construction of rooms and halls in school and colleges")
    assert parsed.work_id == "WS/MP620/2025-2026/133167"
    assert parsed.remainder.startswith("Construction of rooms")


def test_bare_id_from_expenditure_column_is_kept_as_is():
    parsed = parse_work_id("WS/MP18218/2025-2026/233777", bare=True)
    assert parsed.ok and parsed.work_id == "WS/MP18218/2025-2026/233777"
    assert parsed.warnings == () and parsed.remainder == ""


@pytest.mark.parametrize(
    "raw",
    [
        "ws/mp620/2024-2025/133166",             # lower case
        "  WS / MP620 / 2024-2025 / 133166  ",   # spaces around every separator
        "WS/\u00a0MP620/2024-2025/133166",       # non-breaking space
        "WS/MP620/2024\u20132025/133166",        # en dash inside the financial year
        "WS/\tMP620/2024-2025/\n133166",         # tab / newline
        "WS/ MP 620/2024 - 2025/133166",         # space inside the MP code and the year range
    ],
)
def test_variants_normalise_to_one_canonical_id(raw):
    assert normalize_work_id(raw) == CANONICAL


def test_normalisation_is_idempotent():
    once = normalize_work_id(REAL_STYLE)
    assert normalize_work_id(once) == once
    assert is_canonical_work_id(once)


def test_remainder_keeps_hyphens_and_slashes():
    parsed = extract_work_id("WS/MP1/2024-2025/12-Repair of road/drain - phase 2")
    assert parsed.work_id == "WS/MP1/2024-2025/12"
    assert parsed.remainder == "Repair of road/drain - phase 2"


def test_serial_is_preserved_exactly():
    assert normalize_work_id("WS/MP1/2024-2025/000123") == "WS/MP1/2024-2025/000123"


def test_trailing_text_in_a_bare_id_column_is_flagged_not_dropped():
    parsed = parse_work_id("WS/MP1/2024-2025/12-extra words", bare=True)
    assert parsed.ok and "trailing_text_after_id" in parsed.warnings


def test_non_consecutive_financial_year_is_accepted_with_a_warning():
    parsed = parse_work_id("WS/MP1/2024-2026/12")
    assert parsed.ok and "financial_year_not_consecutive" in parsed.warnings


@pytest.mark.parametrize(
    "value, reason",
    [
        (None, "empty"),
        ("", "empty"),
        ("   ", "empty"),
        ("N/A", "placeholder"),
        (133166, "numeric_value"),
        (133166.0, "numeric_value"),
        ("Construction of a road", "wrong_segment_count"),
        ("WS/MP620/2024-2025", "wrong_segment_count"),
        ("133166-Construction of a road", "wrong_segment_count"),
        ("W1S/MP620/2024-2025/133166", "bad_prefix"),
        ("/MP620/2024-2025/133166", "bad_prefix"),
        ("WS/620/2024-2025/133166", "bad_mp_code"),
        ("WS/MP/2024-2025/133166", "bad_mp_code"),
        ("WS/MP620/2024-25/133166-x", "bad_financial_year"),
        ("WS/MP620/FY2024/133166", "bad_financial_year"),
        ("WS/MP620/2024-2025/-Construction", "bad_serial"),
        ("WS/MP620/2024-2025/abc", "bad_serial"),
        ("WS/MP620/2024-2025/1234567890123", "bad_serial"),
        ("WS/MP620/2024-2025/133166A-x", "serial_not_terminated"),
        ("WS/MP620/2024-2025/133166.5", "serial_not_terminated"),
        ("WS/MP\u0662\u0660/2024-2025/1", "bad_mp_code"),           # Arabic-Indic digits are not accepted
    ],
)
def test_malformed_values_are_reported_with_a_reason_and_never_repaired(value, reason):
    parsed = parse_work_id(value)
    assert not parsed.ok
    assert parsed.work_id is None
    assert parsed.error == reason
    assert normalize_work_id(value) is None


def test_failure_keeps_the_raw_text_for_the_log():
    parsed = parse_work_id("Construction of a road")
    assert parsed.raw == "Construction of a road"
    assert parsed.detail


def test_is_canonical_work_id():
    assert is_canonical_work_id(CANONICAL)
    assert not is_canonical_work_id("WS/ MP620/2024-2025/133166")
    assert not is_canonical_work_id(None)
