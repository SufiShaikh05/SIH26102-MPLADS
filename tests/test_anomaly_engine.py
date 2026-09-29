"""Focused tests for anomaly_engine (synthetic fixtures only)."""
import csv, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from anomaly_engine import build_anomalies as ba
from anomaly_engine import scoring, explanations, rules

HEADER = list(ba.USED_FIELDS)

def row(work_id, **over):
    d = {c: "" for c in HEADER}
    d.update(work_id=work_id, state="Karnataka", constituency="C", mp_name="MP", ida="IDA",
              work_category="Normal/Others", work_status="Physical Inspection",
              sanction_amount="500000", total_disbursed_all_rows="0", deduplicated_disbursed_amount="0",
              success_utilization_ratio="", deduplicated_utilization_ratio="",
              days_since_sanction="100", days_since_last_payment="",
              payment_count_all_rows="0", deduplicated_payment_count="0", vendor_count="0",
              duplicate_record_count="0", duplicate_ratio="0", duplicate_amount_ratio="0",
              in_progress_amount="0", in_progress_ratio="0", payment_span_days="",
              sanction_to_first_payment_days="", sanction_to_last_payment_days="")
    for k, _ in ba.peer_signals.LEVELS:
        d[f"peer_group_sufficient_by_{k}"] = "0"
        for pfx in ("peer_count", "peer_median_sanction_amount", "peer_median_disbursed_amount", "peer_median_payment_count"):
            d[f"{pfx}_by_{k}"] = ""
    d.update(over)
    return d

def make(n, **over):
    return [row(f"W{i}", **over) for i in range(n)]

def run_rows(rows):
    return ba.evaluate(rows)

def test_1_one_row_per_work_and_2_no_duplicate_ids():
    res = run_rows(make(30))
    assert len(res.outputs) == 30
    ids = [r["work_id"] for r in res.outputs]
    assert len(set(ids)) == 30

def test_3_deterministic_score_and_18_run_twice_matches():
    rows = make(25, sanction_amount="500000")
    a = run_rows(rows); b = run_rows(rows)
    assert [r["review_priority_score"] for r in a.outputs] == [r["review_priority_score"] for r in b.outputs]

def test_4_score_always_0_to_100():
    res = run_rows(make(25, sanction_amount="1", days_since_sanction="9999", success_utilization_ratio="0.01",
                         deduplicated_utilization_ratio="0.01", days_since_last_payment="9999",
                         payment_count_all_rows="500", deduplicated_payment_count="500", vendor_count="500",
                         duplicate_record_count="500", duplicate_ratio="0.99", duplicate_amount_ratio="0.99",
                         in_progress_ratio="0.99", in_progress_amount="1000000"))
    for r in res.outputs:
        s = float(r["review_priority_score"])
        assert 0.0 <= s <= 100.0

def test_5_label_boundaries():
    assert scoring.review_priority_label(24.99) == "Normal Monitoring"
    assert scoring.review_priority_label(25.0) == "Low Review Priority"
    assert scoring.review_priority_label(49.99) == "Low Review Priority"
    assert scoring.review_priority_label(50.0) == "Medium Review Priority"
    assert scoring.review_priority_label(74.99) == "Medium Review Priority"
    assert scoring.review_priority_label(75.0) == "High Review Priority"
    assert scoring.review_priority_label(100.0) == "High Review Priority"

def test_6_zero_denominator_handling():
    rows = make(25, sanction_amount="0")
    res = run_rows(rows)
    for r in res.outputs:
        assert r["sanction_to_peer_median"] == "" or float(r["sanction_to_peer_median"]) >= 0

def test_7_missing_expenditure_handling():
    rows = make(25)  # payment_count_all_rows = 0, no dates
    res = run_rows(rows)
    for r in res.outputs:
        assert r["sev_payment_count"] == "0" and r["sev_payment_timing"] == "0"
        float(r["review_priority_score"])

def test_8_insufficient_peer_group_handling():
    rows = make(5, work_category="RareCat", peer_group_sufficient_by_work_category="0",
                peer_group_sufficient_by_state="0", peer_group_sufficient_by_state_work_category="0")
    res = run_rows(rows)
    for r in res.outputs:
        assert r["peer_level"] == "none"
        assert r["sanction_to_peer_median"] == ""

def test_9_duplicate_signal():
    base = make(30, duplicate_record_count="1", duplicate_ratio="0.5", duplicate_amount_ratio="0.5")
    res = run_rows(base)
    assert all(r["sev_duplicate_activity"] != "0" for r in res.outputs)
    none_dup = make(30)
    res2 = run_rows(none_dup)
    assert all(r["sev_duplicate_activity"] == "0" for r in res2.outputs)

def test_10_high_payment_count_signal():
    low = [row(f"LOW{i}", payment_count_all_rows="1", deduplicated_payment_count="1",
               peer_group_sufficient_by_work_category="1", peer_count_by_work_category="30",
               peer_median_sanction_amount_by_work_category="500000",
               peer_median_disbursed_amount_by_work_category="500000",
               peer_median_payment_count_by_work_category="1") for i in range(29)]
    high = row("HIGH0", payment_count_all_rows="50", deduplicated_payment_count="50",
              peer_group_sufficient_by_work_category="1", peer_count_by_work_category="30",
              peer_median_sanction_amount_by_work_category="500000",
              peer_median_disbursed_amount_by_work_category="500000",
              peer_median_payment_count_by_work_category="1")
    res = run_rows(low + [high])
    by_id = {r["work_id"]: r for r in res.outputs}
    assert by_id["HIGH0"]["sev_payment_count"] != "0"
    assert all(by_id[f"LOW{i}"]["sev_payment_count"] == "0" for i in range(29))

def test_11_old_plus_low_utilization_combination():
    old_low = row("OLD_LOW", days_since_sanction="900", success_utilization_ratio="0.05",
                  deduplicated_utilization_ratio="0.05")
    young_low = row("YOUNG_LOW", days_since_sanction="1", success_utilization_ratio="0.05",
                    deduplicated_utilization_ratio="0.05")
    filler = make(28, days_since_sanction="200", success_utilization_ratio="0.9", deduplicated_utilization_ratio="0.9")
    res = run_rows([old_low, young_low] + filler)
    by_id = {r["work_id"]: r for r in res.outputs}
    assert by_id["OLD_LOW"]["sev_age_utilization"] != "0"
    assert by_id["YOUNG_LOW"]["sev_age_utilization"] == "0"

def test_12_stale_last_payment_signal():
    stale = row("STALE", payment_count_all_rows="3", deduplicated_payment_count="3",
                success_utilization_ratio="0.2", deduplicated_utilization_ratio="0.2",
                days_since_last_payment="900", days_since_sanction="950")
    fresh = row("FRESH", payment_count_all_rows="3", deduplicated_payment_count="3",
                success_utilization_ratio="0.2", deduplicated_utilization_ratio="0.2",
                days_since_last_payment="2", days_since_sanction="950")
    filler = make(28, payment_count_all_rows="3", deduplicated_payment_count="3", days_since_last_payment="50",
                  days_since_sanction="200", success_utilization_ratio="0.9", deduplicated_utilization_ratio="0.9")
    res = run_rows([stale, fresh] + filler)
    by_id = {r["work_id"]: r for r in res.outputs}
    assert by_id["STALE"]["sev_payment_timing"] != "0" or by_id["STALE"]["sev_age_utilization"] != "0"

def test_13_high_vendor_count_signal():
    common = dict(peer_group_sufficient_by_work_category="1", peer_count_by_work_category="30",
                  peer_median_sanction_amount_by_work_category="500000",
                  peer_median_disbursed_amount_by_work_category="500000",
                  peer_median_payment_count_by_work_category="1")
    rows = [row(f"W{i}", vendor_count="1", **common) for i in range(29)]
    rows.append(row("MANYVEND", vendor_count="30", **common))
    res = run_rows(rows)
    by_id = {r["work_id"]: r for r in res.outputs}
    assert by_id["MANYVEND"]["sev_vendor_count"] != "0"
    assert all(r["sev_vendor_count"] == "0" for r in res.outputs if r["work_id"] != "MANYVEND")

def test_14_explanation_generation():
    assert explanations.explain([], 0) == explanations.NO_SIGNAL_TEXT
    text = explanations.explain(["duplicate_activity", "payment_count"], 2)
    assert "duplicate transaction activity may warrant verification" in text
    assert "does not establish wrongdoing" in text

def test_15_retrospective_fields_excluded_from_scoring():
    for f in ba.RETROSPECTIVE_FIELDS:
        assert f not in ba.USED_FIELDS
    for f in ba.WORKFLOW_FLAGS_NOT_SCORED:
        assert f not in ba.USED_FIELDS

def test_16_no_fraud_wording_anywhere_generated():
    res = run_rows(make(25, duplicate_record_count="3", duplicate_ratio="0.8", duplicate_amount_ratio="0.8"))
    doc = ba.render_doc(res.outputs, res, "work_features_v0.csv", None)
    for text in [r["explanation_text"] for r in res.outputs] + [doc]:
        low = text.lower()
        assert "fraud" not in low or "not a fraud" in low or "not establish" in low or "not a probability of fraud" in low

def test_17_small_synthetic_fixture_end_to_end(tmp_path):
    feat = tmp_path / "work_features_v0.csv"
    with open(feat, "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=HEADER); w.writeheader()
        for r in make(22): w.writerow(r)
    out = tmp_path / "a.csv"; demo = tmp_path / "d.csv"; doc = tmp_path / "doc.md"
    result = ba.run(feat, out, demo, doc, tmp_path / "nope.csv")
    assert len(result.outputs) == 22
    assert doc.is_file() and "## 15. This is NOT a fraud probability" in doc.read_text()
    with open(out) as h:
        rows = list(csv.DictReader(h))
    assert len(rows) == 22 and len({r["work_id"] for r in rows}) == 22

def test_missing_column_raises(tmp_path):
    header = [c for c in HEADER if c != "sanction_amount"]
    feat = tmp_path / "work_features_v0.csv"
    with open(feat, "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=header); w.writeheader()
        w.writerow({k: "" for k in header})
    try:
        ba.load_features(feat)
        assert False, "expected AnomalyInputError"
    except ba.AnomalyInputError:
        pass

def test_duplicate_work_id_raises():
    try:
        ba.evaluate([row("W0"), row("W0")])
        assert False
    except ba.AnomalyInputError:
        pass

def test_18_real_output_validation_if_present():
    real = pathlib.Path(__file__).resolve().parents[1] / "data" / "processed" / "work_anomalies_v1.csv"
    if not real.is_file():
        return
    with open(real, newline="") as h:
        rows = list(csv.DictReader(h))
    ids = [r["work_id"] for r in rows]
    assert len(ids) == len(set(ids))
    for r in rows:
        assert 0.0 <= float(r["review_priority_score"]) <= 100.0
