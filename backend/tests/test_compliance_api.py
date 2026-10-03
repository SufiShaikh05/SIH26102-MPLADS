"""API tests for Compliance & Execution Risk Intelligence v1 endpoints."""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestComplianceApiEndpoints:
    """Tests for /api/v1/compliance/* routes."""

    def test_compliance_summary_endpoint(self, client: TestClient):
        res = client.get("/api/v1/compliance/summary")
        assert res.status_code == 200
        data = res.json()

        assert data["total_works_evaluated"] == 81335
        assert data["snapshot_date"] == "2026-09-25"
        assert "MPLADS Guidelines 2023" in data["policy_baseline"]

        # Check unique counts
        uc = data["unique_counts"]
        assert uc["total_unique_flagged_works"] == 71098
        assert uc["unique_policy_affected_works"] == 68853
        assert uc["policy_derived_reconciliation_works"] == 81
        assert uc["unique_execution_affected_works"] == 18935
        assert uc["unflagged_baseline_works"] == 10237
        assert uc["total_unique_flagged_works"] + uc["unflagged_baseline_works"] == 81335

        # Check trigger density
        td = data["trigger_density"]
        assert td["total_trigger_instances"] == 122724
        assert td["average_triggers_per_flagged_work"] == 1.73

        # Check financial outlay reconciliation
        fo = data["financial_outlay"]
        assert fo["total_sanctioned_outlay_all_works_cr"] == 4293.83
        assert fo["total_deduplicated_disbursed_outlay_all_works_cr"] == 2826.89
        assert fo["flagged_works_sanctioned_outlay_cr"] == 3494.34
        assert fo["flagged_works_deduplicated_disbursed_outlay_cr"] == 2208.55
        assert fo["unflagged_works_sanctioned_outlay_cr"] == 799.49
        assert fo["unflagged_works_deduplicated_disbursed_outlay_cr"] == 618.34
        assert fo["flagged_works_deduplicated_disbursed_outlay_cr"] <= fo["total_deduplicated_disbursed_outlay_all_works_cr"]

        # Check all 8 rules present in rule breakdown
        rb = data["rule_breakdown"]
        assert len(rb) == 8
        for r_id in ["COMP-01", "COMP-02", "COMP-03", "COMP-04", "MON-01", "RISK-01", "RISK-02", "RISK-03"]:
            assert r_id in rb
            assert rb[r_id]["unique_works_count"] > 0

    def test_compliance_rules_endpoint(self, client: TestClient):
        res = client.get("/api/v1/compliance/rules")
        assert res.status_code == 200
        data = res.json()

        assert "registry" in data
        assert "rules" in data
        assert len(data["rules"]) == 8

        reg = data["registry"]
        assert reg["base_document"]["document_id"] == "MPLADS-2023-BASE"
        assert reg["base_document"]["source_document_url"] == "https://mplads.gov.in/MPLADS/guidelines/MPLADSGuidelines2023_English_.pdf"
        assert reg["base_document"]["portal_url"] == "https://mplads.mospi.gov.in/"

    def test_compliance_queue_pagination_and_filters(self, client: TestClient):
        # Default pagination
        res = client.get("/api/v1/compliance/queue?page=1&limit=25")
        assert res.status_code == 200
        data = res.json()
        assert data["total_items"] == 71098
        assert data["page"] == 1
        assert data["limit"] == 25
        assert len(data["items"]) == 25

        # Filter by rule_id COMP-04 (81 works)
        res_comp04 = client.get("/api/v1/compliance/queue?rule_id=COMP-04&limit=50")
        assert res_comp04.status_code == 200
        data_c04 = res_comp04.json()
        assert data_c04["total_items"] == 81
        assert len(data_c04["items"]) == 50
        assert data_c04["total_pages"] == 2
        for item in data_c04["items"]:
            assert "COMP-04" in item["triggered_rule_ids"]

        # Filter by state
        res_state = client.get("/api/v1/compliance/queue?state=Sikkim&limit=10")
        assert res_state.status_code == 200
        data_state = res_state.json()
        assert data_state["total_items"] > 0
        for item in data_state["items"]:
            assert item["state"].lower() == "sikkim"

    def test_work_compliance_detail_and_sikkim_benchmark(self, client: TestClient):
        # Sikkim benchmark work lookup
        sikkim_id = "WS/MP013/2024-2025/151021"
        res = client.get(f"/api/v1/compliance/{sikkim_id}")
        assert res.status_code == 200
        data = res.json()

        assert data["work_id"] == sikkim_id
        assert data["state"] == "Sikkim"
        assert len(data["evaluations"]) == 8

        # Each evaluation must contain explainability attributes
        for ev in data["evaluations"]:
            assert "rule_id" in ev
            assert "authority_type" in ev
            assert "classification" in ev
            assert "source_reference" in ev
            assert "is_triggered" in ev
            assert "observed_value" in ev
            assert "threshold" in ev
            assert "reason" in ev
            assert "limitation" in ev
            assert "recommended_action" in ev

    def test_work_compliance_not_found_and_invalid(self, client: TestClient):
        # Nonexistent valid work ID -> 404
        res_404 = client.get("/api/v1/compliance/WS/MP99999/2025-2026/999999")
        assert res_404.status_code == 404

        # Malformed work ID -> 422
        res_422 = client.get("/api/v1/compliance/INVALID-ID-FORMAT")
        assert res_422.status_code == 422
