"""MPLADS Sentinel 2.0 Compliance & Execution Risk Intelligence Engine v1.

Evaluates guideline-derived review triggers, official monitoring benchmarks,
and execution pipeline health heuristics across MPLADS works.
"""

from .models import (
    AuthorityType,
    ComplianceRuleId,
    ComplianceSummary,
    RuleClassification,
    RuleEvaluation,
    WorkComplianceRecord,
)
from .registry import POLICY_REGISTRY, get_rule_meta
from .rules import evaluate_work
from .aggregator import ComplianceAggregator

__all__ = [
    "AuthorityType",
    "ComplianceRuleId",
    "ComplianceSummary",
    "RuleClassification",
    "RuleEvaluation",
    "WorkComplianceRecord",
    "POLICY_REGISTRY",
    "get_rule_meta",
    "evaluate_work",
    "ComplianceAggregator",
]
