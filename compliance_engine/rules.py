"""Deterministic rule evaluators for Compliance & Execution Risk Intelligence v1."""

from __future__ import annotations

import datetime
from typing import Optional, Union

from .models import (
    AuthorityType,
    ComplianceRuleId,
    RuleClassification,
    RuleEvaluation,
)
from .registry import get_rule_meta

DEFAULT_SNAPSHOT_DATE = datetime.date(2026, 9, 25)


def parse_date(val: Optional[Union[str, datetime.date, datetime.datetime]]) -> Optional[datetime.date]:
    """Resilient date parsing for ISO or common date formats."""
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "nat", ""):
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def eval_comp_01(
    recommended_date: Optional[Union[str, datetime.date]],
    sanction_date: Optional[Union[str, datetime.date]],
) -> RuleEvaluation:
    """Para 3.2.4: Recommendation-to-Sanction Latency (>45 Days)."""
    meta = get_rule_meta("COMP-01")
    d_rec = parse_date(recommended_date)
    d_san = parse_date(sanction_date)

    if d_rec is None or d_san is None:
        return RuleEvaluation(
            rule_id="COMP-01",
            rule_name=meta["rule_name"],
            authority_type=AuthorityType.OPERATIONAL_PROXY,
            classification=RuleClassification.POLICY_PROXY,
            source_reference=meta["source_reference"],
            is_triggered=False,
            observed_value="Dates unavailable",
            threshold=meta["threshold"],
            reason="Recommendation or sanction date is missing; latency check cannot be computed.",
            limitation=meta["limitation"],
            recommended_action=meta["recommended_action"],
        )

    latency_days = (d_san - d_rec).days
    is_triggered = latency_days > 45

    if is_triggered:
        reason = f"Recommendation-to-sanction latency of {latency_days} days exceeds the 45-day guideline benchmark."
        action = f"Review district processing file for recommendation received on {d_rec.isoformat()} and sanctioned on {d_san.isoformat()} ({latency_days} days total)."
    else:
        reason = f"Recommendation-to-sanction latency of {latency_days} days is within the 45-day guideline benchmark."
        action = "No action required; sanction issued within normal guideline timeframe."

    return RuleEvaluation(
        rule_id="COMP-01",
        rule_name=meta["rule_name"],
        authority_type=AuthorityType.OPERATIONAL_PROXY,
        classification=RuleClassification.POLICY_PROXY,
        source_reference=meta["source_reference"],
        is_triggered=is_triggered,
        observed_value=f"{latency_days} days",
        threshold=meta["threshold"],
        reason=reason,
        limitation=meta["limitation"],
        recommended_action=action,
    )


def eval_comp_02(sanction_amount: Optional[Union[float, int, str]]) -> RuleEvaluation:
    """Para 3.2.9: Minimum Sanction Amount Benchmark (<₹2.5 Lakh)."""
    meta = get_rule_meta("COMP-02")
    try:
        amt = float(sanction_amount or 0.0)
    except (ValueError, TypeError):
        amt = 0.0

    is_triggered = amt < 250000.0

    if is_triggered:
        reason = f"Sanction amount of ₹{amt:,.2f} is below the normal ₹2.5 Lakh benchmark."
        action = meta["recommended_action"]
    else:
        reason = f"Sanction amount of ₹{amt:,.2f} meets or exceeds the normal ₹2.5 Lakh benchmark."
        action = "No action required; work meets the normal sanction size benchmark."

    return RuleEvaluation(
        rule_id="COMP-02",
        rule_name=meta["rule_name"],
        authority_type=AuthorityType.GUIDELINE_PROVISION,
        classification=RuleClassification.POLICY_TRIGGER,
        source_reference=meta["source_reference"],
        is_triggered=is_triggered,
        observed_value=f"₹{amt:,.2f}",
        threshold=meta["threshold"],
        reason=reason,
        limitation=meta["limitation"],
        recommended_action=action,
    )


def eval_comp_03(
    sanction_date: Optional[Union[str, datetime.date]],
    completion_date: Optional[Union[str, datetime.date]],
    is_completed: bool,
    as_of_date: Optional[Union[str, datetime.date]] = None,
) -> RuleEvaluation:
    """Para 3.2.12: Completion Timeline (>365 Days / 1 Year)."""
    meta = get_rule_meta("COMP-03")
    d_san = parse_date(sanction_date)
    d_comp = parse_date(completion_date)
    d_as_of = parse_date(as_of_date) or DEFAULT_SNAPSHOT_DATE

    if d_san is None:
        return RuleEvaluation(
            rule_id="COMP-03",
            rule_name=meta["rule_name"],
            authority_type=AuthorityType.GUIDELINE_PROVISION,
            classification=RuleClassification.POLICY_TRIGGER,
            source_reference=meta["source_reference"],
            is_triggered=False,
            observed_value="Sanction date unavailable",
            threshold=meta["threshold"],
            reason="Sanction date is missing; duration cannot be computed.",
            limitation=meta["limitation"],
            recommended_action=meta["recommended_action"],
        )

    if is_completed:
        if d_comp is None:
            # Completed status but missing completion date
            days = (d_as_of - d_san).days
        else:
            days = (d_comp - d_san).days
        is_triggered = days > 365
        scope_str = "Completed work execution duration"
    else:
        days = (d_as_of - d_san).days
        is_triggered = days > 365
        scope_str = "Ongoing work elapsed duration"

    if is_triggered:
        reason = f"{scope_str} of {days} days exceeds the 1-year guideline timeline benchmark."
        action = meta["recommended_action"]
    else:
        reason = f"{scope_str} of {days} days is within the 1-year timeline benchmark."
        action = "No action required; execution timeline conforms to normal 1-year duration."

    return RuleEvaluation(
        rule_id="COMP-03",
        rule_name=meta["rule_name"],
        authority_type=AuthorityType.GUIDELINE_PROVISION,
        classification=RuleClassification.POLICY_TRIGGER,
        source_reference=meta["source_reference"],
        is_triggered=is_triggered,
        observed_value=f"{days} days",
        threshold=meta["threshold"],
        reason=reason,
        limitation=meta["limitation"],
        recommended_action=action,
    )


def eval_comp_04(
    is_completed: bool,
    deduplicated_disbursed_amount: Optional[Union[float, int, str]],
) -> RuleEvaluation:
    """Chapter 11: Completed Work with Zero Recorded Outlay."""
    meta = get_rule_meta("COMP-04")
    try:
        disbursed = float(deduplicated_disbursed_amount or 0.0)
    except (ValueError, TypeError):
        disbursed = 0.0

    is_triggered = is_completed and disbursed <= 0.0

    if is_triggered:
        reason = "Work is recorded as completed in portal registers but has zero recorded disbursements in the financial ledger."
        action = meta["recommended_action"]
    elif is_completed:
        reason = f"Completed work has recorded disbursements of ₹{disbursed:,.2f}."
        action = "No action required; financial ledger contains recorded disbursements."
    else:
        reason = "Work is ongoing; zero outlay is evaluated under execution pipeline rules rather than completion reconciliation."
        action = "Not applicable to ongoing works."

    return RuleEvaluation(
        rule_id="COMP-04",
        rule_name=meta["rule_name"],
        authority_type=AuthorityType.GUIDELINE_PROVISION,
        classification=RuleClassification.POLICY_RECONCILIATION,
        source_reference=meta["source_reference"],
        is_triggered=is_triggered,
        observed_value=f"{'Completed' if is_completed else 'Ongoing'}, ₹{disbursed:,.2f} outlay",
        threshold=meta["threshold"],
        reason=reason,
        limitation=meta["limitation"],
        recommended_action=action,
    )


def eval_mon_01(
    sanction_date: Optional[Union[str, datetime.date]],
    is_completed: bool,
    deduplicated_disbursed_amount: Optional[Union[float, int, str]],
    as_of_date: Optional[Union[str, datetime.date]] = None,
) -> RuleEvaluation:
    """Official Monitoring Benchmark: No Recorded Payment 90–180 Days Post-Sanction."""
    meta = get_rule_meta("MON-01")
    d_san = parse_date(sanction_date)
    d_as_of = parse_date(as_of_date) or DEFAULT_SNAPSHOT_DATE

    try:
        disbursed = float(deduplicated_disbursed_amount or 0.0)
    except (ValueError, TypeError):
        disbursed = 0.0

    if is_completed or d_san is None or disbursed > 0.0:
        is_triggered = False
        days = (d_as_of - d_san).days if d_san else None
        reason = "Work does not meet the 90–180 day zero-payment monitoring criteria."
        action = "No action required."
    else:
        days = (d_as_of - d_san).days
        is_triggered = 90 < days <= 180
        if is_triggered:
            reason = f"Work has remained without recorded disbursements for {days} days post-sanction (falls within the 91–180 day early-warning review window)."
            action = meta["recommended_action"]
        else:
            reason = f"Sanction elapsed time of {days} days is outside the 91–180 day monitoring window."
            action = "No action required under early-warning monitoring."

    obs_val = f"{days} days since sanction, ₹{disbursed:,.2f} outlay" if days is not None else "Sanction date unavailable"

    return RuleEvaluation(
        rule_id="MON-01",
        rule_name=meta["rule_name"],
        authority_type=AuthorityType.OFFICIAL_MONITORING,
        classification=RuleClassification.OFFICIAL_MONITORING,
        source_reference=meta["source_reference"],
        is_triggered=is_triggered,
        observed_value=obs_val,
        threshold=meta["threshold"],
        reason=reason,
        limitation=meta["limitation"],
        recommended_action=action,
    )


def eval_risk_01(
    sanction_date: Optional[Union[str, datetime.date]],
    is_completed: bool,
    deduplicated_disbursed_amount: Optional[Union[float, int, str]],
    as_of_date: Optional[Union[str, datetime.date]] = None,
) -> RuleEvaluation:
    """Execution Pipeline Alert: Prolonged Dormancy (>180 Days, ₹0 Outlay)."""
    meta = get_rule_meta("RISK-01")
    d_san = parse_date(sanction_date)
    d_as_of = parse_date(as_of_date) or DEFAULT_SNAPSHOT_DATE

    try:
        disbursed = float(deduplicated_disbursed_amount or 0.0)
    except (ValueError, TypeError):
        disbursed = 0.0

    if is_completed or d_san is None or disbursed > 0.0:
        is_triggered = False
        days = (d_as_of - d_san).days if d_san else None
        reason = "Work is completed or has active disbursements; dormancy check not triggered."
        action = "No action required."
    else:
        days = (d_as_of - d_san).days
        is_triggered = days > 180
        if is_triggered:
            reason = f"Sanctioned capital has remained completely dormant with zero disbursements for {days} days (>6 months)."
            action = meta["recommended_action"]
        else:
            reason = f"Sanction elapsed time of {days} days is within the initial 180-day mobilization window."
            action = "No action required under prolonged dormancy."

    obs_val = f"{days} days dormant, ₹{disbursed:,.2f} outlay" if days is not None else "Sanction date unavailable"

    return RuleEvaluation(
        rule_id="RISK-01",
        rule_name=meta["rule_name"],
        authority_type=AuthorityType.HEURISTIC,
        classification=RuleClassification.EXECUTION_HEURISTIC,
        source_reference=meta["source_reference"],
        is_triggered=is_triggered,
        observed_value=obs_val,
        threshold=meta["threshold"],
        reason=reason,
        limitation=meta["limitation"],
        recommended_action=action,
    )


def eval_risk_02(
    is_completed: bool,
    deduplicated_disbursed_amount: Optional[Union[float, int, str]],
    sanction_amount: Optional[Union[float, int, str]],
    last_expenditure_date: Optional[Union[str, datetime.date]],
    as_of_date: Optional[Union[str, datetime.date]] = None,
) -> RuleEvaluation:
    """Execution Pipeline Alert: Stalled Disbursement (>180 Days Inactive on Partial Progress)."""
    meta = get_rule_meta("RISK-02")
    d_last = parse_date(last_expenditure_date)
    d_as_of = parse_date(as_of_date) or DEFAULT_SNAPSHOT_DATE

    try:
        disbursed = float(deduplicated_disbursed_amount or 0.0)
    except (ValueError, TypeError):
        disbursed = 0.0

    try:
        s_amt = float(sanction_amount or 0.0)
    except (ValueError, TypeError):
        s_amt = 0.0

    util_ratio = (disbursed / s_amt) if s_amt > 0.0 else 0.0

    if is_completed or disbursed <= 0.0 or util_ratio >= 0.80 or d_last is None:
        is_triggered = False
        days = (d_as_of - d_last).days if d_last else None
        reason = "Work is completed, has zero outlay, meets >=80% utilization, or has no payment date; stalled check not triggered."
        action = "No action required."
    else:
        days = (d_as_of - d_last).days
        is_triggered = days > 180
        if is_triggered:
            reason = f"Financial disbursements have halted for {days} days on an incomplete work with partial utilization ({util_ratio * 100:.1f}%)."
            action = meta["recommended_action"]
        else:
            reason = f"Last payment was {days} days ago (within 180-day active window)."
            action = "No action required."

    obs_val = f"{days} days since last payment, {util_ratio * 100:.1f}% disbursed" if days is not None else "No payment date recorded"

    return RuleEvaluation(
        rule_id="RISK-02",
        rule_name=meta["rule_name"],
        authority_type=AuthorityType.HEURISTIC,
        classification=RuleClassification.EXECUTION_HEURISTIC,
        source_reference=meta["source_reference"],
        is_triggered=is_triggered,
        observed_value=obs_val,
        threshold=meta["threshold"],
        reason=reason,
        limitation=meta["limitation"],
        recommended_action=action,
    )


def eval_risk_03(
    completion_date: Optional[Union[str, datetime.date]],
    has_post_completion_tx: bool,
    post_completion_tx_count: int = 0,
    post_completion_amount: float = 0.0,
    max_days_post_completion: Optional[int] = None,
) -> RuleEvaluation:
    """Operational Review Heuristic: Post-Completion Disbursement (>30 Days)."""
    meta = get_rule_meta("RISK-03")
    d_comp = parse_date(completion_date)

    is_triggered = bool(has_post_completion_tx and post_completion_tx_count > 0)

    if is_triggered:
        reason = f"{post_completion_tx_count} payment(s) totaling ₹{post_completion_amount:,.2f} were disbursed more than 30 days after recorded completion."
        action = meta["recommended_action"]
        obs_val = f"{post_completion_tx_count} tx(s), ₹{post_completion_amount:,.2f} disbursed (up to {max_days_post_completion}d post-completion)"
    else:
        reason = "No transactions were disbursed more than 30 days after physical work completion."
        action = "No action required; ledger transactions precede completion date or fall within normal 30-day closeout."
        obs_val = "0 post-completion transactions"

    return RuleEvaluation(
        rule_id="RISK-03",
        rule_name=meta["rule_name"],
        authority_type=AuthorityType.HEURISTIC,
        classification=RuleClassification.EXECUTION_HEURISTIC,
        source_reference=meta["source_reference"],
        is_triggered=is_triggered,
        observed_value=obs_val,
        threshold=meta["threshold"],
        reason=reason,
        limitation=meta["limitation"],
        recommended_action=action,
    )


def evaluate_work(
    *,
    work_id: str,
    recommended_date: Optional[Union[str, datetime.date]],
    sanction_date: Optional[Union[str, datetime.date]],
    completion_date: Optional[Union[str, datetime.date]],
    sanction_amount: Optional[Union[float, int, str]],
    is_completed: bool,
    deduplicated_disbursed_amount: Optional[Union[float, int, str]],
    last_expenditure_date: Optional[Union[str, datetime.date]],
    has_post_completion_tx: bool = False,
    post_completion_tx_count: int = 0,
    post_completion_amount: float = 0.0,
    max_days_post_completion: Optional[int] = None,
    as_of_date: Optional[Union[str, datetime.date]] = None,
) -> list[RuleEvaluation]:
    """Orchestrates evaluation of all 8 compliance and execution risk rules for a work."""
    return [
        eval_comp_01(recommended_date, sanction_date),
        eval_comp_02(sanction_amount),
        eval_comp_03(sanction_date, completion_date, is_completed, as_of_date),
        eval_comp_04(is_completed, deduplicated_disbursed_amount),
        eval_mon_01(sanction_date, is_completed, deduplicated_disbursed_amount, as_of_date),
        eval_risk_01(sanction_date, is_completed, deduplicated_disbursed_amount, as_of_date),
        eval_risk_02(is_completed, deduplicated_disbursed_amount, sanction_amount, last_expenditure_date, as_of_date),
        eval_risk_03(completion_date, has_post_completion_tx, post_completion_tx_count, post_completion_amount, max_days_post_completion),
    ]


def compute_work_bitmask(
    *,
    recommended_date: Optional[Union[str, datetime.date]],
    sanction_date: Optional[Union[str, datetime.date]],
    completion_date: Optional[Union[str, datetime.date]],
    sanction_amount: Optional[Union[float, int, str]],
    is_completed: bool,
    deduplicated_disbursed_amount: Optional[Union[float, int, str]],
    last_expenditure_date: Optional[Union[str, datetime.date]],
    has_post_completion_tx: bool = False,
    post_completion_tx_count: int = 0,
    as_of_date: Optional[Union[str, datetime.date]] = None,
) -> int:
    """Computes the 8-bit rule bitmask directly using fast deterministic arithmetic."""
    mask = 0
    d_rec = parse_date(recommended_date)
    d_san = parse_date(sanction_date)
    d_comp = parse_date(completion_date)
    d_last = parse_date(last_expenditure_date)
    d_as_of = parse_date(as_of_date) or DEFAULT_SNAPSHOT_DATE

    try:
        s_amt = float(sanction_amount or 0.0)
    except (ValueError, TypeError):
        s_amt = 0.0

    try:
        d_disb = float(deduplicated_disbursed_amount or 0.0)
    except (ValueError, TypeError):
        d_disb = 0.0

    # COMP-01: Recommendation Latency (>45 Days) [bit 0]
    if d_rec is not None and d_san is not None and (d_san - d_rec).days > 45:
        mask |= (1 << 0)

    # COMP-02: Minimum Sanction Amount Benchmark (<₹2.5 Lakh) [bit 1]
    if s_amt < 250000.0:
        mask |= (1 << 1)

    # COMP-03: Extended Execution Duration (>365 Days) [bit 2]
    if d_san is not None:
        if is_completed:
            days = (d_comp - d_san).days if d_comp else (d_as_of - d_san).days
        else:
            days = (d_as_of - d_san).days
        if days > 365:
            mask |= (1 << 2)

    # COMP-04: Completed Work with Zero Recorded Outlay [bit 3]
    if is_completed and d_disb <= 0.0:
        mask |= (1 << 3)

    # MON-01: Official Monitoring Benchmark (91–180 Days, ₹0 Outlay) [bit 4]
    if (not is_completed) and d_san is not None and d_disb <= 0.0:
        days = (d_as_of - d_san).days
        if 90 < days <= 180:
            mask |= (1 << 4)

    # RISK-01: Prolonged Dormancy (>180 Days, ₹0 Outlay) [bit 5]
    if (not is_completed) and d_san is not None and d_disb <= 0.0:
        days = (d_as_of - d_san).days
        if days > 180:
            mask |= (1 << 5)

    # RISK-02: Stalled Disbursement (>180 Days Inactive) [bit 6]
    if (not is_completed) and d_disb > 0.0 and d_last is not None:
        util_ratio = (d_disb / s_amt) if s_amt > 0.0 else 0.0
        if util_ratio < 0.80:
            days = (d_as_of - d_last).days
            if days > 180:
                mask |= (1 << 6)

    # RISK-03: Post-Completion Disbursement (>30 Days) [bit 7]
    if has_post_completion_tx and post_completion_tx_count > 0:
        mask |= (1 << 7)

    return mask
