"""Dashboard summary and lookup lists, built from the in-memory stores.

Nothing here is hard-coded: every figure is read from the CSVs when they are (re)loaded.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from .anomaly_service import AnomalyStore
from .data_service import WorkStore


class SummaryService:
    def __init__(self, settings: Settings, works: WorkStore, anomalies: AnomalyStore) -> None:
        self._settings = settings
        self._works = works
        self._anomalies = anomalies

    def summary(self) -> dict[str, Any]:
        agg = self._works.aggregates()
        data = self._anomalies.data()

        warnings = list(self._works.warnings)
        warnings.extend(data.warnings)
        if data.message and data.message not in warnings and not data.available:
            warnings.append(data.message)

        generated_at = self._works.loaded_at
        if data.loaded_at is not None and data.loaded_at > generated_at:
            generated_at = data.loaded_at

        return {
            "total_works": agg.total_works,
            "sanctioned_works": agg.sanctioned_works,
            "completed_works": agg.completed_works,
            "works_with_expenditure": agg.works_with_expenditure,
            "total_expenditure_transactions": agg.total_expenditure_transactions,
            "review_candidates": data.candidate_count if data.available else None,
            "generated_at": generated_at,
            "snapshot_date": self._works.snapshot_date,
            "review_priority_label_counts": [
                {"label": label, "count": count} for label, count in data.label_counts
            ],
            "work_status_counts": [
                {"status": status, "count": count}
                for status, count in sorted(agg.status_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "anomaly_data": data.info(),
            "warnings": warnings,
        }

    def states(self) -> dict[str, Any]:
        agg = self._works.aggregates()
        data = self._anomalies.data()
        items = [
            {
                "state": state,
                "work_count": count,
                "review_candidate_count": data.candidates_by_state.get(state, 0) if data.available else None,
            }
            for state, count in sorted(agg.state_counts.items(), key=lambda kv: kv[0].casefold())
        ]
        return {"items": items, "total": len(items)}

    def categories(self) -> dict[str, Any]:
        agg = self._works.aggregates()
        data = self._anomalies.data()
        items = [
            {
                "work_category": category,
                "work_count": count,
                "review_candidate_count": (
                    data.candidates_by_category.get(category, 0) if data.available else None
                ),
            }
            for category, count in sorted(agg.category_counts.items(), key=lambda kv: kv[0].casefold())
        ]
        return {"items": items, "total": len(items)}
