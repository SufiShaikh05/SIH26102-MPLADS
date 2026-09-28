"""Single source of truth for every column feature_engineering reads and produces.

Nothing here recomputes anything that belongs to ``data_pipeline/``: amounts, dates, Work
IDs, footer handling, and expenditure aggregation are frozen there and are only ever read
from their already-cleaned CSV output. The column names below are that pipeline's public
output contract (``data_pipeline/merge.py:MASTER_COLUMNS`` and
``data_pipeline/aggregate.py:AGG_COLUMNS``); if a real file is missing a column this module
requires, ``build_features.py`` raises a clear, actionable error rather than guessing.

``FEATURES`` is the one registry both ``build_features.py`` (what to compute, in what order)
and ``profiling.py`` (what to profile and report on) read from, so the feature matrix and the
profiling report can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# --------------------------------------------------------- data_pipeline's output contract
# Columns works_master.csv / expenditure_by_work.csv are known to carry. Anything not listed
# here (e.g. conflict_fields, *_source_row, *_row_count) is intentionally left out of the
# feature matrix - it is audit/traceability detail, not an analytical feature - and stays
# available in the original file for anyone who needs it.
MASTER_REQUIRED_COLUMNS = ("work_id",)
MASTER_PASSTHROUGH_COLUMNS = (
    "state", "constituency", "mp_name", "ida", "work_category", "work_status",
    "id_mp_code", "id_financial_year", "work_description",
    "recommended_date", "recommended_amount", "sanction_date", "sanction_amount",
    "completion_date", "completed_amount_disbursed",
    "in_recommended", "in_sanctioned", "in_completed",
)
EXPENDITURE_REQUIRED_COLUMNS = ("work_id",)
EXPENDITURE_PASSTHROUGH_COLUMNS = (
    "total_disbursed_all_rows", "success_amount", "in_progress_amount",
    "exact_duplicate_amount", "deduplicated_disbursed_amount",
    "payment_count_all_rows", "deduplicated_payment_count", "duplicate_record_count", "duplicate_ratio",
    "vendor_count", "first_expenditure_date", "last_expenditure_date",
)

# --------------------------------------------------------------------------- fixed settings
DEFAULT_SNAPSHOT_DATE = "2026-09-25"   # documented run snapshot date - never date.today() by default
MIN_PEER_GROUP_SIZE = 20               # documented minimum sample size for a peer-group statistic
PEER_GROUPINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("work_category", ("work_category",)),
    ("state", ("state",)),
    ("state_work_category", ("state", "work_category")),
)
RATIO_QUANT = Decimal("0.000001")      # derived ratios are rounded to 6 decimal places for output
DEMO_SIZE = 500
DEMO_SEED = "SIH26102-feature-v0"

# Amount used as "disbursed" for the peer-group median (documented choice: the gross,
# every-status, every-row figure - the most fundamental "how much money moved" measure).
PEER_DISBURSED_FIELD = "total_disbursed_all_rows"
PEER_PAYMENT_COUNT_FIELD = "payment_count_all_rows"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    source: str     # "works_master" | "expenditure_by_work" | "derived"
    group: str      # "context" | "financial" | "time" | "transaction" | "completion" | "text" | "peer"
    dtype: str      # "text" | "int" | "decimal" | "date" | "bool"
    definition: str
    timing: str     # "monitoring" | "retrospective" | "context" (identifiers/labels: neither)
    future_use: str


def _peer_specs() -> tuple[FeatureSpec, ...]:
    specs = []
    for key, _cols in PEER_GROUPINGS:
        label = key.replace("_", " + ")
        specs.extend([
            FeatureSpec(f"peer_count_by_{key}", "derived", "peer", "int",
                        f"Number of works sharing this work's {label}", "monitoring",
                        "Denominator for judging whether the other peer_* columns are reliable"),
            FeatureSpec(f"peer_median_sanction_amount_by_{key}", "derived", "peer", "decimal",
                        f"Median sanction_amount among works sharing {label} (peer group >= {MIN_PEER_GROUP_SIZE} only)",
                        "monitoring", "Baseline for a future peer-relative cost-anomaly signal"),
            FeatureSpec(f"peer_median_disbursed_amount_by_{key}", "derived", "peer", "decimal",
                        f"Median {PEER_DISBURSED_FIELD} among works sharing {label} (peer group >= {MIN_PEER_GROUP_SIZE} only)",
                        "monitoring", "Baseline for a future peer-relative utilization signal"),
            FeatureSpec(f"peer_median_payment_count_by_{key}", "derived", "peer", "int",
                        f"Median {PEER_PAYMENT_COUNT_FIELD} among works sharing {label} (peer group >= {MIN_PEER_GROUP_SIZE} only)",
                        "monitoring", "Baseline for a future peer-relative payment-frequency signal"),
            FeatureSpec(f"peer_group_sufficient_by_{key}", "derived", "peer", "bool",
                        f"1 if this work's {label} peer group has >= {MIN_PEER_GROUP_SIZE} members, else 0",
                        "monitoring", "Gate: treat the three peer_median_* columns above as unreliable when 0"),
        ])
    return tuple(specs)


FEATURES: tuple[FeatureSpec, ...] = (
    # ---------------------------------------------------------- Group 1: identifiers / context
    FeatureSpec("work_id", "works_master", "context", "text", "Canonical Work ID (join key)", "context",
                "Row identity; dashboard lookup key"),
    FeatureSpec("state", "works_master", "context", "text", "State, as recorded in works_master", "context",
                "Dashboard display; a candidate peer-group key"),
    FeatureSpec("constituency", "works_master", "context", "text", "Constituency, as recorded in works_master", "context",
                "Dashboard display"),
    FeatureSpec("mp_name", "works_master", "context", "text", "MP name, as recorded in works_master", "context",
                "Dashboard display"),
    FeatureSpec("ida", "works_master", "context", "text", "Implementing district authority, as recorded in works_master",
                "context", "Dashboard display"),
    FeatureSpec("work_category", "works_master", "context", "text", "Work category, as recorded in works_master", "context",
                "Dashboard display; a candidate peer-group key"),
    FeatureSpec("work_status", "works_master", "context", "text", "Work status (Sanctioned source), as recorded in works_master",
                "monitoring", "Basis for the completion-classification flags in Group 5"),
    FeatureSpec("id_mp_code", "works_master", "context", "text", "MP-code segment parsed from the Work ID", "context",
                "Metadata only - see Feature suitability; not validated as a standalone MP identity"),
    FeatureSpec("id_financial_year", "works_master", "context", "text", "Financial-year segment parsed from the Work ID",
                "context", "Metadata only - see Feature suitability"),
    FeatureSpec("in_recommended", "works_master", "context", "bool", "1 if the work appears in the Recommended source",
                "monitoring", "Structural context; distinguishes recommended-only works"),
    FeatureSpec("in_sanctioned", "works_master", "context", "bool", "1 if the work appears in the Sanctioned source",
                "monitoring", "Structural context; gates work_status-derived features"),
    FeatureSpec("in_completed", "works_master", "context", "bool", "1 if the work appears in the Completed source",
                "retrospective", "Structural context; gates completion-derived features"),

    # ---------------------------------------------------------------------- Group 2: financial
    FeatureSpec("recommended_amount", "works_master", "financial", "decimal", "Recommended amount, as recorded", "monitoring",
                "Baseline for utilization/ratio features"),
    FeatureSpec("sanction_amount", "works_master", "financial", "decimal", "Sanctioned amount, as recorded", "monitoring",
                "Denominator for every utilization ratio below"),
    FeatureSpec("recommendation_to_sanction_amount_ratio", "derived", "financial", "decimal",
                "sanction_amount / recommended_amount (interpreted as: share of the recommended amount that was "
                "sanctioned - see Known limitations for the alternative reading)", "monitoring",
                "Flags works sanctioned for much less, or much more, than recommended"),
    FeatureSpec("total_disbursed_all_rows", "expenditure_by_work", "financial", "decimal",
                "Gross disbursed amount, every status and duplicate row included (pass-through)", "monitoring",
                "Headline utilization numerator"),
    FeatureSpec("success_amount", "expenditure_by_work", "financial", "decimal",
                "Disbursed amount with payment_status exactly 'Payment Success' (pass-through)", "monitoring",
                "Numerator of success_utilization_ratio"),
    FeatureSpec("in_progress_amount", "expenditure_by_work", "financial", "decimal",
                "Disbursed amount with payment_status exactly 'Payment In-Progress' (pass-through)", "monitoring",
                "Numerator of in_progress_ratio"),
    FeatureSpec("exact_duplicate_amount", "expenditure_by_work", "financial", "decimal",
                "Disbursed amount on exact-duplicate transaction rows (pass-through)", "monitoring",
                "Numerator of duplicate_amount_ratio; a data-quality signal, not by itself a finding of error"),
    FeatureSpec("deduplicated_disbursed_amount", "expenditure_by_work", "financial", "decimal",
                "Disbursed amount excluding exact-duplicate rows (pass-through)", "monitoring",
                "Conservative utilization numerator"),
    FeatureSpec("success_utilization_ratio", "derived", "financial", "decimal", "success_amount / sanction_amount",
                "monitoring", "Primary utilization signal for Anomaly Engine v1"),
    FeatureSpec("gross_utilization_ratio", "derived", "financial", "decimal", "total_disbursed_all_rows / sanction_amount",
                "monitoring", "Upper-bound utilization signal (includes in-progress and duplicate amounts)"),
    FeatureSpec("deduplicated_utilization_ratio", "derived", "financial", "decimal",
                "deduplicated_disbursed_amount / sanction_amount", "monitoring",
                "Conservative utilization signal (duplicates excluded)"),
    FeatureSpec("remaining_sanction_amount", "derived", "financial", "decimal",
                "sanction_amount - deduplicated_disbursed_amount", "monitoring",
                "Funds apparently still available against the sanction; can be negative (over-disbursement)"),
    FeatureSpec("in_progress_ratio", "derived", "financial", "decimal", "in_progress_amount / total_disbursed_all_rows",
                "monitoring", "Share of disbursement still in progress"),
    FeatureSpec("duplicate_amount_ratio", "derived", "financial", "decimal",
                "exact_duplicate_amount / total_disbursed_all_rows", "monitoring",
                "Share of disbursement sitting on exact-duplicate rows - a review-candidate signal, not fraud"),

    # --------------------------------------------------------------------------- Group 3: time
    FeatureSpec("recommendation_to_sanction_days", "derived", "time", "int", "sanction_date - recommended_date, in days",
                "monitoring", "Process-delay signal"),
    FeatureSpec("sanction_to_first_payment_days", "derived", "time", "int", "first_expenditure_date - sanction_date, in days",
                "monitoring", "Time to first disbursement"),
    FeatureSpec("sanction_to_last_payment_days", "derived", "time", "int", "last_expenditure_date - sanction_date, in days",
                "monitoring", "Disbursement span so far"),
    FeatureSpec("sanction_to_completion_days", "derived", "time", "int", "completion_date - sanction_date, in days",
                "retrospective", "Only knowable after completion - see Leakage risks"),
    FeatureSpec("days_since_sanction", "derived", "time", "int", "snapshot_date - sanction_date, in days", "monitoring",
                "Work age; needed to interpret a low utilization ratio (new vs. stalled)"),
    FeatureSpec("days_since_last_payment", "derived", "time", "int", "snapshot_date - last_expenditure_date, in days",
                "monitoring", "Stalled-payment signal"),

    # ------------------------------------------------------------- Group 4: transaction behavior
    FeatureSpec("payment_count_all_rows", "expenditure_by_work", "transaction", "int",
                "Every payment row for the work, including missing-amount rows (pass-through)", "monitoring",
                "Payment-frequency signal; also the duplicate_ratio denominator"),
    FeatureSpec("deduplicated_payment_count", "expenditure_by_work", "transaction", "int",
                "Payment rows with duplicate_record = 0 (pass-through)", "monitoring", "Conservative payment count"),
    FeatureSpec("vendor_count", "expenditure_by_work", "transaction", "int",
                "Distinct vendor names after case/punctuation folding (pass-through)", "monitoring",
                "Vendor-concentration signal"),
    FeatureSpec("duplicate_record_count", "expenditure_by_work", "transaction", "int",
                "Payment rows with duplicate_record = 1 (pass-through)", "monitoring",
                "A data-quality signal requiring verification, not a finding of error"),
    FeatureSpec("duplicate_ratio", "expenditure_by_work", "transaction", "decimal",
                "duplicate_record_count / payment_count_all_rows (pass-through)", "monitoring",
                "Normalised duplicate signal, comparable across works of different size"),
    FeatureSpec("payment_span_days", "derived", "transaction", "int",
                "last_expenditure_date - first_expenditure_date, in days (0 for a single payment)", "monitoring",
                "Disbursement spread; see single_payment_work before interpreting a 0"),
    FeatureSpec("average_payment_interval_days", "derived", "transaction", "decimal",
                "payment_span_days / (payment_count_all_rows - 1); left blank (not 0) for a single payment", "monitoring",
                "Payment cadence; only meaningful for 2+ payments"),
    FeatureSpec("single_payment_work", "derived", "transaction", "bool",
                "1 if payment_count_all_rows == 1 (average_payment_interval_days is undefined, not 0, in this case)",
                "monitoring", "Disambiguates a blank average_payment_interval_days from a real 0"),

    # -------------------------------------------------------------- Group 5: completion / execution
    FeatureSpec("is_completed", "derived", "completion", "bool",
                "1 if work_status text indicates completion without 'partial' (see report for the exact rule and "
                "every distinct status value seen)", "monitoring", "Execution-stage flag"),
    FeatureSpec("is_partially_completed", "derived", "completion", "bool",
                "1 if work_status text indicates completion AND 'partial'", "monitoring", "Execution-stage flag"),
    FeatureSpec("is_sanctioned_only", "derived", "completion", "bool",
                "1 if in_sanctioned and neither of the above applies (0 for a never-sanctioned work)", "monitoring",
                "Execution-stage flag"),
    FeatureSpec("completion_date", "works_master", "completion", "date", "Completion date, as recorded", "retrospective",
                "Only knowable after completion - see Leakage risks"),
    FeatureSpec("completed_amount_disbursed", "works_master", "completion", "decimal",
                "Amount Disbursed from the Completed source, as recorded", "retrospective",
                "Only knowable after completion - see Leakage risks"),

    # --------------------------------------------------------------------- Group 6: text preparation
    FeatureSpec("cleaned_work_description", "derived", "text", "text",
                "work_description with whitespace collapsed and placeholder tokens (N/A etc.) blanked; case and "
                "wording otherwise untouched", "monitoring", "Input for a future text-similarity feature (not built here)"),
    FeatureSpec("description_length_chars", "derived", "text", "int", "len(cleaned_work_description)", "monitoring",
                "Cheap proxy for description detail/completeness"),
    FeatureSpec("description_token_count", "derived", "text", "int",
                "Whitespace-separated token count of cleaned_work_description", "monitoring",
                "Cheap proxy for description detail/completeness"),

    # ------------------------------------------------------------------- Group 7: peer-group metadata
    *_peer_specs(),
)

FEATURE_BY_NAME: dict[str, FeatureSpec] = {f.name: f for f in FEATURES}
OUTPUT_COLUMNS: tuple[str, ...] = tuple(f.name for f in FEATURES)

assert len(OUTPUT_COLUMNS) == len(set(OUTPUT_COLUMNS)), "duplicate feature name in the registry"
