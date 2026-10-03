"""Integration tests for ComplianceAggregator with production dataset and mathematical invariants."""

from pathlib import Path
import pytest

from compliance_engine.aggregator import ComplianceAggregator
from compliance_engine.models import AuthorityType, RuleClassification
from compliance_engine.registry import POLICY_REGISTRY


@pytest.fixture(scope="module")
def aggregator():
    processed_dir = Path("data/processed")
    agg = ComplianceAggregator(processed_dir)
    agg.load()
    return agg


class TestComplianceAggregatorCensus:
    """Verifies that ComplianceAggregator produces the authoritative verified census numbers."""

    def test_total_works_and_invariants(self, aggregator: ComplianceAggregator):
        summary = aggregator.get_summary()

        assert summary.total_works_evaluated == 81335
        counts = summary.unique_counts

        # Core census counts
        assert counts.total_unique_flagged_works == 71098
        assert counts.unflagged_baseline_works == 10237
        assert counts.unique_policy_affected_works == 68853
        assert counts.policy_derived_reconciliation_works == 81
        assert counts.unique_execution_affected_works == 18935

        # Invariant 1: Flagged + Unflagged == Total Works
        assert counts.total_unique_flagged_works + counts.unflagged_baseline_works == summary.total_works_evaluated

        # Invariant 2: Trigger Instances Density
        assert summary.trigger_density.total_trigger_instances == 122724
        assert summary.trigger_density.average_triggers_per_flagged_work == 1.73

    def test_financial_reconciliation_invariants(self, aggregator: ComplianceAggregator):
        fin = aggregator.get_summary().financial_outlay

        # Sanctioned outlay invariant
        assert fin.total_sanctioned_outlay_all_works_cr == 4293.83
        assert fin.flagged_works_sanctioned_outlay_cr == 3494.34
        assert fin.unflagged_works_sanctioned_outlay_cr == 799.49
        assert round(fin.flagged_works_sanctioned_outlay_cr + fin.unflagged_works_sanctioned_outlay_cr, 2) == fin.total_sanctioned_outlay_all_works_cr

        # Disbursed outlay invariant
        assert fin.total_deduplicated_disbursed_outlay_all_works_cr == 2826.89
        assert fin.flagged_works_deduplicated_disbursed_outlay_cr == 2208.55
        assert fin.unflagged_works_deduplicated_disbursed_outlay_cr == 618.34
        assert round(fin.flagged_works_deduplicated_disbursed_outlay_cr + fin.unflagged_works_deduplicated_disbursed_outlay_cr, 2) == fin.total_deduplicated_disbursed_outlay_all_works_cr

        # Inequality verification
        assert fin.flagged_works_deduplicated_disbursed_outlay_cr <= fin.total_deduplicated_disbursed_outlay_all_works_cr

        # Specific cohort outlays
        assert fin.policy_affected_sanctioned_outlay_cr == 3334.01
        assert fin.policy_affected_deduplicated_disbursed_outlay_cr == 2174.14
        assert fin.execution_affected_sanctioned_outlay_cr == 1033.01
        assert fin.execution_affected_deduplicated_disbursed_outlay_cr == 192.70
        assert fin.post_completion_disbursed_outlay_cr == 36.50

    def test_exact_rule_breakdown_census(self, aggregator: ComplianceAggregator):
        rules = aggregator.get_summary().rule_breakdown

        assert rules["COMP-01"].unique_works_count == 57342
        assert rules["COMP-01"].percentage_of_all_works == 70.50
        assert rules["COMP-01"].classification == RuleClassification.POLICY_PROXY

        assert rules["COMP-02"].unique_works_count == 30275
        assert rules["COMP-02"].percentage_of_all_works == 37.22
        assert rules["COMP-02"].classification == RuleClassification.POLICY_TRIGGER

        assert rules["COMP-03"].unique_works_count == 16091
        assert rules["COMP-03"].percentage_of_all_works == 19.78
        assert rules["COMP-03"].classification == RuleClassification.POLICY_TRIGGER

        assert rules["COMP-04"].unique_works_count == 81
        assert rules["COMP-04"].percentage_of_all_works == 0.10
        assert rules["COMP-04"].classification == RuleClassification.POLICY_RECONCILIATION

        assert rules["MON-01"].unique_works_count == 3510
        assert rules["MON-01"].percentage_of_all_works == 4.32
        assert rules["MON-01"].classification == RuleClassification.OFFICIAL_MONITORING

        assert rules["RISK-01"].unique_works_count == 10969
        assert rules["RISK-01"].percentage_of_all_works == 13.49
        assert rules["RISK-01"].classification == RuleClassification.EXECUTION_HEURISTIC

        assert rules["RISK-02"].unique_works_count == 3256
        assert rules["RISK-02"].percentage_of_all_works == 4.00
        assert rules["RISK-02"].classification == RuleClassification.EXECUTION_HEURISTIC

        assert rules["RISK-03"].unique_works_count == 1200
        assert rules["RISK-03"].percentage_of_all_works == 1.48
        assert rules["RISK-03"].classification == RuleClassification.EXECUTION_HEURISTIC

    def test_queue_filtering_and_pagination(self, aggregator: ComplianceAggregator):
        # Default queue
        q_default = aggregator.get_queue(page=1, limit=10)
        assert q_default["total_items"] == 71098
        assert len(q_default["items"]) == 10
        assert q_default["total_pages"] == 7110

        # Filter by rule_id: COMP-04 (81 works)
        q_comp04 = aggregator.get_queue(rule_id="COMP-04", page=1, limit=50)
        assert q_comp04["total_items"] == 81
        assert len(q_comp04["items"]) == 50
        assert q_comp04["total_pages"] == 2

        # Filter by state
        q_state = aggregator.get_queue(state="Sikkim", page=1, limit=20)
        assert q_state["total_items"] > 0
        for item in q_state["items"]:
            assert item.state.lower() == "sikkim"

        # Filter by category
        q_cat = aggregator.get_queue(work_category="Trust and Society", page=1, limit=20)
        assert q_cat["total_items"] > 0
        for item in q_cat["items"]:
            assert item.work_category == "Trust and Society"

    def test_work_compliance_lookup(self, aggregator: ComplianceAggregator):
        # Verify Sikkim benchmark work lookup
        sikkim_id = "WS/MP013/2024-2025/151021"
        rec = aggregator.get_work(sikkim_id)
        assert rec is not None
        assert rec.work_id == sikkim_id
        assert len(rec.evaluations) == 8

        # Nonexistent work lookup
        assert aggregator.get_work("INVALID/WORK/ID") is None

    def test_policy_registry_metadata_integrity(self):
        reg = POLICY_REGISTRY
        assert reg.base_document.document_id == "MPLADS-2023-BASE"
        assert reg.base_document.source_document_url == "https://mplads.gov.in/MPLADS/guidelines/MPLADSGuidelines2023_English_.pdf"
        assert reg.base_document.portal_url == "https://mplads.mospi.gov.in/"
        assert len(reg.provisions) == 6
        assert len(reg.official_monitoring_benchmarks) == 1

        # Check Para 6.2.6.2 text
        prov_trust = next(p for p in reg.provisions if p.clause_reference == "Para 6.2.6.2")
        assert "Up to 10% of total authorization in a financial year" in prov_trust.mandate_summary
        assert "₹1 crore for any particular entity" in prov_trust.mandate_summary

        # Check official monitoring meta
        mon_meta = reg.official_monitoring_benchmarks[0]
        assert mon_meta.authority_type == AuthorityType.OFFICIAL_MONITORING
        assert mon_meta.source_url == "https://mplads.mospi.gov.in/"
        assert "no payments have been made three months" in mon_meta.description
