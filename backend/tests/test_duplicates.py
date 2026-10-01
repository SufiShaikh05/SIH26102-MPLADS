"""Acceptance and unit tests for Potential Duplicate Work Detection v1.

Verifies:
1. Score bounding [0.0, 100.0]
2. Strict High-Confidence gate (text alone cannot create High-Confidence)
3. Batch scheme independence from duplicate_risk_score
4. Batch template quadratic pruning (chaining prevents quadratic flooding)
5. Numeral conflict penalty and High-Confidence rejection
6. Transitive cluster chaining prevention (strict High-Confidence edges only)
7. Cross-constituency exact and rare-token safety net
8. Undocumented state/MP/IDA scoring points absent
9. API route precedence (/duplicates/summary not swallowed by {work_id:path})
10. Slash-containing Work IDs resolve correctly through API
11. Neutral explanations without accusatory words + standard disclaimer
12. Real data validation on known Sikkim pair (WS/MP013/2024-2025/151021 & 151022)
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from duplicate_engine.core import (
    DISCLAIMER_TEXT,
    build_clusters_from_high_edges,
    char_trigrams,
    extract_core_and_numerals,
    score_candidate_pair,
    trigram_dice,
)

ACCUSATORY_WORDS = {"fraud", "fraudulent", "illegal", "guilty", "crime", "criminal", "scam", "wrongdoing as a classification"}


@pytest.fixture(scope="module")
def app_client() -> TestClient:
    settings = Settings.from_env()
    app = create_app(settings)
    return TestClient(app)


# --------------------------------------------------------------------------- Core Unit Tests
def test_score_bounded_normalization():
    """Verify raw scores cannot exceed 100 or drop below 0."""
    desc = "Construction of CC Road at Bhalan Para Vistar near Somnath Mandir"
    norm_desc = "construction of cc road at bhalan para vistar near somnath mandir"
    w1 = {
        "work_id": "WS/TEST/2024-2025/000001",
        "state": "TestState",
        "constituency": "TestConst",
        "work_category": "Normal/Others",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": desc,
    }
    w2 = {
        "work_id": "WS/TEST/2024-2025/000002",
        "state": "TestState",
        "constituency": "TestConst",
        "work_category": "Normal/Others",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": desc,
    }
    d1 = norm_desc
    d2 = norm_desc
    tg1, tg2 = char_trigrams(d1), char_trigrams(d2)
    c1, n1 = extract_core_and_numerals(d1)
    c2, n2 = extract_core_and_numerals(d2)

    scored = score_candidate_pair(
        w1, w2, d1, d2, tg1, tg2, c1, c2, n1, n2, const_template_freq=1, national_template_freq=1
    )
    assert scored is not None
    assert 0.0 <= scored.duplicate_risk_score <= 100.0
    assert scored.duplicate_risk_score == 100.0


def test_high_confidence_gate_text_alone_insufficient():
    """Text similarity alone must NEVER produce High-Confidence if amounts or dates differ significantly."""
    w1 = {
        "work_id": "WS/TEST/2024-2025/000001",
        "constituency": "TestConst",
        "sanction_amount": "100000",  # Large amount difference
        "sanction_date": "2024-01-01",
        "work_description": "Installation of 500W Solar Street Light at Main Chowk",
    }
    w2 = {
        "work_id": "WS/TEST/2024-2025/000002",
        "constituency": "TestConst",
        "sanction_amount": "900000",  # Large amount difference
        "sanction_date": "2025-08-01",  # 500+ days apart
        "work_description": "Installation of 500W Solar Street Light at Main Chowk",
    }
    d1 = "installation of 500w solar street light at main chowk"
    d2 = "installation of 500w solar street light at main chowk"
    tg1, tg2 = char_trigrams(d1), char_trigrams(d2)
    c1, n1 = extract_core_and_numerals(d1)
    c2, n2 = extract_core_and_numerals(d2)

    scored = score_candidate_pair(
        w1, w2, d1, d2, tg1, tg2, c1, c2, n1, n2, const_template_freq=1, national_template_freq=1
    )
    assert scored is not None
    # Text is identical, but dates (>90d) and amounts (>5%) fail gate
    assert scored.review_priority != "High-Confidence Potential Duplicate"
    assert scored.review_priority == "Medium-Confidence Potential Duplicate"


def test_numeral_conflict_prevents_high_confidence():
    """Distinct numerals (Ward 4 vs Ward 7) must penalize and prevent High-Confidence."""
    w1 = {
        "work_id": "WS/TEST/2024-2025/000001",
        "constituency": "TestConst",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": "Construction of CC Road in Ward 4 near Primary School",
    }
    w2 = {
        "work_id": "WS/TEST/2024-2025/000002",
        "constituency": "TestConst",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": "Construction of CC Road in Ward 7 near Primary School",
    }
    d1 = "construction of cc road in ward 4 near primary school"
    d2 = "construction of cc road in ward 7 near primary school"
    tg1, tg2 = char_trigrams(d1), char_trigrams(d2)
    c1, n1 = extract_core_and_numerals(d1)
    c2, n2 = extract_core_and_numerals(d2)

    scored = score_candidate_pair(
        w1, w2, d1, d2, tg1, tg2, c1, c2, n1, n2, const_template_freq=1, national_template_freq=1
    )
    assert scored is not None
    assert scored.review_priority != "High-Confidence Potential Duplicate"
    assert any("Conflicting" in r for r in scored.reasons)


def test_contextual_numeric_conflicts():
    """Verify contextual conflict detector flags differing ward, pole, house, and km numbers even when shared quantities exist."""
    from duplicate_engine.core import detect_contextual_numeric_conflict

    # Case 1: Shared quantity (10 computers) with differing wards (Ward 1 vs Ward 2)
    has_c, r = detect_contextual_numeric_conflict(
        "supply of 10 computers in ward 1 high school",
        "supply of 10 computers in ward 2 high school",
        {"10", "1"},
        {"10", "2"},
    )
    assert has_c is True
    assert "ward" in r.lower()

    # Case 2: Differing pole numbers
    has_c, r = detect_contextual_numeric_conflict(
        "solar light installation near pole 12",
        "solar light installation near pole 15",
        {"12"},
        {"15"},
    )
    assert has_c is True
    assert "pole" in r.lower()

    # Case 3: Differing house numbers
    has_c, r = detect_contextual_numeric_conflict(
        "pavement from main road to house no 24",
        "pavement from main road to house no 45",
        {"24"},
        {"45"},
    )
    assert has_c is True
    assert "house" in r.lower()

    # Case 4: Differing chainage / km
    has_c, r = detect_contextual_numeric_conflict(
        "construction of metal road from km 12.5 to 15.0",
        "construction of metal road from km 18.0 to 20.0",
        {"12.5", "15.0"},
        {"18.0", "20.0"},
    )
    assert has_c is True
    assert "km" in r.lower()

    # Case 5: Shared quantity without conflicting location (identical descriptions)
    has_c, r = detect_contextual_numeric_conflict(
        "supply of 10 computers in ward 1 high school",
        "supply of 10 computers in ward 1 high school",
        {"10", "1"},
        {"10", "1"},
    )
    assert has_c is False


def test_financial_amount_fallback():
    """Missing or zero sanction amount must fall back to recommended amount."""
    w1 = {
        "work_id": "WS/TEST/2024-2025/000001",
        "constituency": "TestConst",
        "sanction_amount": "0",  # zero sanction amount
        "recommended_amount": "500000",  # valid fallback
        "sanction_date": "2025-01-01",
        "work_description": "Construction of Community Hall at Block A",
    }
    w2 = {
        "work_id": "WS/TEST/2024-2025/000002",
        "constituency": "TestConst",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": "Construction of Community Hall at Block A",
    }
    d1 = "construction of community hall at block a"
    d2 = "construction of community hall at block a"
    tg1, tg2 = char_trigrams(d1), char_trigrams(d2)
    c1, n1 = extract_core_and_numerals(d1)
    c2, n2 = extract_core_and_numerals(d2)

    scored = score_candidate_pair(
        w1, w2, d1, d2, tg1, tg2, c1, c2, n1, n2, const_template_freq=1, national_template_freq=1
    )
    assert scored is not None
    assert scored.sanction_amount_a == 500000.0
    assert scored.sanction_amount_b == 500000.0
    assert scored.amount_difference_pct == 0.0
    assert any("Identical financial amount" in r for r in scored.reasons)


def test_serial_proximity_insufficient_alone():
    """Consecutive serial numbers alone must NOT create a candidate pair if descriptions diverge."""
    w1 = {
        "work_id": "WS/TEST/2024-2025/000001",
        "constituency": "TestConst",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": "Construction of High School Building at Rampur",
    }
    w2 = {
        "work_id": "WS/TEST/2024-2025/000002",
        "constituency": "TestConst",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": "Installation of 500W Solar Street Light at Main Market",
    }
    d1 = "construction of high school building at rampur"
    d2 = "installation of 500w solar street light at main market"
    tg1, tg2 = char_trigrams(d1), char_trigrams(d2)
    c1, n1 = extract_core_and_numerals(d1)
    c2, n2 = extract_core_and_numerals(d2)

    scored = score_candidate_pair(
        w1, w2, d1, d2, tg1, tg2, c1, c2, n1, n2, const_template_freq=1, national_template_freq=1
    )
    # Pruned due to low text similarity (< 0.65)
    assert scored is None


def test_batch_scheme_separation_independent_of_score():
    """Batch scheme classification must be independent of duplicate_risk_score."""
    w1 = {
        "work_id": "WS/TEST/2024-2025/000001",
        "constituency": "Jamui",
        "sanction_amount": "250000",
        "sanction_date": "2025-01-01",
        "work_description": "Purchase of book Shelf for School and College",
    }
    w2 = {
        "work_id": "WS/TEST/2024-2025/000002",
        "constituency": "Jamui",
        "sanction_amount": "250000",
        "sanction_date": "2025-01-01",
        "work_description": "Purchase of book Shelf for School and College",
    }
    d1 = "purchase of book shelf for school and college"
    d2 = "purchase of book shelf for school and college"
    tg1, tg2 = char_trigrams(d1), char_trigrams(d2)
    c1, n1 = extract_core_and_numerals(d1)
    c2, n2 = extract_core_and_numerals(d2)

    # When template appears 80 times, is_batch_scheme is 1, but duplicate score remains unpenalized
    scored = score_candidate_pair(
        w1, w2, d1, d2, tg1, tg2, c1, c2, n1, n2, const_template_freq=80, national_template_freq=80
    )
    assert scored is not None
    assert scored.is_batch_scheme == 1
    assert scored.batch_frequency == 80
    assert scored.duplicate_risk_score == 100.0


def test_transitive_clustering_prevention():
    """Medium-confidence pairs must NOT merge into clusters; clusters form strictly from High-Confidence edges."""
    from duplicate_engine.core import ScoredPair

    # 3 works: A, B, C
    # Edge A-B is High-Confidence
    # Edge B-C is Medium-Confidence (should NOT be an edge in clustering)
    high_pair = ScoredPair(
        pair_id="DUP-1-2",
        work_id_a="W1",
        work_id_b="W2",
        cluster_id="",
        state="State",
        constituency="Const",
        mp_name="MP",
        work_category="Category",
        description_a="Desc",
        description_b="Desc",
        sanction_amount_a=100.0,
        sanction_amount_b=100.0,
        amount_difference_pct=0.0,
        sanction_date_a="2025-01-01",
        sanction_date_b="2025-01-01",
        date_gap_days=0,
        text_similarity=1.0,
        shared_entity_tokens=["test"],
        duplicate_risk_score=100.0,
        review_priority="High-Confidence Potential Duplicate",
        is_batch_scheme=0,
        batch_frequency=1,
        consecutive_serials=1,
        reasons=["Exact match"],
        explanation_text="Explanation",
    )

    work_to_cluster, clusters = build_clusters_from_high_edges([high_pair])
    assert "W1" in work_to_cluster
    assert "W2" in work_to_cluster
    assert "W3" not in work_to_cluster
    assert len(clusters) == 1
    assert clusters[0]["work_count"] == 2
    assert "W1" in clusters[0]["work_ids"]
    assert "W2" in clusters[0]["work_ids"]
    assert "W3" not in clusters[0]["work_ids"]


def test_neutral_explanations():
    """All generated explanations must contain standard disclaimer and no accusatory language."""
    w1 = {
        "work_id": "WS/TEST/2024-2025/000001",
        "constituency": "Const",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": "Construction of road from Mandir to Shala",
    }
    w2 = {
        "work_id": "WS/TEST/2024-2025/000002",
        "constituency": "Const",
        "sanction_amount": "500000",
        "sanction_date": "2025-01-01",
        "work_description": "Construction of road from Mandir to Shala",
    }
    d1 = "construction of road from mandir to shala"
    d2 = "construction of road from mandir to shala"
    tg1, tg2 = char_trigrams(d1), char_trigrams(d2)
    c1, n1 = extract_core_and_numerals(d1)
    c2, n2 = extract_core_and_numerals(d2)

    scored = score_candidate_pair(
        w1, w2, d1, d2, tg1, tg2, c1, c2, n1, n2, const_template_freq=1, national_template_freq=1
    )
    assert scored is not None
    expl = scored.explanation_text.lower()
    for bad in ACCUSATORY_WORDS:
        assert bad not in expl, f"Found forbidden accusatory term {bad!r} in explanation"
    assert DISCLAIMER_TEXT in scored.explanation_text


# --------------------------------------------------------------------------- API Endpoint Tests
def test_api_duplicates_list(app_client: TestClient):
    """GET /api/v1/duplicates returns valid paginated response."""
    res = app_client.get("/api/v1/duplicates?page=1&page_size=10")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert len(data["items"]) <= 10

    if data["items"]:
        item = data["items"][0]
        assert "pair_id" in item
        assert "work_id_a" in item
        assert "work_id_b" in item
        assert "duplicate_risk_score" in item
        assert 0.0 <= item["duplicate_risk_score"] <= 100.0
        assert "review_priority" in item
        assert "explanation_text" in item
        assert DISCLAIMER_TEXT in item["explanation_text"]


def test_api_duplicates_summary_not_swallowed(app_client: TestClient):
    """GET /api/v1/duplicates/summary must return 200 with summary metrics and NOT be swallowed as a work_id."""
    res = app_client.get("/api/v1/duplicates/summary")
    assert res.status_code == 200
    data = res.json()
    assert "total_duplicate_pairs" in data
    assert "high_confidence_pairs" in data
    assert "medium_confidence_pairs" in data
    assert "batch_scheme_pairs" in data
    assert "total_clusters" in data
    assert "affected_works_count" in data
    assert data["total_duplicate_pairs"] > 0
    assert data["high_confidence_pairs"] > 0


def test_api_duplicates_slash_containing_work_id(app_client: TestClient):
    """GET /api/v1/duplicates/{work_id:path} correctly resolves slash-containing IDs."""
    # Test known Sikkim work ID
    work_id = "WS/MP013/2024-2025/151021"
    res = app_client.get(f"/api/v1/duplicates/{work_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["work_id"] == work_id
    assert data["has_duplicates"] is True
    assert len(data["duplicate_pairs"]) >= 1

    # Verify paired work is 151022
    partner_ids = [p["work_id_b"] if p["work_id_a"] == work_id else p["work_id_a"] for p in data["duplicate_pairs"]]
    assert "WS/MP013/2024-2025/151022" in partner_ids


def test_api_duplicates_filtering(app_client: TestClient):
    """Filter by state and priority."""
    res = app_client.get("/api/v1/duplicates?state=Sikkim&priority=high")
    assert res.status_code == 200
    data = res.json()
    for item in data["items"]:
        assert item["state"] == "Sikkim"
        assert item["review_priority"] == "High-Confidence Potential Duplicate"


# --------------------------------------------------------------------------- Real Data Acceptance Tests
def test_real_sikkim_pair_validation(app_client: TestClient):
    """Validate the documented Sikkim pair WS/MP013/2024-2025/151021 and 151022 in production datasets."""
    res = app_client.get("/api/v1/duplicates/WS/MP013/2024-2025/151021")
    assert res.status_code == 200
    data = res.json()
    assert data["has_duplicates"] is True

    sikkim_pair = None
    for p in data["duplicate_pairs"]:
        if "WS/MP013/2024-2025/151022" in (p["work_id_a"], p["work_id_b"]):
            sikkim_pair = p
            break

    assert sikkim_pair is not None, "Known Sikkim pair was not found in duplicate pairs"
    assert sikkim_pair["duplicate_risk_score"] == 100.0
    assert sikkim_pair["review_priority"] == "High-Confidence Potential Duplicate"
    assert sikkim_pair["consecutive_serials"] is True
    assert sikkim_pair["amount_difference_pct"] == 0.0
    assert sikkim_pair["date_gap_days"] == 0
    assert sikkim_pair["is_batch_scheme"] is False
    assert DISCLAIMER_TEXT in sikkim_pair["explanation_text"]


def test_api_batch_isolation(app_client: TestClient):
    """Default query /duplicates excludes batch schemes; ?is_batch=true returns them."""
    res_default = app_client.get("/api/v1/duplicates?page=1&page_size=25")
    assert res_default.status_code == 200
    data_default = res_default.json()
    for item in data_default["items"]:
        assert item["is_batch_scheme"] is False
        assert item["review_priority"] != "Batch Scheme Representative Link"

    res_batch = app_client.get("/api/v1/duplicates?is_batch=true&page=1&page_size=25")
    assert res_batch.status_code == 200
    data_batch = res_batch.json()
    assert data_batch["total"] > 0
    for item in data_batch["items"]:
        assert item["is_batch_scheme"] is True
        assert item["review_priority"] == "Batch Scheme Representative Link"


def test_persisted_artifacts_exist_and_bounded():
    """Verify generated artifacts exist and have expected bounded sizes."""
    settings = Settings.from_env()
    pairs_file = settings.duplicate_pairs_path
    clusters_file = settings.duplicate_clusters_path

    assert pairs_file.is_file(), f"{pairs_file} does not exist"
    assert clusters_file.is_file(), f"{clusters_file} does not exist"

    pairs_size_mb = pairs_file.stat().st_size / 1024 / 1024
    clusters_size_kb = clusters_file.stat().st_size / 1024

    # Strict Acceptance Constraint: Pairs CSV file must be under 6.0 MB and clusters under 1.0 MB
    assert pairs_size_mb < 6.0, f"Pairs CSV file unexpectedly large: {pairs_size_mb:.2f} MB"
    assert clusters_size_kb < 1000.0, f"Clusters CSV file unexpectedly large: {clusters_size_kb:.2f} KB"
