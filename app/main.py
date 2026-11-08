from __future__ import annotations

import uuid
import shutil
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import AwareDatetime

from app.config import settings
from app.semantic_review import SemanticReview, review_analysis
from app.scenario_rules import ScenarioOutcomePage
from app.logging_config import audit_event, configure_logging
from app.models import (
    AiAnalysisResult,
    AiAnalysisTask,
    AiAnalysisTaskRequest,
    AnalysisPage,
    AnalysisSnapshot,
    BarPage,
    CryptoMarketContext,
    EquityFundamentals,
    ErrorCode,
    HealthResponse,
    IndicatorBundle,
    IndicatorEvidenceView,
    InstrumentSearchResponse,
    Interval,
    MacroSnapshot,
    OutcomePage,
    ProblemDetail,
    SnapshotRef,
    SnapshotRequest,
)
from app.service import (
    AnalysisValidationError,
    DataUnavailableError,
    MarketWorkbenchService,
    NotFoundError,
    SnapshotSchemaError,
    service,
)


configure_logging()


@asynccontextmanager
async def lifespan(app):
    # Tasks queued by a previous process cannot be resumed; close them out so no
    # request is left hanging, without any automatic retry or model call.
    try:
        recovered = await asyncio.to_thread(service.recover_interrupted_tasks)
        if recovered:
            audit_event("startup_task_recovery", recovered=len(recovered))
    except Exception as exc:
        audit_event("startup_task_recovery_error", error_type=type(exc).__name__)

    async def refill_loop():
        from app.outcome_worker import refill_once
        while True:
            try:
                if settings.data_mode=='live': await asyncio.to_thread(refill_once, service)
            except Exception as exc:
                audit_event('outcome_worker_error',error_type=type(exc).__name__)
            await asyncio.sleep(60)
    task=asyncio.create_task(refill_loop())
    try: yield
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

app = FastAPI(
    lifespan=lifespan,
    title="AI Market Workbench Read-Only API",
    version="0.1.0",
    openapi_version="3.1.0",
    description=(
        "Single-underlying, read-only market analysis. No account, order, transfer, "
        "withdrawal, or trading-permission endpoints exist."
    ),
    contact={"name": "Local single-user deployment"},
    license_info={"name": "Project code license pending; provider data licenses apply separately"},
)


def get_service() -> MarketWorkbenchService:
    return service


ERROR_RESPONSES = {
    400: {"model": ProblemDetail, "description": "Invalid request or pagination cursor"},
    404: {"model": ProblemDetail, "description": "Resource not found or not applicable"},
    409: {"model": ProblemDetail, "description": "Idempotency conflict"},
    422: {"model": ProblemDetail, "description": "Schema or analysis validation failure"},
    429: {"model": ProblemDetail, "description": "Upstream rate or local budget limit"},
    502: {"model": ProblemDetail, "description": "Upstream provider failure"},
    503: {"model": ProblemDetail, "description": "Data unavailable or network disabled"},
    500: {"model": ProblemDetail, "description": "Internal fail-closed error"},
}


def _problem(status: int, code: ErrorCode, title: str, detail: str) -> JSONResponse:
    trace_id = f"trace-{uuid.uuid4().hex}"
    payload = ProblemDetail(
        type=f"urn:amw:error:{code.value.lower()}",
        title=title,
        status=status,
        code=code,
        detail=detail,
        trace_id=trace_id,
    )
    audit_event("api_error", status=status, code=code.value, trace_id=trace_id)
    return JSONResponse(status_code=status, content=payload.model_dump(mode="json"), media_type="application/problem+json")


@app.middleware("http")
async def local_origin_boundary(request: Request, call_next):
    import os
    public_share = os.environ.get("AMW_PUBLIC_READONLY_SHARE", "false").lower() == "true"
    allowed_hosts = {"127.0.0.1", "localhost"}
    if settings.data_mode == "fixture":
        allowed_hosts.add("testserver")
    is_local_host = request.url.hostname in allowed_hosts
    if not is_local_host and not public_share:
        audit_event("security_rejected_host")
        return _problem(403, ErrorCode.VALIDATION_ERROR, "Local-only service", "Host is not local.")
    if request.method == "POST":
        origin = request.headers.get("origin")
        if not public_share and origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            audit_event("security_rejected_cross_origin_write")
            return _problem(403, ErrorCode.VALIDATION_ERROR, "Cross-origin request denied", "Use the local project page.")
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _problem(422, ErrorCode.VALIDATION_ERROR, "Request validation failed", "Request does not match the API contract.")


@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return _problem(404, ErrorCode.NOT_FOUND, "Resource not found", str(exc))


@app.exception_handler(AnalysisValidationError)
async def analysis_validation_handler(_: Request, exc: AnalysisValidationError) -> JSONResponse:
    return _problem(422, ErrorCode.ANALYSIS_VALIDATION_FAILED, "Analysis validation failed", str(exc))


@app.exception_handler(DataUnavailableError)
async def data_unavailable_handler(_: Request, exc: DataUnavailableError) -> JSONResponse:
    return _problem(503, ErrorCode.UPSTREAM_UNAVAILABLE, "Market data unavailable", str(exc))


@app.exception_handler(SnapshotSchemaError)
async def snapshot_schema_handler(_: Request, exc: SnapshotSchemaError) -> JSONResponse:
    # Fail closed with an actionable typed error instead of a bare 500 traceback;
    # the legacy record is never rewritten or deleted.
    return _problem(500, ErrorCode.INTERNAL_ERROR, "Stored snapshot is not readable", str(exc))


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    if "idempotency" in str(exc).lower():
        return _problem(409, ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency conflict", str(exc))
    return _problem(400, ErrorCode.VALIDATION_ERROR, "Invalid request", str(exc))


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    audit_event("unhandled_exception", error_type=type(exc).__name__)
    return _problem(500, ErrorCode.INTERNAL_ERROR, "Internal fail-closed error", "Unexpected internal error; inspect the trace ID.")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(settings.root / "static" / "index.html")


@app.get("/v1/health", response_model=HealthResponse, operation_id="getHealth", responses=ERROR_RESPONSES)
def get_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        checked_at=datetime.now(UTC),
        data_mode=settings.data_mode,
        network_enabled=settings.network_enabled,
        crypto_provider=settings.crypto_provider,
        codex_cli_available=shutil.which(settings.codex_command) is not None,
        real_ai_cost_boundary="Codex/ChatGPT usage quota; USD amount unavailable",
        real_ai_model=settings.codex_default_model,
    )


@app.get("/static/real-ai.js", include_in_schema=False)
def real_ai_script() -> FileResponse:
    return FileResponse(settings.root / "static" / "real-ai.js", media_type="application/javascript")


@app.get("/v1/subjects/{subject_id}/indicator-evidence", response_model=IndicatorEvidenceView,
         operation_id="getIndicatorEvidence", responses=ERROR_RESPONSES)
def get_indicator_evidence(subject_id: str, interval: Interval,
                           instrument_id: str | None = None, cutoff_at: AwareDatetime | None = None,
                           svc: MarketWorkbenchService = Depends(get_service)) -> IndicatorEvidenceView:
    return svc.get_indicator_evidence(subject_id, interval, cutoff_at, instrument_id)


@app.get("/v1/instruments", response_model=InstrumentSearchResponse, operation_id="searchInstruments", responses=ERROR_RESPONSES)
def search_instruments(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    svc: MarketWorkbenchService = Depends(get_service),
) -> InstrumentSearchResponse:
    return svc.search_instruments(q, limit, cursor)


@app.get("/v1/instruments/{instrument_id}/bars", response_model=BarPage, operation_id="getBars", responses=ERROR_RESPONSES)
def get_bars(
    instrument_id: str,
    interval: Interval,
    cutoff_at: AwareDatetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    cursor: str | None = Query(default=None),
    svc: MarketWorkbenchService = Depends(get_service),
) -> BarPage:
    return svc.get_bars(instrument_id, interval, cutoff_at, limit, cursor)


@app.get("/v1/subjects/{subject_id}/indicator-bundle", response_model=IndicatorBundle, operation_id="getIndicatorBundle", responses=ERROR_RESPONSES)
def get_indicator_bundle(
    subject_id: str,
    interval: Interval,
    instrument_id: str | None = Query(default=None),
    cutoff_at: AwareDatetime | None = Query(default=None),
    svc: MarketWorkbenchService = Depends(get_service),
) -> IndicatorBundle:
    return svc.get_indicator_bundle(subject_id, interval, cutoff_at, instrument_id)


@app.get("/v1/subjects/{subject_id}/fundamentals", response_model=EquityFundamentals, operation_id="getEquityFundamentals", responses=ERROR_RESPONSES)
def get_equity_fundamentals(
    subject_id: str,
    svc: MarketWorkbenchService = Depends(get_service),
) -> EquityFundamentals:
    return svc.get_fundamentals(subject_id)


@app.get("/v1/subjects/{subject_id}/market-context", response_model=CryptoMarketContext, operation_id="getCryptoMarketContext", responses=ERROR_RESPONSES)
def get_crypto_market_context(
    subject_id: str,
    svc: MarketWorkbenchService = Depends(get_service),
) -> CryptoMarketContext:
    return svc.get_market_context(subject_id)


@app.get("/v1/macro/context", response_model=MacroSnapshot, operation_id="getMacroContext", responses=ERROR_RESPONSES)
def get_macro_context(
    limit: int = Query(default=30, ge=2, le=200),
    vintage: datetime | None = Query(default=None, description="按该日期可见的 ALFRED 版本取值（点时口径）"),
    svc: MarketWorkbenchService = Depends(get_service),
) -> MacroSnapshot:
    return svc.get_macro_context(limit=limit, vintage=vintage)


@app.post("/v1/analysis-snapshots", response_model=SnapshotRef, status_code=201, operation_id="createAnalysisSnapshot", responses=ERROR_RESPONSES)
def create_analysis_snapshot(
    payload: SnapshotRequest,
    svc: MarketWorkbenchService = Depends(get_service),
) -> SnapshotRef:
    return svc.snapshot_ref(svc.create_snapshot(payload))


@app.get("/v1/analysis-snapshots/{snapshot_id}", response_model=AnalysisSnapshot, operation_id="getAnalysisSnapshot", responses=ERROR_RESPONSES)
def get_analysis_snapshot(
    snapshot_id: str,
    svc: MarketWorkbenchService = Depends(get_service),
) -> AnalysisSnapshot:
    return svc.get_snapshot(snapshot_id)


@app.post("/v1/ai-analysis-tasks", response_model=AiAnalysisTask, status_code=202, operation_id="createAiAnalysisTask", responses=ERROR_RESPONSES)
def create_ai_analysis_task(
    request: Request,
    payload: AiAnalysisTaskRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
    svc: MarketWorkbenchService = Depends(get_service),
):
    allowed_hosts = {"127.0.0.1", "localhost", "testserver"}
    if payload.provider == "CODEX_CLI" and request.url.hostname not in allowed_hosts:
        return _problem(
            403,
            ErrorCode.VALIDATION_ERROR,
            "Quota-consuming provider restricted to local host",
            "External shared visitors may run DETERMINISTIC_DEMO or inspect existing CODEX_CLI reports, but cannot trigger new CODEX_CLI runs.",
        )
    task, created = svc.create_task(payload, idempotency_key)
    if created:
        background_tasks.add_task(svc.run_task, task.task_id, payload)
    return task


@app.get("/v1/ai-analysis-tasks/{task_id}", response_model=AiAnalysisTask, operation_id="getAiAnalysisTask", responses=ERROR_RESPONSES)
def get_ai_analysis_task(task_id: str, svc: MarketWorkbenchService = Depends(get_service)) -> AiAnalysisTask:
    return svc.get_task(task_id)


@app.get('/v1/ai-analyses/{analysis_id}/semantic-review', response_model=SemanticReview,
         operation_id='getAnalysisSemanticReview', responses=ERROR_RESPONSES)
def get_semantic_review(analysis_id: str, svc: MarketWorkbenchService = Depends(get_service)) -> SemanticReview:
    result = svc.get_analysis(analysis_id)
    return review_analysis(result,svc.get_snapshot(result.snapshot.snapshot_id))


@app.get("/v1/ai-analyses", response_model=AnalysisPage, operation_id="listAiAnalyses", responses=ERROR_RESPONSES)
def list_ai_analyses(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    svc: MarketWorkbenchService = Depends(get_service),
) -> AnalysisPage:
    return svc.list_analyses(limit, cursor)


@app.get("/v1/ai-analyses/{analysis_id}", response_model=AiAnalysisResult, operation_id="getAiAnalysis", responses=ERROR_RESPONSES)
def get_ai_analysis(analysis_id: str, svc: MarketWorkbenchService = Depends(get_service)) -> AiAnalysisResult:
    return svc.get_analysis(analysis_id)


@app.get("/v1/ai-analyses/{analysis_id}/outcomes", response_model=OutcomePage, operation_id="getAnalysisOutcomes", responses=ERROR_RESPONSES)
def get_analysis_outcomes(
    analysis_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    svc: MarketWorkbenchService = Depends(get_service),
) -> OutcomePage:
    return svc.list_outcomes(analysis_id, limit, cursor)


@app.get('/v1/ai-analyses/{analysis_id}/scenario-outcomes', response_model=ScenarioOutcomePage,
         operation_id='getScenarioOutcomes', responses=ERROR_RESPONSES)
def get_scenario_outcomes(analysis_id: str, limit: int=Query(default=20,ge=1,le=100),
                         cursor: str | None=None, svc: MarketWorkbenchService=Depends(get_service)):
    return svc.list_scenario_outcomes(analysis_id, limit, cursor)


@app.get("/v1/workbench/overview", include_in_schema=False)
def workbench_overview(svc: MarketWorkbenchService = Depends(get_service)) -> JSONResponse:
    from app.workbench import get_workbench_overview
    return JSONResponse(content=get_workbench_overview(svc))


@app.get("/v1/workbench/subjects/{subject_id}", include_in_schema=False)
def workbench_subject_detail(
    subject_id: str,
    interval: str = Query(default="1d"),
    instrument_id: str | None = Query(default=None),
    svc: MarketWorkbenchService = Depends(get_service),
) -> JSONResponse:
    from app.workbench import get_subject_workbench_detail
    return JSONResponse(content=get_subject_workbench_detail(svc, subject_id, interval, instrument_id))


@app.get("/v1/workbench/registry", include_in_schema=False)
def workbench_registry() -> JSONResponse:
    from app.workbench import get_registry_summary
    return JSONResponse(content=get_registry_summary())

