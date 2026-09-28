# Frontend (MPLADS AI Monitoring Dashboard)

React + TypeScript + Vite. No routing, chart or UI library dependencies (charts are plain CSS bars).

## Start

```bash
cd frontend
cp .env.example .env      # once
npm install
npm run dev               # http://localhost:5173
npm run build             # type-check + production build
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | FastAPI base URL. Read only in `src/api/client.ts`. |
| `VITE_USE_MOCK` | `false` | `true` serves synthetic data from `src/api/mock.ts` instead of the backend. |

## API dependency

The dashboard needs the FastAPI backend running. Endpoints consumed:
`GET /api/v1/summary`, `/api/v1/anomalies`, `/api/v1/anomalies/{work_id}`, `/api/v1/works/{work_id}`, `/api/v1/states`, `/api/v1/work-categories`.
(`/health` is part of the contract but not used by the UI.)

Shapes the frontend assumes beyond the written contract (adjust in `src/api/client.ts` if the backend differs):

- `/summary` returns `total_works`, `works_with_expenditure`, `completed_works`, `review_candidates`, and optionally `label_distribution` (`{"High": n, ...}`) and `state_breakdown` (`[{state, works, review_candidates}]`) for the two charts. Without them the charts show an explanatory empty state.
- `/anomalies` returns `{items, total, page, page_size}` (`results`/`data` and `total_count` are also accepted).
- `sort` is `field` (ascending) or `-field` (descending), e.g. `-review_priority_score`. See `sortParam`.
- Priority labels are `High`, `Medium`, `Low`. Ratios are fractions (0.25 = 25%). Amounts are rupees.
- `/states` and `/work-categories` return a list of strings or objects with a name field.

## Structure

- `src/api/client.ts`: typed interfaces, the only place that calls `fetch`.
- `src/api/mock.ts`: development fallback, imported lazily and only in mock mode.
- `src/App.tsx`: page shell, hash routing (`#/work/<id>`), mock-mode banner.
- `src/Dashboard.tsx`: summary cards, distribution and state charts, filterable review-candidate table.
- `src/WorkDetail.tsx`: work facts, score, signal cards, explanation, suggested interpretation.
- `src/ui.tsx`: shared formatting, loading/empty/error components, `useAsync`.

## Mock mode

```bash
VITE_USE_MOCK=true npm run dev
```

A yellow banner is shown while mock mode is on. Mock records use `MOCK-` work IDs. Do not use it for the final demo.

## Wording

The UI uses Review Priority, Anomaly, Unusual Pattern, Review Candidate and Indicator. Scores are presented as prompts for verification, never as proof of fraud.
