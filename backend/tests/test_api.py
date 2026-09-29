"""API tests for the MPLADS dashboard backend.

No external service and no real data are needed: everything runs against the small
deterministic dataset in ``fixture_data.py`` (read its tables first, the expected numbers
below come straight from them). The tests skip cleanly if FastAPI/httpx are not installed.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import os
import re
from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402

from . import fixture_data as fx  # noqa: E402
from .conftest import DEMO_DIR  # noqa: E402

wid = fx.work_id
ORDER = [wid(n) for n in fx.ANOMALY_ORDER]  # default order: highest review priority first

CONTRACT_FIELDS = {
    "work_id", "state", "constituency", "mp_name", "ida", "work_category", "work_status",
    "sanction_amount", "total_disbursed_all_rows", "deduplicated_disbursed_amount",
    "success_utilization_ratio", "days_since_sanction", "days_since_last_payment", "payment_count",
    "vendor_count", "duplicate_ratio", "review_priority_score", "review_priority_label",
    "signal_count", "top_signal_1", "top_signal_2", "top_signal_3", "explanation_text",
}


# --------------------------------------------------------------------------- helpers
def ids(response) -> list[str]:
    return [item["work_id"] for item in response.json()["items"]]


def get_ids(client, query: str = "") -> list[str]:
    response = client.get("/api/v1/anomalies?page_size=200" + ("&" + query if query else ""))
    assert response.status_code == 200, response.text
    return ids(response)


def strict_json(response) -> dict:
    """Parse the body refusing NaN / Infinity, which are not valid JSON."""

    def refuse(token):
        raise AssertionError(f"non-finite number {token} in JSON body")

    return json.loads(response.text, parse_constant=refuse)


def make_client(processed_dir, **overrides) -> TestClient:
    # demo_dir points at a folder that does not exist unless a test asks for the demo fixture
    base = Settings(processed_dir=processed_dir, demo_dir=processed_dir / "no-demo-here")
    return TestClient(create_app(dataclasses.replace(base, **overrides)))


@pytest.fixture()
def fresh_dir(tmp_path):
    """A private copy of the full dataset that a test may modify."""
    return fx.build_processed_dir(tmp_path / "processed")


@pytest.fixture()
def no_anomaly_dir(tmp_path):
    return fx.build_processed_dir(tmp_path / "processed", anomalies=False)


def bump_mtime(path, seconds: int = 10) -> None:
    stamp = path.stat().st_mtime_ns + seconds * 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


# =========================================================================== 1. /health
def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# =========================================================================== 2. /summary
def test_summary_is_computed_from_the_data(client):
    body = client.get("/api/v1/summary").json()
    assert body["total_works"] == fx.TOTAL_WORKS
    assert body["sanctioned_works"] == fx.TOTAL_WORKS
    assert body["completed_works"] == fx.COMPLETED_WORKS   # in_completed = 1, NOT status == "Work Completed"
    status_completed = next(i["count"] for i in body["work_status_counts"] if i["status"] == "Work Completed")
    assert status_completed == 5 and body["completed_works"] != status_completed
    assert body["works_with_expenditure"] == fx.WORKS_WITH_EXPENDITURE
    assert body["total_expenditure_transactions"] == fx.TOTAL_TRANSACTIONS
    assert body["review_candidates"] == fx.REVIEW_CANDIDATES
    assert body["snapshot_date"] == "2026-09-25"
    assert datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00")).year >= 2026
    assert body["warnings"] == []


def test_summary_details(client):
    body = client.get("/api/v1/summary").json()
    # labels are ordered from the highest to the lowest mean score, whatever their names are
    assert body["review_priority_label_counts"] == [
        {"label": "High", "count": 3},
        {"label": "Medium", "count": 3},
        {"label": "Low", "count": 5},
    ]
    assert sum(item["count"] for item in body["work_status_counts"]) == fx.TOTAL_WORKS
    assert body["anomaly_data"] == {
        "available": True,
        "kind": "engine",
        "file": "work_anomalies_v1.csv",
        "records": 11,
        "review_candidate_rule": "review_priority_score >= 25",
        "missing_columns": [],
        "message": None,
    }


def test_summary_follows_the_files_not_constants(fresh_dir):
    """Cut the features file down to 5 works: every figure must follow."""
    path = fresh_dir / "work_features_v0.csv"
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows[:6])  # header + 5 works

    body = make_client(fresh_dir).get("/api/v1/summary").json()
    assert body["total_works"] == 5
    assert body["sanctioned_works"] == 5


def test_summary_review_candidate_rule_is_configurable(fresh_dir):
    body = make_client(fresh_dir, review_candidate_labels=("high",)).get("/api/v1/summary").json()
    assert body["review_candidates"] == 3
    assert body["anomaly_data"]["review_candidate_rule"] == "review_priority_label in [high]"

    body = make_client(fresh_dir, review_candidate_min_signals=2).get("/api/v1/summary").json()
    assert body["review_candidates"] == 4  # works 6, 2, 12, 7
    assert body["anomaly_data"]["review_candidate_rule"] == "signal_count >= 2"

    body = make_client(fresh_dir, review_candidate_min_score=50.0).get("/api/v1/summary").json()
    assert body["review_candidates"] == 5  # works 6, 2, 12, 7, 5 (scores 95.5..55.5)
    assert body["anomaly_data"]["review_candidate_rule"] == "review_priority_score >= 50"


def test_review_candidates_default_to_score_ge_25(client):
    """The default rule, with no overrides: review_priority_score >= 25.

    This matches every label except the real anomaly engine's "Normal Monitoring" -
    i.e. "Low/Medium/High Review Priority" all count, "Normal Monitoring" does not.
    """
    body = client.get("/api/v1/summary").json()
    assert body["anomaly_data"]["review_candidate_rule"] == "review_priority_score >= 25"
    assert body["review_candidates"] == fx.REVIEW_CANDIDATES

    states = {item["state"]: item["review_candidate_count"] for item in client.get("/api/v1/states").json()["items"]}
    assert states == {"Bihar": 3, "Gujarat": 3, "Kerala": 1}
    categories = {
        item["work_category"]: item["review_candidate_count"]
        for item in client.get("/api/v1/work-categories").json()["items"]
    }
    assert categories == {"Normal/Others": 5, "Repair and Renovation": 2}


def test_review_candidate_score_boundary(fresh_dir):
    """score 24.99 -> not a candidate; 25.00, 50.00, 75.00 -> candidates (default rule, no overrides)."""
    path = fresh_dir / "work_anomalies_v1.csv"
    boundary = [
        (wid(1), 24.99, "Normal Monitoring"),
        (wid(2), 25.00, "Low Review Priority"),
        (wid(3), 50.00, "Medium Review Priority"),
        (wid(4), 75.00, "High Review Priority"),
    ]
    fieldnames = ["work_id", "review_priority_score", "review_priority_label", "signal_count", "explanation_text"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for work_id, score, label in boundary:
            writer.writerow(
                {"work_id": work_id, "review_priority_score": score, "review_priority_label": label,
                 "signal_count": 0, "explanation_text": "boundary test row"}
            )

    client = make_client(fresh_dir)  # default settings: no overrides
    assert client.get("/api/v1/summary").json()["anomaly_data"]["review_candidate_rule"] == "review_priority_score >= 25"

    scores = {item["work_id"]: item["review_priority_score"] for item in client.get("/api/v1/anomalies").json()["items"]}
    assert scores == {work_id: score for work_id, score, _ in boundary}

    # 1 (24.99) is excluded; 2, 3, 4 (25.00, 50.00, 75.00) are included - all four works are Gujarat
    summary = client.get("/api/v1/summary").json()
    assert summary["review_candidates"] == 3
    states = {item["state"]: item["review_candidate_count"] for item in client.get("/api/v1/states").json()["items"]}
    assert states["Gujarat"] == 3

    # work_category breakdown: works 1, 2, 4 = Normal/Others (candidates: 2, 4 -> 2); work 3 = Repair and Renovation (candidate -> 1)
    categories = {
        item["work_category"]: item["review_candidate_count"]
        for item in client.get("/api/v1/work-categories").json()["items"]
    }
    assert categories == {"Normal/Others": 2, "Repair and Renovation": 1}


def test_snapshot_date_can_be_derived_without_works_master(fresh_dir):
    (fresh_dir / "works_master.csv").unlink()
    body = make_client(fresh_dir).get("/api/v1/summary").json()
    assert body["snapshot_date"] == "2026-09-25"  # recovered from completion_date / durations
    assert any("works_master.csv" in warning for warning in body["warnings"])


def test_snapshot_date_override(fresh_dir):
    body = make_client(fresh_dir, snapshot_date=date(2027, 1, 2)).get("/api/v1/summary").json()
    assert body["snapshot_date"] == "2027-01-02"


# =========================================================================== 3. pagination
def test_default_page(client):
    response = client.get("/api/v1/anomalies")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "page", "page_size", "total", "pages", "data_kind"}
    assert (body["page"], body["page_size"], body["total"], body["pages"]) == (1, 25, 11, 1)
    assert body["data_kind"] == "engine"
    assert ids(response) == ORDER


def test_pagination_walks_all_rows_once(client):
    pages = []
    for page in (1, 2, 3):
        response = client.get(f"/api/v1/anomalies?page={page}&page_size=4")
        body = response.json()
        assert (body["page"], body["page_size"], body["total"], body["pages"]) == (page, 4, 11, 3)
        pages.append(ids(response))
    assert [len(p) for p in pages] == [4, 4, 3]
    assert sum(pages, []) == ORDER  # no gaps, no overlaps, same order


def test_page_beyond_the_end_is_empty_not_an_error(client):
    body = client.get("/api/v1/anomalies?page=4&page_size=4").json()
    assert body["items"] == []
    assert (body["total"], body["pages"], body["page"]) == (11, 3, 4)


@pytest.mark.parametrize(
    "query",
    [
        "page=0", "page=-1", "page=abc", "page_size=0", "page_size=201", "page_size=abc",
        "min_score=abc", "min_score=nan", "max_score=inf",
    ],
)
def test_invalid_query_parameters_are_422(client, query):
    response = client.get(f"/api/v1/anomalies?{query}")
    assert response.status_code == 422, response.text
    body = response.json()
    assert isinstance(body["detail"], str)                       # always a string, never FastAPI's list
    assert body["errors"][0]["field"] == query.split("=")[0]     # the offending parameter is named
    assert body["errors"][0]["message"]


def test_min_score_above_max_score_is_422(client):
    response = client.get("/api/v1/anomalies?min_score=80&max_score=20")
    assert response.status_code == 422
    assert "min_score" in response.json()["detail"]


# =========================================================================== 4. filtering
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("label=High", [6, 2, 12]),
        ("label=high", [6, 2, 12]),                      # case-insensitive
        ("label=High&label=Medium", [6, 2, 12, 7, 5, 8]),  # repeated values are OR-ed
        ("state=Gujarat", [2, 5, 3, 1]),
        ("state=bihar", [6, 7, 8, 9]),
        ("work_category=Repair and Renovation", [8, 3]),
        ("min_score=50", [6, 2, 12, 7, 5]),
        ("min_score=65", [6, 2, 12, 7]),                 # bounds are inclusive: 65.0 is kept
        ("min_score=65&max_score=65", [7]),
        ("max_score=40", [8, 3, 11, 1, 14, 9]),         # bounds are inclusive
        ("min_score=30&max_score=70", [7, 5, 8, 3]),
        ("state=Bihar&min_score=60", [6, 7]),            # different filters are AND-ed
        ("state=Kerala&label=High", [12]),
        ("label=Low&work_category=Repair and Renovation", [3]),
    ],
)
def test_filters(client, query, expected):
    assert get_ids(client, query) == [wid(n) for n in expected]


def test_filter_matching_nothing_is_an_empty_page(client):
    for query in ("state=Atlantis", "label=Severe", "work_category=Nope", "min_score=1000"):
        body = client.get(f"/api/v1/anomalies?{query}").json()
        assert (body["items"], body["total"], body["pages"]) == ([], 0, 0), query


def test_total_and_pages_reflect_the_filter(client):
    body = client.get("/api/v1/anomalies?state=Gujarat&page_size=3").json()
    assert (body["total"], body["pages"], len(body["items"])) == (4, 2, 3)


# =========================================================================== 5. sorting
@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        (None, fx.ANOMALY_ORDER),                                            # default: score, high to low
        ("-review_priority_score", fx.ANOMALY_ORDER),
        ("review_priority_score", list(reversed(fx.ANOMALY_ORDER))),
        ("review_priority_score:asc", list(reversed(fx.ANOMALY_ORDER))),
        ("-sanction_amount", [6, 2, 12, 7, 1, 8, 11, 14, 3, 5, 9]),         # 4.0M ... 0.25M
        ("state,-review_priority_score", [6, 7, 8, 9, 2, 5, 3, 1, 12, 11, 14]),
        ("days_since_last_payment", [6, 12, 5, 2, 8, 1, 11, 3, 7, 14, 9]),   # missing value last
        ("-days_since_last_payment", [14, 7, 3, 11, 1, 8, 2, 5, 12, 6, 9]),  # ... also when descending
    ],
)
def test_sorting(client, sort, expected):
    query = f"sort={sort}" if sort else ""
    assert get_ids(client, query) == [wid(n) for n in expected]


def test_sort_applies_before_pagination(client):
    first_page = ids(client.get("/api/v1/anomalies?sort=review_priority_score&page_size=3"))
    assert first_page == [wid(9), wid(14), wid(1)]


def test_sort_combines_with_filters(client):
    assert get_ids(client, "state=Gujarat&sort=-sanction_amount") == [wid(2), wid(1), wid(3), wid(5)]


@pytest.mark.parametrize(
    "sort", ["nonsense", "state:sideways", "-state:asc", "state,,", "a,b,c,d", "explanation_text"]
)
def test_invalid_sort_is_422(client, sort):
    response = client.get("/api/v1/anomalies", params={"sort": sort})
    assert response.status_code == 422, response.text
    assert "sort" in response.json()["detail"].lower()


# =========================================================================== 6. unknown / invalid work ids
UNKNOWN = "WS/MP005/2025-2026/999999"


@pytest.mark.parametrize("prefix", ["/api/v1/works/", "/api/v1/anomalies/"])
def test_nonexistent_work_is_404(client, prefix):
    response = client.get(prefix + UNKNOWN)
    assert response.status_code == 404
    assert UNKNOWN in response.json()["detail"]


@pytest.mark.parametrize("prefix", ["/api/v1/works/", "/api/v1/anomalies/"])
@pytest.mark.parametrize("bad", ["12345", "not-a-work-id", "WS/MP005/2025/1", "WS/MP005/2025-2026/"])
def test_malformed_work_id_is_422(client, prefix, bad):
    response = client.get(prefix + bad)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert "work_id" in response.json()["detail"]


def test_work_ids_with_slashes_can_be_url_encoded_and_are_case_insensitive(client):
    target = wid(6)
    plain = client.get(f"/api/v1/works/{target}")
    assert plain.status_code == 200
    assert client.get("/api/v1/works/" + target.replace("/", "%2F")).json() == plain.json()
    assert client.get("/api/v1/works/" + target.lower()).json() == plain.json()


def test_anomaly_endpoint_distinguishes_unscored_from_unknown(client):
    response = client.get(f"/api/v1/anomalies/{wid(4)}")  # in the features, not in the anomaly file
    assert response.status_code == 404
    assert "No review record" in response.json()["detail"]


# =========================================================================== 7. work detail
def test_work_detail_valid_work(client):
    response = client.get(f"/api/v1/works/{wid(6)}")
    assert response.status_code == 200
    body = strict_json(response)

    assert body["work_id"] == wid(6)
    assert body["snapshot_date"] == "2026-09-25"
    assert body["identity"] == {
        "work_id": wid(6),
        "work": "Type of work 6",
        "description": "Construction work number 6 at PATNA SAHIB",
        "state": "Bihar",
        "constituency": "PATNA SAHIB",
        "mp_name": "Mp Three",
        "ida": "PATNA SAHIB(DISTRICT COLLECTOR PATNA SAHIB_IDA)",
        "work_category": "Normal/Others",
        "work_status": "Work Completed",
        "mp_code": "MP018",
        "financial_year": "2025-2026",
    }
    assert body["flags"]["in_completed"] is True
    assert body["flags"]["is_partially_completed"] is False

    amounts = body["amounts"]
    assert amounts["recommended_amount"] == 4_000_000
    assert amounts["sanction_amount"] == 4_000_000
    assert amounts["total_disbursed_all_rows"] == 3_900_000
    assert amounts["exact_duplicate_amount"] == 487_500
    assert amounts["deduplicated_disbursed_amount"] == 3_412_500
    assert amounts["remaining_sanction_amount"] == 100_000
    assert body["utilization"]["success_utilization_ratio"] == 0.975

    payments = body["payments"]
    assert payments["payment_count"] == 8
    assert payments["vendor_count"] == 3
    assert payments["duplicate_ratio"] == 0.125
    assert payments["duplicate_record_count"] == 1
    assert payments["single_payment_work"] is False

    sanction = fx.SNAPSHOT - timedelta(days=700)
    timeline = body["timeline"]
    assert timeline["sanction_date"] == sanction.isoformat()
    assert timeline["recommended_date"] == (sanction - timedelta(days=30)).isoformat()
    assert timeline["first_payment_date"] == (sanction + timedelta(days=30)).isoformat()   # expenditure_by_work.csv
    assert timeline["last_payment_date"] == (fx.SNAPSHOT - timedelta(days=15)).isoformat()
    assert timeline["completion_date"] == (sanction + timedelta(days=640)).isoformat()
    assert (timeline["days_since_sanction"], timeline["days_since_last_payment"]) == (700, 15)

    # peers: a small group has no medians; the one sufficient group carries them
    assert body["peers"]["work_category"] == {
        "peer_count": 11, "sufficient": False,
        "median_sanction_amount": None, "median_disbursed_amount": None, "median_payment_count": None,
    }
    assert body["peers"]["state_work_category"] == {
        "peer_count": 22, "sufficient": True,
        "median_sanction_amount": 900000.0, "median_disbursed_amount": 850000.0, "median_payment_count": 3.0,
    }

    assert body["review_status"] == "scored"
    assert body["review"] == {
        "review_priority_score": 95.5,
        "review_priority_label": "High",
        "signal_count": 3,
        "top_signal_1": "signal A for work 6",
        "top_signal_2": "signal B for work 6",
        "top_signal_3": "signal C for work 6",
        "explanation_text": "Work 6 shows 3 unusual pattern(s) that may merit review.",
        "extra": {"engine_version": "test-1", "signal_detail": "detail-6"},
        "data_kind": "engine",
    }


def test_work_without_expenditure_or_review_record(client):
    body = strict_json(client.get(f"/api/v1/works/{wid(4)}"))  # blank cells + an 'inf' text value
    assert body["identity"]["work_status"] == "Sanction"
    assert body["amounts"]["sanction_amount"] == 800_000
    assert body["amounts"]["total_disbursed_all_rows"] is None
    assert body["utilization"]["gross_utilization_ratio"] is None  # 'inf' never leaks into the JSON
    assert body["payments"]["payment_count"] is None   # blank upstream, not 0
    assert body["timeline"]["first_payment_date"] is None
    assert body["review_status"] == "not_scored"
    assert body["review"] is None


def test_nan_text_becomes_null(client):
    body = strict_json(client.get(f"/api/v1/works/{wid(9)}"))
    assert body["payments"]["average_payment_interval_days"] is None
    assert body["review_status"] == "scored"  # its anomaly row has blank metric cells
    assert body["review"]["review_priority_score"] == 2.0


def test_anomaly_detail_returns_one_record_with_extra_columns(client):
    response = client.get(f"/api/v1/anomalies/{wid(7)}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == CONTRACT_FIELDS | {"extra", "data_kind"}
    assert body["work_id"] == wid(7)
    assert body["review_priority_score"] == 65.0
    assert body["review_priority_label"] == "Medium"
    assert body["top_signal_3"] is None
    assert body["extra"] == {"engine_version": "test-1", "signal_detail": "detail-7"}


# =========================================================================== 8. states / 9. categories
def test_states(client):
    body = client.get("/api/v1/states").json()
    assert body == {
        "items": [
            {"state": "Bihar", "work_count": 5, "review_candidate_count": 3},
            {"state": "Gujarat", "work_count": 5, "review_candidate_count": 3},
            {"state": "Kerala", "work_count": 4, "review_candidate_count": 1},
        ],
        "total": 3,
    }
    assert sum(item["work_count"] for item in body["items"]) == fx.TOTAL_WORKS


def test_work_categories(client):
    body = client.get("/api/v1/work-categories").json()
    assert body == {
        "items": [
            {"work_category": "Normal/Others", "work_count": 11, "review_candidate_count": 5},
            {"work_category": "Repair and Renovation", "work_count": 3, "review_candidate_count": 2},
        ],
        "total": 2,
    }


# =========================================================================== 10. deterministic structure
def test_response_structure_is_stable(client):
    endpoints = [
        "/health", "/api/v1/summary", "/api/v1/anomalies?page_size=5", f"/api/v1/anomalies/{wid(6)}",
        f"/api/v1/works/{wid(6)}", "/api/v1/states", "/api/v1/work-categories",
    ]
    for endpoint in endpoints:
        first, second = client.get(endpoint), client.get(endpoint)
        assert first.status_code == 200, endpoint
        assert first.json() == second.json(), endpoint  # same request -> same body, key for key
        assert first.text == second.text, endpoint       # ... and byte for byte


def test_top_level_keys(client):
    keys = lambda path: set(client.get(path).json())  # noqa: E731
    assert keys("/api/v1/summary") == {
        "total_works", "sanctioned_works", "completed_works", "works_with_expenditure",
        "total_expenditure_transactions", "review_candidates", "generated_at", "snapshot_date",
        "review_priority_label_counts", "work_status_counts", "anomaly_data", "warnings",
    }
    assert keys(f"/api/v1/works/{wid(6)}") == {
        "work_id", "snapshot_date", "identity", "flags", "amounts", "utilization", "payments",
        "timeline", "peers", "review_status", "review",
    }
    assert keys("/api/v1/states") == keys("/api/v1/work-categories") == {"items", "total"}


def test_list_items_carry_exactly_the_contract_columns(client):
    """Extra engine columns (engine_version, ...) never leak into list rows."""
    for item in client.get("/api/v1/anomalies").json()["items"]:
        assert set(item) == CONTRACT_FIELDS


def test_no_response_contains_non_finite_numbers(client):
    for n in range(1, 15):
        strict_json(client.get(f"/api/v1/works/{wid(n)}"))
    strict_json(client.get("/api/v1/anomalies?page_size=200"))
    strict_json(client.get("/api/v1/summary"))


# =========================================================================== anomaly file: tolerance
def test_anomaly_columns_may_be_reordered_and_extended(client):
    """The fixture shuffles the column order and adds engine_version / signal_detail."""
    item = client.get("/api/v1/anomalies").json()["items"][0]
    assert item["work_id"] == wid(6) and item["review_priority_score"] == 95.5


def test_absent_identity_columns_are_filled_from_the_features(fresh_dir):
    fx.write_anomalies(
        fresh_dir / "work_anomalies_v1.csv",
        ["work_id", "review_priority_score", "review_priority_label", "signal_count", "explanation_text"],
    )
    client = make_client(fresh_dir)
    item = client.get(f"/api/v1/anomalies/{wid(6)}").json()
    assert (item["state"], item["work_category"], item["sanction_amount"]) == ("Bihar", "Normal/Others", 4_000_000)
    assert item["payment_count"] == 8
    assert item["review_priority_score"] == 95.5      # engine columns are never invented
    assert item["top_signal_1"] is None                # ... and neither are absent engine columns
    assert get_ids(client, "state=Kerala") == [wid(12), wid(11), wid(14)]  # filters work on filled values

    info = client.get("/api/v1/summary").json()
    assert "top_signal_1" in info["anomaly_data"]["missing_columns"]
    assert any("top_signal_1" in warning for warning in info["warnings"])
    missing_warning = next(w for w in info["warnings"] if "no column(s)" in w)
    assert "state" not in missing_warning  # filled from the features, so not reported as lost


def test_anomaly_file_without_a_score_column_is_reported_not_served(fresh_dir):
    fx.write_anomalies(fresh_dir / "work_anomalies_v1.csv", ["work_id", "state", "explanation_text"])
    client = make_client(fresh_dir)
    body = client.get("/api/v1/anomalies").json()
    assert (body["items"], body["total"], body["data_kind"]) == ([], 0, "none")
    summary = client.get("/api/v1/summary").json()
    assert summary["review_candidates"] is None
    assert "review_priority_score" in summary["anomaly_data"]["message"]


def test_duplicate_and_blank_work_ids_in_the_anomaly_file(fresh_dir):
    path = fresh_dir / "work_anomalies_v1.csv"
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        writer.writerow({**rows[0], "review_priority_score": "1.0"})   # duplicate id: first row wins
        writer.writerow({**rows[0], "work_id": "  "})                  # blank id: skipped
    client = make_client(fresh_dir)
    assert client.get("/api/v1/anomalies").json()["total"] == 11
    assert client.get(f"/api/v1/anomalies/{rows[0]['work_id']}").json()["review_priority_score"] == float(
        rows[0]["review_priority_score"]
    )
    assert len(client.get("/api/v1/summary").json()["warnings"]) == 2


# =========================================================================== anomaly file: missing / reload
def test_missing_anomaly_file_is_handled_gracefully(no_anomaly_dir):
    client = make_client(no_anomaly_dir)

    body = client.get("/api/v1/anomalies").json()
    assert body == {"items": [], "page": 1, "page_size": 25, "total": 0, "pages": 0, "data_kind": "none"}

    summary = client.get("/api/v1/summary").json()
    assert summary["review_candidates"] is None  # null, never a made-up 0
    assert summary["review_priority_label_counts"] == []
    assert summary["anomaly_data"]["available"] is False
    assert summary["anomaly_data"]["kind"] == "none"
    assert "work_anomalies_v1.csv" in summary["anomaly_data"]["message"]
    assert summary["total_works"] == fx.TOTAL_WORKS  # everything else still works

    work = client.get(f"/api/v1/works/{wid(6)}").json()
    assert (work["review_status"], work["review"]) == ("unavailable", None)

    assert client.get(f"/api/v1/anomalies/{wid(6)}").status_code == 503
    assert client.get(f"/api/v1/anomalies/{UNKNOWN}").status_code == 404

    states = client.get("/api/v1/states").json()["items"]
    assert all(item["review_candidate_count"] is None for item in states)


def test_demo_fixture_is_opt_in_and_clearly_marked(no_anomaly_dir):
    off = make_client(no_anomaly_dir, demo_dir=DEMO_DIR)
    assert off.get("/api/v1/anomalies").json()["data_kind"] == "none"  # never loaded by accident

    on = make_client(no_anomaly_dir, demo_dir=DEMO_DIR, use_demo_anomalies=True)
    body = on.get("/api/v1/anomalies?page_size=200").json()
    assert body["data_kind"] == "demo_fixture"
    assert body["total"] == 38
    assert all(item["explanation_text"].startswith("[DEMO FIXTURE") for item in body["items"])

    summary = on.get("/api/v1/summary").json()
    assert summary["anomaly_data"]["kind"] == "demo_fixture"
    assert any("DEMO FIXTURE" in warning for warning in summary["warnings"])

    detail = on.get(f"/api/v1/anomalies/{body['items'][0]['work_id']}").json()
    assert detail["data_kind"] == "demo_fixture"
    assert detail["extra"]["is_demo_fixture"] == "true"


def test_real_engine_output_always_wins_over_the_demo_fixture(fresh_dir):
    body = make_client(fresh_dir, demo_dir=DEMO_DIR, use_demo_anomalies=True).get("/api/v1/anomalies").json()
    assert (body["data_kind"], body["total"]) == ("engine", 11)


def test_anomaly_file_is_reloaded_when_it_changes(fresh_dir):
    client = make_client(fresh_dir)
    path = fresh_dir / "work_anomalies_v1.csv"
    assert client.get("/api/v1/anomalies").json()["total"] == 11

    # a new engine run with fewer rows shows up without restarting the server
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows[:3])
    bump_mtime(path)
    assert client.get("/api/v1/anomalies").json()["total"] == 3

    # a half-written / broken file keeps the last good data and says so
    path.write_text("work_id\nWS/MP005/2025-2026/100001\n", encoding="utf-8")
    bump_mtime(path, 20)
    body = client.get("/api/v1/anomalies").json()
    assert (body["total"], body["data_kind"]) == (3, "engine")
    assert "previous" in client.get("/api/v1/summary").json()["anomaly_data"]["message"]

    # the file disappears
    path.unlink()
    assert client.get("/api/v1/anomalies").json()["data_kind"] == "none"

    # ... and comes back
    fx.write_anomalies(path)
    assert client.get("/api/v1/anomalies").json()["total"] == 11


# =========================================================================== degraded: features missing
def test_missing_feature_file_gives_503_but_health_stays_up(tmp_path):
    only_anomalies = tmp_path / "processed"
    only_anomalies.mkdir()
    fx.write_anomalies(only_anomalies / "work_anomalies_v1.csv")
    client = make_client(only_anomalies)

    assert client.get("/health").json() == {"status": "ok"}
    for path in ("/api/v1/summary", "/api/v1/states", f"/api/v1/works/{wid(6)}"):
        response = client.get(path)
        assert response.status_code == 503, path
        assert "work_features_v0.csv" in response.json()["detail"]
    assert client.get("/api/v1/anomalies").json()["total"] == 11  # the anomaly list does not need it


def test_feature_file_without_a_required_column_is_a_clear_503(tmp_path):
    directory = tmp_path / "processed"
    directory.mkdir()
    (directory / "work_features_v0.csv").write_text("work_id,state\nWS/MP005/2025-2026/1,Gujarat\n")
    response = make_client(directory).get("/api/v1/summary")
    assert response.status_code == 503
    assert "missing required column" in response.json()["detail"]


# =========================================================================== CORS & settings
def test_cors_allows_local_frontends_only(client):
    for origin in ("http://localhost:5173", "http://127.0.0.1:3000", "http://localhost:8080"):
        response = client.get("/health", headers={"Origin": origin})
        assert response.headers["access-control-allow-origin"] == origin

    blocked = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in blocked.headers


def test_cors_preflight(client):
    response = client.options(
        "/api/v1/anomalies",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_extra_cors_origins_from_the_environment(processed_dir):
    settings = Settings.from_env(
        {"MPLADS_PROCESSED_DIR": str(processed_dir), "MPLADS_CORS_ORIGINS": "https://dashboard.example, https://b.example"}
    )
    client = TestClient(create_app(settings))
    ok = client.get("/health", headers={"Origin": "https://dashboard.example"})
    assert ok.headers["access-control-allow-origin"] == "https://dashboard.example"


def test_settings_from_environment(tmp_path):
    settings = Settings.from_env(
        {
            "MPLADS_PROCESSED_DIR": str(tmp_path),
            "MPLADS_USE_DEMO_ANOMALIES": "1",
            "MPLADS_SNAPSHOT_DATE": "2026-09-25",
            "MPLADS_REVIEW_CANDIDATE_LABELS": "High, Critical",
            "MPLADS_REVIEW_CANDIDATE_MIN_SCORE": "30",
            "MPLADS_REVIEW_CANDIDATE_MIN_SIGNALS": "2",
        }
    )
    assert settings.processed_dir == tmp_path
    assert settings.use_demo_anomalies is True
    assert settings.snapshot_date == date(2026, 9, 25)
    assert settings.review_candidate_labels == ("High", "Critical")
    assert settings.review_candidate_min_score == 30.0
    assert settings.review_candidate_min_signals == 2
    assert Settings.from_env({}).use_demo_anomalies is False

    # defaults: score >= 25 is active out of the box, min_signals is unset (not a default rule)
    defaults = Settings.from_env({})
    assert defaults.review_candidate_min_score == 25.0
    assert defaults.review_candidate_min_signals is None

    with pytest.raises(ValueError, match="MPLADS_SNAPSHOT_DATE"):
        Settings.from_env({"MPLADS_SNAPSHOT_DATE": "25/09/2026"})
    with pytest.raises(ValueError, match="MIN_SCORE"):
        Settings.from_env({"MPLADS_REVIEW_CANDIDATE_MIN_SCORE": "not-a-number"})
    with pytest.raises(ValueError, match="MIN_SIGNALS"):
        Settings.from_env({"MPLADS_REVIEW_CANDIDATE_MIN_SIGNALS": "many"})


def test_openapi_documents_the_endpoints(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/health", "/api/v1/summary", "/api/v1/anomalies", "/api/v1/anomalies/{work_id}",
        "/api/v1/works/{work_id}", "/api/v1/states", "/api/v1/work-categories",
    } <= paths


def test_work_id_pattern_example_is_a_valid_id():
    from backend.app.services.data_service import WORK_ID_EXAMPLE, is_valid_work_id

    assert re.fullmatch(r"[A-Z]+/MP\d+/\d{4}-\d{4}/\d+", WORK_ID_EXAMPLE)
    assert is_valid_work_id(WORK_ID_EXAMPLE)
