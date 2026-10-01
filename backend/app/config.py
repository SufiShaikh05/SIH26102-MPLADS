"""Backend settings.

Every setting has a working default for the standard repo layout
(``<repo>/data/processed``). Environment variables override them, so nothing
needs editing to point the API at another folder or to change CORS.

Environment variables (all optional)
------------------------------------
MPLADS_PROCESSED_DIR                 folder holding the processed CSVs
MPLADS_USE_DEMO_ANOMALIES            1/true: serve the clearly-marked demo fixture
                                     while work_anomalies_v1.csv does not exist
MPLADS_CORS_ORIGINS                  extra allowed origins, comma separated, or ``*``
MPLADS_SNAPSHOT_DATE                 YYYY-MM-DD; overrides the snapshot date that
                                     is otherwise derived from the data
MPLADS_REVIEW_CANDIDATE_LABELS       comma-separated review_priority_label values
                                     that count as "review candidates" (overrides the
                                     score/signal rules below when set)
MPLADS_REVIEW_CANDIDATE_MIN_SCORE    default rule: review_priority_score >= N (default 25;
                                     matches "Low/Medium/High Review Priority" and excludes
                                     "Normal Monitoring" in the current anomaly-engine vocabulary)
MPLADS_REVIEW_CANDIDATE_MIN_SIGNALS  optional override: signal_count >= N instead of the
                                     score rule above (unset by default)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# backend/app/config.py -> parents[0]=app, [1]=backend, [2]=repository root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

FEATURES_FILE = "work_features_v0.csv"
WORKS_MASTER_FILE = "works_master.csv"
EXPENDITURE_BY_WORK_FILE = "expenditure_by_work.csv"
ANOMALIES_FILE = "work_anomalies_v1.csv"
DEMO_ANOMALIES_FILE = "work_anomalies_v1.demo.csv"
DUPLICATE_PAIRS_FILE = "potential_duplicate_pairs_v1.csv"
DUPLICATE_CLUSTERS_FILE = "potential_duplicate_clusters_v1.csv"

# Any local dev server (React, Vite, Next, Angular, python -m http.server ...) on any port.
LOCALHOST_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _csv_list(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    processed_dir: Path = PROJECT_ROOT / "data" / "processed"
    demo_dir: Path = PROJECT_ROOT / "backend" / "demo_data"
    use_demo_anomalies: bool = False

    cors_origins: tuple[str, ...] = ()
    cors_origin_regex: str | None = LOCALHOST_ORIGIN_REGEX

    # Explicit override; when None the snapshot date is derived from the data
    # (sanction_date + days_since_sanction, which is how the features were built).
    snapshot_date: date | None = None

    # "Review candidate" definition, in precedence order:
    #   1. review_candidate_labels, when given (review_priority_label in the set)
    #   2. review_candidate_min_signals, when explicitly set (signal_count >= N)
    #   3. otherwise the default: review_priority_score >= review_candidate_min_score
    review_candidate_labels: tuple[str, ...] = ()
    review_candidate_min_score: float | None = 25.0
    review_candidate_min_signals: int | None = None

    default_page_size: int = 25
    max_page_size: int = 200

    # ---- file locations -------------------------------------------------
    @property
    def features_path(self) -> Path:
        return self.processed_dir / FEATURES_FILE

    @property
    def works_master_path(self) -> Path:
        return self.processed_dir / WORKS_MASTER_FILE

    @property
    def expenditure_by_work_path(self) -> Path:
        return self.processed_dir / EXPENDITURE_BY_WORK_FILE

    @property
    def anomalies_path(self) -> Path:
        return self.processed_dir / ANOMALIES_FILE

    @property
    def demo_anomalies_path(self) -> Path:
        return self.demo_dir / DEMO_ANOMALIES_FILE

    @property
    def duplicate_pairs_path(self) -> Path:
        return self.processed_dir / DUPLICATE_PAIRS_FILE

    @property
    def duplicate_clusters_path(self) -> Path:
        return self.processed_dir / DUPLICATE_CLUSTERS_FILE

    def display(self, path: Path) -> str:
        """Repo-relative path for messages (never leaks the absolute user path)."""
        try:
            return path.resolve().relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            return path.name

    # ---- construction ---------------------------------------------------
    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        kwargs: dict[str, object] = {}

        if env.get("MPLADS_PROCESSED_DIR"):
            kwargs["processed_dir"] = Path(env["MPLADS_PROCESSED_DIR"]).expanduser()

        kwargs["use_demo_anomalies"] = _truthy(env.get("MPLADS_USE_DEMO_ANOMALIES"))

        origins = _csv_list(env.get("MPLADS_CORS_ORIGINS"))
        if "*" in origins:
            kwargs["cors_origins"] = ("*",)
            kwargs["cors_origin_regex"] = None
        else:
            kwargs["cors_origins"] = origins

        raw_snapshot = (env.get("MPLADS_SNAPSHOT_DATE") or "").strip()
        if raw_snapshot:
            try:
                kwargs["snapshot_date"] = date.fromisoformat(raw_snapshot)
            except ValueError as exc:
                raise ValueError(
                    f"MPLADS_SNAPSHOT_DATE must be YYYY-MM-DD, got {raw_snapshot!r}"
                ) from exc

        kwargs["review_candidate_labels"] = _csv_list(env.get("MPLADS_REVIEW_CANDIDATE_LABELS"))

        raw_min_score = (env.get("MPLADS_REVIEW_CANDIDATE_MIN_SCORE") or "").strip()
        if raw_min_score:
            try:
                kwargs["review_candidate_min_score"] = float(raw_min_score)
            except ValueError as exc:
                raise ValueError(
                    f"MPLADS_REVIEW_CANDIDATE_MIN_SCORE must be a number, got {raw_min_score!r}"
                ) from exc

        raw_signals = (env.get("MPLADS_REVIEW_CANDIDATE_MIN_SIGNALS") or "").strip()
        if raw_signals:
            try:
                kwargs["review_candidate_min_signals"] = max(0, int(raw_signals))
            except ValueError as exc:
                raise ValueError(
                    f"MPLADS_REVIEW_CANDIDATE_MIN_SIGNALS must be an integer, got {raw_signals!r}"
                ) from exc

        return cls(**kwargs)  # type: ignore[arg-type]
