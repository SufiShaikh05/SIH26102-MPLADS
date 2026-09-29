"""Acceptance checks against the REAL processed data.

Skipped automatically when ``data/processed/work_features_v0.csv`` does not exist (for example
in a fresh clone). When it does exist, the headline numbers are recomputed here with the plain
``csv`` module - independently of the backend code - and compared with what the API reports,
so a wrong definition or a loader bug shows up on the first run against real files.

    python -m pytest backend/tests/test_real_data.py -v

Point it at another folder with MPLADS_PROCESSED_DIR.
"""

from __future__ import annotations

import csv
import json
from collections import Counter

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402

SETTINGS = Settings.from_env()

pytestmark = pytest.mark.skipif(
    not SETTINGS.features_path.is_file(),
    reason=f"real processed data not built ({SETTINGS.features_path.name} not found)",
)


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def flag(value: str) -> bool:
    return value.strip().lower() in {"1", "1.0", "true"}


def count(value: str) -> int:
    return int(float(value)) if value.strip() else 0


def strict_json(response):
    def refuse(token):
        raise AssertionError(f"non-finite number {token} in JSON body")

    return json.loads(response.text, parse_constant=refuse)


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(SETTINGS))


@pytest.fixture(scope="module")
def recount():
    """Headline figures computed straight from work_features_v0.csv."""
    ids, states, categories = [], Counter(), Counter()
    sanctioned = completed = with_expenditure = transactions = 0
    for row in read_rows(SETTINGS.features_path):
        ids.append(row["work_id"].strip().upper())
        sanctioned += flag(row["in_sanctioned"])
        completed += flag(row["in_completed"])
        payments = count(row["payment_count_all_rows"])
        with_expenditure += payments > 0
        transactions += payments
        if row["state"].strip():
            states[row["state"].strip()] += 1
        if row["work_category"].strip():
            categories[row["work_category"].strip()] += 1
    return {
        "ids": ids, "sanctioned": sanctioned, "completed": completed,
        "with_expenditure": with_expenditure, "transactions": transactions,
        "states": states, "categories": categories,
    }


def test_summary_matches_an_independent_recount(client, recount):
    body = client.get("/api/v1/summary").json()
    assert body["total_works"] == len(set(recount["ids"]))
    assert body["sanctioned_works"] == recount["sanctioned"]
    assert body["completed_works"] == recount["completed"]
    assert body["works_with_expenditure"] == recount["with_expenditure"]
    assert body["total_expenditure_transactions"] == recount["transactions"]
    assert body["snapshot_date"] is not None


def test_no_duplicate_work_ids_in_the_feature_matrix(client, recount):
    assert client.get("/api/v1/summary").json()["total_works"] == len(recount["ids"])


def test_states_and_categories_cover_every_work(client, recount):
    states = client.get("/api/v1/states").json()["items"]
    assert {item["state"]: item["work_count"] for item in states} == dict(recount["states"])
    categories = client.get("/api/v1/work-categories").json()["items"]
    assert {item["work_category"]: item["work_count"] for item in categories} == dict(recount["categories"])


def test_a_spread_of_works_resolves_to_clean_json(client, recount):
    ids = recount["ids"]
    sample = ids[:: max(1, len(ids) // 150)] + [ids[0], ids[-1]]
    for work_id in sample:
        response = client.get(f"/api/v1/works/{work_id}")
        assert response.status_code == 200, work_id
        body = strict_json(response)  # no NaN / Infinity anywhere
        assert body["identity"]["work_id"] == work_id
        assert body["review_status"] in {"scored", "not_scored", "unavailable"}


@pytest.mark.skipif(not SETTINGS.anomalies_path.is_file(), reason="work_anomalies_v1.csv not produced yet")
class TestRealAnomalyOutput:
    def test_engine_output_is_served_as_engine_data(self, client):
        body = client.get("/api/v1/anomalies").json()
        assert body["data_kind"] == "engine"
        distinct = len({row["work_id"].strip().upper() for row in read_rows(SETTINGS.anomalies_path)})
        assert body["total"] == distinct

    def test_default_order_is_highest_score_first(self, client):
        for page in (1, 2):
            items = client.get(f"/api/v1/anomalies?page={page}&page_size=100").json()["items"]
            scores = [item["review_priority_score"] for item in items]
            assert scores == sorted(scores, reverse=True)

    def test_every_scored_work_exists_in_the_feature_matrix(self, recount):
        known = set(recount["ids"])
        orphans = [
            row["work_id"] for row in read_rows(SETTINGS.anomalies_path) if row["work_id"].strip().upper() not in known
        ]
        assert not orphans, f"{len(orphans)} anomaly work_id(s) are not in work_features_v0.csv, e.g. {orphans[:3]}"

    def test_work_detail_and_anomaly_record_agree(self, client):
        items = client.get("/api/v1/anomalies?page_size=10").json()["items"]
        for item in items:
            detail = client.get(f"/api/v1/works/{item['work_id']}").json()
            assert detail["review_status"] == "scored"
            assert detail["review"]["review_priority_score"] == item["review_priority_score"]
            record = client.get(f"/api/v1/anomalies/{item['work_id']}").json()
            assert record["review_priority_score"] == item["review_priority_score"]
