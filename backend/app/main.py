"""MPLADS review-priority API (FastAPI, CSV-backed).

Start (from the repository root)::

    python -m uvicorn backend.app.main:app --reload --port 8000

Interactive docs: http://localhost:8000/docs
"""

import logging
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .models import (
    AnomalyDetail,
    AnomalyPage,
    AnomalyRecord,
    CategoryList,
    ErrorResponse,
    HealthResponse,
    ReviewInfo,
    StateList,
    SummaryResponse,
    WorkDetail,
)
from .services.anomaly_service import (
    AnomalyStore,
    InvalidQueryError,
    parse_sort,
    row_as_dict,
)
from .services.data_service import (
    WORK_ID_EXAMPLE,
    DataUnavailableError,
    WorkStore,
    is_valid_work_id,
    normalize_work_id,
)
from .services.summary_service import SummaryService

log = logging.getLogger("uvicorn.error.mplads")

API_DESCRIPTION = (
    "Serves MPLADS works, spending features and **review-priority** records to the dashboard. "
    "Scores, labels and signals point to unusual patterns worth a human look; they are not "
    "findings of fraud or wrongdoing."
)


@dataclass
class Context:
    settings: Settings
    works: WorkStore
    anomalies: AnomalyStore
    summary: SummaryService


def _not_found(work_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Work not found: {work_id}")


def resolve_work_id(ctx: Context, raw: str) -> str:
    """Normalise a Work ID from the URL and reject anything that cannot be one (422).

    A well-formed but unknown ID passes here and becomes a 404 later. An ID that exists in the
    data is always accepted, so a real work can never be rejected by the format check."""
    work_id = normalize_work_id(raw)
    if is_valid_work_id(work_id):
        return work_id
    if ctx.works.get(work_id) is not None or ctx.anomalies.lookup(work_id)[0] is not None:
        return work_id
    raise HTTPException(
        status_code=422,
        detail=(
            f"Invalid work_id {raw!r}. Expected PREFIX/MPCODE/YYYY-YYYY/SERIAL, "
            f"for example {WORK_ID_EXAMPLE}."
        ),
    )


def _review_info(row, data) -> ReviewInfo:
    return ReviewInfo(
        review_priority_score=row.review_priority_score,
        review_priority_label=row.review_priority_label,
        signal_count=row.signal_count,
        top_signal_1=row.top_signal_1,
        top_signal_2=row.top_signal_2,
        top_signal_3=row.top_signal_3,
        explanation_text=row.explanation_text,
        extra=dict(zip(data.extra_columns, row.extra, strict=True)),
        data_kind=data.kind,
    )


def build_router(ctx: Context) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    max_page_size = ctx.settings.max_page_size

    @router.get("/summary", response_model=SummaryResponse, tags=["dashboard"])
    def get_summary():
        """Headline figures for the dashboard, computed from the loaded CSVs."""
        return ctx.summary.summary()

    @router.get("/anomalies", response_model=AnomalyPage, tags=["review"])
    def list_anomalies(
        page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
        page_size: Annotated[
            int | None,
            Query(ge=1, le=max_page_size, description=f"Rows per page (1-{max_page_size}); default {ctx.settings.default_page_size}."),
        ] = None,
        label: Annotated[
            list[str] | None,
            Query(description="review_priority_label; repeat the parameter for several values. Case-insensitive."),
        ] = None,
        state: Annotated[list[str] | None, Query(description="State; repeatable. Case-insensitive.")] = None,
        work_category: Annotated[
            list[str] | None, Query(description="Work category; repeatable. Case-insensitive.")
        ] = None,
        min_score: Annotated[
            float | None, Query(allow_inf_nan=False, description="review_priority_score >= min_score.")
        ] = None,
        max_score: Annotated[
            float | None, Query(allow_inf_nan=False, description="review_priority_score <= max_score.")
        ] = None,
        sort: Annotated[
            str | None,
            Query(description="Comma-separated fields, '-' prefix = descending. Default '-review_priority_score'."),
        ] = None,
    ):
        """Paginated review records. Filters combine with AND; values inside one filter combine with OR."""
        size = page_size or ctx.settings.default_page_size
        if min_score is not None and max_score is not None and min_score > max_score:
            raise InvalidQueryError("min_score must be less than or equal to max_score.")
        keys = parse_sort(sort)
        rows, total, data = ctx.anomalies.query(
            labels=label,
            states=state,
            categories=work_category,
            min_score=min_score,
            max_score=max_score,
            sort=keys,
            page=page,
            page_size=size,
        )
        return AnomalyPage(
            items=[AnomalyRecord(**row_as_dict(r)) for r in rows],
            page=page,
            page_size=size,
            total=total,
            pages=math.ceil(total / size) if total else 0,
            data_kind=data.kind,
        )

    @router.get(
        "/anomalies/{work_id:path}",
        response_model=AnomalyDetail,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["review"],
    )
    def get_anomaly(work_id: str):
        """The review record of exactly one work (Work IDs contain '/', so the path is matched greedily)."""
        work_id = resolve_work_id(ctx, work_id)
        row, data = ctx.anomalies.lookup(work_id)
        if row is None:
            if ctx.works.get(work_id) is None:
                raise _not_found(work_id)
            if not data.available:
                raise HTTPException(status_code=503, detail=data.message or "Anomaly data is not available.")
            raise HTTPException(status_code=404, detail=f"No review record for work {work_id}.")
        return AnomalyDetail(
            **row_as_dict(row),
            extra=dict(zip(data.extra_columns, row.extra, strict=True)),
            data_kind=data.kind,
        )

    @router.get(
        "/works/{work_id:path}",
        response_model=WorkDetail,
        responses={404: {"model": ErrorResponse}},
        tags=["works"],
    )
    def get_work(work_id: str):
        """One work: identity, money, utilisation, payments, timeline, peers and its review record."""
        work_id = resolve_work_id(ctx, work_id)
        detail = ctx.works.detail(work_id)
        if detail is None:
            raise _not_found(work_id)
        row, data = ctx.anomalies.lookup(work_id)
        if row is not None:
            detail["review_status"] = "scored"
            detail["review"] = _review_info(row, data)
        else:
            detail["review_status"] = "not_scored" if data.available else "unavailable"
            detail["review"] = None
        return detail

    @router.get("/states", response_model=StateList, tags=["lookups"])
    def list_states():
        """States with work counts (and review-candidate counts once anomaly data exists)."""
        return ctx.summary.states()

    @router.get("/work-categories", response_model=CategoryList, tags=["lookups"])
    def list_work_categories():
        """Work categories with work counts (and review-candidate counts once anomaly data exists)."""
        return ctx.summary.categories()

    return router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    works = WorkStore(settings)
    anomalies = AnomalyStore(settings, works)
    ctx = Context(settings, works, anomalies, SummaryService(settings, works, anomalies))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Warm the caches so the first dashboard request is not the slow one. A problem here
        # is logged, not fatal: /health stays up and each endpoint reports the issue itself.
        try:
            works.ensure_loaded()
            anomalies.data()
        except DataUnavailableError as exc:
            log.warning("Startup: %s", exc)
        except Exception:
            log.exception("Startup: could not load the processed data")
        yield

    app = FastAPI(
        title="MPLADS Review-Priority API",
        version="1.0.0",
        description=API_DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.ctx = ctx

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_origin_regex=settings.cors_origin_regex,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(DataUnavailableError)
    async def _data_unavailable(_request: Request, exc: DataUnavailableError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(InvalidQueryError)
    async def _invalid_query(_request: Request, exc: InvalidQueryError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        # FastAPI's default puts a list in `detail`; the dashboard gets a string like every other error,
        # with the per-field breakdown alongside in `errors`.
        errors = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
                "message": str(error["msg"]),
            }
            for error in exc.errors()
        ]
        detail = "; ".join(f"{e['field']}: {e['message']}" for e in errors) or "Invalid request."
        return JSONResponse(status_code=422, content={"detail": detail, "errors": errors})

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health():
        return {"status": "ok"}

    app.include_router(build_router(ctx))
    return app


app = create_app()
