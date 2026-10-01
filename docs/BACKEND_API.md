# MPLADS Backend API

A small, read-only REST API that serves the processed MPLADS CSVs to the dashboard.
It is CSV-backed on purpose: no database, no authentication, no external service.

> **Wording.** Everything this API returns is a *review-priority indicator*: scores, labels
> and signals point at unusual patterns that may merit a human look. They are **not** findings
> of fraud or wrongdoing, and the dashboard should not present them as such.

---

## 1. Start the backend

Run everything from the **repository root** (the folder that contains `backend/` and `data/`).

```powershell
# one-time: install the backend's two runtime packages
python -m pip install -r backend/requirements.txt

# start (auto-reloads when backend code changes)
python -m uvicorn backend.app.main:app --reload --port 8000
```

| What | URL |
|---|---|
| **Frontend base URL** | `http://localhost:8000` (all data endpoints live under `/api/v1`) |
| Health check | `http://localhost:8000/health` |
| Interactive docs (Swagger UI) | `http://localhost:8000/docs` |
| OpenAPI schema (generate TS types from it) | `http://localhost:8000/openapi.json` |

Startup reads the CSVs once and keeps them in memory. On a synthetic file of the real size
(81,335 works) that took about 3.5 s and roughly 370 MB peak memory; after that a default
page is served in ~2 ms and the slowest query (re-sorting all rows by a non-default field)
in ~150 ms.

The API keeps working while the anomaly engine is unfinished: with no
`work_anomalies_v1.csv` the anomaly endpoints return empty results and say so
(see [section 6](#6-when-the-anomaly-file-is-missing)).

---

## 2. Configuration

Nothing needs configuring for the standard layout (`data/processed/` next to `backend/`).
Environment variables override the defaults:

| Variable | Default | Purpose |
|---|---|---|
| `MPLADS_PROCESSED_DIR` | `<repo>/data/processed` | Folder holding the processed CSVs |
| `MPLADS_USE_DEMO_ANOMALIES` | off | `1` serves the clearly-marked demo fixture while the real anomaly file does not exist ([section 7](#7-demo-fixture-frontend-development-only)) |
| `MPLADS_CORS_ORIGINS` | none | Extra allowed origins, comma separated, or `*`. Any `http(s)://localhost` / `127.0.0.1` / `[::1]` origin on any port is always allowed |
| `MPLADS_SNAPSHOT_DATE` | derived from the data | `YYYY-MM-DD`; overrides the snapshot date |
| `MPLADS_REVIEW_CANDIDATE_LABELS` | none | Comma-separated `review_priority_label` values that count as review candidates, e.g. `High,Critical`. Overrides the score/signal rules below when set |
| `MPLADS_REVIEW_CANDIDATE_MIN_SCORE` | `25` | **Default candidate rule:** `review_priority_score >= N` |
| `MPLADS_REVIEW_CANDIDATE_MIN_SIGNALS` | unset | Optional override: `signal_count >= N` instead of the score rule above |

PowerShell: `$env:MPLADS_USE_DEMO_ANOMALIES = "1"` before the `uvicorn` command.
cmd.exe: `set MPLADS_USE_DEMO_ANOMALIES=1`.

### Files the API reads

| File | Needed? | Used for |
|---|---|---|
| `work_features_v0.csv` | **required** | the backbone: identity, amounts, ratios, counts, timing, peer groups, summary figures, state/category lists |
| `works_master.csv` | optional | work type, recommended date, sanction date, and trend milestone counts |
| `expenditure_by_work.csv` | optional | first / last payment date |
| `expenditure_transactions.csv` | optional | monthly expenditure trends and transaction volumes |
| `work_anomalies_v1.csv` | optional (until the engine ships) | review priority score, label, signals, explanation |
| `potential_duplicate_pairs_v1.csv` | optional | potential duplicate work pairs, risk scores, evidence reasons, and batch indicators |
| `potential_duplicate_clusters_v1.csv` | optional | high-confidence duplicate work clusters and batch scheme sets |

Only the columns the API serves are kept in memory (60 of the 63 feature columns, and just the type / date columns of the other two files).
If `work_features_v0.csv` is missing, `/health` still answers and every data endpoint returns
`503` with a message naming the file.

---

## 3. Conventions

* JSON over HTTP, `GET` only. Money is in rupees as plain numbers; dates are ISO `YYYY-MM-DD`; timestamps are UTC ISO-8601.
* **Every documented field is always present.** Unknown or not-applicable values are `null` (a work with no payments has `null` amounts, not `0`). `NaN`/`Infinity` never appear.
* Filter values (`label`, `state`, `work_category`) are case-insensitive exact matches; an unknown value simply matches nothing.
* A **Work ID** looks like `WS/MP18201/2025-2026/194865` and contains `/`. Put it in the URL as is, or URL-encode it (`encodeURIComponent`); both work. IDs are trimmed and upper-cased before lookup.
* Errors are always `{"detail": "<message>"}` with a non-2xx status:

| Status | When |
|---|---|
| `404` | Well-formed Work ID that does not exist |
| `422` | Invalid query parameter or malformed Work ID. Validation errors add `errors: [{field, message}]` |
| `503` | A required data file is missing or unusable, or (`/anomalies/{work_id}` only) no anomaly data is loaded |

---

## 4. Endpoints

The example payloads show the exact response *shape*. Their figures are illustrative: the summary
totals and status counts are the documented snapshot values, `/states` and `/work-categories`
counts come from a 500-work sample, and every review score, label, signal and explanation is a
made-up placeholder.

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | `{"status": "ok"}` |
| GET | `/api/v1/summary` | dashboard headline figures |
| GET | `/api/v1/anomalies` | paginated, filterable, sortable review records |
| GET | `/api/v1/anomalies/{work_id}` | the review record of one work |
| GET | `/api/v1/works/{work_id}` | one work: identity, money, payments, timeline, peers, review |
| GET | `/api/v1/states` | states with counts |
| GET | `/api/v1/work-categories` | work categories with counts |
| GET | `/api/v1/trends` | monthly event-based milestone & expenditure trends |
| GET | `/api/v1/duplicates` | paginated, filterable list of potential duplicate work pairs |
| GET | `/api/v1/duplicates/summary` | aggregate summary of duplicate pairs, clusters, and batch schemes |
| GET | `/api/v1/duplicates/{work_id}` | potential duplicate pairs and clusters for a specific work ID |

### 4.1 `GET /api/v1/summary`

All figures are computed from the CSVs at load time, never hard-coded.

| Field | Definition |
|---|---|
| `total_works` | distinct works in `work_features_v0.csv` |
| `sanctioned_works` | `in_sanctioned = 1` |
| `completed_works` | `in_completed = 1` (work appears in the Completed source; not the same as `work_status = "Work Completed"`) |
| `works_with_expenditure` | works with at least one payment row (`payment_count_all_rows > 0`) |
| `total_expenditure_transactions` | sum of `payment_count_all_rows` |
| `review_candidates` | works in the anomaly file that meet the candidate rule (below). **`null` when no anomaly data is available**, never a fabricated `0` |
| `generated_at` | when the backend last (re)built these figures from the CSVs |
| `snapshot_date` | data snapshot date, derived from `sanction_date + days_since_sanction` (or the completion data); `null` if it cannot be derived |
| `review_priority_label_counts` | label -> count, ordered from the highest to the lowest mean score. Build the legend/filter from this; do not hard-code label names |
| `work_status_counts` | count per `work_status` |
| `anomaly_data` | what anomaly data is loaded (`kind` = `engine` / `demo_fixture` / `none`), record count, candidate rule, contract columns missing from the file, and a message |
| `warnings` | human-readable data problems (missing optional files, duplicate IDs, demo data ...). Show them somewhere visible in development |

**Review candidate rule.** By default a work is a review candidate when its
`review_priority_score >= 25`. In the current anomaly-engine label vocabulary
(`Normal Monitoring`, `Low Review Priority`, `Medium Review Priority`, `High Review Priority`)
this matches every "Review Priority" label and excludes `Normal Monitoring`, without the backend
hard-coding those label names. Set `MPLADS_REVIEW_CANDIDATE_MIN_SCORE` to change the threshold,
`MPLADS_REVIEW_CANDIDATE_LABELS` to use labels instead, or `MPLADS_REVIEW_CANDIDATE_MIN_SIGNALS`
to use a signal-count rule instead. The rule in force is reported in
`anomaly_data.review_candidate_rule`.

```json
{
  "total_works": 81335,
  "sanctioned_works": 81335,
  "completed_works": 35475,
  "works_with_expenditure": 57700,
  "total_expenditure_transactions": 86122,
  "review_candidates": 4210,
  "generated_at": "2026-09-28T10:12:35Z",
  "snapshot_date": "2026-09-25",
  "review_priority_label_counts": [
    {
      "label": "High",
      "count": 1310
    },
    {
      "label": "Medium",
      "count": 4980
    },
    {
      "label": "Low",
      "count": 75045
    }
  ],
  "work_status_counts": [
    {
      "status": "Physical Inspection",
      "count": 36173
    },
    {
      "status": "Sanction",
      "count": 21086
    },
    {
      "status": "Vendor Identification",
      "count": 11227
    },
    {
      "status": "Work partially Completed",
      "count": 8236
    },
    {
      "status": "Work Completed",
      "count": 3769
    },
    {
      "status": "Time Estimation",
      "count": 844
    }
  ],
  "anomaly_data": {
    "available": true,
    "kind": "engine",
    "file": "work_anomalies_v1.csv",
    "records": 81335,
    "review_candidate_rule": "review_priority_score >= 25",
    "missing_columns": [],
    "message": null
  },
  "warnings": []
}
```

### 4.2 `GET /api/v1/anomalies`

Paginated review records. **Default order: `review_priority_score` descending.**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int >= 1 | `1` | 1-based page number |
| `page_size` | int 1-200 | `25` | rows per page |
| `label` | string, repeatable | - | `review_priority_label`; `?label=High&label=Medium` matches either |
| `state` | string, repeatable | - | state name |
| `work_category` | string, repeatable | - | work category |
| `min_score` | number | - | `review_priority_score >= min_score` (inclusive) |
| `max_score` | number | - | `review_priority_score <= max_score` (inclusive) |
| `sort` | string | `-review_priority_score` | comma-separated fields (max 3); a leading `-` means descending; `field:asc` / `field:desc` also accepted |

Different filters combine with **AND**; repeated values of one filter combine with **OR**.
Rows whose sort value is missing always come last, in either direction; ties break on `work_id`,
so pages never overlap or skip rows. `min_score > max_score` is a `422`.

Sortable fields: `work_id`, `state`, `constituency`, `mp_name`, `ida`, `work_category`, `work_status`, `review_priority_score`, `signal_count`, `sanction_amount`, `total_disbursed_all_rows`, `deduplicated_disbursed_amount`, `success_utilization_ratio`, `days_since_sanction`, `days_since_last_payment`, `payment_count`, `vendor_count`, `duplicate_ratio`.

Response: `items`, `page`, `page_size`, `total` (rows matching the filters), `pages`
(`0` when nothing matches), `data_kind`. A page past the end returns `items: []` with the
real `total`. List items contain exactly the contract columns below, never engine extras.

Example: `GET /api/v1/anomalies?state=Uttar Pradesh&label=High&sort=-sanction_amount&page_size=2`

```json
{
  "items": [
    {
      "work_id": "WS/MP18201/2025-2026/194865",
      "state": "Uttar Pradesh",
      "constituency": "AONLA",
      "mp_name": "NEERAJ MAURYA",
      "ida": "BUDAUN(DISTRICT MAGISTRATE BUDAUN_IDA)",
      "work_category": "Normal/Others",
      "work_status": "Physical Inspection",
      "sanction_amount": 21030400.0,
      "total_disbursed_all_rows": 21030400.0,
      "deduplicated_disbursed_amount": 3730588.0,
      "success_utilization_ratio": 1.0,
      "days_since_sanction": 480,
      "days_since_last_payment": 450,
      "payment_count": 21,
      "vendor_count": 2,
      "duplicate_ratio": 0.8095,
      "review_priority_score": 78.4,
      "review_priority_label": "High",
      "signal_count": 2,
      "top_signal_1": "Share of repeated payment rows is far above comparable works",
      "top_signal_2": "Unusually many payments recorded for a single work",
      "top_signal_3": null,
      "explanation_text": "This work has 21 payment rows, 81% of which repeat an earlier row, far above comparable works. This is an unusual pattern that may merit a human review; it is not evidence of wrongdoing."
    },
    {
      "work_id": "WS/MP380/2025-2026/231503",
      "state": "Punjab",
      "constituency": "FATEHGARH SAHIB(SC)",
      "mp_name": "Shri Amar Singh",
      "ida": "LUDHIANA(DEPUTY COMMISSIONER LUDHIANA_IDA)",
      "work_category": "Normal/Others",
      "work_status": "Vendor Identification",
      "sanction_amount": 300000.0,
      "total_disbursed_all_rows": 280546.0,
      "deduplicated_disbursed_amount": 91178.0,
      "success_utilization_ratio": 0.935153,
      "days_since_sanction": 359,
      "days_since_last_payment": 53,
      "payment_count": 7,
      "vendor_count": 2,
      "duplicate_ratio": 0.5714,
      "review_priority_score": 78.4,
      "review_priority_label": "High",
      "signal_count": 2,
      "top_signal_1": "Share of repeated payment rows is far above comparable works",
      "top_signal_2": "Unusually many payments recorded for a single work",
      "top_signal_3": null,
      "explanation_text": "This work has 21 payment rows, 81% of which repeat an earlier row, far above comparable works. This is an unusual pattern that may merit a human review; it is not evidence of wrongdoing."
    }
  ],
  "page": 1,
  "page_size": 2,
  "total": 81335,
  "pages": 40668,
  "data_kind": "engine"
}
```

### 4.3 `GET /api/v1/anomalies/{work_id}`

The review record of exactly one work: the contract columns, plus `extra` (any additional
columns the engine wrote, as raw text) and `data_kind`.

* `404` - unknown work, or the work exists but the engine produced no record for it
* `503` - no anomaly data is loaded at all

```json
{
  "work_id": "WS/MP18201/2025-2026/194865",
  "state": "Uttar Pradesh",
  "constituency": "AONLA",
  "mp_name": "NEERAJ MAURYA",
  "ida": "BUDAUN(DISTRICT MAGISTRATE BUDAUN_IDA)",
  "work_category": "Normal/Others",
  "work_status": "Physical Inspection",
  "sanction_amount": 21030400.0,
  "total_disbursed_all_rows": 21030400.0,
  "deduplicated_disbursed_amount": 3730588.0,
  "success_utilization_ratio": 1.0,
  "days_since_sanction": 480,
  "days_since_last_payment": 450,
  "payment_count": 21,
  "vendor_count": 2,
  "duplicate_ratio": 0.8095,
  "review_priority_score": 78.4,
  "review_priority_label": "High",
  "signal_count": 2,
  "top_signal_1": "Share of repeated payment rows is far above comparable works",
  "top_signal_2": "Unusually many payments recorded for a single work",
  "top_signal_3": null,
  "explanation_text": "This work has 21 payment rows, 81% of which repeat an earlier row, far above comparable works. This is an unusual pattern that may merit a human review; it is not evidence of wrongdoing.",
  "extra": {
    "engine_version": "1.0"
  },
  "data_kind": "engine"
}
```

### 4.4 `GET /api/v1/works/{work_id}`

Everything the work-details page needs as a curated, grouped subset of the feature matrix (not the raw 63-column feature row):
`identity`, `flags`, `amounts`, `utilization`, `payments`, `timeline`, `peers`, and `review`.

`review_status` tells the UI how to render the review block:

| `review_status` | Meaning | `review` |
|---|---|---|
| `scored` | the engine has a record for this work | filled |
| `not_scored` | anomaly data is loaded but holds no record for this work | `null` |
| `unavailable` | no anomaly data is loaded | `null` |

`peers` holds three peer groups (`work_category`, `state`, `state_work_category`). Group medians
are `null` unless `sufficient` is `true` (a group needs at least 20 works).

```json
{
  "work_id": "WS/MP18201/2025-2026/194865",
  "snapshot_date": "2026-09-25",
  "identity": {
    "work_id": "WS/MP18201/2025-2026/194865",
    "work": "Construction",
    "description": "supply and installation of led semi high mast light as per list enclosed",
    "state": "Uttar Pradesh",
    "constituency": "AONLA",
    "mp_name": "NEERAJ MAURYA",
    "ida": "BUDAUN(DISTRICT MAGISTRATE BUDAUN_IDA)",
    "work_category": "Normal/Others",
    "work_status": "Physical Inspection",
    "mp_code": "MP18201",
    "financial_year": "2025-2026"
  },
  "flags": {
    "in_recommended": true,
    "in_sanctioned": true,
    "in_completed": true,
    "is_completed": false,
    "is_partially_completed": false,
    "is_sanctioned_only": true
  },
  "amounts": {
    "recommended_amount": 21030400.0,
    "sanction_amount": 21030400.0,
    "total_disbursed_all_rows": 21030400.0,
    "success_amount": 21030400.0,
    "in_progress_amount": 0.0,
    "exact_duplicate_amount": 17299812.0,
    "deduplicated_disbursed_amount": 3730588.0,
    "remaining_sanction_amount": 17299812.0,
    "completed_amount_disbursed": 21030400.0
  },
  "utilization": {
    "success_utilization_ratio": 1.0,
    "gross_utilization_ratio": 1.0,
    "deduplicated_utilization_ratio": 0.17739,
    "in_progress_ratio": 0.0
  },
  "payments": {
    "payment_count": 21,
    "deduplicated_payment_count": 4,
    "vendor_count": 2,
    "duplicate_record_count": 17,
    "duplicate_ratio": 0.8095,
    "duplicate_amount_ratio": 0.82261,
    "single_payment_work": false,
    "payment_span_days": 30,
    "average_payment_interval_days": 1.5
  },
  "timeline": {
    "recommended_date": "2025-04-28",
    "sanction_date": "2025-06-02",
    "first_payment_date": "2025-06-02",
    "last_payment_date": "2025-07-02",
    "completion_date": "2025-07-19",
    "recommendation_to_sanction_days": 35,
    "sanction_to_first_payment_days": 0,
    "sanction_to_last_payment_days": 30,
    "sanction_to_completion_days": 47,
    "days_since_sanction": 480,
    "days_since_last_payment": 450
  },
  "peers": {
    "work_category": {
      "peer_count": 79719,
      "sufficient": true,
      "median_sanction_amount": 300000.0,
      "median_disbursed_amount": 299938.0,
      "median_payment_count": 1.0
    },
    "state": {
      "peer_count": 15392,
      "sufficient": true,
      "median_sanction_amount": 232197.98,
      "median_disbursed_amount": 232197.0,
      "median_payment_count": 1.0
    },
    "state_work_category": {
      "peer_count": 15293,
      "sufficient": true,
      "median_sanction_amount": 231930.0,
      "median_disbursed_amount": 231280.0,
      "median_payment_count": 1.0
    }
  },
  "review_status": "scored",
  "review": {
    "review_priority_score": 78.4,
    "review_priority_label": "High",
    "signal_count": 2,
    "top_signal_1": "Share of repeated payment rows is far above comparable works",
    "top_signal_2": "Unusually many payments recorded for a single work",
    "top_signal_3": null,
    "explanation_text": "This work has 21 payment rows, 81% of which repeat an earlier row, far above comparable works. This is an unusual pattern that may merit a human review; it is not evidence of wrongdoing.",
    "extra": {
      "engine_version": "1.0"
    },
    "data_kind": "engine"
  }
}
```

### 4.5 `GET /api/v1/states` and 4.6 `GET /api/v1/work-categories`

Sorted alphabetically. `work_count` comes from the feature matrix; `review_candidate_count`
follows the candidate rule and is `null` when no anomaly data is available.

```json
{
  "items": [
    {
      "state": "Andhra Pradesh",
      "work_count": 14,
      "review_candidate_count": 41
    },
    {
      "state": "Arunachal Pradesh",
      "work_count": 2,
      "review_candidate_count": 7
    },
    {
      "state": "Assam",
      "work_count": 7,
      "review_candidate_count": 303
    }
  ],
  "total": 28
}
```

```json
{
  "items": [
    {
      "work_category": "Normal/Others",
      "work_count": 488,
      "review_candidate_count": 39
    },
    {
      "work_category": "Repair and Renovation",
      "work_count": 7,
      "review_candidate_count": 2
    },
    {
      "work_category": "Trust and Society",
      "work_count": 5,
      "review_candidate_count": 1
    }
  ],
  "total": 3
}
```

### 4.7 Error examples

`GET /api/v1/works/WS/MP005/2025-2026/999999` -> `404`

```json
{
  "detail": "Work not found: WS/MP005/2025-2026/999999"
}
```

`GET /api/v1/anomalies?page=0&page_size=abc` -> `422`

```json
{
  "detail": "page: Input should be greater than or equal to 1; page_size: Input should be a valid integer, unable to parse string as an integer",
  "errors": [
    {
      "field": "page",
      "message": "Input should be greater than or equal to 1"
    },
    {
      "field": "page_size",
      "message": "Input should be a valid integer, unable to parse string as an integer"
    }
  ]
}
```

`GET /api/v1/works/abc` -> `422`

```json
{
  "detail": "Invalid work_id 'abc'. Expected PREFIX/MPCODE/YYYY-YYYY/SERIAL, for example WS/MP005/2024-2025/145074."
}
### 4.8 `GET /api/v1/trends`

Event-based monthly trends for implementation milestones and expenditure. Aggregates timestamps directly from `works_master.csv` (`recommended_date`, `sanction_date`, `completed_date`) and `expenditure_transactions.csv` (`payment_date`, `amount`, `payment_status`).

**Query parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `state` | string | `null` | Exact state name (e.g. `Bihar`). When omitted, aggregates all states nationwide. |

```json
{
  "granularity": "month",
  "start_period": "2023-04",
  "end_period": "2025-09",
  "state": "Bihar",
  "series": [
    {
      "period": "2024-06",
      "recommended_works": 42,
      "sanctioned_works": 38,
      "completed_works": 12,
      "expenditure_transactions": 85,
      "expenditure_amount": 14500000.0,
      "payment_success_amount": 14200000.0,
      "payment_in_progress_amount": 300000.0
    }
  ]
}
```

### 4.9 `GET /api/v1/duplicates`

Paginated, filterable list of potential duplicate work pairs identified by Sentinel 2.0. Every pair is scored on an explainable `0.0–100.0` bounded risk scale and accompanied by clear evidence reasons and non-accusatory review guidance.

**Query parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer >= 1 | `1` | Page number |
| `page_size` | integer 1..100 | `25` | Page size |
| `state` | string | `null` | Exact state filter |
| `constituency` | string | `null` | Exact constituency filter |
| `priority` | string | `null` | `High`, `Medium`, `Low`, or `High-Confidence Potential Duplicate` |
| `is_batch` | boolean | `null` | Filter for batch scheme pairs (`true` / `false`) |
| `min_score` | float 0..100 | `null` | Minimum duplicate risk score |
| `sort_by` | string | `score_desc` | `score_desc`, `score_asc`, `amount_desc`, `date_gap_asc` |

```json
{
  "items": [
    {
      "pair_id": "DUP-151021-151022",
      "work_id_a": "WS/MP013/2024-2025/151021",
      "work_id_b": "WS/MP013/2024-2025/151022",
      "cluster_id": "CLUST-00637",
      "state": "Sikkim",
      "constituency": "Sikkim",
      "mp_name": "Indra Hang Subba",
      "work_category": "Others",
      "description_a": "Construction of road from Namphing to lower Namphing GPU in South Sikkim",
      "description_b": "Construction of road from Namphing to lower Namphing GPU in South Sikkim",
      "sanction_amount_a": 1000000.0,
      "sanction_amount_b": 1000000.0,
      "amount_difference_pct": 0.0,
      "sanction_date_a": "2024-07-15",
      "sanction_date_b": "2024-07-15",
      "date_gap_days": 0,
      "consecutive_serials": 1,
      "duplicate_risk_score": 100.0,
      "review_priority": "High-Confidence Potential Duplicate",
      "reasons": [
        "Identical or near-identical work description (similarity 1.00)",
        "Matching financial sanction amounts",
        "Sanctions approved on identical or near-identical dates",
        "Consecutive serial numbers (151021 / 151022)",
        "Both works in same constituency (Sikkim)"
      ],
      "shared_entity_tokens": ["gpu", "namphing", "lower", "south", "sikkim"],
      "is_batch_scheme": false,
      "batch_frequency": 2,
      "batch_scheme_reason": null,
      "explanation_text": "Review recommended because identical or near-identical work description (similarity 1.00); matching financial sanction amounts; sanctions approved on identical or near-identical dates; consecutive serial numbers (151021 / 151022); both works in same constituency (sikkim). This indicates potential duplicate entry or overlapping scope that may warrant verification and does not establish wrongdoing."
    }
  ],
  "total": 56504,
  "page": 1,
  "page_size": 25,
  "pages": 2261
}
```

### 4.10 `GET /api/v1/duplicates/summary`

Aggregate summary metrics for duplicate review candidates, multi-work clusters, and batch schemes.

```json
{
  "total_duplicate_pairs": 56504,
  "high_confidence_pairs": 16661,
  "medium_confidence_pairs": 39616,
  "batch_scheme_pairs": 227,
  "total_clusters": 3232,
  "works_in_clusters": 7180,
  "data_available": true
}
```

### 4.11 `GET /api/v1/duplicates/{work_id}`

Retrieves all duplicate pairs and cluster memberships associated with a specific Work ID.

```json
{
  "work_id": "WS/MP013/2024-2025/151021",
  "duplicate_pair_count": 1,
  "pairs": [
    {
      "pair_id": "DUP-151021-151022",
      "work_id_a": "WS/MP013/2024-2025/151021",
      "work_id_b": "WS/MP013/2024-2025/151022",
      "cluster_id": "CLUST-00637",
      "duplicate_risk_score": 100.0,
      "review_priority": "High-Confidence Potential Duplicate",
      "is_batch_scheme": false,
      "explanation_text": "Review recommended because identical or near-identical work description (similarity 1.00); matching financial sanction amounts; sanctions approved on identical or near-identical dates; consecutive serial numbers (151021 / 151022); both works in same constituency (sikkim). This indicates potential duplicate entry or overlapping scope that may warrant verification and does not establish wrongdoing."
    }
  ],
  "cluster": {
    "cluster_id": "CLUST-00637",
    "work_count": 2,
    "is_batch_scheme": false,
    "work_ids": [
      "WS/MP013/2024-2025/151021",
      "WS/MP013/2024-2025/151022"
    ]
  }
}
```

---

## 5. Expected anomaly CSV: `data/processed/work_anomalies_v1.csv`

Written by `anomaly_engine/`, read by this backend. UTF-8 (a BOM is fine), one row per work.

| Column | Type | Status | Notes |
|---|---|---|---|
| `work_id` | text | **required** | Canonical Work ID (trimmed and upper-cased on load); must exist in `work_features_v0.csv`. One row per work: on duplicates the first row wins |
| `review_priority_score` | number | **required** | Any numeric scale; **higher = higher review priority**. Filtering, sorting, and the default `review_candidates` rule (`>= 25`, see [4.1](#41-get-apiv1summary)) are all numeric on this column |
| `review_priority_label` | text | expected | Free vocabulary (`High`/`Medium`/`Low`, ...). The backend never assumes label names |
| `signal_count` | integer | expected | Number of triggered signals; used for the candidate rule only if `MPLADS_REVIEW_CANDIDATE_MIN_SIGNALS` is explicitly set (see [2](#2-configuration)) |
| `top_signal_1`, `top_signal_2`, `top_signal_3` | text | expected | Short signal descriptions, strongest first; blank when fewer signals |
| `explanation_text` | text | expected | Plain-language explanation shown on the work-details page |
| `state`, `constituency`, `mp_name`, `ida`, `work_category`, `work_status` | text | optional* | Copies of the feature-matrix values |
| `sanction_amount`, `total_disbursed_all_rows`, `deduplicated_disbursed_amount` | number | optional* | |
| `success_utilization_ratio`, `duplicate_ratio` | number | optional* | |
| `days_since_sanction`, `days_since_last_payment`, `payment_count`, `vendor_count` | integer | optional* | `payment_count_all_rows` is also accepted as the column name |

\* If one of these columns is **absent from the file**, the backend fills it from
`work_features_v0.csv`, so the engine may omit them. Engine columns (`review_priority_*`,
`signal_count`, `top_signal_*`, `explanation_text`) are **never** invented. Any contract column
missing from the file is listed in `summary.anomaly_data.missing_columns`.

**Tolerance rules**

* Column **order does not matter**; header names are matched case-insensitively.
* **Extra columns are welcome.** They never appear in list responses and are exposed as raw
  text under `extra` on `/anomalies/{work_id}` and `/works/{work_id}`.
* Blank cells, `nan` and `inf` become `null`.
* The file is re-checked on every request and **reloaded automatically when it changes**, so a
  new engine run shows up without restarting the server. If a new version is unreadable
  (for example half-written), the last good data keeps being served and
  `anomaly_data.message` says so. Writing to a temporary file and renaming it over
  `work_anomalies_v1.csv` avoids ever exposing a partial file.
* `work_features_v0.csv`, `works_master.csv`, `expenditure_by_work.csv`, `potential_duplicate_pairs_v1.csv`, and `potential_duplicate_clusters_v1.csv` are read at startup; restart the server after rebuilding them.

### 5.1 Duplicate pairs CSV: `data/processed/potential_duplicate_pairs_v1.csv`

Generated by `duplicate_engine/build_duplicate_pairs.py`. Stores pairwise candidate records for potential duplicate or substantially overlapping works.

| Column | Type | Description |
|---|---|---|
| `pair_id` | text | Unique identifier (e.g. `DUP-151021-151022`) |
| `work_id_a`, `work_id_b` | text | Canonical Work IDs forming the candidate pair |
| `cluster_id` | text | Connected-component cluster identifier (or empty if not in high-confidence cluster) |
| `state`, `constituency`, `mp_name`, `work_category` | text | Geographic & administrative context from `works_master.csv` |
| `description_a`, `description_b` | text | Official work descriptions from `works_master.csv` |
| `sanction_amount_a`, `sanction_amount_b` | number | Financial sanction amounts (rupees) |
| `amount_difference_pct` | number | Percentage difference between sanction amounts `|A - B| / max(A, B) * 100` |
| `sanction_date_a`, `sanction_date_b` | text | Sanction approval dates (`YYYY-MM-DD`) |
| `date_gap_days` | integer | Absolute calendar days between sanction dates |
| `consecutive_serials` | integer | `1` if work ID serials are sequential ($|S_a - S_b| = 1$), else `0` |
| `duplicate_risk_score` | number | Bounded normalized risk score (`0.0` to `100.0`) |
| `review_priority` | text | `High-Confidence Potential Duplicate`, `Medium-Confidence Potential Duplicate`, or `Low-Confidence Review Candidate` |
| `reasons` | text | Semicolon-separated human-readable evidence reasons |
| `shared_entity_tokens` | text | Semicolon-separated shared geographic/facility tokens |
| `is_batch_scheme` | integer | `1` if classified as an intentional multi-location batch scheme, else `0` |
| `batch_frequency` | integer | Template repetition frequency within constituency / nationwide |
| `batch_scheme_reason` | text | Reason for batch classification (or empty) |
| `explanation_text` | text | Comprehensive explanation ending with standard neutral review disclaimer |

### 5.2 Duplicate clusters CSV: `data/processed/potential_duplicate_clusters_v1.csv`

Stores connected-component clusters formed strictly from high-confidence edges to group multi-work duplicate sets without transitive chaining drift.

| Column | Type | Description |
|---|---|---|
| `cluster_id` | text | Identifier (e.g. `CLUST-00637`) |
| `work_count` | integer | Number of works in the cluster (bounded $\le 30$) |
| `is_batch_scheme` | integer | `1` if the cluster represents a batch scheme |
| `work_ids` | text | Semicolon-separated list of canonical Work IDs |

---

## 6. When the anomaly file is missing

The API stays up and never invents scores:

* `GET /api/v1/anomalies` -> `200`, `items: []`, `total: 0`, `pages: 0`, `data_kind: "none"`
* `GET /api/v1/summary` -> `review_candidates: null`, `anomaly_data.available: false`, plus an explanatory `message` and `warnings` entry
* `GET /api/v1/works/{id}` -> `review_status: "unavailable"`, `review: null`
* `GET /api/v1/anomalies/{id}` -> `503` for an existing work, `404` for an unknown one
* `/states` and `/work-categories` -> `review_candidate_count: null`

The frontend should treat `data_kind: "none"` (or `review_candidates: null`) as "anomaly engine
output not available yet" and show that instead of an empty success state.

---

## 7. Demo fixture (frontend development only)

`backend/demo_data/work_anomalies_v1.demo.csv` holds 38 placeholder review records built from
works in the feature sample so the anomaly screens can be built before the engine exists. Its
scores, labels and explanations are **not real**.

* It is **opt-in**: nothing loads it unless `MPLADS_USE_DEMO_ANOMALIES=1`.
* The real `work_anomalies_v1.csv` always wins as soon as it exists.
* It is marked everywhere: `data_kind: "demo_fixture"` on every anomaly response,
  `anomaly_data.kind: "demo_fixture"`, a `DEMO FIXTURE` entry in `summary.warnings`, an
  `is_demo_fixture` extra column, and every `explanation_text` starts with
  `[DEMO FIXTURE - placeholder, not an engine result]`.
* Its work IDs come from a sample of the feature matrix, so on a different dataset some may not
  exist in `work_features_v0.csv` (their detail pages then return `404`).
* The frontend should show a visible "demo data" banner whenever `data_kind` is not `engine`.
* Delete the folder before the final demo if you do not want it shipped.

---

## 8. Notes for the frontend

* Use `review_priority_label_counts` (ordered by severity) for legends and filter dropdowns; the
  label names are decided by the engine.
* Populate the state and category filters from `/states` and `/work-categories`.
* Render `null` as "-" / "not available", not as `0`.
* Keep paging state in `page` + `page_size`; use `pages` (not `total`) to decide whether a next page exists.
* Show `summary.warnings` and `data_kind` in a dev-only diagnostics area.

---

## 9. Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest                          # whole suite: pipeline tests + backend tests
python -m pytest backend/tests -v         # backend only
python -m pytest backend/tests/test_real_data.py -v   # acceptance check on the REAL data/processed files
```

* `backend/tests/test_api.py` needs no real data and no network: it builds a small deterministic
  dataset (shuffled column order, extra columns, blank / `nan` / `inf` cells, a BOM) in a temp folder.
* `backend/tests/test_real_data.py` recounts the headline numbers straight from the real CSVs
  and compares them with the API. It skips itself when `data/processed/` is not built.
* If FastAPI or httpx is not installed, the backend tests are skipped (not failed), so the
  existing pipeline suite stays green.

## 10. Layout

```text
backend/
  requirements.txt
  app/
    main.py                   FastAPI app, routes, CORS, error handling
    config.py                 settings + environment overrides
    models.py                 response models (the API contract)
    services/
      data_service.py         CSV loading, in-memory work store, work detail
      anomaly_service.py      anomaly CSV loading, reload, filter / sort / paginate
      summary_service.py      summary, states, categories
  demo_data/                  clearly-marked demo anomaly fixture
  tests/                      test_api.py, test_real_data.py, fixture_data.py, conftest.py
```

Design limits, by intent: single process, everything in memory, read-only, no authentication.
That is right for a local demo; a shared deployment would put a reverse proxy and auth in front.
