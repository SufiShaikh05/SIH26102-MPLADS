"""Tests for the expenditure_by_work.csv duplicate/status breakdown: exact duplicate transaction
rows are never deleted (the raw workbook has no transaction ID, so an identical row could be a
genuine repeat payment), and every total is produced both with and without them so a reader can
judge for themselves. All data is INVENTED.

Unit-level tests build a small ``txn`` table directly and check `aggregate.iter_expenditure_aggregates`
in isolation; end-to-end tests run the full pipeline on a small synthetic workbook set.
"""

import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import FILE_NAMES, HEADERS, TITLES, read_csv, write_workbook

from data_pipeline import staging
from data_pipeline.aggregate import AGG_COLUMNS, IN_PROGRESS_STATUS, SUCCESS_STATUS, iter_expenditure_aggregates
from data_pipeline.build_dataset import PipelineConfig, run_pipeline
from data_pipeline.ingest import TXN_TABLE_COLUMNS

AS_OF = dt.date(2026, 9, 26)
IDA = "SOME DISTRICT(DISTRICT MAGISTRATE SOME DISTRICT_IDA)"


def make_txn_table(rows: list[tuple]) -> sqlite3.Connection:
    """``rows``: (source_row, work_id, amount, expenditure_date, vendor_key, payment_status, duplicate)."""
    conn = sqlite3.connect(":memory:")
    staging.create_txn_table(conn)
    staging.insert_rows(conn, "txn", TXN_TABLE_COLUMNS, rows)
    conn.commit()
    return conn


def one(conn: sqlite3.Connection) -> dict:
    results = list(iter_expenditure_aggregates(conn))
    assert len(results) == 1
    return results[0]


def test_every_agg_column_is_present_in_the_yielded_dict():
    conn = make_txn_table([(1, "W1", "100", "2024-01-01", "v1", SUCCESS_STATUS, 0)])
    agg = one(conn)
    assert set(agg) == set(AGG_COLUMNS)


# --------------------------------------------------------------------- 1-2: status aggregation
def test_1_payment_success_aggregation():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 0),
        (2, "W1", "50000", "2024-01-02", "v2", SUCCESS_STATUS, 0),
        (3, "W1", "20000", "2024-01-03", "v3", IN_PROGRESS_STATUS, 0),
    ])
    agg = one(conn)
    assert agg["success_amount"] == Decimal("150000")
    assert agg["total_disbursed_all_rows"] == Decimal("170000")


def test_2_payment_in_progress_aggregation():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 0),
        (2, "W1", "40000", "2024-01-02", "v2", IN_PROGRESS_STATUS, 0),
        (3, "W1", "10000", "2024-01-03", "v3", IN_PROGRESS_STATUS, 0),
    ])
    agg = one(conn)
    assert agg["in_progress_amount"] == Decimal("50000")
    assert agg["success_amount"] == Decimal("100000")


def test_a_status_other_than_the_two_named_ones_counts_toward_neither_bucket():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", "Rejected", 0),
        (2, "W1", "50000", "2024-01-02", "v2", None, 0),
    ])
    agg = one(conn)
    assert agg["success_amount"] == Decimal(0) and agg["in_progress_amount"] == Decimal(0)
    assert agg["total_disbursed_all_rows"] == Decimal("150000")   # still counted in the "all rows" total


def test_status_amounts_include_duplicate_rows_same_as_the_all_rows_total():
    """success_amount / in_progress_amount are defined purely by status - duplicate_record does
    not filter them, matching the user-facing definition exactly."""
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 0),
        (2, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 1),   # exact duplicate of row 1
    ])
    agg = one(conn)
    assert agg["success_amount"] == Decimal("200000")


# ---------------------------------------------------------------- 3-4: duplicate/dedup amounts
def test_3_exact_duplicate_amount():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 0),
        (2, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 1),
        (3, "W1", "50000", "2024-01-02", "v2", SUCCESS_STATUS, 0),
    ])
    agg = one(conn)
    assert agg["exact_duplicate_amount"] == Decimal("100000")


def test_4_deduplicated_amount():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 0),
        (2, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 1),
        (3, "W1", "50000", "2024-01-02", "v2", SUCCESS_STATUS, 0),
    ])
    agg = one(conn)
    assert agg["deduplicated_disbursed_amount"] == Decimal("150000")
    # the two always add back up to the "all rows" total
    assert agg["exact_duplicate_amount"] + agg["deduplicated_disbursed_amount"] == agg["total_disbursed_all_rows"]


def test_duplicate_and_deduplicated_amounts_ignore_payment_status():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 1),
        (2, "W1", "40000", "2024-01-02", "v2", IN_PROGRESS_STATUS, 1),
        (3, "W1", "10000", "2024-01-03", "v3", SUCCESS_STATUS, 0),
    ])
    agg = one(conn)
    assert agg["exact_duplicate_amount"] == Decimal("140000")   # both duplicate rows, regardless of status
    assert agg["deduplicated_disbursed_amount"] == Decimal("10000")


def test_a_duplicate_rows_missing_amount_is_excluded_from_every_sum_but_still_counted():
    conn = make_txn_table([
        (1, "W1", None, "2024-01-01", "v1", SUCCESS_STATUS, 1),
        (2, "W1", "50000", "2024-01-02", "v2", SUCCESS_STATUS, 0),
    ])
    agg = one(conn)
    assert agg["exact_duplicate_amount"] == Decimal(0) and agg["amount_missing_count"] == 1
    assert agg["duplicate_record_count"] == 1   # still counted as a duplicate row, just with no amount to add


# ---------------------------------------------------------------------------- 5. duplicate_ratio
def test_5_duplicate_ratio():
    conn = make_txn_table([(i, "W1", "1000", "2024-01-01", f"v{i}", SUCCESS_STATUS, 1 if i <= 3 else 0) for i in range(1, 11)])
    agg = one(conn)
    assert agg["duplicate_record_count"] == 3 and agg["payment_count_all_rows"] == 10
    assert agg["duplicate_ratio"] == Decimal("0.3000")


def test_duplicate_ratio_rounds_to_four_places():
    conn = make_txn_table([(i, "W1", "1000", "2024-01-01", f"v{i}", SUCCESS_STATUS, 1 if i == 1 else 0) for i in range(1, 4)])
    agg = one(conn)
    assert agg["duplicate_ratio"] == Decimal("0.3333")   # 1/3 rounded to 4 places


def test_duplicate_ratio_formula_matches_the_definition_exactly():
    conn = make_txn_table([(i, "W1", "1000", "2024-01-01", f"v{i}", SUCCESS_STATUS, 1 if i <= 2 else 0) for i in range(1, 8)])
    agg = one(conn)
    assert agg["duplicate_ratio"] == (Decimal(agg["duplicate_record_count"]) / Decimal(agg["payment_count_all_rows"])).quantize(Decimal("0.0001"))


# ----------------------------------------------------------- 6-8: no / one / multiple duplicates
def test_6_a_work_with_no_duplicates():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 0),
        (2, "W1", "50000", "2024-01-02", "v2", SUCCESS_STATUS, 0),
    ])
    agg = one(conn)
    assert agg["duplicate_record_count"] == 0
    assert agg["duplicate_ratio"] == Decimal(0)
    assert agg["exact_duplicate_amount"] == Decimal(0)
    assert agg["deduplicated_payment_count"] == agg["payment_count_all_rows"] == 2
    assert agg["deduplicated_disbursed_amount"] == agg["total_disbursed_all_rows"]


def test_7_a_work_with_exactly_one_duplicate():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 0),
        (2, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 1),
        (3, "W1", "50000", "2024-01-02", "v2", SUCCESS_STATUS, 0),
    ])
    agg = one(conn)
    assert agg["duplicate_record_count"] == 1
    assert agg["payment_count_all_rows"] == 3 and agg["deduplicated_payment_count"] == 2
    assert agg["duplicate_ratio"] == Decimal("0.3333")


def test_8_a_work_with_multiple_duplicates():
    conn = make_txn_table([
        (1, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 0),
        (2, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 1),
        (3, "W1", "100000", "2024-01-01", "v1", SUCCESS_STATUS, 1),
        (4, "W1", "50000", "2024-01-02", "v2", SUCCESS_STATUS, 0),
        (5, "W1", "50000", "2024-01-02", "v2", SUCCESS_STATUS, 1),
    ])
    agg = one(conn)
    assert agg["duplicate_record_count"] == 3 and agg["payment_count_all_rows"] == 5
    assert agg["exact_duplicate_amount"] == Decimal("250000")   # 100000 + 100000 + 50000
    assert agg["deduplicated_disbursed_amount"] == Decimal("150000")
    assert agg["duplicate_ratio"] == Decimal("0.6000")


# -------------------------------------------------------------------------------- end to end
def _minimal_other_workbooks(raw: Path) -> None:
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


def expenditure_rows_with_duplicate_and_footer() -> list[list[object]]:
    return [
        ["1", "Karnataka", "Road work", "WS/MP1/2024-2025/9", IDA, "Real MP", "SOME CONSTITUENCY",
         "01-Aug-2025", "Ordinary Builders Pvt Ltd", SUCCESS_STATUS, "300000"],
        ["2", "Karnataka", "Road work", "WS/MP1/2024-2025/9", IDA, "Real MP", "SOME CONSTITUENCY",
         "05-Aug-2025", "Another Vendor", IN_PROGRESS_STATUS, "200000"],
        # an EXACT duplicate of row 1 - same vendor, date, amount, status
        ["3", "Karnataka", "Road work", "WS/MP1/2024-2025/9", IDA, "Real MP", "SOME CONSTITUENCY",
         "01-Aug-2025", "Ordinary Builders Pvt Ltd", SUCCESS_STATUS, "300000"],
        # genuine grand-total footer: blank vendor/status, amount == exact sum of the three rows above
        [None, None, None, None, None, None, None, None, None, None, str(300000 + 200000 + 300000)],
    ]


@pytest.fixture
def duplicate_pipeline(tmp_path):
    raw = tmp_path / "raw"
    write_workbook(raw / FILE_NAMES["expenditure"], TITLES["expenditure"], HEADERS["expenditure"], expenditure_rows_with_duplicate_and_footer())
    _minimal_other_workbooks(raw)
    out = tmp_path / "out"
    stats = run_pipeline(PipelineConfig(raw_dir=raw, output_root=out, as_of=AS_OF, demo_size=0, progress_every=1000))
    return SimpleNamespace(stats=stats, processed=out / "data" / "processed")


def test_9_the_duplicate_transaction_remains_in_expenditure_transactions_csv(duplicate_pipeline):
    transactions = read_csv(duplicate_pipeline.processed / "expenditure_transactions.csv")
    assert len(transactions) == 3   # the footer never became a transaction, but the duplicate stays
    flags = {row["source_row"]: row["duplicate_record"] for row in transactions}
    assert flags == {"3": "0", "4": "0", "5": "1"}   # row 5 = the exact duplicate of row 3
    amounts = [row["fund_disbursed_amount"] for row in transactions if row["duplicate_record"] == "1"]
    assert amounts == ["300000"]   # the duplicate row's own amount is untouched, not zeroed or removed


def test_10_the_genuine_grand_total_row_is_still_excluded_alongside_a_real_duplicate(duplicate_pipeline):
    summary = [r for r in read_csv(duplicate_pipeline.processed / "summary_rows.csv") if r["source"] == "expenditure"]
    assert len(summary) == 1
    assert summary[0]["reason"] == "summary_amount_only"
    assert summary[0]["source_row"] == "6"   # title + header + 3 data rows -> footer is row 6


def test_end_to_end_totals_reconcile_with_the_duplicate_retained(duplicate_pipeline):
    by_work = {r["work_id"]: r for r in read_csv(duplicate_pipeline.processed / "expenditure_by_work.csv")}
    row = by_work["WS/MP1/2024-2025/9"]
    assert row["total_disbursed_all_rows"] == row["total_disbursed"] == "800000"    # 300000*2 + 200000
    assert row["success_amount"] == "600000"           # both Payment-Success rows, duplicate included
    assert row["in_progress_amount"] == "200000"
    assert row["exact_duplicate_amount"] == "300000"
    assert row["deduplicated_disbursed_amount"] == "500000"
    assert row["payment_count_all_rows"] == row["payment_count"] == "3"
    assert row["deduplicated_payment_count"] == "2"
    assert row["duplicate_record_count"] == "1"
    assert row["duplicate_ratio"] == "0.3333"


def test_raw_workbooks_are_unchanged_after_the_run(duplicate_pipeline):
    assert duplicate_pipeline.stats.run.raw_unchanged is True
