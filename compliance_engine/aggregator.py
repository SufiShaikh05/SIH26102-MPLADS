"""In-memory aggregator and query engine for Compliance & Execution Risk Intelligence v1."""

from __future__ import annotations

import csv
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .models import (
    AuthorityType,
    CompactWorkRecord,
    ComplianceRuleId,
    ComplianceSummary,
    FinancialReconciliationOutlay,
    POLICY_RULES_MASK,
    RECONCILIATION_RULES_MASK,
    EXECUTION_RULES_MASK,
    RULE_BIT_MAP,
    RULE_ID_TO_BIT,
    RuleClassification,
    RuleEvaluation,
    RuleSummaryStat,
    TriggerDensity,
    UniqueCounts,
    WorkComplianceRecord,
    mask_to_rule_ids,
)
from .registry import POLICY_REGISTRY, get_rule_meta
from .rules import DEFAULT_SNAPSHOT_DATE, compute_work_bitmask, evaluate_work, parse_date

log = logging.getLogger("uvicorn.error.mplads")


@dataclass(slots=True)
class PostCompletionTxInfo:
    count: int = 0
    total_amount: float = 0.0
    max_days: int = 0


class ComplianceAggregator:
    """Evaluates and indexes all works against the 8 compliance/risk rules."""

    def __init__(
        self,
        processed_dir: Path,
        snapshot_date: date = DEFAULT_SNAPSHOT_DATE,
        work_store: Any = None,
    ) -> None:
        self.processed_dir = Path(processed_dir)
        self.snapshot_date = snapshot_date
        self.work_store = work_store
        self._loaded = False

        # In-memory stores
        self._records: dict[str, CompactWorkRecord] = {}
        self._post_comp_info: dict[str, PostCompletionTxInfo] = {}
        self._flagged_work_ids: set[str] = set()
        self._unflagged_work_ids: set[str] = set()
        self._policy_work_ids: set[str] = set()
        self._reconciliation_work_ids: set[str] = set()
        self._execution_work_ids: set[str] = set()
        self._rule_to_work_ids: dict[str, set[str]] = defaultdict(set)
        self._summary: Optional[ComplianceSummary] = None

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self.load()

    def load(self) -> None:
        """Loads data files and precomputes rule evaluations."""
        works_master_path = self.processed_dir / "works_master.csv"
        exp_by_work_path = self.processed_dir / "expenditure_by_work.csv"
        exp_tx_path = self.processed_dir / "expenditure_transactions.csv"

        if not works_master_path.is_file():
            log.warning("works_master.csv not found at %s", works_master_path)
            return

        # 1. Load compact expenditure by work lookup: work_id -> (disbursed_amount, last_expenditure_date)
        exp_lookup: dict[str, tuple[float, str | None]] = {}
        if exp_by_work_path.is_file():
            with open(exp_by_work_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        amt = float(row.get("deduplicated_disbursed_amount", 0) or 0)
                    except (ValueError, TypeError):
                        amt = 0.0
                    last_d = row.get("last_expenditure_date")
                    exp_lookup[row["work_id"]] = (amt, sys.intern(last_d) if last_d else None)

        # 2. First pass over works_master to get completion dates for post-completion tx analysis
        completion_dates: dict[str, date] = {}
        with open(works_master_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                c_d = parse_date(row.get("completion_date"))
                if c_d is not None:
                    completion_dates[row["work_id"]] = c_d

        # 3. Analyze post-completion transactions
        post_comp_info: dict[str, PostCompletionTxInfo] = defaultdict(PostCompletionTxInfo)
        if exp_tx_path.is_file():
            with open(exp_tx_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for tx in reader:
                    wid = tx.get("work_id", "")
                    c_d = completion_dates.get(wid)
                    p_d = parse_date(tx.get("expenditure_date"))
                    if c_d is not None and p_d is not None:
                        diff_days = (p_d - c_d).days
                        if diff_days > 30:
                            try:
                                amt = float(tx.get("fund_disbursed_amount", 0) or 0)
                            except (ValueError, TypeError):
                                amt = 0.0
                            info = post_comp_info[wid]
                            info.count += 1
                            info.total_amount += amt
                            if diff_days > info.max_days:
                                info.max_days = diff_days
        del completion_dates

        # 4. Evaluate all works using fast deterministic bitmask arithmetic
        records: dict[str, CompactWorkRecord] = {}
        rule_to_work_ids: dict[str, set[str]] = defaultdict(set)
        flagged_work_ids: set[str] = set()
        unflagged_work_ids: set[str] = set()
        policy_work_ids: set[str] = set()
        reconciliation_work_ids: set[str] = set()
        execution_work_ids: set[str] = set()

        total_sanctioned_all = 0.0
        total_dedup_disbursed_all = 0.0

        with open(works_master_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                wid = row["work_id"]
                try:
                    s_amt = float(row.get("sanction_amount", 0) or 0)
                except (ValueError, TypeError):
                    s_amt = 0.0
                total_sanctioned_all += s_amt

                d_disb, last_exp_d = exp_lookup.get(wid, (0.0, None))
                total_dedup_disbursed_all += d_disb

                is_comp = (row.get("in_completed") == "1") or (row.get("work_status") == "Work Completed")
                rec_d = row.get("recommended_date")
                san_d = row.get("sanction_date")
                comp_d = row.get("completion_date")

                post_info = post_comp_info.get(wid)
                has_post_tx = post_info is not None and post_info.count > 0
                post_tx_cnt = post_info.count if post_info else 0

                # Compute 8-bit rule bitmask directly
                mask = compute_work_bitmask(
                    recommended_date=rec_d,
                    sanction_date=san_d,
                    completion_date=comp_d,
                    sanction_amount=s_amt,
                    is_completed=is_comp,
                    deduplicated_disbursed_amount=d_disb,
                    last_expenditure_date=last_exp_d,
                    has_post_completion_tx=has_post_tx,
                    post_completion_tx_count=post_tx_cnt,
                    as_of_date=self.snapshot_date,
                )

                # Populate index sets
                for bit, r_id in RULE_BIT_MAP:
                    if mask & bit:
                        rule_to_work_ids[r_id].add(wid)

                if mask > 0:
                    flagged_work_ids.add(wid)
                else:
                    unflagged_work_ids.add(wid)

                if mask & POLICY_RULES_MASK:
                    policy_work_ids.add(wid)
                if mask & RECONCILIATION_RULES_MASK:
                    reconciliation_work_ids.add(wid)
                if mask & EXECUTION_RULES_MASK:
                    execution_work_ids.add(wid)

                # Intern strings with low cardinality
                st = sys.intern(row.get("state", "").strip())
                cat = sys.intern(row.get("work_category", "Normal/Others").strip())
                stat = sys.intern(row.get("work_status", "").strip())
                const = sys.intern(row.get("constituency", "").strip())

                records[wid] = CompactWorkRecord(
                    work_id=wid,
                    sanction_amount=s_amt,
                    disbursed_amount=d_disb,
                    state=st,
                    constituency=const,
                    work_category=cat,
                    work_status=stat,
                    triggered_mask=mask,
                    rec_date=sys.intern(rec_d) if rec_d else None,
                    san_date=sys.intern(san_d) if san_d else None,
                    comp_date=sys.intern(comp_d) if comp_d else None,
                    last_pay_date=last_exp_d,
                    is_completed=is_comp,
                )

        del exp_lookup

        self._records = records
        self._post_comp_info = dict(post_comp_info)
        self._flagged_work_ids = flagged_work_ids
        self._unflagged_work_ids = unflagged_work_ids
        self._policy_work_ids = policy_work_ids
        self._reconciliation_work_ids = reconciliation_work_ids
        self._execution_work_ids = execution_work_ids
        self._rule_to_work_ids = rule_to_work_ids

        # 5. Build precomputed summary
        self._summary = self._build_summary(total_sanctioned_all, total_dedup_disbursed_all, post_comp_info)
        self._loaded = True
        log.info(
            "ComplianceAggregator loaded: %d works, %d flagged, %d unflagged",
            len(self._records),
            len(self._flagged_work_ids),
            len(self._unflagged_work_ids),
        )

    def _build_summary(
        self,
        total_sanctioned_all: float,
        total_dedup_disbursed_all: float,
        post_comp_info: dict[str, PostCompletionTxInfo],
    ) -> ComplianceSummary:
        total_works = len(self._records)
        total_trigger_instances = sum(len(wids) for wids in self._rule_to_work_ids.values())

        # Financial outlays
        flagged_sanc = sum(self._records[w].sanction_amount for w in self._flagged_work_ids) / 1e7
        flagged_disb = sum(self._records[w].disbursed_amount for w in self._flagged_work_ids) / 1e7
        unflagged_sanc = sum(self._records[w].sanction_amount for w in self._unflagged_work_ids) / 1e7
        unflagged_disb = sum(self._records[w].disbursed_amount for w in self._unflagged_work_ids) / 1e7

        policy_sanc = sum(self._records[w].sanction_amount for w in self._policy_work_ids) / 1e7
        policy_disb = sum(self._records[w].disbursed_amount for w in self._policy_work_ids) / 1e7
        exec_sanc = sum(self._records[w].sanction_amount for w in self._execution_work_ids) / 1e7
        exec_disb = sum(self._records[w].disbursed_amount for w in self._execution_work_ids) / 1e7

        # Post-completion outlay specifically for RISK-03
        risk_03_works = self._rule_to_work_ids.get("RISK-03", set())
        exact_post_comp_disb = sum(post_comp_info[wid].total_amount for wid in risk_03_works) / 1e7

        rule_breakdown: dict[str, RuleSummaryStat] = {}
        for r_id in [
            "COMP-01", "COMP-02", "COMP-03", "COMP-04",
            "MON-01", "RISK-01", "RISK-02", "RISK-03"
        ]:
            wids = self._rule_to_work_ids.get(r_id, set())
            meta = get_rule_meta(r_id)
            r_sanc = sum(self._records[w].sanction_amount for w in wids) / 1e7
            r_disb = sum(self._records[w].disbursed_amount for w in wids) / 1e7
            rule_breakdown[r_id] = RuleSummaryStat(
                rule_id=r_id,
                rule_name=meta.get("rule_name", r_id),
                authority_type=AuthorityType(meta.get("authority_type", "HEURISTIC")),
                classification=RuleClassification(meta.get("classification", "EXECUTION HEURISTIC")),
                source_reference=meta.get("source_reference", ""),
                threshold=meta.get("threshold", ""),
                unique_works_count=len(wids),
                percentage_of_all_works=round((len(wids) / total_works * 100), 2) if total_works else 0.0,
                sanctioned_outlay_cr=round(r_sanc, 2),
                disbursed_outlay_cr=round(r_disb, 2),
                description=meta.get("description", ""),
                limitation=meta.get("limitation", ""),
                recommended_action=meta.get("recommended_action", ""),
            )

        avg_triggers = (
            round(total_trigger_instances / len(self._flagged_work_ids), 2)
            if self._flagged_work_ids
            else 0.0
        )

        return ComplianceSummary(
            snapshot_date=self.snapshot_date.isoformat(),
            policy_baseline="MPLADS Guidelines 2023, as amended/clarified by subsequent official MoSPI provisions applicable to the dataset period",
            total_works_evaluated=total_works,
            unique_counts=UniqueCounts(
                total_unique_flagged_works=len(self._flagged_work_ids),
                unique_policy_affected_works=len(self._policy_work_ids),
                policy_derived_reconciliation_works=len(self._reconciliation_work_ids),
                unique_execution_affected_works=len(self._execution_work_ids),
                unflagged_baseline_works=len(self._unflagged_work_ids),
                total_works_evaluated=total_works,
            ),
            trigger_density=TriggerDensity(
                total_trigger_instances=total_trigger_instances,
                average_triggers_per_flagged_work=avg_triggers,
            ),
            financial_outlay=FinancialReconciliationOutlay(
                total_sanctioned_outlay_all_works_cr=round(total_sanctioned_all / 1e7, 2),
                total_deduplicated_disbursed_outlay_all_works_cr=round(total_dedup_disbursed_all / 1e7, 2),
                flagged_works_sanctioned_outlay_cr=round(flagged_sanc, 2),
                flagged_works_deduplicated_disbursed_outlay_cr=round(flagged_disb, 2),
                unflagged_works_sanctioned_outlay_cr=round(unflagged_sanc, 2),
                unflagged_works_deduplicated_disbursed_outlay_cr=round(unflagged_disb, 2),
                policy_affected_sanctioned_outlay_cr=round(policy_sanc, 2),
                policy_affected_deduplicated_disbursed_outlay_cr=round(policy_disb, 2),
                execution_affected_sanctioned_outlay_cr=round(exec_sanc, 2),
                execution_affected_deduplicated_disbursed_outlay_cr=round(exec_disb, 2),
                post_completion_disbursed_outlay_cr=round(exact_post_comp_disb, 2),
            ),
            rule_breakdown=rule_breakdown,
        )

    def get_summary(self) -> ComplianceSummary:
        self.ensure_loaded()
        if self._summary is None:
            raise RuntimeError("Compliance summary was not initialized")
        return self._summary

    def to_api_record(self, record: CompactWorkRecord, include_evaluations: bool = True) -> WorkComplianceRecord:
        """Converts a lightweight CompactWorkRecord to a full WorkComplianceRecord on demand."""
        desc = ""
        ida = ""
        mp_name = ""
        if self.work_store is not None:
            w = self.work_store.get(record.work_id)
            if w is not None:
                desc = w.cleaned_work_description or ""
                ida = w.ida or ""
                mp_name = w.mp_name or ""

        d_san = parse_date(record.san_date)
        d_last = parse_date(record.last_pay_date)
        days_since_san = (self.snapshot_date - d_san).days if d_san else None
        days_since_pay = (self.snapshot_date - d_last).days if d_last else None

        evals: list[RuleEvaluation] = []
        if include_evaluations:
            post_info = self._post_comp_info.get(record.work_id)
            has_post_tx = post_info is not None and post_info.count > 0
            post_tx_cnt = post_info.count if post_info else 0
            post_tx_amt = post_info.total_amount if post_info else 0.0
            max_post_days = post_info.max_days if post_info else None

            evals = evaluate_work(
                work_id=record.work_id,
                recommended_date=record.rec_date,
                sanction_date=record.san_date,
                completion_date=record.comp_date,
                sanction_amount=record.sanction_amount,
                is_completed=record.is_completed,
                deduplicated_disbursed_amount=record.disbursed_amount,
                last_expenditure_date=record.last_pay_date,
                has_post_completion_tx=has_post_tx,
                post_completion_tx_count=post_tx_cnt,
                post_completion_amount=post_tx_amt,
                max_days_post_completion=max_post_days,
                as_of_date=self.snapshot_date,
            )

        return WorkComplianceRecord(
            work_id=record.work_id,
            work_description=desc,
            state=record.state,
            constituency=record.constituency,
            ida=ida,
            mp_name=mp_name,
            work_category=record.work_category,
            work_status=record.work_status,
            is_completed=record.is_completed,
            sanction_amount=record.sanction_amount,
            deduplicated_disbursed_amount=record.disbursed_amount,
            utilization_ratio=record.utilization_ratio,
            recommended_date=record.rec_date,
            sanction_date=record.san_date,
            completion_date=record.comp_date or "",
            last_expenditure_date=record.last_pay_date,
            days_since_sanction=days_since_san,
            days_since_last_payment=days_since_pay,
            triggered_rule_ids=record.triggered_rule_ids,
            evaluations=evals,
        )

    def get_work(self, work_id: str) -> Optional[WorkComplianceRecord]:
        self.ensure_loaded()
        rec = self._records.get(work_id)
        if rec is None:
            return None
        return self.to_api_record(rec, include_evaluations=True)

    def get_queue(
        self,
        *,
        rule_id: Optional[str] = None,
        authority_type: Optional[str] = None,
        classification: Optional[str] = None,
        state: Optional[str] = None,
        work_category: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Serves filterable paginated review queue."""
        self.ensure_loaded()

        # Base candidate work IDs
        if rule_id and rule_id in self._rule_to_work_ids:
            candidates = self._rule_to_work_ids[rule_id]
        else:
            candidates = self._flagged_work_ids

        # Bitmask filters for authority_type and classification
        auth_mask = None
        if authority_type:
            if authority_type == AuthorityType.OPERATIONAL_PROXY.value:
                auth_mask = 1 << 0
            elif authority_type == AuthorityType.GUIDELINE_PROVISION.value:
                auth_mask = (1 << 1) | (1 << 2) | (1 << 3)
            elif authority_type == AuthorityType.OFFICIAL_MONITORING.value:
                auth_mask = 1 << 4
            elif authority_type == AuthorityType.HEURISTIC.value:
                auth_mask = (1 << 5) | (1 << 6) | (1 << 7)

        class_mask = None
        if classification:
            if classification == RuleClassification.POLICY_PROXY.value:
                class_mask = 1 << 0
            elif classification == RuleClassification.POLICY_TRIGGER.value:
                class_mask = (1 << 1) | (1 << 2)
            elif classification == RuleClassification.POLICY_RECONCILIATION.value:
                class_mask = 1 << 3
            elif classification == RuleClassification.OFFICIAL_MONITORING.value:
                class_mask = 1 << 4
            elif classification == RuleClassification.EXECUTION_HEURISTIC.value:
                class_mask = (1 << 5) | (1 << 6) | (1 << 7)

        filtered_wids: list[str] = []
        search_term = search.lower().strip() if search else None
        state_term = state.strip().lower() if state else None
        cat_term = work_category.strip() if work_category else None

        for wid in candidates:
            rec = self._records[wid]

            if state_term and rec.state.strip().lower() != state_term:
                continue

            if cat_term and rec.work_category.strip() != cat_term:
                continue

            if auth_mask is not None and not (rec.triggered_mask & auth_mask):
                continue

            if class_mask is not None and not (rec.triggered_mask & class_mask):
                continue

            if search_term:
                desc = ""
                mp_name = ""
                ida = ""
                if self.work_store is not None:
                    w = self.work_store.get(wid)
                    if w is not None:
                        desc = w.cleaned_work_description or ""
                        mp_name = w.mp_name or ""
                        ida = w.ida or ""
                match = (
                    search_term in wid.lower()
                    or search_term in desc.lower()
                    or search_term in mp_name.lower()
                    or search_term in rec.constituency.lower()
                    or search_term in ida.lower()
                )
                if not match:
                    continue

            filtered_wids.append(wid)

        # Stable sort: by sanction_amount descending, then work_id
        filtered_wids.sort(key=lambda w: (-self._records[w].sanction_amount, w))

        total_items = len(filtered_wids)
        total_pages = max(1, (total_items + limit - 1) // limit) if limit > 0 else 1
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit

        items = [self.to_api_record(self._records[w], include_evaluations=True) for w in filtered_wids[start_idx:end_idx]]

        return {
            "items": items,
            "total_items": total_items,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }
