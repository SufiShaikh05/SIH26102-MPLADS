"""Deterministic tests for Trend Intelligence v1.

Covers:
1. monthly recommendation aggregation
2. monthly sanction aggregation
3. monthly completion aggregation
4. expenditure transaction count
5. expenditure amount
6. Payment Success amount
7. Payment In-Progress amount
8. state filtering
9. chronological ordering
10. missing months represented by zero
11. invalid dates ignored safely
12. invalid numeric amounts handled safely
13. unknown state returns valid empty response
14. API response validates against Pydantic model
15. real data acceptance reconciliation (when real data is available)
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import TrendResponse
from backend.app.services.trend_service import TrendService

REAL_SETTINGS = Settings.from_env()
HAS_REAL_DATA = (
    REAL_SETTINGS.works_master_path.is_file()
    and (REAL_SETTINGS.processed_dir / "expenditure_transactions.csv").is_file()
)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def strict_json(response) -> dict:
    """Parse JSON body, rejecting NaN / Infinity tokens."""
    def refuse(token):
        raise AssertionError(f"non-finite number {token} in JSON body")

    return json.loads(response.text, parse_constant=refuse)


@pytest.fixture()
def mock_processed_dir(tmp_path: Path) -> Path:
    pdir = tmp_path / "processed"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


# =========================================================================== Unit & Mock Tests

def test_monthly_recommendation_aggregation(mock_processed_dir: Path):
    wm = [
        {"work_id": "W1", "state": "StateA", "recommended_date": "2025-01-10", "in_recommended": "1"},
        {"work_id": "W2", "state": "StateA", "recommended_date": "2025-02-15", "in_recommended": "1"},
        {"work_id": "W3", "state": "StateB", "recommended_date": "2025-02-20", "in_recommended": "1"},
        # in_recommended = 0 must NOT be counted
        {"work_id": "W4", "state": "StateA", "recommended_date": "2025-02-25", "in_recommended": "0"},
    ]
    write_csv(
        mock_processed_dir / "works_master.csv",
        wm,
        ["work_id", "state", "recommended_date", "in_recommended"],
    )

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    assert resp.start_period == "2025-01"
    assert resp.end_period == "2025-02"
    assert len(resp.series) == 2

    by_period = {pt.period: pt for pt in resp.series}
    assert by_period["2025-01"].recommended_works == 1
    assert by_period["2025-02"].recommended_works == 2  # W4 excluded because in_recommended == 0


def test_monthly_sanction_aggregation(mock_processed_dir: Path):
    wm = [
        {"work_id": "W1", "state": "StateA", "sanction_date": "2025-02-10"},
        {"work_id": "W2", "state": "StateA", "sanction_date": "2025-03-05"},
        {"work_id": "W3", "state": "StateB", "sanction_date": "2025-03-20"},
    ]
    write_csv(
        mock_processed_dir / "works_master.csv",
        wm,
        ["work_id", "state", "sanction_date"],
    )

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    assert resp.start_period == "2025-02"
    assert resp.end_period == "2025-03"
    by_period = {pt.period: pt for pt in resp.series}
    assert by_period["2025-02"].sanctioned_works == 1
    assert by_period["2025-03"].sanctioned_works == 2


def test_monthly_completion_aggregation(mock_processed_dir: Path):
    wm = [
        {"work_id": "W1", "state": "StateA", "completion_date": "2025-04-12"},
        {"work_id": "W2", "state": "StateA", "completion_date": "2025-05-01"},
        {"work_id": "W3", "state": "StateB", "completion_date": "2025-05-15"},
        {"work_id": "W4", "state": "StateB", "completion_date": "2025-05-28"},
    ]
    write_csv(
        mock_processed_dir / "works_master.csv",
        wm,
        ["work_id", "state", "completion_date"],
    )

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    assert resp.start_period == "2025-04"
    assert resp.end_period == "2025-05"
    by_period = {pt.period: pt for pt in resp.series}
    assert by_period["2025-04"].completed_works == 1
    assert by_period["2025-05"].completed_works == 3


def test_expenditure_transaction_count(mock_processed_dir: Path):
    et = [
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-06-01", "fund_disbursed_amount": "100"},
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-06-15", "fund_disbursed_amount": "200"},
        {"work_id": "W2", "state": "StateB", "expenditure_date": "2025-06-20", "fund_disbursed_amount": "300"},
        {"work_id": "W2", "state": "StateB", "expenditure_date": "2025-07-01", "fund_disbursed_amount": "400"},
    ]
    write_csv(
        mock_processed_dir / "expenditure_transactions.csv",
        et,
        ["work_id", "state", "expenditure_date", "fund_disbursed_amount"],
    )

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    assert resp.start_period == "2025-06"
    assert resp.end_period == "2025-07"
    by_period = {pt.period: pt for pt in resp.series}
    assert by_period["2025-06"].expenditure_transactions == 3
    assert by_period["2025-07"].expenditure_transactions == 1


def test_expenditure_amount(mock_processed_dir: Path):
    et = [
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-03-01", "fund_disbursed_amount": "1000.50"},
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-03-10", "fund_disbursed_amount": "2000.25"},
        {"work_id": "W2", "state": "StateB", "expenditure_date": "2025-03-20", "fund_disbursed_amount": "500.00"},
    ]
    write_csv(
        mock_processed_dir / "expenditure_transactions.csv",
        et,
        ["work_id", "state", "expenditure_date", "fund_disbursed_amount"],
    )

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    by_period = {pt.period: pt for pt in resp.series}
    assert by_period["2025-03"].expenditure_amount == 3500.75


def test_payment_success_amount(mock_processed_dir: Path):
    et = [
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-03-01", "fund_disbursed_amount": "1000.00", "payment_status": "Payment Success"},
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-03-10", "fund_disbursed_amount": "500.00", "payment_status": "Payment In-Progress"},
        {"work_id": "W2", "state": "StateB", "expenditure_date": "2025-03-20", "fund_disbursed_amount": "200.00", "payment_status": "Failed"},
    ]
    write_csv(
        mock_processed_dir / "expenditure_transactions.csv",
        et,
        ["work_id", "state", "expenditure_date", "fund_disbursed_amount", "payment_status"],
    )

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    by_period = {pt.period: pt for pt in resp.series}
    assert by_period["2025-03"].payment_success_amount == 1000.00
    assert by_period["2025-03"].expenditure_amount == 1700.00


def test_payment_in_progress_amount(mock_processed_dir: Path):
    et = [
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-03-01", "fund_disbursed_amount": "1000.00", "payment_status": "Payment Success"},
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-03-10", "fund_disbursed_amount": "500.00", "payment_status": "Payment In-Progress"},
        {"work_id": "W2", "state": "StateB", "expenditure_date": "2025-03-20", "fund_disbursed_amount": "300.00", "payment_status": "Payment In-Progress"},
    ]
    write_csv(
        mock_processed_dir / "expenditure_transactions.csv",
        et,
        ["work_id", "state", "expenditure_date", "fund_disbursed_amount", "payment_status"],
    )

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    by_period = {pt.period: pt for pt in resp.series}
    assert by_period["2025-03"].payment_in_progress_amount == 800.00


def test_state_filtering(mock_processed_dir: Path):
    wm = [
        {"work_id": "W1", "state": "Bihar", "recommended_date": "2025-01-10", "sanction_date": "2025-02-10", "in_recommended": "1"},
        {"work_id": "W2", "state": "Gujarat", "recommended_date": "2025-01-15", "sanction_date": "2025-02-15", "in_recommended": "1"},
    ]
    et = [
        {"work_id": "W1", "state": "Bihar", "expenditure_date": "2025-03-01", "fund_disbursed_amount": "500.00", "payment_status": "Payment Success"},
        {"work_id": "W2", "state": "Gujarat", "expenditure_date": "2025-03-05", "fund_disbursed_amount": "700.00", "payment_status": "Payment Success"},
    ]
    write_csv(mock_processed_dir / "works_master.csv", wm, ["work_id", "state", "recommended_date", "sanction_date", "in_recommended"])
    write_csv(mock_processed_dir / "expenditure_transactions.csv", et, ["work_id", "state", "expenditure_date", "fund_disbursed_amount", "payment_status"])

    service = TrendService(Settings(processed_dir=mock_processed_dir))

    # National
    nat = service.get_trends()
    assert nat.state is None
    by_p_nat = {pt.period: pt for pt in nat.series}
    assert by_p_nat["2025-01"].recommended_works == 2
    assert by_p_nat["2025-02"].sanctioned_works == 2
    assert by_p_nat["2025-03"].expenditure_amount == 1200.00

    # Filtered by Bihar
    bihar = service.get_trends(state="Bihar")
    assert bihar.state == "Bihar"
    by_p_bihar = {pt.period: pt for pt in bihar.series}
    assert by_p_bihar["2025-01"].recommended_works == 1
    assert by_p_bihar["2025-02"].sanctioned_works == 1
    assert by_p_bihar["2025-03"].expenditure_amount == 500.00

    # Filtered by Gujarat
    gujarat = service.get_trends(state="Gujarat")
    assert gujarat.state == "Gujarat"
    by_p_gujarat = {pt.period: pt for pt in gujarat.series}
    assert by_p_gujarat["2025-01"].recommended_works == 1
    assert by_p_gujarat["2025-02"].sanctioned_works == 1
    assert by_p_gujarat["2025-03"].expenditure_amount == 700.00


def test_chronological_ordering(mock_processed_dir: Path):
    wm = [
        {"work_id": "W1", "state": "StateA", "sanction_date": "2025-05-10"},
        {"work_id": "W2", "state": "StateA", "sanction_date": "2024-11-15"},
        {"work_id": "W3", "state": "StateA", "sanction_date": "2025-02-20"},
        {"work_id": "W4", "state": "StateA", "sanction_date": "2024-09-01"},
    ]
    write_csv(mock_processed_dir / "works_master.csv", wm, ["work_id", "state", "sanction_date"])

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    periods = [pt.period for pt in resp.series]
    assert periods == sorted(periods)
    assert periods[0] == "2024-09"
    assert periods[-1] == "2025-05"


def test_missing_months_represented_by_zero(mock_processed_dir: Path):
    wm = [
        {"work_id": "W1", "state": "StateA", "sanction_date": "2025-01-15"},
        {"work_id": "W2", "state": "StateA", "sanction_date": "2025-04-10"},
    ]
    write_csv(mock_processed_dir / "works_master.csv", wm, ["work_id", "state", "sanction_date"])

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    periods = [pt.period for pt in resp.series]
    assert periods == ["2025-01", "2025-02", "2025-03", "2025-04"]

    by_p = {pt.period: pt for pt in resp.series}
    # 2025-02 and 2025-03 are zero-filled
    for p in ("2025-02", "2025-03"):
        pt = by_p[p]
        assert pt.recommended_works == 0
        assert pt.sanctioned_works == 0
        assert pt.completed_works == 0
        assert pt.expenditure_transactions == 0
        assert pt.expenditure_amount == 0.0
        assert pt.payment_success_amount == 0.0
        assert pt.payment_in_progress_amount == 0.0


def test_invalid_dates_ignored_safely(mock_processed_dir: Path):
    wm = [
        {"work_id": "W1", "state": "StateA", "sanction_date": "2025-01-15"},
        {"work_id": "W2", "state": "StateA", "sanction_date": "not-a-date"},
        {"work_id": "W3", "state": "StateA", "sanction_date": "2025-99-99"},
        {"work_id": "W4", "state": "StateA", "sanction_date": ""},
    ]
    et = [
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-01-20", "fund_disbursed_amount": "100"},
        {"work_id": "W2", "state": "StateA", "expenditure_date": "invalid", "fund_disbursed_amount": "200"},
    ]
    write_csv(mock_processed_dir / "works_master.csv", wm, ["work_id", "state", "sanction_date"])
    write_csv(mock_processed_dir / "expenditure_transactions.csv", et, ["work_id", "state", "expenditure_date", "fund_disbursed_amount"])

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    assert resp.start_period == "2025-01"
    assert resp.end_period == "2025-01"
    assert len(resp.series) == 1
    pt = resp.series[0]
    assert pt.sanctioned_works == 1
    assert pt.expenditure_transactions == 1
    assert pt.expenditure_amount == 100.0


def test_invalid_numeric_amounts_handled_safely(mock_processed_dir: Path):
    et = [
        {"work_id": "W1", "state": "StateA", "expenditure_date": "2025-01-10", "fund_disbursed_amount": "nan"},
        {"work_id": "W2", "state": "StateA", "expenditure_date": "2025-01-11", "fund_disbursed_amount": "inf"},
        {"work_id": "W3", "state": "StateA", "expenditure_date": "2025-01-12", "fund_disbursed_amount": "-inf"},
        {"work_id": "W4", "state": "StateA", "expenditure_date": "2025-01-13", "fund_disbursed_amount": "junk"},
        {"work_id": "W5", "state": "StateA", "expenditure_date": "2025-01-14", "fund_disbursed_amount": ""},
        {"work_id": "W6", "state": "StateA", "expenditure_date": "2025-01-15", "fund_disbursed_amount": "500.25"},
    ]
    write_csv(mock_processed_dir / "expenditure_transactions.csv", et, ["work_id", "state", "expenditure_date", "fund_disbursed_amount"])

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends()

    assert len(resp.series) == 1
    pt = resp.series[0]
    assert pt.expenditure_transactions == 6
    assert pt.expenditure_amount == 500.25
    assert not math.isnan(pt.expenditure_amount)
    assert not math.isinf(pt.expenditure_amount)


def test_unknown_state_returns_valid_empty_response(mock_processed_dir: Path):
    wm = [{"work_id": "W1", "state": "Bihar", "sanction_date": "2025-01-15"}]
    write_csv(mock_processed_dir / "works_master.csv", wm, ["work_id", "state", "sanction_date"])

    service = TrendService(Settings(processed_dir=mock_processed_dir))
    resp = service.get_trends(state="Atlantis")

    assert resp.granularity == "month"
    assert resp.start_period is None
    assert resp.end_period is None
    assert resp.state == "Atlantis"
    assert resp.series == []


def test_api_response_validates_against_pydantic_model(mock_processed_dir: Path):
    # Need work_features_v0.csv for app startup
    features = [
        {"work_id": "WS/MP01/2025-2026/001", "state": "Bihar", "work_category": "CatA", "in_sanctioned": "1", "in_completed": "0", "payment_count_all_rows": "1"}
    ]
    write_csv(mock_processed_dir / "work_features_v0.csv", features, list(features[0].keys()))

    wm = [
        {"work_id": "WS/MP01/2025-2026/001", "state": "Bihar", "recommended_date": "2025-01-01", "sanction_date": "2025-02-01", "completion_date": "2025-05-01", "in_recommended": "1"}
    ]
    write_csv(mock_processed_dir / "works_master.csv", wm, list(wm[0].keys()))

    et = [
        {"work_id": "WS/MP01/2025-2026/001", "state": "Bihar", "expenditure_date": "2025-03-01", "fund_disbursed_amount": "12345.67", "payment_status": "Payment Success"}
    ]
    write_csv(mock_processed_dir / "expenditure_transactions.csv", et, list(et[0].keys()))

    settings = Settings(processed_dir=mock_processed_dir)
    client = TestClient(create_app(settings))

    # 1. National trends endpoint
    res_nat = client.get("/api/v1/trends")
    assert res_nat.status_code == 200
    body_nat = strict_json(res_nat)
    validated_nat = TrendResponse.model_validate(body_nat)
    assert validated_nat.state is None
    assert validated_nat.start_period == "2025-01"
    assert validated_nat.end_period == "2025-05"
    assert len(validated_nat.series) == 5

    # 2. State-filtered endpoint
    res_bihar = client.get("/api/v1/trends?state=Bihar")
    assert res_bihar.status_code == 200
    body_bihar = strict_json(res_bihar)
    validated_bihar = TrendResponse.model_validate(body_bihar)
    assert validated_bihar.state == "Bihar"
    assert validated_bihar.start_period == "2025-01"
    assert validated_bihar.end_period == "2025-05"

    # 3. Unknown state endpoint returns HTTP 200 with empty series
    res_unknown = client.get("/api/v1/trends?state=UnknownTerritory")
    assert res_unknown.status_code == 200
    body_unknown = strict_json(res_unknown)
    validated_unknown = TrendResponse.model_validate(body_unknown)
    assert validated_unknown.state == "UnknownTerritory"
    assert validated_unknown.start_period is None
    assert validated_unknown.end_period is None
    assert validated_unknown.series == []


# =========================================================================== Real Data Acceptance

@pytest.mark.skipif(not HAS_REAL_DATA, reason="Real processed dataset not found")
class TestRealDataTrendsReconciliation:
    @pytest.fixture(scope="class")
    @classmethod
    def client(cls):
        return TestClient(create_app(REAL_SETTINGS))

    def test_reconciles_recommended_works(self, client):
        """Monthly recommended event count must reconcile to 80,956 valid recommendation IDs."""
        res = client.get("/api/v1/trends")
        assert res.status_code == 200
        body = strict_json(res)
        total_rec = sum(pt["recommended_works"] for pt in body["series"])
        assert total_rec == 80956

    def test_reconciles_sanctioned_works(self, client):
        """Monthly sanctioned event count must reconcile to 81,335 sanctioned works."""
        res = client.get("/api/v1/trends")
        assert res.status_code == 200
        body = strict_json(res)
        total_san = sum(pt["sanctioned_works"] for pt in body["series"])
        assert total_san == 81335

    def test_reconciles_completed_works(self, client):
        """Monthly completed event count must reconcile to 35,475 completed works."""
        res = client.get("/api/v1/trends")
        assert res.status_code == 200
        body = strict_json(res)
        total_com = sum(pt["completed_works"] for pt in body["series"])
        assert total_com == 35475

    def test_reconciles_expenditure_transactions(self, client):
        """Monthly expenditure transaction count must reconcile to 86,122 transactions."""
        res = client.get("/api/v1/trends")
        assert res.status_code == 200
        body = strict_json(res)
        total_tx = sum(pt["expenditure_transactions"] for pt in body["series"])
        assert total_tx == 86122

    def test_reconciles_expenditure_amounts(self, client):
        """Payment success + in-progress amounts must reconcile with expenditure amount."""
        res = client.get("/api/v1/trends")
        assert res.status_code == 200
        body = strict_json(res)
        total_exp = round(sum(pt["expenditure_amount"] for pt in body["series"]), 2)
        total_succ = round(sum(pt["payment_success_amount"] for pt in body["series"]), 2)
        total_inp = round(sum(pt["payment_in_progress_amount"] for pt in body["series"]), 2)

        assert round(total_succ + total_inp, 2) == total_exp
        assert total_exp == 28426493256.45
        assert total_succ == 27116184572.45
        assert total_inp == 1310308684.0

    def test_real_data_state_filtering_bihar(self, client):
        res = client.get("/api/v1/trends?state=Bihar")
        assert res.status_code == 200
        body = strict_json(res)
        assert body["state"] == "Bihar"
        assert body["granularity"] == "month"
        assert body["start_period"] == "2024-07"
        assert body["end_period"] == "2026-09"
        assert len(body["series"]) == 27

        total_rec = sum(pt["recommended_works"] for pt in body["series"])
        total_san = sum(pt["sanctioned_works"] for pt in body["series"])
        total_com = sum(pt["completed_works"] for pt in body["series"])
        total_tx = sum(pt["expenditure_transactions"] for pt in body["series"])

        assert total_rec == 4582
        assert total_san == 4612
        assert total_com == 2763
        assert total_tx == 3793

    def test_real_data_unknown_state_returns_empty_series(self, client):
        res = client.get("/api/v1/trends?state=UnknownState")
        assert res.status_code == 200
        body = strict_json(res)
        assert body["state"] == "UnknownState"
        assert body["start_period"] is None
        assert body["end_period"] is None
        assert body["series"] == []
