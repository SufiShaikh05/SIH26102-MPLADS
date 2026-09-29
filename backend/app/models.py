"""Response models - the stable contract the dashboard codes against.

Rules that keep the contract stable:
* every field is always present in the JSON (``null`` when unknown), so the
  frontend never has to test for missing keys;
* anomaly-engine columns that are not part of the contract never appear in list
  responses (they are exposed only under ``extra`` on single-record endpoints);
* wording: these are review-priority indicators, not findings of fraud.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Where the anomaly/review records come from:
#   engine        work_anomalies_v1.csv written by anomaly_engine/
#   demo_fixture  clearly-marked placeholder data (MPLADS_USE_DEMO_ANOMALIES=1)
#   none          no anomaly file available
DataKind = Literal["engine", "demo_fixture", "none"]
ReviewStatus = Literal["scored", "not_scored", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    detail: str


# --------------------------------------------------------------------------- summary
class AnomalyDataInfo(BaseModel):
    available: bool = Field(description="True when anomaly/review records are loaded.")
    kind: DataKind
    file: str | None = Field(default=None, description="File name that was loaded.")
    records: int = 0
    review_candidate_rule: str | None = Field(
        default=None, description="How `review_candidates` is counted."
    )
    missing_columns: list[str] = Field(
        default_factory=list,
        description="Contract columns absent from the anomaly CSV (filled from the "
        "feature matrix when possible; engine-only columns stay null).",
    )
    message: str | None = None


class LabelCount(BaseModel):
    label: str
    count: int


class StatusCount(BaseModel):
    status: str
    count: int


class SummaryResponse(BaseModel):
    total_works: int
    sanctioned_works: int
    completed_works: int = Field(description="Works present in the Completed source (in_completed = 1).")
    works_with_expenditure: int
    total_expenditure_transactions: int
    review_candidates: int | None = Field(
        description="Null when no anomaly data is available (never a fabricated 0)."
    )
    generated_at: datetime = Field(
        description="When the backend last built these figures from the CSVs "
        "(changes only when the data files are (re)loaded)."
    )
    snapshot_date: date | None
    review_priority_label_counts: list[LabelCount] = Field(
        description="Ordered from highest to lowest mean review_priority_score."
    )
    work_status_counts: list[StatusCount]
    anomaly_data: AnomalyDataInfo
    warnings: list[str]


# --------------------------------------------------------------------------- anomalies
class AnomalyRecord(BaseModel):
    """One row of work_anomalies_v1.csv (contract columns only)."""

    work_id: str
    state: str | None = None
    constituency: str | None = None
    mp_name: str | None = None
    ida: str | None = None
    work_category: str | None = None
    work_status: str | None = None
    sanction_amount: float | None = None
    total_disbursed_all_rows: float | None = None
    deduplicated_disbursed_amount: float | None = None
    success_utilization_ratio: float | None = None
    days_since_sanction: int | None = None
    days_since_last_payment: int | None = None
    payment_count: int | None = None
    vendor_count: int | None = None
    duplicate_ratio: float | None = None
    review_priority_score: float | None = None
    review_priority_label: str | None = None
    signal_count: int | None = None
    top_signal_1: str | None = None
    top_signal_2: str | None = None
    top_signal_3: str | None = None
    explanation_text: str | None = None


class AnomalyDetail(AnomalyRecord):
    extra: dict[str, str | None] = Field(
        default_factory=dict,
        description="Additional anomaly-engine columns, passed through as raw CSV text "
        "(empty -> null). Not part of the stable contract.",
    )
    data_kind: DataKind


class AnomalyPage(BaseModel):
    items: list[AnomalyRecord]
    page: int
    page_size: int
    total: int
    pages: int = Field(description="0 when there are no matching rows.")
    data_kind: DataKind


# --------------------------------------------------------------------------- lookups
class StateCount(BaseModel):
    state: str
    work_count: int
    review_candidate_count: int | None = Field(
        description="Null when no anomaly data is available."
    )


class StateList(BaseModel):
    items: list[StateCount]
    total: int


class CategoryCount(BaseModel):
    work_category: str
    work_count: int
    review_candidate_count: int | None = Field(
        description="Null when no anomaly data is available."
    )


class CategoryList(BaseModel):
    items: list[CategoryCount]
    total: int


# --------------------------------------------------------------------------- work detail
class WorkIdentity(BaseModel):
    work_id: str
    work: str | None = Field(default=None, description="Type of work (works_master).")
    description: str | None = Field(default=None, description="Cleaned work description.")
    state: str | None = None
    constituency: str | None = None
    mp_name: str | None = None
    ida: str | None = None
    work_category: str | None = None
    work_status: str | None = None
    mp_code: str | None = Field(default=None, description="MP-code segment of the Work ID.")
    financial_year: str | None = Field(default=None, description="Financial-year segment of the Work ID.")


class WorkFlags(BaseModel):
    in_recommended: bool | None = None
    in_sanctioned: bool | None = None
    in_completed: bool | None = None
    is_completed: bool | None = None
    is_partially_completed: bool | None = None
    is_sanctioned_only: bool | None = None


class WorkAmounts(BaseModel):
    recommended_amount: float | None = None
    sanction_amount: float | None = None
    total_disbursed_all_rows: float | None = None
    success_amount: float | None = None
    in_progress_amount: float | None = None
    exact_duplicate_amount: float | None = None
    deduplicated_disbursed_amount: float | None = None
    remaining_sanction_amount: float | None = None
    completed_amount_disbursed: float | None = None


class WorkUtilization(BaseModel):
    success_utilization_ratio: float | None = None
    gross_utilization_ratio: float | None = None
    deduplicated_utilization_ratio: float | None = None
    in_progress_ratio: float | None = None


class WorkPayments(BaseModel):
    payment_count: int | None = None
    deduplicated_payment_count: int | None = None
    vendor_count: int | None = None
    duplicate_record_count: int | None = None
    duplicate_ratio: float | None = None
    duplicate_amount_ratio: float | None = None
    single_payment_work: bool | None = None
    payment_span_days: int | None = None
    average_payment_interval_days: float | None = None


class WorkTimeline(BaseModel):
    recommended_date: date | None = None
    sanction_date: date | None = None
    first_payment_date: date | None = None
    last_payment_date: date | None = None
    completion_date: date | None = None
    recommendation_to_sanction_days: int | None = None
    sanction_to_first_payment_days: int | None = None
    sanction_to_last_payment_days: int | None = None
    sanction_to_completion_days: int | None = None
    days_since_sanction: int | None = None
    days_since_last_payment: int | None = None


class PeerGroup(BaseModel):
    peer_count: int | None = None
    sufficient: bool | None = Field(
        default=None, description="False -> the medians below are not reliable (group < 20 works)."
    )
    median_sanction_amount: float | None = None
    median_disbursed_amount: float | None = None
    median_payment_count: float | None = None


class WorkPeers(BaseModel):
    work_category: PeerGroup
    state: PeerGroup
    state_work_category: PeerGroup


class ReviewInfo(BaseModel):
    review_priority_score: float | None = None
    review_priority_label: str | None = None
    signal_count: int | None = None
    top_signal_1: str | None = None
    top_signal_2: str | None = None
    top_signal_3: str | None = None
    explanation_text: str | None = None
    extra: dict[str, str | None] = Field(default_factory=dict)
    data_kind: DataKind


class WorkDetail(BaseModel):
    work_id: str
    snapshot_date: date | None
    identity: WorkIdentity
    flags: WorkFlags
    amounts: WorkAmounts
    utilization: WorkUtilization
    payments: WorkPayments
    timeline: WorkTimeline
    peers: WorkPeers
    review_status: ReviewStatus = Field(
        description="scored: a review record exists; not_scored: anomaly data is loaded but "
        "has no record for this work; unavailable: no anomaly data is loaded."
    )
    review: ReviewInfo | None = None
