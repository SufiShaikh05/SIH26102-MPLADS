"""Unit tests for deterministic compliance and execution risk rule boundary conditions."""

import datetime
import pytest

from compliance_engine.models import (
    AuthorityType,
    CompactWorkRecord,
    POLICY_RULES_MASK,
    RECONCILIATION_RULES_MASK,
    EXECUTION_RULES_MASK,
    RULE_BIT_COMP_01,
    RULE_BIT_COMP_02,
    RULE_BIT_COMP_03,
    RULE_BIT_COMP_04,
    RULE_BIT_MON_01,
    RULE_BIT_RISK_01,
    RULE_BIT_RISK_02,
    RULE_BIT_RISK_03,
    RULE_BIT_MAP,
    RULE_ID_TO_BIT,
    RuleClassification,
    mask_to_rule_ids,
    rule_ids_to_mask,
)
from compliance_engine.rules import (
    DEFAULT_SNAPSHOT_DATE,
    compute_work_bitmask,
    eval_comp_01,
    eval_comp_02,
    eval_comp_03,
    eval_comp_04,
    eval_mon_01,
    eval_risk_01,
    eval_risk_02,
    eval_risk_03,
    evaluate_work,
)


class TestRuleBoundaryConditions:
    """Rigorous boundary condition testing across all 8 rules."""

    # 1. COMP-01: Recommendation-to-Sanction Latency (>45 Days)
    def test_comp_01_boundary_45_vs_46_days(self):
        rec_date = datetime.date(2025, 1, 1)

        # 45 days exactly -> NOT triggered (is within benchmark)
        sanc_45 = rec_date + datetime.timedelta(days=45)
        res_45 = eval_comp_01(rec_date, sanc_45)
        assert res_45.is_triggered is False
        assert res_45.authority_type == AuthorityType.OPERATIONAL_PROXY
        assert res_45.classification == RuleClassification.POLICY_PROXY
        assert res_45.observed_value == "45 days"

        # 46 days -> TRIGGERED
        sanc_46 = rec_date + datetime.timedelta(days=46)
        res_46 = eval_comp_01(rec_date, sanc_46)
        assert res_46.is_triggered is True
        assert res_46.observed_value == "46 days"

        # Missing dates -> NOT triggered
        res_none = eval_comp_01(None, sanc_46)
        assert res_none.is_triggered is False
        assert res_none.observed_value == "Dates unavailable"

    # 2. COMP-02: Minimum Sanction Amount Benchmark (<₹2.5 Lakh)
    def test_comp_02_boundary_250k_vs_249999(self):
        # Exactly 2,50,000 -> NOT triggered
        res_250k = eval_comp_02(250000.0)
        assert res_250k.is_triggered is False
        assert res_250k.authority_type == AuthorityType.GUIDELINE_PROVISION
        assert res_250k.classification == RuleClassification.POLICY_TRIGGER

        # 2,49,999 -> TRIGGERED
        res_249k = eval_comp_02(249999.0)
        assert res_249k.is_triggered is True

        # String amounts and 0
        res_str = eval_comp_02("100000")
        assert res_str.is_triggered is True

        res_zero = eval_comp_02(0)
        assert res_zero.is_triggered is True

        res_large = eval_comp_02(500000)
        assert res_large.is_triggered is False

    # 3. COMP-03: Completion Timeline (>365 Days / 1 Year)
    def test_comp_03_boundary_365_vs_366_days(self):
        sanc_date = datetime.date(2025, 1, 1)

        # Completed work: 365 days -> NOT triggered
        comp_365 = sanc_date + datetime.timedelta(days=365)
        res_365 = eval_comp_03(sanc_date, comp_365, is_completed=True)
        assert res_365.is_triggered is False

        # Completed work: 366 days -> TRIGGERED
        comp_366 = sanc_date + datetime.timedelta(days=366)
        res_366 = eval_comp_03(sanc_date, comp_366, is_completed=True)
        assert res_366.is_triggered is True
        assert res_366.observed_value == "366 days"

        # Ongoing work evaluated against snapshot date
        as_of = datetime.date(2026, 1, 1)  # exactly 365 days from 2025-01-01 (non-leap: 365 days)
        res_ong_365 = eval_comp_03(sanc_date, None, is_completed=False, as_of_date=as_of)
        assert res_ong_365.is_triggered is False

        as_of_366 = datetime.date(2026, 1, 2)
        res_ong_366 = eval_comp_03(sanc_date, None, is_completed=False, as_of_date=as_of_366)
        assert res_ong_366.is_triggered is True

    # 4. COMP-04: Completed Work with Zero Recorded Outlay
    def test_comp_04_reconciliation_check(self):
        # Completed with 0 outlay -> TRIGGERED
        res_comp_zero = eval_comp_04(is_completed=True, deduplicated_disbursed_amount=0.0)
        assert res_comp_zero.is_triggered is True
        assert res_comp_zero.classification == RuleClassification.POLICY_RECONCILIATION

        # Completed with positive outlay -> NOT triggered
        res_comp_paid = eval_comp_04(is_completed=True, deduplicated_disbursed_amount=50000.0)
        assert res_comp_paid.is_triggered is False

        # Ongoing with 0 outlay -> NOT triggered under COMP-04 (covered by MON-01/RISK-01)
        res_ong_zero = eval_comp_04(is_completed=False, deduplicated_disbursed_amount=0.0)
        assert res_ong_zero.is_triggered is False

    # 5. MON-01: Monitoring-Derived Early Warning (91–180 Days, ₹0 Outlay)
    def test_mon_01_boundary_90_vs_91_and_180_vs_181(self):
        sanc_date = datetime.date(2026, 1, 1)

        # 90 days exactly -> NOT triggered (outside window)
        as_of_90 = sanc_date + datetime.timedelta(days=90)
        res_90 = eval_mon_01(sanc_date, is_completed=False, deduplicated_disbursed_amount=0.0, as_of_date=as_of_90)
        assert res_90.is_triggered is False

        # 91 days -> TRIGGERED (start of early warning window)
        as_of_91 = sanc_date + datetime.timedelta(days=91)
        res_91 = eval_mon_01(sanc_date, is_completed=False, deduplicated_disbursed_amount=0.0, as_of_date=as_of_91)
        assert res_91.is_triggered is True
        assert res_91.authority_type == AuthorityType.OFFICIAL_MONITORING
        assert res_91.classification == RuleClassification.OFFICIAL_MONITORING

        # 180 days -> TRIGGERED (end of early warning window)
        as_of_180 = sanc_date + datetime.timedelta(days=180)
        res_180 = eval_mon_01(sanc_date, is_completed=False, deduplicated_disbursed_amount=0.0, as_of_date=as_of_180)
        assert res_180.is_triggered is True

        # 181 days -> NOT triggered (escalates to RISK-01 prolonged dormancy)
        as_of_181 = sanc_date + datetime.timedelta(days=181)
        res_181 = eval_mon_01(sanc_date, is_completed=False, deduplicated_disbursed_amount=0.0, as_of_date=as_of_181)
        assert res_181.is_triggered is False

        # If payments exist -> NOT triggered
        as_of_100 = sanc_date + datetime.timedelta(days=100)
        res_paid = eval_mon_01(sanc_date, is_completed=False, deduplicated_disbursed_amount=1000.0, as_of_date=as_of_100)
        assert res_paid.is_triggered is False

        # If completed -> NOT triggered
        res_comp = eval_mon_01(sanc_date, is_completed=True, deduplicated_disbursed_amount=0.0, as_of_date=as_of_100)
        assert res_comp.is_triggered is False

    # 6. RISK-01: Prolonged Dormancy (>180 Days, ₹0 Outlay)
    def test_risk_01_boundary_180_vs_181_days(self):
        sanc_date = datetime.date(2025, 1, 1)

        # 180 days -> NOT triggered
        as_of_180 = sanc_date + datetime.timedelta(days=180)
        res_180 = eval_risk_01(sanc_date, is_completed=False, deduplicated_disbursed_amount=0.0, as_of_date=as_of_180)
        assert res_180.is_triggered is False

        # 181 days -> TRIGGERED
        as_of_181 = sanc_date + datetime.timedelta(days=181)
        res_181 = eval_risk_01(sanc_date, is_completed=False, deduplicated_disbursed_amount=0.0, as_of_date=as_of_181)
        assert res_181.is_triggered is True
        assert res_181.authority_type == AuthorityType.HEURISTIC
        assert res_181.classification == RuleClassification.EXECUTION_HEURISTIC

        # With expenditure -> NOT triggered
        res_paid = eval_risk_01(sanc_date, is_completed=False, deduplicated_disbursed_amount=1000.0, as_of_date=as_of_181)
        assert res_paid.is_triggered is False

    # 7. RISK-02: Stalled Disbursement (>180 Days Inactive, Partial Progress < 80%)
    def test_risk_02_boundary_days_and_utilization(self):
        sanc_amt = 1000000.0  # 10 Lakh
        last_pay = datetime.date(2025, 1, 1)

        # Case 1: Inactive 180 days, util = 50% (< 80%) -> NOT triggered (boundary)
        as_of_180 = last_pay + datetime.timedelta(days=180)
        res_180 = eval_risk_02(
            is_completed=False,
            deduplicated_disbursed_amount=500000.0,
            sanction_amount=sanc_amt,
            last_expenditure_date=last_pay,
            as_of_date=as_of_180,
        )
        assert res_180.is_triggered is False

        # Case 2: Inactive 181 days, util = 50% (< 80%) -> TRIGGERED
        as_of_181 = last_pay + datetime.timedelta(days=181)
        res_181 = eval_risk_02(
            is_completed=False,
            deduplicated_disbursed_amount=500000.0,
            sanction_amount=sanc_amt,
            last_expenditure_date=last_pay,
            as_of_date=as_of_181,
        )
        assert res_181.is_triggered is True

        # Case 3: Inactive 181 days, but util = 80.0% -> NOT triggered (>= 80% excluded)
        res_80 = eval_risk_02(
            is_completed=False,
            deduplicated_disbursed_amount=800000.0,
            sanction_amount=sanc_amt,
            last_expenditure_date=last_pay,
            as_of_date=as_of_181,
        )
        assert res_80.is_triggered is False

        # Case 4: Work completed -> NOT triggered
        res_comp = eval_risk_02(
            is_completed=True,
            deduplicated_disbursed_amount=500000.0,
            sanction_amount=sanc_amt,
            last_expenditure_date=last_pay,
            as_of_date=as_of_181,
        )
        assert res_comp.is_triggered is False

    # 8. RISK-03: Post-Completion Disbursement (>30 Days)
    def test_risk_03_boundary_post_completion(self):
        comp_date = datetime.date(2025, 1, 1)

        # Has post-completion tx -> TRIGGERED
        res_post = eval_risk_03(
            completion_date=comp_date,
            has_post_completion_tx=True,
            post_completion_tx_count=2,
            post_completion_amount=150000.0,
            max_days_post_completion=75,
        )
        assert res_post.is_triggered is True
        assert res_post.observed_value == "2 tx(s), ₹150,000.00 disbursed (up to 75d post-completion)"

        # No post-completion tx -> NOT triggered
        res_no_post = eval_risk_03(
            completion_date=comp_date,
            has_post_completion_tx=False,
            post_completion_tx_count=0,
            post_completion_amount=0.0,
        )
        assert res_no_post.is_triggered is False

    # 9. Full Orchestration and Non-Punitive Phrasing
    def test_evaluate_work_returns_exactly_8_rules_with_neutral_text(self):
        evals = evaluate_work(
            work_id="TEST/001",
            recommended_date="2025-01-01",
            sanction_date="2025-03-01",  # 59 days -> COMP-01
            completion_date=None,
            sanction_amount=200000.0,     # < 2.5L -> COMP-02
            is_completed=False,
            deduplicated_disbursed_amount=0.0,
            last_expenditure_date=None,
            as_of_date=datetime.date(2025, 4, 15),  # 45 days after sanction
        )
        assert len(evals) == 8
        rule_ids = [e.rule_id for e in evals]
        assert rule_ids == ["COMP-01", "COMP-02", "COMP-03", "COMP-04", "MON-01", "RISK-01", "RISK-02", "RISK-03"]

        triggered = [e.rule_id for e in evals if e.is_triggered]
        assert "COMP-01" in triggered
        assert "COMP-02" in triggered

        # Verify no prohibited terminology in reasons, limitations, or actions
        forbidden = ["fraud", "legal violation", "statutory breach", "guilty", "fraud probability", "statutory"]
        for e in evals:
            combined_text = f"{e.reason} {e.limitation} {e.recommended_action}".lower()
            for term in forbidden:
                assert term not in combined_text, f"Prohibited term '{term}' found in rule {e.rule_id}"


class TestBitmaskRepresentation:
    """Verifies that 8-bit integer representation matches full evaluation logic identically."""

    def test_bitmask_constants_and_mapping(self):
        assert RULE_BIT_COMP_01 == 1 << 0
        assert RULE_BIT_COMP_02 == 1 << 1
        assert RULE_BIT_COMP_03 == 1 << 2
        assert RULE_BIT_COMP_04 == 1 << 3
        assert RULE_BIT_MON_01 == 1 << 4
        assert RULE_BIT_RISK_01 == 1 << 5
        assert RULE_BIT_RISK_02 == 1 << 6
        assert RULE_BIT_RISK_03 == 1 << 7

        assert POLICY_RULES_MASK == 0b00001111  # bits 0..3 (COMP-01..04)
        assert RECONCILIATION_RULES_MASK == 0b00001000  # bit 3 (COMP-04)
        assert EXECUTION_RULES_MASK == 0b11110000  # bits 4..7 (MON-01, RISK-01..03)

        assert len(RULE_BIT_MAP) == 8
        assert len(RULE_ID_TO_BIT) == 8

    def test_mask_to_rule_ids_and_reverse(self):
        # Empty mask
        assert mask_to_rule_ids(0) == []
        assert rule_ids_to_mask([]) == 0

        # Single rule
        assert mask_to_rule_ids(RULE_BIT_COMP_01) == ["COMP-01"]
        assert rule_ids_to_mask(["COMP-01"]) == RULE_BIT_COMP_01

        # Multiple rules
        mask = RULE_BIT_COMP_01 | RULE_BIT_COMP_04 | RULE_BIT_RISK_03
        expected = ["COMP-01", "COMP-04", "RISK-03"]
        assert mask_to_rule_ids(mask) == expected
        assert rule_ids_to_mask(expected) == mask

        # All 8 rules
        all_mask = 0xFF
        assert len(mask_to_rule_ids(all_mask)) == 8

    def test_bitmask_equivalence_with_evaluate_work(self):
        """Ensures compute_work_bitmask produces the EXACT same set of triggered rules as evaluate_work."""
        test_cases = [
            # Case 1: COMP-01 + COMP-02
            dict(
                recommended_date="2025-01-01",
                sanction_date="2025-03-01",  # 59 days -> COMP-01
                completion_date=None,
                sanction_amount=200000.0,    # < 2.5L -> COMP-02
                is_completed=False,
                deduplicated_disbursed_amount=0.0,
                last_expenditure_date=None,
                has_post_completion_tx=False,
                post_completion_tx_count=0,
                as_of_date=datetime.date(2025, 4, 15),
            ),
            # Case 2: COMP-04 (Completed work with 0 disbursement)
            dict(
                recommended_date="2025-01-01",
                sanction_date="2025-01-10",
                completion_date="2025-06-01",
                sanction_amount=500000.0,
                is_completed=True,
                deduplicated_disbursed_amount=0.0,
                last_expenditure_date=None,
                has_post_completion_tx=False,
                post_completion_tx_count=0,
                as_of_date=datetime.date(2025, 7, 1),
            ),
            # Case 3: MON-01 + RISK-01 (No payment post-sanction)
            dict(
                recommended_date="2025-01-01",
                sanction_date="2025-01-10",
                completion_date=None,
                sanction_amount=500000.0,
                is_completed=False,
                deduplicated_disbursed_amount=0.0,
                last_expenditure_date=None,
                has_post_completion_tx=False,
                post_completion_tx_count=0,
                as_of_date=datetime.date(2025, 9, 1),  # 234 days post-sanction with 0 payments
            ),
            # Case 4: RISK-03 (Post completion tx)
            dict(
                recommended_date="2025-01-01",
                sanction_date="2025-01-10",
                completion_date="2025-03-01",
                sanction_amount=500000.0,
                is_completed=True,
                deduplicated_disbursed_amount=450000.0,
                last_expenditure_date="2025-05-01",
                has_post_completion_tx=True,
                post_completion_tx_count=3,
                as_of_date=datetime.date(2025, 6, 1),
            ),
            # Case 5: Unflagged work (all within benchmarks)
            dict(
                recommended_date="2025-01-01",
                sanction_date="2025-01-20",  # 19 days -> ok
                completion_date="2025-05-01",
                sanction_amount=1000000.0,   # 10L -> ok
                is_completed=True,
                deduplicated_disbursed_amount=950000.0,
                last_expenditure_date="2025-04-15",
                has_post_completion_tx=False,
                post_completion_tx_count=0,
                as_of_date=datetime.date(2025, 6, 1),
            ),
        ]

        for idx, kwargs in enumerate(test_cases):
            # Compute bitmask
            mask = compute_work_bitmask(**kwargs)
            mask_rules = set(mask_to_rule_ids(mask))

            # Full evaluations
            evals = evaluate_work(work_id=f"TEST_{idx}", **kwargs)
            eval_rules = {e.rule_id for e in evals if e.is_triggered}

            assert mask_rules == eval_rules, f"Mismatch in case {idx}: {mask_rules} vs {eval_rules}"

    def test_compact_work_record_slots_and_memory(self):
        """Verifies CompactWorkRecord uses slots without __dict__ overhead."""
        rec = CompactWorkRecord(
            work_id="WS/TEST/2025-2026/001",
            sanction_amount=500000.0,
            disbursed_amount=250000.0,
            state="Maharashtra",
            constituency="Pune",
            work_category="Roads and Bridges",
            work_status="Work in Progress",
            triggered_mask=RULE_BIT_COMP_01 | RULE_BIT_RISK_02,
            rec_date="2025-01-01",
            san_date="2025-03-01",
            comp_date=None,
            last_pay_date="2025-04-01",
            is_completed=False,
        )

        assert not hasattr(rec, "__dict__"), "CompactWorkRecord should use slots without __dict__"
        assert rec.triggered_rule_ids == ["COMP-01", "RISK-02"]
        assert rec.utilization_ratio == 0.5
        assert rec.is_flagged is True
