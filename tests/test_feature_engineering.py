"""Tests for feature_engineering/. All fixtures are hand-built, invented CSV content - no
real MPLADS data and no raw workbooks are touched anywhere in this file.
"""

import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from feature_engineering import feature_definitions as fd
from feature_engineering import profiling
from feature_engineering.build_features import (
    FeatureBuildConfig,
    FeatureBuildError,
    classify_work_status,
    clean_description,
    day_diff,
    run,
    safe_ratio,
    select_demo_work_ids,
)

SNAPSHOT = dt.date(2026, 9, 25)

MASTER_HEADER = [
    "work_id", "id_mp_code", "id_financial_year", "work_category", "work", "state", "ida", "mp_name",
    "constituency", "work_description", "recommended_date", "recommended_amount", "sanction_date",
    "sanction_amount", "work_status", "completion_date", "completed_amount_disbursed",
    "in_recommended", "in_sanctioned", "in_completed",
    "recommended_source_row", "sanctioned_source_row", "completed_source_row",
    "recommended_row_count", "sanctioned_row_count", "completed_row_count", "conflict_fields",
]
EXPENDITURE_HEADER = [
    "work_id", "total_disbursed_all_rows", "total_disbursed", "success_amount", "in_progress_amount",
    "exact_duplicate_amount", "deduplicated_disbursed_amount", "payment_count_all_rows", "payment_count",
    "deduplicated_payment_count", "duplicate_record_count", "duplicate_ratio",
    "first_expenditure_date", "last_expenditure_date", "vendor_count",
    "amount_missing_count", "date_missing_count", "negative_amount_count",
]


def master_row(work_id, **over):
    row = dict.fromkeys(MASTER_HEADER, "")
    row.update({
        "work_id": work_id, "id_mp_code": "MP1", "id_financial_year": "2024-2025",
        "work_category": "Normal/Others", "work": "Road work", "state": "Karnataka", "ida": "SOME IDA",
        "mp_name": "Real MP", "constituency": "DHARWAD", "work_description": "Construction of a road",
        "recommended_date": "2024-06-01", "recommended_amount": "500000", "sanction_date": "2024-06-15",
        "sanction_amount": "480000", "work_status": "Physical Inspection",
        "in_recommended": "1", "in_sanctioned": "1", "in_completed": "0",
    })
    row.update(over)
    return row


def expenditure_row(work_id, **over):
    row = dict.fromkeys(EXPENDITURE_HEADER, "")
    row.update({
        "work_id": work_id, "total_disbursed_all_rows": "400000", "total_disbursed": "400000",
        "success_amount": "400000", "in_progress_amount": "0", "exact_duplicate_amount": "0",
        "deduplicated_disbursed_amount": "400000", "payment_count_all_rows": "2", "payment_count": "2",
        "deduplicated_payment_count": "2", "duplicate_record_count": "0", "duplicate_ratio": "0",
        "first_expenditure_date": "2024-07-01", "last_expenditure_date": "2024-07-15", "vendor_count": "1",
        "amount_missing_count": "0", "date_missing_count": "0", "negative_amount_count": "0",
    })
    row.update(over)
    return row


def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build(tmp_path, master_rows, expenditure_rows, **cfg_over):
    processed = tmp_path / "data" / "processed"
    demo = tmp_path / "data" / "demo"
    docs = tmp_path / "docs"
    write_csv(processed / "works_master.csv", MASTER_HEADER, master_rows)
    write_csv(processed / "expenditure_by_work.csv", EXPENDITURE_HEADER, expenditure_rows)
    cfg = FeatureBuildConfig(processed_dir=processed, demo_dir=demo, docs_dir=docs, snapshot_date=SNAPSHOT, demo_size=10, **cfg_over)
    result = run(cfg)
    return SimpleNamespace(result=result, processed=processed, demo=demo, docs=docs)


def read_features(processed: Path) -> list[dict]:
    with open(processed / "work_features_v0.csv", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def by_id(rows):
    return {r["work_id"]: r for r in rows}


# ------------------------------------------------------------------------- 1-2: one row per work
def test_1_one_row_per_work_invariant(tmp_path):
    masters = [master_row(f"W{i}") for i in range(5)]
    out = build(tmp_path, masters, [expenditure_row("W0")])
    rows = read_features(out.processed)
    assert len(rows) == 5
    assert len({r["work_id"] for r in rows}) == 5


def test_2_left_join_does_not_duplicate_works_with_or_without_expenditure(tmp_path):
    masters = [master_row("W0"), master_row("W1"), master_row("W2")]
    # W1 has expenditure, W0 and W2 do not - the join must still yield exactly 3 rows
    out = build(tmp_path, masters, [expenditure_row("W1")])
    rows = read_features(out.processed)
    assert len(rows) == 3
    assert by_id(rows)["W1"]["total_disbursed_all_rows"] == "400000"
    assert by_id(rows)["W0"]["total_disbursed_all_rows"] == ""
    assert by_id(rows)["W2"]["total_disbursed_all_rows"] == ""


def test_duplicate_work_id_in_works_master_raises_a_clear_error(tmp_path):
    masters = [master_row("W0"), master_row("W0")]
    with pytest.raises(FeatureBuildError, match="work_id is not unique"):
        build(tmp_path, masters, [])


def test_duplicate_work_id_in_expenditure_by_work_raises_a_clear_error(tmp_path):
    masters = [master_row("W0")]
    with pytest.raises(FeatureBuildError, match="work_id is not unique"):
        build(tmp_path, masters, [expenditure_row("W0"), expenditure_row("W0")])


def test_missing_required_column_raises_a_clear_error(tmp_path):
    processed = tmp_path / "data" / "processed"
    header = [c for c in MASTER_HEADER if c != "work_id"]
    write_csv(processed / "works_master.csv", header, [{c: "" for c in header}])
    write_csv(processed / "expenditure_by_work.csv", EXPENDITURE_HEADER, [])
    cfg = FeatureBuildConfig(processed_dir=processed, demo_dir=tmp_path / "demo", docs_dir=tmp_path / "docs", snapshot_date=SNAPSHOT)
    with pytest.raises(FeatureBuildError, match="missing required column"):
        run(cfg)


# --------------------------------------------------------------------------------- 3: utilization
def test_3_utilization_ratio():
    assert safe_ratio(Decimal("250000"), Decimal("500000")) == Decimal("0.5")


def test_3_utilization_ratio_end_to_end(tmp_path):
    out = build(
        tmp_path,
        [master_row("W0", sanction_amount="500000")],
        [expenditure_row("W0", success_amount="250000", total_disbursed_all_rows="300000")],
    )
    row = by_id(read_features(out.processed))["W0"]
    assert row["success_utilization_ratio"] == "0.5"
    assert row["gross_utilization_ratio"] == "0.6"


# ------------------------------------------------------------------------- 4: zero denominator
def test_4_zero_denominator_returns_none_not_an_error_or_zero():
    assert safe_ratio(Decimal("100"), Decimal("0")) is None
    assert safe_ratio(Decimal("100"), None) is None
    assert safe_ratio(None, Decimal("100")) is None


def test_4_zero_sanction_amount_end_to_end(tmp_path):
    out = build(tmp_path, [master_row("W0", sanction_amount="0")], [expenditure_row("W0")])
    row = by_id(read_features(out.processed))["W0"]
    assert row["success_utilization_ratio"] == ""     # blank, never "0" and never an error
    assert row["remaining_sanction_amount"] == "-400000"   # subtraction still computed; not clipped


def test_4_missing_sanction_amount_end_to_end(tmp_path):
    out = build(tmp_path, [master_row("W0", sanction_amount="")], [expenditure_row("W0")])
    row = by_id(read_features(out.processed))["W0"]
    assert row["success_utilization_ratio"] == "" and row["remaining_sanction_amount"] == ""


# ------------------------------------------------------------------------------ 5: missing dates
def test_5_missing_date_returns_none():
    assert day_diff(None, dt.date(2024, 1, 1)) is None
    assert day_diff(dt.date(2024, 1, 1), None) is None


def test_5_missing_sanction_date_blanks_every_dependent_feature(tmp_path):
    out = build(tmp_path, [master_row("W0", sanction_date="")], [expenditure_row("W0")])
    row = by_id(read_features(out.processed))["W0"]
    assert row["recommendation_to_sanction_days"] == ""
    assert row["sanction_to_first_payment_days"] == ""
    assert row["days_since_sanction"] == ""
    # a date that IS available must not be silently invented/blanked because a different one is missing
    assert row["sanction_to_first_payment_days"] == "" and row["days_since_last_payment"] != ""


def test_5_missing_completion_date_leaves_completion_features_blank_not_fabricated(tmp_path):
    out = build(tmp_path, [master_row("W0", completion_date="", in_completed="0")], [expenditure_row("W0")])
    row = by_id(read_features(out.processed))["W0"]
    assert row["sanction_to_completion_days"] == "" and row["completion_date"] == ""


# ----------------------------------------------------------------------------- 6-7: payment span
def test_6_payment_span_days(tmp_path):
    out = build(
        tmp_path, [master_row("W0")],
        [expenditure_row("W0", first_expenditure_date="2024-01-01", last_expenditure_date="2024-01-31", payment_count_all_rows="4")],
    )
    row = by_id(read_features(out.processed))["W0"]
    assert row["payment_span_days"] == "30"
    assert row["average_payment_interval_days"] == "10"   # 30 / (4-1)


def test_7_one_payment_interval_is_blank_not_zero_but_span_is_a_real_zero(tmp_path):
    out = build(
        tmp_path, [master_row("W0")],
        [expenditure_row("W0", first_expenditure_date="2024-03-01", last_expenditure_date="2024-03-01", payment_count_all_rows="1")],
    )
    row = by_id(read_features(out.processed))["W0"]
    assert row["single_payment_work"] == "1"
    assert row["average_payment_interval_days"] == ""    # never fabricated as 0
    assert row["payment_span_days"] == "0"                # a real, literal 0 (same first/last date)


def test_no_expenditure_leaves_payment_span_and_interval_blank(tmp_path):
    out = build(tmp_path, [master_row("W0")], [])
    row = by_id(read_features(out.processed))["W0"]
    assert row["payment_span_days"] == "" and row["average_payment_interval_days"] == "" and row["single_payment_work"] == "0"


# --------------------------------------------------------------------------------- 8: duplicate ratio
def test_8_duplicate_ratio_is_a_pass_through_not_recomputed(tmp_path):
    out = build(tmp_path, [master_row("W0")], [expenditure_row("W0", duplicate_ratio="0.3333", duplicate_record_count="1")])
    row = by_id(read_features(out.processed))["W0"]
    assert row["duplicate_ratio"] == "0.3333" and row["duplicate_record_count"] == "1"


# ---------------------------------------------------------------------- 9: completion classification
@pytest.mark.parametrize(
    "status, expected",
    [
        ("Work Completed", (True, False, False)),
        ("Completed", (True, False, False)),
        ("Work partially Completed", (False, True, False)),
        ("PARTIALLY COMPLETED", (False, True, False)),
        ("Physical Inspection", (False, False, True)),
        ("Sanction", (False, False, True)),
        ("Work Incomplete", (False, False, True)),          # guard: "incomplete" contains "complet"
        ("incomplete", (False, False, True)),
        (None, (False, False, False)),
        ("", (False, False, False)),
    ],
)
def test_9_completion_classification(status, expected):
    assert classify_work_status(status) == expected


def test_9_completion_classification_end_to_end(tmp_path):
    out = build(
        tmp_path,
        [
            master_row("W0", work_status="Work Completed", in_completed="1", completion_date="2024-08-01", completed_amount_disbursed="480000"),
            master_row("W1", work_status="Work partially Completed"),
            master_row("W2", work_status="Physical Inspection"),
            master_row("W3", work_status="", in_sanctioned="0", in_recommended="1"),
        ],
        [],
    )
    rows = by_id(read_features(out.processed))
    assert (rows["W0"]["is_completed"], rows["W0"]["is_partially_completed"], rows["W0"]["is_sanctioned_only"]) == ("1", "0", "0")
    assert (rows["W1"]["is_completed"], rows["W1"]["is_partially_completed"], rows["W1"]["is_sanctioned_only"]) == ("0", "1", "0")
    assert (rows["W2"]["is_completed"], rows["W2"]["is_partially_completed"], rows["W2"]["is_sanctioned_only"]) == ("0", "0", "1")
    assert (rows["W3"]["is_completed"], rows["W3"]["is_partially_completed"], rows["W3"]["is_sanctioned_only"]) == ("0", "0", "0")


# --------------------------------------------------------------- 10: monitoring vs retrospective
def test_10_retrospective_features_are_tagged_retrospective_in_the_registry():
    retrospective = {f.name for f in fd.FEATURES if f.timing == "retrospective"}
    assert retrospective == {"in_completed", "sanction_to_completion_days", "completion_date", "completed_amount_disbursed"}


def test_10_monitoring_examples_from_the_spec_are_tagged_monitoring():
    monitoring_examples = ["sanction_amount", "work_status", "days_since_sanction", "success_amount",
                            "payment_count_all_rows", "vendor_count", "gross_utilization_ratio"]
    for name in monitoring_examples:
        assert fd.FEATURE_BY_NAME[name].timing == "monitoring", name


def test_10_suitability_marks_every_retrospective_feature_retrospective_only():
    for feat in fd.FEATURES:
        if feat.timing == "retrospective":
            verdict, _ = profiling.classify_suitability(feat, None, None)
            assert verdict == "RETROSPECTIVE ONLY", feat.name


def test_10_recommended_feature_set_never_includes_a_retrospective_feature(tmp_path):
    out = build(tmp_path, [master_row("W0", in_completed="1", completion_date="2024-08-01")], [expenditure_row("W0")])
    report = (out.docs / "FEATURE_PROFILING_REPORT.md").read_text(encoding="utf-8")
    section = report.split("## 9. Recommended feature set", 1)[1].split("## Known limitations", 1)[0]
    for name in ("in_completed", "completion_date", "completed_amount_disbursed", "sanction_to_completion_days"):
        assert f"`{name}`" not in section, name


# --------------------------------------------------------------------- 11: deterministic text cleaning
def test_11_text_cleaning_is_deterministic_and_idempotent():
    raw = "  Construction of  a   road\tnear\nthe market  "
    once = clean_description(raw)
    assert once == "Construction of a road near the market"
    assert clean_description(once) == once   # cleaning an already-clean value changes nothing


def test_11_text_cleaning_preserves_place_and_person_terms():
    cleaned = clean_description("Construction of Community Bhavan at DHARWAD near Shri Ramesh temple, Ward 12")
    assert "DHARWAD" in cleaned and "Shri Ramesh" in cleaned and "Ward 12" in cleaned
    assert cleaned == "Construction of Community Bhavan at DHARWAD near Shri Ramesh temple, Ward 12"


@pytest.mark.parametrize("placeholder", ["N/A", "n/a", "NIL", "-", "", None])
def test_11_placeholder_descriptions_become_none_not_a_literal_string(placeholder):
    assert clean_description(placeholder) is None


def test_11_description_length_and_token_count_match_the_cleaned_text(tmp_path):
    out = build(tmp_path, [master_row("W0", work_description="  Road   work  ")], [])
    row = by_id(read_features(out.processed))["W0"]
    assert row["cleaned_work_description"] == "Road work"
    assert row["description_length_chars"] == "9" and row["description_token_count"] == "2"


# ------------------------------------------------------------------------- 12: peer group minimum
def test_12_peer_group_at_exactly_the_minimum_is_sufficient(tmp_path):
    big = [master_row(f"BIG{i}", work_category="BigCat", sanction_amount=str(100000 + i)) for i in range(fd.MIN_PEER_GROUP_SIZE)]
    small = [master_row(f"SMALL{i}", work_category="SmallCat", sanction_amount="999999") for i in range(fd.MIN_PEER_GROUP_SIZE - 1)]
    out = build(tmp_path, big + small, [])
    rows = by_id(read_features(out.processed))
    assert rows["BIG0"]["peer_group_sufficient_by_work_category"] == "1"
    assert rows["BIG0"]["peer_count_by_work_category"] == str(fd.MIN_PEER_GROUP_SIZE)
    assert rows["BIG0"]["peer_median_sanction_amount_by_work_category"] != ""
    assert rows["SMALL0"]["peer_group_sufficient_by_work_category"] == "0"
    assert rows["SMALL0"]["peer_count_by_work_category"] == str(fd.MIN_PEER_GROUP_SIZE - 1)
    assert rows["SMALL0"]["peer_median_sanction_amount_by_work_category"] == ""   # gated off, not a misleading value


def test_12_peer_median_is_correct_when_sufficient(tmp_path):
    amounts = [100000, 200000, 300000, 400000] + [500000] * (fd.MIN_PEER_GROUP_SIZE - 4)
    rows_in = [master_row(f"W{i}", work_category="Cat", sanction_amount=str(a)) for i, a in enumerate(amounts)]
    out = build(tmp_path, rows_in, [])
    row = by_id(read_features(out.processed))["W0"]
    expected_median = statistics_median(amounts)
    assert Decimal(row["peer_median_sanction_amount_by_work_category"]) == expected_median


def statistics_median(values):
    ordered = sorted(Decimal(v) for v in values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def test_12_a_work_missing_its_grouping_value_is_never_assigned_a_peer_group(tmp_path):
    rows_in = [master_row(f"W{i}", work_category="Cat") for i in range(fd.MIN_PEER_GROUP_SIZE)]
    rows_in.append(master_row("NOCAT", work_category=""))
    out = build(tmp_path, rows_in, [])
    row = by_id(read_features(out.processed))["NOCAT"]
    assert row["peer_count_by_work_category"] == "" and row["peer_group_sufficient_by_work_category"] == "0"


# -------------------------------------------------------------------- 13: output reproducibility
def test_13_feature_output_is_byte_for_byte_reproducible(tmp_path):
    masters = [master_row(f"W{i}", sanction_amount=str(100000 * (i + 1))) for i in range(8)]
    expenditures = [expenditure_row(f"W{i}") for i in range(0, 8, 2)]

    def run_once(name):
        return build(tmp_path / name, masters, expenditures)

    first = run_once("a")
    second = run_once("b")
    a = (first.processed / "work_features_v0.csv").read_bytes()
    b = (second.processed / "work_features_v0.csv").read_bytes()
    assert a == b
    a_demo = (first.demo / "work_features_v0_demo.csv").read_bytes()
    b_demo = (second.demo / "work_features_v0_demo.csv").read_bytes()
    assert a_demo == b_demo
    a_report = (first.docs / "FEATURE_PROFILING_REPORT.md").read_bytes()
    b_report = (second.docs / "FEATURE_PROFILING_REPORT.md").read_bytes()
    assert a_report == b_report


def test_13_feature_output_order_is_deterministic_regardless_of_input_file_order(tmp_path):
    masters = [master_row("W2"), master_row("W0"), master_row("W1")]
    out = build(tmp_path, masters, [])
    rows = read_features(out.processed)
    assert [r["work_id"] for r in rows] == ["W0", "W1", "W2"]


def test_13_column_order_matches_the_registry(tmp_path):
    out = build(tmp_path, [master_row("W0")], [])
    with open(out.processed / "work_features_v0.csv", newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert tuple(header) == fd.OUTPUT_COLUMNS


# --------------------------------------------------------------------------------- demo selection
def test_demo_file_reuses_the_existing_main_pipeline_demo_cohort_when_present(tmp_path):
    processed = tmp_path / "data" / "processed"
    demo = tmp_path / "data" / "demo"
    write_csv(processed / "works_master.csv", MASTER_HEADER, [master_row(f"W{i}") for i in range(10)])
    write_csv(processed / "expenditure_by_work.csv", EXPENDITURE_HEADER, [])
    write_csv(demo / "works_master_demo.csv", MASTER_HEADER, [master_row("W3"), master_row("W7")])
    cfg = FeatureBuildConfig(processed_dir=processed, demo_dir=demo, docs_dir=tmp_path / "docs", snapshot_date=SNAPSHOT)
    run(cfg)
    assert {r["work_id"] for r in read_features_from(demo / "work_features_v0_demo.csv")} == {"W3", "W7"}


def read_features_from(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_demo_selection_falls_back_to_a_deterministic_sample_without_an_existing_cohort():
    ids = [f"W{i}" for i in range(50)]
    first = select_demo_work_ids(ids, None, size=10, seed="seed-a")
    second = select_demo_work_ids(ids, None, size=10, seed="seed-a")
    assert first == second and len(first) == 10
    different_seed = select_demo_work_ids(ids, None, size=10, seed="seed-b")
    assert different_seed != first


# --------------------------------------------------------------------------------- report content
def test_report_never_asserts_fraud_or_a_risk_score_only_denies_them(tmp_path):
    out = build(tmp_path, [master_row("W0")], [expenditure_row("W0")])
    report = (out.docs / "FEATURE_PROFILING_REPORT.md").read_text(encoding="utf-8")
    for line in report.splitlines():
        lowered = line.lower()
        if "fraud" in lowered:
            assert "not" in lowered or "never" in lowered, line
        if "risk score" in lowered:
            assert "no anomaly algorithm, risk score" in lowered or "not" in lowered, line


def test_report_has_every_required_section(tmp_path):
    out = build(tmp_path, [master_row("W0")], [expenditure_row("W0")])
    report = (out.docs / "FEATURE_PROFILING_REPORT.md").read_text(encoding="utf-8")
    for heading in (
        "## 1. Dataset summary", "## 2. Feature inventory", "## 3. Distribution analysis", "## 4. Missingness",
        "## 5. Outlier analysis", "## 6. Peer-group viability", "## 7. Feature suitability",
        "## 8. Leakage risks", "## 9. Recommended feature set for Anomaly Engine v1",
    ):
        assert heading in report, heading


def test_no_raw_workbooks_or_data_pipeline_files_are_touched(tmp_path):
    import hashlib

    frozen = Path("data_pipeline")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen.glob("*.py")}
    build(tmp_path, [master_row("W0")], [expenditure_row("W0")])
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen.glob("*.py")}
    assert before == after
