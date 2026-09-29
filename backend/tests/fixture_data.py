"""A tiny, fully deterministic stand-in for ``data/processed`` used by the API tests.

It mimics the real files where it matters for the backend:
* column ORDER is shuffled (seeded) and unknown extra columns are added, because the
  anomaly engine is free to reorder and extend its output;
* the features file has a UTF-8 BOM and Windows line endings, like a file saved on Windows;
* blank cells stand for "no expenditure", and one row carries ``nan`` / ``inf`` text that
  must never reach the JSON response.

The universe is 14 works. Everything the tests assert about it is derived from the tables
below, so read them first.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SNAPSHOT = date(2026, 9, 25)

# n, state, constituency, mp_name, category, status, in_completed,
# sanction, disbursed (None = no expenditure), payments, vendors, duplicate_ratio, success_util,
# days_since_sanction, days_since_last_payment
WORK_SPECS = [
    (1, "Gujarat", "KHEDA", "Mp One", "Normal/Others", "Work Completed", 1, 1_000_000, 1_000_000, 3, 1, 0.0, 1.0, 400, 120),
    (2, "Gujarat", "KHEDA", "Mp One", "Normal/Others", "Work partially Completed", 0, 2_000_000, 1_500_000, 5, 2, 0.2, 0.75, 500, 60),
    (3, "Gujarat", "ANAND", "Mp Two", "Repair and Renovation", "Physical Inspection", 1, 500_000, 500_000, 1, 1, 0.0, 1.0, 300, 250),
    (4, "Gujarat", "ANAND", "Mp Two", "Normal/Others", "Sanction", 0, 800_000, None, 0, 0, None, None, 200, None),
    (5, "Gujarat", "KHEDA", "Mp One", "Normal/Others", "Vendor Identification", 0, 300_000, 300_000, 2, 1, 0.5, 1.0, 600, 30),
    (6, "Bihar", "PATNA SAHIB", "Mp Three", "Normal/Others", "Work Completed", 1, 4_000_000, 3_900_000, 8, 3, 0.125, 0.975, 700, 15),
    (7, "Bihar", "PATNA SAHIB", "Mp Three", "Normal/Others", "Physical Inspection", 0, 1_200_000, 600_000, 2, 1, 0.0, 0.5, 450, 300),
    (8, "Bihar", "GAYA", "Mp Four", "Repair and Renovation", "Work Completed", 1, 900_000, 900_000, 4, 2, 0.25, 1.0, 380, 90),
    (9, "Bihar", "GAYA", "Mp Four", "Normal/Others", "Time Estimation", 0, 250_000, None, 0, 0, None, None, 90, None),
    (10, "Bihar", "GAYA", "Mp Four", "Normal/Others", "Work partially Completed", 0, 3_000_000, 2_000_000, 6, 2, 0.0, 0.66, 520, 45),
    (11, "Kerala", "KOCHI", "Mp Five", "Normal/Others", "Work Completed", 1, 700_000, 700_000, 2, 1, 0.0, 1.0, 410, 200),
    (12, "Kerala", "KOCHI", "Mp Five", "Normal/Others", "Physical Inspection", 0, 1_500_000, 1_500_000, 10, 4, 0.4, 1.0, 550, 20),
    (13, "Kerala", "KOCHI", "Mp Five", "Repair and Renovation", "Sanction", 0, 400_000, None, 0, 0, None, None, 100, None),
    (14, "Kerala", "KOCHI", "Mp Five", "Normal/Others", "Work Completed", 1, 600_000, 600_000, 1, 1, 0.0, 1.0, 350, 340),
]

MP_CODE = {"Gujarat": "MP005", "Bihar": "MP018", "Kerala": "MP009"}

# Derived facts the tests rely on.
TOTAL_WORKS = 14
COMPLETED_WORKS = 6            # in_completed = 1: works 1, 3, 6, 8, 11, 14
# NB work 3 is in the Completed source but its status text is still "Physical Inspection" - as in the
# real data (35,475 in the source vs 3,769 with status "Work Completed"), so in_completed != status.
WORKS_WITH_EXPENDITURE = 11    # payment count > 0
TOTAL_TRANSACTIONS = 44        # 3+5+1+2+8+2+4+6+2+10+1
STATE_COUNTS = {"Bihar": 5, "Gujarat": 5, "Kerala": 4}
CATEGORY_COUNTS = {"Normal/Others": 11, "Repair and Renovation": 3}

# work n, review_priority_score, label, signal_count  (listed highest score first)
ANOMALY_SPECS = [
    (6, 95.5, "High", 3),
    (2, 88.0, "High", 3),
    (12, 72.5, "High", 2),
    (7, 65.0, "Medium", 2),
    (5, 55.5, "Medium", 1),
    (8, 40.0, "Medium", 1),
    (3, 33.3, "Low", 1),
    (11, 20.0, "Low", 0),
    (1, 12.5, "Low", 0),
    (14, 5.0, "Low", 0),
    (9, 2.0, "Low", 0),   # a work with no expenditure: its metric cells are blank
]
ANOMALY_ORDER = [n for n, *_ in ANOMALY_SPECS]      # default order: highest score first
REVIEW_CANDIDATES = 7                               # scores >= 25 (works 6,2,12,7,5,8,3);
                                                     # signal_count >= 1 gives the same 7 works here
UNSCORED_WORKS = (4, 10, 13)                        # in the features, absent from the anomaly file


def work_id(n: int) -> str:
    _, state = next((s[0], s[1]) for s in WORK_SPECS if s[0] == n)
    return f"WS/{MP_CODE[state]}/2025-2026/{100000 + n}"


def _spec(n: int) -> tuple:
    return next(s for s in WORK_SPECS if s[0] == n)


def _blank(value):
    return "" if value is None else value


def _write(path: Path, rows: list[dict], columns: list[str], seed: int, *, bom: bool = False) -> None:
    shuffled = list(columns)
    random.Random(seed).shuffle(shuffled)
    with open(path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=shuffled, extrasaction="ignore", lineterminator="\r\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _blank(row.get(key)) for key in shuffled})


# --------------------------------------------------------------------------- features
FEATURE_COLUMNS = [
    "work_id", "state", "constituency", "mp_name", "ida", "work_category", "work_status",
    "id_mp_code", "id_financial_year",
    "in_recommended", "in_sanctioned", "in_completed", "is_completed", "is_partially_completed",
    "is_sanctioned_only", "single_payment_work",
    "recommended_amount", "sanction_amount", "total_disbursed_all_rows", "success_amount",
    "in_progress_amount", "exact_duplicate_amount", "deduplicated_disbursed_amount",
    "remaining_sanction_amount", "completed_amount_disbursed",
    "success_utilization_ratio", "gross_utilization_ratio", "deduplicated_utilization_ratio",
    "in_progress_ratio", "duplicate_amount_ratio", "duplicate_ratio",
    "recommendation_to_sanction_days", "sanction_to_first_payment_days", "sanction_to_last_payment_days",
    "sanction_to_completion_days", "days_since_sanction", "days_since_last_payment",
    "payment_span_days", "average_payment_interval_days", "completion_date",
    "payment_count_all_rows", "deduplicated_payment_count", "vendor_count", "duplicate_record_count",
    "cleaned_work_description",
    "peer_count_by_work_category", "peer_median_sanction_amount_by_work_category",
    "peer_median_disbursed_amount_by_work_category", "peer_median_payment_count_by_work_category",
    "peer_group_sufficient_by_work_category",
    "peer_count_by_state", "peer_median_sanction_amount_by_state",
    "peer_median_disbursed_amount_by_state", "peer_median_payment_count_by_state",
    "peer_group_sufficient_by_state",
    "peer_count_by_state_work_category", "peer_median_sanction_amount_by_state_work_category",
    "peer_median_disbursed_amount_by_state_work_category",
    "peer_median_payment_count_by_state_work_category", "peer_group_sufficient_by_state_work_category",
    # not used by the API - proves that unknown columns are tolerated
    "text_similarity_note", "zz_future_feature",
]


def _sanction_date(dss: int) -> date:
    return SNAPSHOT - timedelta(days=dss)


def feature_rows() -> list[dict]:
    group_size = {"category": {}, "state": {}, "both": {}}
    for _, state, _, _, category, *_ in WORK_SPECS:
        group_size["category"][category] = group_size["category"].get(category, 0) + 1
        group_size["state"][state] = group_size["state"].get(state, 0) + 1
        group_size["both"][(state, category)] = group_size["both"].get((state, category), 0) + 1

    rows = []
    for (n, state, constituency, mp, category, status, completed, sanction, disbursed,
         payments, vendors, dup, util, dss, dslp) in WORK_SPECS:
        paid = payments > 0
        exact_dup = round(disbursed * dup) if paid else None
        std = dss - 60 if completed else None  # sanction -> completion
        row = {
            "work_id": work_id(n), "state": state, "constituency": constituency, "mp_name": mp,
            "ida": f"{constituency}(DISTRICT COLLECTOR {constituency}_IDA)",
            "work_category": category, "work_status": status,
            "id_mp_code": MP_CODE[state], "id_financial_year": "2025-2026",
            "in_recommended": 1, "in_sanctioned": 1, "in_completed": completed,
            "is_completed": int(status == "Work Completed"),
            "is_partially_completed": int(status == "Work partially Completed"),
            "is_sanctioned_only": int(not paid), "single_payment_work": int(payments == 1),
            "recommended_amount": sanction, "sanction_amount": sanction,
            "total_disbursed_all_rows": disbursed, "success_amount": disbursed,
            "in_progress_amount": 0 if paid else None, "exact_duplicate_amount": exact_dup,
            "deduplicated_disbursed_amount": disbursed - exact_dup if paid else None,
            "remaining_sanction_amount": sanction - disbursed if paid else None,
            "completed_amount_disbursed": disbursed if completed else None,
            "success_utilization_ratio": util,
            "gross_utilization_ratio": round(disbursed / sanction, 6) if paid else None,
            "deduplicated_utilization_ratio": round((disbursed - exact_dup) / sanction, 6) if paid else None,
            "in_progress_ratio": 0.0 if paid else None,
            "duplicate_amount_ratio": dup, "duplicate_ratio": dup,
            "recommendation_to_sanction_days": 30,
            "sanction_to_first_payment_days": 30 if paid else None,
            "sanction_to_last_payment_days": dss - dslp if paid else None,
            "sanction_to_completion_days": std,
            "days_since_sanction": dss, "days_since_last_payment": dslp,
            "payment_span_days": max(dss - dslp - 30, 0) if paid else None,
            "average_payment_interval_days": 12.5 if payments > 1 else None,
            "completion_date": (_sanction_date(dss) + timedelta(days=std)).isoformat() if completed else None,
            # like the real file: blank (not 0) for works without any payment row
            "payment_count_all_rows": payments if paid else None,
            "deduplicated_payment_count": round(payments * (1 - dup)) if paid else None,
            "vendor_count": vendors if paid else None,
            "duplicate_record_count": payments - round(payments * (1 - dup)) if paid else None,
            "cleaned_work_description": f"Construction work number {n} at {constituency}",
            # every group in this mini universe is below the real 20-works threshold
            "peer_count_by_work_category": group_size["category"][category],
            "peer_group_sufficient_by_work_category": 0,
            "peer_count_by_state": group_size["state"][state],
            "peer_group_sufficient_by_state": 0,
            "peer_count_by_state_work_category": group_size["both"][(state, category)],
            "peer_group_sufficient_by_state_work_category": 0,
            "text_similarity_note": "ignored", "zz_future_feature": "1",
        }
        if n == 6:  # one work that sits in a sufficiently large peer group
            row.update({
                "peer_count_by_state_work_category": 22,
                "peer_group_sufficient_by_state_work_category": 1,
                "peer_median_sanction_amount_by_state_work_category": 900000.0,
                "peer_median_disbursed_amount_by_state_work_category": 850000.0,
                "peer_median_payment_count_by_state_work_category": 3.0,
            })
        if n == 9:
            row["average_payment_interval_days"] = "nan"   # must become null, never NaN in JSON
        if n == 4:
            row["gross_utilization_ratio"] = "inf"          # ditto
        rows.append(row)
    return rows


def write_features(path: Path) -> None:
    _write(path, feature_rows(), FEATURE_COLUMNS, seed=11, bom=True)


# --------------------------------------------------------------------------- works_master
def write_works_master(path: Path) -> None:
    rows = []
    for spec in WORK_SPECS:
        n, dss = spec[0], spec[13]
        sanction = _sanction_date(dss)
        rows.append({
            "work_id": work_id(n),
            "work": f"Type of work {n}",
            "recommended_date": (sanction - timedelta(days=30)).isoformat(),
            "sanction_date": sanction.isoformat(),
            "mp_name": spec[3],
            "sanction_amount": spec[7],
            "data_quality_note": "ignored",
        })
    _write(path, rows, list(rows[0]), seed=5)


# --------------------------------------------------------------------------- expenditure_by_work
def write_expenditure(path: Path) -> None:
    rows = []
    for spec in WORK_SPECS:
        n, payments, dss, dslp = spec[0], spec[9], spec[13], spec[14]
        if not payments:
            continue
        rows.append({
            "work_id": work_id(n),
            "first_expenditure_date": (_sanction_date(dss) + timedelta(days=30)).isoformat(),
            "last_expenditure_date": (SNAPSHOT - timedelta(days=dslp)).isoformat(),
            "expenditure_row_count": payments,
        })
    _write(path, rows, list(rows[0]), seed=9)


# --------------------------------------------------------------------------- anomalies
ANOMALY_COLUMNS = [
    "work_id", "state", "constituency", "mp_name", "ida", "work_category", "work_status",
    "sanction_amount", "total_disbursed_all_rows", "deduplicated_disbursed_amount",
    "success_utilization_ratio", "days_since_sanction", "days_since_last_payment", "payment_count",
    "vendor_count", "duplicate_ratio", "review_priority_score", "review_priority_label", "signal_count",
    "top_signal_1", "top_signal_2", "top_signal_3", "explanation_text",
    # additional engine columns the backend must tolerate
    "engine_version", "signal_detail",
]


def anomaly_rows() -> list[dict]:
    features = {row["work_id"]: row for row in feature_rows()}
    rows = []
    for n, score, label, signals in ANOMALY_SPECS:
        f = features[work_id(n)]
        rows.append({
            "work_id": f["work_id"], "state": f["state"], "constituency": f["constituency"],
            "mp_name": f["mp_name"], "ida": f["ida"], "work_category": f["work_category"],
            "work_status": f["work_status"], "sanction_amount": f["sanction_amount"],
            "total_disbursed_all_rows": f["total_disbursed_all_rows"],
            "deduplicated_disbursed_amount": f["deduplicated_disbursed_amount"],
            "success_utilization_ratio": f["success_utilization_ratio"],
            "days_since_sanction": f["days_since_sanction"],
            "days_since_last_payment": f["days_since_last_payment"],
            "payment_count": f["payment_count_all_rows"], "vendor_count": f["vendor_count"],
            "duplicate_ratio": f["duplicate_ratio"],
            "review_priority_score": score, "review_priority_label": label, "signal_count": signals,
            "top_signal_1": f"signal A for work {n}" if signals >= 1 else None,
            "top_signal_2": f"signal B for work {n}" if signals >= 2 else None,
            "top_signal_3": f"signal C for work {n}" if signals >= 3 else None,
            "explanation_text": f"Work {n} shows {signals} unusual pattern(s) that may merit review.",
            "engine_version": "test-1", "signal_detail": f"detail-{n}",
        })
    return rows


def write_anomalies(path: Path, columns: list[str] | None = None) -> None:
    _write(path, anomaly_rows(), columns or ANOMALY_COLUMNS, seed=3)


def build_processed_dir(directory: Path, *, anomalies: bool = True) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    write_features(directory / "work_features_v0.csv")
    write_works_master(directory / "works_master.csv")
    write_expenditure(directory / "expenditure_by_work.csv")
    if anomalies:
        write_anomalies(directory / "work_anomalies_v1.csv")
    return directory
