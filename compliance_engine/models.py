"""Data models for Compliance & Execution Risk Intelligence v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- Rule Bitmask
RULE_BIT_COMP_01: int = 1 << 0  # 1
RULE_BIT_COMP_02: int = 1 << 1  # 2
RULE_BIT_COMP_03: int = 1 << 2  # 4
RULE_BIT_COMP_04: int = 1 << 3  # 8
RULE_BIT_MON_01: int = 1 << 4   # 16
RULE_BIT_RISK_01: int = 1 << 5  # 32
RULE_BIT_RISK_02: int = 1 << 6  # 64
RULE_BIT_RISK_03: int = 1 << 7  # 128

RULE_BIT_MAP: list[tuple[int, str]] = [
    (1 << 0, "COMP-01"),
    (1 << 1, "COMP-02"),
    (1 << 2, "COMP-03"),
    (1 << 3, "COMP-04"),
    (1 << 4, "MON-01"),
    (1 << 5, "RISK-01"),
    (1 << 6, "RISK-02"),
    (1 << 7, "RISK-03"),
]

RULE_ID_TO_BIT: dict[str, int] = {
    "COMP-01": 1 << 0,
    "COMP-02": 1 << 1,
    "COMP-03": 1 << 2,
    "COMP-04": 1 << 3,
    "MON-01": 1 << 4,
    "RISK-01": 1 << 5,
    "RISK-02": 1 << 6,
    "RISK-03": 1 << 7,
}

POLICY_RULES_MASK: int = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)  # 15
RECONCILIATION_RULES_MASK: int = 1 << 3                             # 8
EXECUTION_RULES_MASK: int = (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7) # 240


def mask_to_rule_ids(mask: int) -> list[str]:
    """Decodes an 8-bit integer into an ordered list of triggered rule IDs."""
    return [r_id for bit, r_id in RULE_BIT_MAP if mask & bit]


def rule_ids_to_mask(rule_ids: list[str]) -> int:
    """Encodes a list of rule IDs into an 8-bit integer bitmask."""
    mask = 0
    for r in rule_ids:
        mask |= RULE_ID_TO_BIT.get(r, 0)
    return mask


@dataclass(slots=True)
class CompactWorkRecord:
    """Memory-compact slotted representation of a work's compliance attributes."""

    work_id: str
    sanction_amount: float
    disbursed_amount: float
    state: str
    constituency: str
    work_category: str
    work_status: str
    triggered_mask: int
    rec_date: Optional[str]
    san_date: Optional[str]
    comp_date: Optional[str]
    last_pay_date: Optional[str]
    is_completed: bool

    @property
    def triggered_rule_ids(self) -> list[str]:
        return mask_to_rule_ids(self.triggered_mask)

    @property
    def is_flagged(self) -> bool:
        return self.triggered_mask > 0

    @property
    def utilization_ratio(self) -> float:
        if self.sanction_amount > 0:
            return round(self.disbursed_amount / self.sanction_amount, 4)
        return 0.0


class AuthorityType(str, Enum):
    """Authority basis for a rule or provision."""

    GUIDELINE_PROVISION = "GUIDELINE_PROVISION"
    OFFICIAL_MONITORING = "OFFICIAL_MONITORING"
    OPERATIONAL_PROXY = "OPERATIONAL_PROXY"
    HEURISTIC = "HEURISTIC"


class RuleClassification(str, Enum):
    """Classification category for UI display and filtering."""

    POLICY_PROXY = "POLICY-DERIVED PROXY"
    POLICY_TRIGGER = "POLICY-DERIVED REVIEW TRIGGER"
    POLICY_RECONCILIATION = "POLICY-DERIVED RECONCILIATION CHECK"
    OFFICIAL_MONITORING = "OFFICIAL MONITORING BENCHMARK"
    EXECUTION_HEURISTIC = "EXECUTION HEURISTIC"


class ComplianceRuleId(str, Enum):
    """The 8 authoritative v1 rule identifiers."""

    COMP_01 = "COMP-01"
    COMP_02 = "COMP-02"
    COMP_03 = "COMP-03"
    COMP_04 = "COMP-04"
    MON_01 = "MON-01"
    RISK_01 = "RISK-01"
    RISK_02 = "RISK-02"
    RISK_03 = "RISK-03"


class RuleEvaluation(BaseModel):
    """Evaluation result of a single rule on a specific work."""

    rule_id: str
    rule_name: str
    authority_type: AuthorityType
    classification: RuleClassification
    source_reference: str
    is_triggered: bool
    observed_value: str
    threshold: str
    reason: str
    limitation: str
    recommended_action: str


class WorkComplianceRecord(BaseModel):
    """Full compliance & execution risk profile for a single work."""

    work_id: str
    work_description: str = ""
    state: str = ""
    constituency: str = ""
    ida: str = ""
    mp_name: str = ""
    work_category: str = ""
    work_status: str = ""
    is_completed: bool = False
    sanction_amount: float = 0.0
    deduplicated_disbursed_amount: float = 0.0
    utilization_ratio: float = 0.0
    recommended_date: Optional[str] = None
    sanction_date: Optional[str] = None
    completion_date: Optional[str] = None
    last_expenditure_date: Optional[str] = None
    days_since_sanction: Optional[int] = None
    days_since_last_payment: Optional[int] = None
    triggered_rule_ids: list[str] = Field(default_factory=list)
    evaluations: list[RuleEvaluation] = Field(default_factory=list)


class RuleSummaryStat(BaseModel):
    """Aggregated statistics for a specific rule."""

    rule_id: str
    rule_name: str
    authority_type: AuthorityType
    classification: RuleClassification
    source_reference: str
    threshold: str
    unique_works_count: int
    percentage_of_all_works: float
    sanctioned_outlay_cr: float
    disbursed_outlay_cr: float = 0.0
    description: str = ""
    limitation: str = ""
    recommended_action: str = ""


class UniqueCounts(BaseModel):
    """Deduplicated unique affected work counts."""

    total_unique_flagged_works: int
    unique_policy_affected_works: int
    policy_derived_reconciliation_works: int
    unique_execution_affected_works: int
    unflagged_baseline_works: int
    total_works_evaluated: int


class TriggerDensity(BaseModel):
    """Total trigger occurrences across all works."""

    total_trigger_instances: int
    average_triggers_per_flagged_work: float


class FinancialReconciliationOutlay(BaseModel):
    """Reconciled financial outlays in Crores across cohorts."""

    total_sanctioned_outlay_all_works_cr: float
    total_deduplicated_disbursed_outlay_all_works_cr: float
    flagged_works_sanctioned_outlay_cr: float
    flagged_works_deduplicated_disbursed_outlay_cr: float
    unflagged_works_sanctioned_outlay_cr: float
    unflagged_works_deduplicated_disbursed_outlay_cr: float
    policy_affected_sanctioned_outlay_cr: float
    policy_affected_deduplicated_disbursed_outlay_cr: float
    execution_affected_sanctioned_outlay_cr: float
    execution_affected_deduplicated_disbursed_outlay_cr: float
    post_completion_disbursed_outlay_cr: float


class ComplianceSummary(BaseModel):
    """Global summary of Compliance & Execution Risk Intelligence."""

    snapshot_date: str
    policy_baseline: str
    total_works_evaluated: int
    unique_counts: UniqueCounts
    trigger_density: TriggerDensity
    financial_outlay: FinancialReconciliationOutlay
    rule_breakdown: dict[str, RuleSummaryStat]


class PolicyDocumentMeta(BaseModel):
    """Metadata describing a governing policy document."""

    document_id: str
    title: str
    short_title: str
    version: str
    effective_date: str
    authority: str
    source_document_url: Optional[str] = None
    portal_url: Optional[str] = None
    description: str


class PolicyProvisionMeta(BaseModel):
    """Metadata describing an individual clause or guideline provision."""

    provision_id: str
    document_id: str
    clause_reference: str
    title: str
    authority_type: AuthorityType
    mandate_summary: str
    affected_rule_ids: list[str]


class OfficialMonitoringMeta(BaseModel):
    """Metadata describing an official administrative monitoring benchmark."""

    monitoring_id: str
    benchmark_name: str
    authority: str
    authority_type: AuthorityType
    source_url: str
    description: str
    affected_rule_ids: list[str]


class PolicyRegistryMeta(BaseModel):
    """Complete registry of governing guidelines, provisions, and monitoring benchmarks."""

    base_document: PolicyDocumentMeta
    provisions: list[PolicyProvisionMeta]
    official_monitoring_benchmarks: list[OfficialMonitoringMeta]
    deferred_rules_notes: dict[str, str] = Field(default_factory=dict)
