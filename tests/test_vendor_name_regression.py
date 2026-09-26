"""Regression tests for the false-positive reported on real Expenditure data: a bare numeric
vendor_name (an account-style vendor identifier, which is ordinary content in real MPLADS
expenditure records) must never, by itself, be read as a footer/summary row.

All data here is INVENTED - structured to match the reported pattern (a purely numeric
vendor_name, sometimes repeated across more than one payment) without reusing any of the
real values from the report. Kept in its own file and its own small fixture so the existing,
already-verified footer_fixtures.py tests are untouched by this fix.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import FILE_NAMES, HEADERS, TITLES, read_csv, write_workbook

from data_pipeline.build_dataset import PipelineConfig, run_pipeline
from data_pipeline.footer_detection import classify_cheap
from data_pipeline.parsing import parse_amount, parse_date

AS_OF = dt.date(2026, 9, 25)
IDA = "SOME DISTRICT(DISTRICT MAGISTRATE SOME DISTRICT_IDA)"
NUMERIC_VENDOR_A = "111001111008"    # invented, account-style vendor identifiers -
NUMERIC_VENDOR_B = "222002222071"    # structurally like the reported false positives, values invented


def parsed_for(clean: dict, date_fields=(), amount_fields=()) -> dict:
    fields = {}
    for name in date_fields:
        fields[name] = parse_date(clean.get(name))
    for name in amount_fields:
        fields[name] = parse_amount(clean.get(name))
    return fields


BASE_EXPENDITURE = {
    "state": "West Bengal", "ida": IDA, "mp_name": "A Real MP", "constituency": "A CONSTITUENCY",
    "work": "Street lights", "vendor_name": NUMERIC_VENDOR_A, "payment_status": "Payment Success",
    "expenditure_date": "2025-09-11", "fund_disbursed_amount": "295292",
}


# ----------------------------------------------------------------------- unit-level (classify_cheap)
def test_1_numeric_vendor_name_alone_is_not_a_footer():
    clean = dict(BASE_EXPENDITURE)
    parsed = parsed_for(clean, ("expenditure_date",), ("fund_disbursed_amount",))
    verdict = classify_cheap("expenditure", clean, clean, parsed)
    assert not verdict, verdict


def test_2_a_second_numeric_vendor_id_is_also_valid():
    clean = dict(BASE_EXPENDITURE, vendor_name=NUMERIC_VENDOR_B, state="Assam", constituency="GUWAHATI")
    parsed = parsed_for(clean, ("expenditure_date",), ("fund_disbursed_amount",))
    assert not classify_cheap("expenditure", clean, clean, parsed)


@pytest.mark.parametrize("vendor", [NUMERIC_VENDOR_A, "0", "000123", "1", "9999999999999"])
def test_3_payment_success_with_a_numeric_vendor_name_is_valid_for_any_numeric_shape(vendor):
    clean = dict(BASE_EXPENDITURE, vendor_name=vendor, payment_status="Payment Success")
    parsed = parsed_for(clean, ("expenditure_date",), ("fund_disbursed_amount",))
    assert not classify_cheap("expenditure", clean, clean, parsed)


def test_regression_other_expenditure_fields_still_catch_a_real_field_semantic_mismatch():
    """The fix must be scoped to vendor_name only - it must not weaken detection elsewhere."""
    for field in ("payment_status", "state", "mp_name", "constituency", "work"):
        clean = dict(BASE_EXPENDITURE)
        clean[field] = "42938335066.78"
        parsed = parsed_for(clean, ("expenditure_date",), ("fund_disbursed_amount",))
        verdict = classify_cheap("expenditure", clean, clean, parsed)
        assert verdict.is_footer and verdict.reason == "footer_row", field


def test_regression_a_genuine_total_label_in_vendor_name_still_triggers():
    """Excluding vendor_name from the numeric-mismatch check is not the same as ignoring it -
    an actual "Total" label there is still exactly as suspicious as anywhere else."""
    clean = dict(BASE_EXPENDITURE, vendor_name="Grand Total")
    parsed = parsed_for(clean, ("expenditure_date",), ("fund_disbursed_amount",))
    verdict = classify_cheap("expenditure", clean, clean, parsed)
    assert verdict.is_footer and verdict.reason == "grand_total_label"


def test_regression_a_populated_numeric_vendor_name_still_blocks_summary_amount_only():
    """vendor_name must still count as a present identity field for the OTHER detector -
    the fix only turns off the numeric-mismatch check for this one field."""
    clean = {k: None for k in BASE_EXPENDITURE}
    clean["vendor_name"] = NUMERIC_VENDOR_A
    clean["fund_disbursed_amount"] = "999999"
    parsed = parsed_for(clean, ("expenditure_date",), ("fund_disbursed_amount",))
    assert not classify_cheap("expenditure", clean, clean, parsed)


def test_4_summary_amount_only_expenditure_footer_is_still_caught():
    clean = {k: None for k in BASE_EXPENDITURE}   # vendor_name AND payment_status both blank
    clean["fund_disbursed_amount"] = "1150000"
    parsed = parsed_for(clean, ("expenditure_date",), ("fund_disbursed_amount",))
    verdict = classify_cheap("expenditure", clean, clean, parsed)
    assert verdict.is_footer and verdict.reason == "summary_amount_only"


# --------------------------------------------------------------------------------- end to end
def _minimal_other_workbooks(raw: Path) -> None:
    """One trivial, footer-free row per remaining workbook - just enough for the pipeline to run."""
    write_workbook(raw / FILE_NAMES["recommended"], TITLES["recommended"], HEADERS["recommended"],
                    [["1", "Normal/Others", "WS/MP1/2024-2025/1-Road work", "Bihar", IDA, "MP", "C", "d", "01-Jan-2024", "1000", "02-Jan-2024"]])
    write_workbook(raw / FILE_NAMES["sanctioned"], TITLES["sanctioned"], HEADERS["sanctioned"],
                    [["1", "Normal/Others", "WS/MP1/2024-2025/1-Road work", "Bihar", IDA, "MP", "C", "d", "01-Jan-2024", "02-Jan-2024", "1000", "Sanction"]])
    write_workbook(raw / FILE_NAMES["completed"], TITLES["completed"], HEADERS["completed"],
                    [["1", "Normal/Others", "WS/MP1/2024-2025/1-Road work", "Bihar", IDA, "d", "MP", "C", "N/A", "03-Jan-2024", "1000"]])
    write_workbook(raw / FILE_NAMES["allocation"], TITLES["allocation"], HEADERS["allocation"],
                    [["1", "Bihar", "MP", "C", "1000"]])
    write_workbook(raw / FILE_NAMES["calamity"], TITLES["calamity"], HEADERS["calamity"],
                    [["1", "National Calamity", "Some calamity", "MP", "01-Jan-2024", "1000"]])


def numeric_vendor_expenditure_rows() -> list[list[object]]:
    return [
        # a single payment to a numeric-code vendor (like the reported row 36457)
        ["1", "West Bengal", "Street lights", "WS/MP620/2024-2025/1", IDA, "Real MP One", "CONSTITUENCY ONE",
         "11-Sep-2025", NUMERIC_VENDOR_A, "Payment Success", "295292"],
        # the SAME numeric-code vendor paid twice on different dates (like rows 47982 / 47983)
        ["2", "Assam", "Road work", "WS/MP620/2024-2025/2", IDA, "Real MP Two", "CONSTITUENCY TWO",
         "26-Mar-2025", NUMERIC_VENDOR_B, "Payment Success", "900000"],
        ["3", "Assam", "Road work", "WS/MP620/2024-2025/2", IDA, "Real MP Two", "CONSTITUENCY TWO",
         "26-Aug-2025", NUMERIC_VENDOR_B, "Payment Success", "600000"],
        # an ordinary, textually-named-vendor payment, for contrast
        ["4", "Karnataka", "Community hall", "WS/MP620/2024-2025/3", IDA, "Real MP Three", "CONSTITUENCY THREE",
         "01-Aug-2025", "Ordinary Builders Pvt Ltd", "Payment Success", "150000"],
        # the genuine grand-total footer: blank vendor/status, amount == exact sum of the four rows above
        [None, None, None, None, None, None, None, None, None, None, str(295292 + 900000 + 600000 + 150000)],
    ]


@pytest.fixture
def vendor_pipeline(tmp_path):
    raw = tmp_path / "raw"
    write_workbook(raw / FILE_NAMES["expenditure"], TITLES["expenditure"], HEADERS["expenditure"], numeric_vendor_expenditure_rows())
    _minimal_other_workbooks(raw)
    out = tmp_path / "out"
    stats = run_pipeline(PipelineConfig(raw_dir=raw, output_root=out, as_of=AS_OF, demo_size=0, progress_every=1000))
    return SimpleNamespace(stats=stats, processed=out / "data" / "processed", raw=raw)


def test_5_the_genuine_grand_total_row_is_still_excluded(vendor_pipeline):
    summary = read_csv(vendor_pipeline.processed / "summary_rows.csv")
    expenditure_summary = [r for r in summary if r["source"] == "expenditure"]
    assert len(expenditure_summary) == 1
    assert expenditure_summary[0]["reason"] == "summary_amount_only"
    assert expenditure_summary[0]["source_row"] == "7"   # title + header + 4 data rows -> footer is row 7


def test_6_legitimate_numeric_vendor_rows_are_not_excluded(vendor_pipeline):
    summary_source_rows = {
        int(r["source_row"]) for r in read_csv(vendor_pipeline.processed / "summary_rows.csv") if r["source"] == "expenditure"
    }
    assert summary_source_rows == {7}   # only the genuine footer - rows 3, 4, 5, 6 are all untouched

    transactions = read_csv(vendor_pipeline.processed / "expenditure_transactions.csv")
    assert len(transactions) == 4   # the footer never became a transaction
    vendors = {row["source_row"]: row["vendor_name"] for row in transactions}
    assert vendors == {"3": NUMERIC_VENDOR_A, "4": NUMERIC_VENDOR_B, "5": NUMERIC_VENDOR_B, "6": "Ordinary Builders Pvt Ltd"}
    assert vendors["3"] == "111001111008" and vendors["4"] == vendors["5"] == "222002222071"


def test_a_leading_zero_numeric_vendor_name_is_preserved_exactly_as_text():
    """'Preserve numeric vendor identifiers exactly as provided' - a leading zero must never be
    dropped the way it would be if the value were ever treated as a number."""
    from data_pipeline.parsing import normalize_text

    cleaned, missing = normalize_text("000123456")
    assert cleaned == "000123456" and missing is None
    clean = dict(BASE_EXPENDITURE, vendor_name="000123456")
    parsed = parsed_for(clean, ("expenditure_date",), ("fund_disbursed_amount",))
    assert not classify_cheap("expenditure", clean, clean, parsed)


def test_expenditure_by_work_totals_are_correct_with_numeric_vendors_included(vendor_pipeline):
    by_work = {r["work_id"]: r for r in read_csv(vendor_pipeline.processed / "expenditure_by_work.csv")}
    assert by_work["WS/MP620/2024-2025/1"]["total_disbursed"] == "295292"
    assert by_work["WS/MP620/2024-2025/1"]["vendor_count"] == "1"
    assert by_work["WS/MP620/2024-2025/2"]["total_disbursed"] == "1500000"   # 900000 + 600000, same vendor twice
    assert by_work["WS/MP620/2024-2025/2"]["payment_count"] == "2"
    assert by_work["WS/MP620/2024-2025/2"]["vendor_count"] == "1"           # one vendor, two payments
    total = sum(Decimal(r["total_disbursed"]) for r in read_csv(vendor_pipeline.processed / "expenditure_by_work.csv"))
    assert total == Decimal("1945292")   # 295292 + 900000 + 600000 + 150000 - the footer's own total not re-added


def test_raw_workbooks_are_unchanged_after_the_run(vendor_pipeline):
    assert vendor_pipeline.stats.run.raw_unchanged is True
