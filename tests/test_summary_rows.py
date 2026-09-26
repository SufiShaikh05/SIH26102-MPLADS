"""End-to-end regression tests: summary/footer rows, NA-unkeyed recommendations, and genuinely
malformed Work IDs must land in three different places and never be confused with one another.
All data is INVENTED (see footer_fixtures.py) - no real MPLADS data is used anywhere here.
"""

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from footer_fixtures import C1, C2, C3, E1, E2, E3, MALFORMED_RAW, NA_RAW, R1, R2, R3, S1, S2, build_footer_raw_dir
from helpers import read_csv

from data_pipeline.build_dataset import PipelineConfig, run_pipeline
from data_pipeline.excel_io import file_fingerprint

AS_OF = dt.date(2026, 9, 24)


@pytest.fixture
def footer_raw_dir(tmp_path):
    return build_footer_raw_dir(tmp_path / "raw")


@pytest.fixture
def footer_pipeline(tmp_path, footer_raw_dir):
    out = tmp_path / "out"
    stats = run_pipeline(
        PipelineConfig(raw_dir=footer_raw_dir, output_root=out, as_of=AS_OF, demo_size=0, progress_every=1000)
    )
    return SimpleNamespace(stats=stats, out=out, raw=footer_raw_dir, processed=out / "data" / "processed", docs=out / "docs")


def by_id(rows):
    return {row["work_id"]: row for row in rows}


# ------------------------------------------------------------------------- raw files untouched
def test_raw_workbooks_are_byte_identical_after_the_run(footer_pipeline):
    assert footer_pipeline.stats.run.raw_unchanged is True
    for path in footer_pipeline.raw.glob("*.xlsx"):
        # re-fingerprint independently of the pipeline's own before/after check
        assert file_fingerprint(path)["sha256"]  # file still readable and hashes cleanly


# -------------------------------------------------------- 1-4: keyed and expenditure footer rows
def test_1_recommended_summary_row_is_excluded_from_the_master_dataset(footer_pipeline):
    master = by_id(read_csv(footer_pipeline.processed / "works_master.csv"))
    assert {R1, R2, R3} <= set(master)   # master also holds the (non-overlapping) Sanctioned/Completed works
    assert sum(Decimal(master[w]["recommended_amount"]) for w in (R1, R2, R3)) == Decimal("450000")
    assert not any(row.get("recommended_amount") == "450000" for wid, row in master.items() if wid not in (R1, R2, R3))


def test_master_has_exactly_the_eight_legitimate_works_no_phantom_footer_entry(footer_pipeline):
    master = read_csv(footer_pipeline.processed / "works_master.csv")
    assert {row["work_id"] for row in master} == {R1, R2, R3, S1, S2, C1, C2, C3}
    assert len(master) == 8


def test_2_sanctioned_summary_row_is_excluded_from_the_master_dataset(footer_pipeline):
    master = by_id(read_csv(footer_pipeline.processed / "works_master.csv"))
    assert master[S1]["sanction_amount"] == "497185" and master[S2]["sanction_amount"] == "300000"
    assert sum(Decimal(master[w]["sanction_amount"]) for w in (S1, S2)) == Decimal("797185")
    # the footer's "797185.00" never becomes any row's work_status
    assert all(row["work_status"] != "797185.00" for row in master.values())


def test_3_completed_summary_row_is_excluded_even_though_it_looks_like_an_ordinary_row(footer_pipeline):
    """This footer has no label and no field-semantic mismatch - only the exact-aggregate check
    (signal 3) catches it, run once the whole column total is known."""
    master = by_id(read_csv(footer_pipeline.processed / "works_master.csv"))
    completed = {w: r for w, r in master.items() if r["completed_amount_disbursed"]}
    assert set(completed) == {C1, C2, C3}
    assert sum(Decimal(r["completed_amount_disbursed"]) for r in completed.values()) == Decimal("500000")


def test_4_expenditure_grand_total_row_is_excluded_from_transactions_and_aggregates(footer_pipeline):
    transactions = read_csv(footer_pipeline.processed / "expenditure_transactions.csv")
    assert {r["work_id"] for r in transactions} == {E1, E2, E3}
    assert len(transactions) == 3
    by_work = by_id(read_csv(footer_pipeline.processed / "expenditure_by_work.csv"))
    assert set(by_work) == {E1, E2, E3}
    total = sum(Decimal(r["total_disbursed"]) for r in by_work.values())
    assert total == Decimal("1150000")   # NOT 2,300,000 - the footer's own "total" is not added again


# ------------------------------------------------------------------------------- 5. allocation
def test_5_allocation_total_row_does_not_inflate_the_reference_dataset(footer_pipeline):
    rows = read_csv(footer_pipeline.processed / "mp_allocated_limits.csv")
    assert len(rows) == 2
    assert sum(Decimal(r["allocated_amount"]) for r in rows) == Decimal("1000000")   # NOT 2,000,000
    assert all(r["mp_name"] != "Total" for r in rows)


def test_calamity_total_row_does_not_inflate_the_reference_dataset(footer_pipeline):
    """Bonus coverage: the same two-phase handling applies to Calamity, not just Allocation."""
    rows = read_csv(footer_pipeline.processed / "calamity_consents.csv")
    assert len(rows) == 2
    assert sum(Decimal(r["consent_amount"]) for r in rows) == Decimal("1000000")   # NOT 2,000,000


# ------------------------------------------------------------------------- summary_rows.csv audit
def test_summary_rows_csv_has_exactly_one_row_per_source_with_the_expected_reason(footer_pipeline):
    rows = {r["source"]: r for r in read_csv(footer_pipeline.processed / "summary_rows.csv")}
    assert set(rows) == {"recommended", "sanctioned", "completed", "expenditure", "allocation", "calamity"}
    expected_reasons = {
        "recommended": "grand_total_label", "sanctioned": "footer_row", "completed": "invalid_record_shape",
        "expenditure": "summary_amount_only", "allocation": "total_label", "calamity": "invalid_record_shape",
    }
    for source, reason in expected_reasons.items():
        assert rows[source]["reason"] == reason, source
        assert rows[source]["representative_values"]   # never blank - always something to look at
    stats = footer_pipeline.stats.summary_rows
    assert stats.total == 6
    for source, reason in expected_reasons.items():
        assert stats.counts[(source, reason)] == 1


# ------------------------------------------------------------------------------- 6. NA-unkeyed
def test_6_legitimate_na_recommendation_is_not_a_summary_row_and_keeps_its_own_identity(footer_pipeline):
    unkeyed = read_csv(footer_pipeline.processed / "unkeyed_recommendations.csv")
    assert len(unkeyed) == 1
    row = unkeyed[0]
    assert row["raw_value"] == NA_RAW
    assert (row["state"], row["mp_name"], row["constituency"], row["recommended_amount"]) == (
        "Punjab", "Another Real MP", "FARIDKOT", "250000",
    )
    summary_sources = {r["source"] for r in read_csv(footer_pipeline.processed / "summary_rows.csv")}
    assert int(row["source_row"]) not in {
        int(r["source_row"]) for r in read_csv(footer_pipeline.processed / "summary_rows.csv") if r["source"] == "recommended"
    }
    assert "recommended" in summary_sources  # the OTHER (Grand Total) recommended row is still there
    malformed_recommended_raw = {
        r["raw_value"] for r in read_csv(footer_pipeline.processed / "malformed_ids.csv") if r["source"] == "recommended"
    }
    assert NA_RAW not in malformed_recommended_raw
    assert footer_pipeline.stats.unkeyed.total == 1
    assert footer_pipeline.stats.unkeyed.by_source["recommended"] == 1
    assert footer_pipeline.stats.profiles["recommended"].id.na_unkeyed == 1


def test_na_recommendation_correctly_gets_no_master_row_of_its_own(footer_pipeline):
    """No Work ID means no join key - absence from works_master.csv is correct, not a bug."""
    master = read_csv(footer_pipeline.processed / "works_master.csv")
    assert all(row["work_description"] != "Not yet assigned an ID" for row in master)


# --------------------------------------------------------------------------- genuinely malformed
def test_genuinely_malformed_row_is_logged_as_malformed_only(footer_pipeline):
    malformed = read_csv(footer_pipeline.processed / "malformed_ids.csv")
    recommended_malformed = [r for r in malformed if r["source"] == "recommended"]
    assert any(r["raw_value"] == MALFORMED_RAW for r in recommended_malformed)
    assert not any(r["raw_value"] == MALFORMED_RAW for r in read_csv(footer_pipeline.processed / "unkeyed_recommendations.csv"))
    assert not any(
        MALFORMED_RAW in r["representative_values"] for r in read_csv(footer_pipeline.processed / "summary_rows.csv")
    )
    assert footer_pipeline.stats.profiles["recommended"].id.malformed == 1


def test_the_three_categories_are_disjoint_by_source_row(footer_pipeline):
    """A single Recommended source_row can never appear in more than one of the three audit files."""
    malformed_rows = {int(r["source_row"]) for r in read_csv(footer_pipeline.processed / "malformed_ids.csv") if r["source"] == "recommended"}
    unkeyed_rows = {int(r["source_row"]) for r in read_csv(footer_pipeline.processed / "unkeyed_recommendations.csv")}
    summary_rows = {int(r["source_row"]) for r in read_csv(footer_pipeline.processed / "summary_rows.csv") if r["source"] == "recommended"}
    assert malformed_rows and unkeyed_rows and summary_rows   # all three categories are actually present
    assert malformed_rows.isdisjoint(unkeyed_rows)
    assert malformed_rows.isdisjoint(summary_rows)
    assert unkeyed_rows.isdisjoint(summary_rows)


# ------------------------------------------------------------------------------- report content
def test_report_headline_findings_give_the_three_categories_separate_distinct_lines(footer_pipeline):
    report = (footer_pipeline.docs / "DATA_QUALITY_REPORT.md").read_text(encoding="utf-8")
    assert "**Summary/footer row excluded**" in report
    assert "**Unkeyed recommendations**" in report
    assert "**Malformed Work IDs**" in report
    # never described as the same kind of problem
    summary_line = next(line for line in report.splitlines() if line.startswith("- **Summary/footer row excluded**"))
    unkeyed_line = next(line for line in report.splitlines() if line.startswith("- **Unkeyed recommendations**"))
    malformed_line = next(line for line in report.splitlines() if line.startswith("- **Malformed Work IDs**"))
    assert summary_line != unkeyed_line != malformed_line


def test_report_has_a_summary_footer_rows_section_with_source_reason_count_and_amount(footer_pipeline):
    report = (footer_pipeline.docs / "DATA_QUALITY_REPORT.md").read_text(encoding="utf-8")
    assert "## 5. Summary/footer rows excluded" in report
    section = report.split("## 5. Summary/footer rows excluded", 1)[1].split("## 6.", 1)[0]
    assert "| Source | Reason | Count | Amount involved" in section
    assert "Source rows" in section
    for source in ("Recommended", "Sanctioned", "Completed", "Expenditure", "Allocated Limit", "Calamity Consent"):
        assert source in section
    assert "Total excluded across all sources: **6**" in section


def test_report_has_an_unkeyed_subsection_under_work_ids(footer_pipeline):
    report = (footer_pipeline.docs / "DATA_QUALITY_REPORT.md").read_text(encoding="utf-8")
    assert "### 4.1 Unkeyed / NA recommendations (not malformed)" in report
    section = report.split("### 4.1", 1)[1].split("## 5.", 1)[0]
    assert "Recommended" in section and "1" in section


def test_pipeline_summary_json_reports_all_three_categories_separately(footer_pipeline):
    import json

    data = json.loads((footer_pipeline.processed / "pipeline_summary.json").read_text(encoding="utf-8"))
    assert data["summary_rows"]["total"] == 6
    assert data["unkeyed_recommendations"]["total"] == 1
    assert data["unkeyed_recommendations"]["by_source"]["recommended"] == 1
    assert data["sources"]["recommended"]["work_ids"]["na_unkeyed"] == 1
    assert data["sources"]["recommended"]["work_ids"]["malformed"] == 1
    assert data["sources"]["recommended"]["summary_rows_excluded"] == 1


# --------------------------------------------------------------------- output reproducibility
def test_footer_output_is_reproducible_byte_for_byte(tmp_path, footer_raw_dir):
    def run(name):
        out = tmp_path / name
        run_pipeline(PipelineConfig(raw_dir=footer_raw_dir, output_root=out, as_of=AS_OF, demo_size=0, progress_every=1000))
        return out / "data" / "processed"

    first, second = run("a"), run("b")
    names = sorted(p.name for p in first.glob("*.csv"))
    assert "summary_rows.csv" in names and "unkeyed_recommendations.csv" in names
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
