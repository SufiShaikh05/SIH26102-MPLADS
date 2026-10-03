"""In-memory service for Compliance & Execution Risk Intelligence v1.

Loads compliance evaluations and policy registry into memory for instant queries.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any, Optional

# Ensure project root is on sys.path for compliance_engine imports
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compliance_engine.aggregator import ComplianceAggregator
from compliance_engine.models import (
    ComplianceSummary,
    PolicyRegistryMeta,
    WorkComplianceRecord,
)
from compliance_engine.registry import POLICY_REGISTRY, RULE_DEFINITIONS
from ..config import Settings

log = logging.getLogger("uvicorn.error.mplads")


class ComplianceService:
    """Thread-safe in-memory service for compliance and risk intelligence."""

    def __init__(
        self,
        settings: Settings,
        *,
        aggregator: Optional[ComplianceAggregator] = None,
        work_store: Any = None,
    ) -> None:
        self._settings = settings
        self._aggregator = aggregator or ComplianceAggregator(
            settings.processed_dir, work_store=work_store
        )
        self._lock = threading.Lock()
        self._loaded = False

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if not self._loaded:
                self._aggregator.ensure_loaded()
                self._loaded = True

    def get_summary(self) -> ComplianceSummary:
        self.ensure_loaded()
        return self._aggregator.get_summary()

    def get_rules_metadata(self) -> dict[str, Any]:
        return {
            "registry": POLICY_REGISTRY.model_dump(),
            "rules": list(RULE_DEFINITIONS.values()),
        }

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
        self.ensure_loaded()
        return self._aggregator.get_queue(
            rule_id=rule_id,
            authority_type=authority_type,
            classification=classification,
            state=state,
            work_category=work_category,
            search=search,
            page=page,
            limit=limit,
        )

    def get_work(self, work_id: str) -> Optional[WorkComplianceRecord]:
        self.ensure_loaded()
        return self._aggregator.get_work(work_id)
