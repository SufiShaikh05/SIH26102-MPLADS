import datetime as dt
from decimal import Decimal

import pytest

from data_pipeline.parsing import (
    classify_missing,
    clean_text,
    decimal_to_str,
    financial_year_label,
    fold_person_name,
    fold_text,
    has_suspect_encoding,
    normalize_text,
    parse_amount,
    parse_date,
)


# ------------------------------------------------------------------------- amounts
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("497185", "497185"),
        (497185, "497185"),
        (497185.0, "497185.0"),
        ("154773472.11", "154773472.11"),
        (154773472.11, "154773472.11"),          # float is read via repr: no binary noise
        ("1,50,000", "150000"),                   # Indian grouping
        ("1,500,000", "1500000"),                 # Western grouping
        ("\u20b9 5,00,000.50", "500000.50"),      # rupee sign
        ("Rs. 1,200", "1200"),
        ("INR 750", "750"),
        ("(1,000)", "-1000"),                     # accounting negative
        ("-500", "-500"),
        ("+42", "42"),
        (".5", "0.5"),
        ("  7 ", "7"),
        ("1e3", "1E+3"),
        ("0", "0"),
    ],
)
def test_parse_amount_valid(raw, expected):
    result = parse_amount(raw)
    assert result.ok
    assert result.value == Decimal(expected)


def test_amounts_are_exact_decimals_not_floats():
    assert parse_amount("0.1").value + parse_amount("0.2").value == Decimal("0.3")


def test_float_noise_is_rounded_but_real_extra_decimals_are_flagged():
    noisy = parse_amount(0.1 + 0.2)
    assert noisy.value == Decimal("0.30") and "float_noise_rounded" in noisy.notes
    precise = parse_amount("12.3456")
    assert precise.value == Decimal("12.3456") and "more_than_2_decimals" in precise.notes


def test_negative_values_are_valid_here_and_left_to_the_quality_checks():
    result = parse_amount("-1")
    assert result.ok and result.value < 0


@pytest.mark.parametrize("raw", [None, "", "   ", "N/A", "na", "NIL", "-", "--", "null"])
def test_missing_and_placeholder_amounts(raw):
    result = parse_amount(raw)
    assert result.status == "missing" and result.value is None


@pytest.mark.parametrize(
    "raw",
    ["abc", "12abc", "1,5", "12,34,56", "1.2.3", "--5", "5 000", "0x10", "\u0661\u0662", True, float("nan"), float("inf"), object()],
)
def test_invalid_amounts(raw):
    result = parse_amount(raw)
    assert result.status == "invalid" and result.value is None and result.notes


@pytest.mark.parametrize(
    "raw, reason",
    [
        ("1e999999", "magnitude_out_of_range"),        # would render as a million-digit string
        ("1e15", "magnitude_out_of_range"),
        ("1000000000000000", "magnitude_out_of_range"),
        ("9" * 400, "magnitude_out_of_range"),
        (10**20, "magnitude_out_of_range"),
        (1e300, "magnitude_out_of_range"),
        (Decimal("1E+50"), "magnitude_out_of_range"),
        ("1e-13", "excessive_precision"),
        ("1e-999999", "excessive_precision"),
    ],
)
def test_absurd_magnitudes_are_rejected_as_corrupt_cells(raw, reason):
    result = parse_amount(raw)
    assert result.status == "invalid" and result.notes == (reason,)


def test_amount_just_below_the_limit_is_still_accepted():
    assert parse_amount("999999999999999").ok
    assert parse_amount(-999999999999999).ok


def test_decimal_to_str_is_plain_text():
    assert decimal_to_str(Decimal("497185.00")) == "497185"
    assert decimal_to_str(Decimal("1E+3")) == "1000"
    assert decimal_to_str(Decimal("1234.50")) == "1234.5"
    assert decimal_to_str(Decimal("-0.00")) == "0"
    assert decimal_to_str(Decimal("154773472.11")) == "154773472.11"


# --------------------------------------------------------------------------- dates
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("08-Jul-2024", dt.date(2024, 7, 8)),
        ("8-Jul-2024", dt.date(2024, 7, 8)),
        ("08-JUL-2024", dt.date(2024, 7, 8)),
        ("21-aug-2026", dt.date(2026, 8, 21)),
        ("08 July 2024", dt.date(2024, 7, 8)),
        ("08-Sept-2024", dt.date(2024, 9, 8)),
        ("2024-07-08", dt.date(2024, 7, 8)),
        ("2024-07-08 10:30:00", dt.date(2024, 7, 8)),
        ("08/07/2024", dt.date(2024, 7, 8)),      # day-first
        ("31-12-2024", dt.date(2024, 12, 31)),
        ("Jul 8, 2024", dt.date(2024, 7, 8)),
        (dt.datetime(2024, 7, 8), dt.date(2024, 7, 8)),
        (dt.date(2024, 7, 8), dt.date(2024, 7, 8)),
        (45481, dt.date(2024, 7, 8)),              # Excel serial number
        (45481.0, dt.date(2024, 7, 8)),
    ],
)
def test_parse_date_valid(raw, expected):
    result = parse_date(raw)
    assert result.ok and result.value == expected


def test_standard_format_has_no_notes_and_other_formats_are_flagged():
    assert parse_date("08-Jul-2024").notes == ()
    assert "alt_format" in parse_date("2024-07-08").notes
    assert "numeric_day_first_assumed" in parse_date("08/07/2024").notes
    assert parse_date(45481).notes == ("excel_serial",)
    assert parse_date(dt.datetime(2024, 7, 8, 13, 5)).notes == ("time_component_dropped",)


@pytest.mark.parametrize("raw", [None, "", "  ", "N/A", "-"])
def test_missing_dates(raw):
    assert parse_date(raw).status == "missing"


@pytest.mark.parametrize(
    "raw",
    ["31-Feb-2024", "00-Jan-2024", "32-Jan-2024", "08-Foo-2024", "08-Jul-24", "2024-13-01", "not a date",
     "1/2/3", "45481abc", 0, -5, 3_000_000, True, float("nan"), dt.time(10, 30)],
)
def test_invalid_dates_are_reported_not_guessed(raw):
    result = parse_date(raw)
    assert result.status == "invalid" and result.value is None and result.notes


def test_leap_day_handling():
    assert parse_date("29-Feb-2024").ok
    assert parse_date("29-Feb-2023").status == "invalid"


def test_financial_year_label():
    assert financial_year_label(dt.date(2024, 4, 1)) == "2024-2025"
    assert financial_year_label(dt.date(2025, 3, 31)) == "2024-2025"
    assert financial_year_label(dt.date(2025, 1, 15)) == "2024-2025"


# ---------------------------------------------------------------------------- text
def test_clean_text_normalises_whitespace_but_keeps_case():
    assert clean_text("  Construction   of\n road \u200b ") == "Construction of road"
    assert clean_text("DHARWAD") == "DHARWAD"
    assert clean_text(12.0) == "12"
    assert clean_text(7) == "7"


@pytest.mark.parametrize("raw", [None, "", "   ", "N/A", "n/a", "null", "-"])
def test_clean_text_returns_none_for_missing(raw):
    assert clean_text(raw) is None


def test_classify_missing_distinguishes_empty_from_placeholder():
    assert classify_missing(None) == "empty"
    assert classify_missing("  ") == "empty"
    assert classify_missing("N/A") == "placeholder"
    assert classify_missing("Karnataka") is None
    assert classify_missing(0) is None


def test_fold_text_ignores_case_spacing_and_punctuation():
    assert fold_text("Uttar  Pradesh") == fold_text("UTTAR PRADESH") == "uttar pradesh"
    assert fold_text("AURANGABAD_BR") == "aurangabad br"
    assert fold_text(None) == ""


def test_fold_person_name_drops_honorifics_only_at_the_start():
    assert fold_person_name("Shri Gurjeet Singh Aujla") == fold_person_name("GURJEET SINGH AUJLA")
    assert fold_person_name("Smt. Aparajita Sarangi") == "aparajita sarangi"
    assert fold_person_name("Om Prakash Dr Singh") == "om prakash dr singh"


def test_suspect_encoding_detection():
    assert has_suspect_encoding("Caf\u00c3\u00a9")
    assert has_suspect_encoding("bad \ufffd char")
    assert not has_suspect_encoding("Normal text")


def test_normalize_text_is_a_single_pass_clean_plus_classify():
    assert normalize_text("  A   b ") == ("A b", None)
    assert normalize_text("N/A") == (None, "placeholder")
    assert normalize_text("") == (None, "empty")
    assert normalize_text(None) == (None, "empty")
    assert normalize_text("\u200b  ") == (None, "empty")
    assert normalize_text(5.0) == ("5", None)
    assert normalize_text("Caf\u00e9\u00a0Bar") == ("Caf\u00e9 Bar", None)
