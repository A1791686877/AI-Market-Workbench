from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field, StringConstraints

from app.models import (
    AiAnalysisResult,
    AiAnalysisTask,
    AnalysisSnapshot,
    BarPage,
    CryptoMarketContext,
    EquityFundamentals,
    IndicatorBundle,
    IndicatorEvidenceView,
    InstrumentSearchResponse,
    Interval,
    Identifier,
    MacroSnapshot,
    OutcomePage,
    SnapshotRef,
    SnapshotRequest,
    AiAnalysisTaskRequest,
)
from app.service import service


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

mcp = MCPServer(
    "ai-market-workbench-read-only",
    version="0.1.0",
    instructions=(
        "Read market data and deterministic indicator evidence. This server has no account, "
        "order, transfer, withdrawal, or trading tools. VPVR is an estimate based on K-line "
        "range allocation. Analysis is single-underlying and is not investment advice."
    ),
)


@mcp.tool(name="market_search_instruments", annotations=READ_ONLY, structured_output=True)
def market_search_instruments(
    q: Annotated[str, Field(min_length=1, max_length=100)],
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> InstrumentSearchResponse:
    """Search the read-only instrument catalog."""
    return service.search_instruments(q, limit, cursor)


@mcp.tool(name="market_get_bars", annotations=READ_ONLY, structured_output=True)
def market_get_bars(
    instrument_id: Identifier,
    interval: Interval,
    cutoff_at: datetime | None = None,
    limit: Annotated[int, Field(ge=1, le=5000)] = 500,
    cursor: str | None = None,
) -> BarPage:
    """Get confirmed bars with provenance, delay, gaps and completeness."""
    return service.get_bars(instrument_id, interval, cutoff_at, limit, cursor)


@mcp.tool(name="market_get_indicator_bundle", annotations=READ_ONLY, structured_output=True)
def market_get_indicator_bundle(
    subject_id: Identifier,
    interval: Interval,
    instrument_id: Identifier | None = None,
    cutoff_at: datetime | None = None,
) -> IndicatorBundle:
    """Get local deterministic indicators, FVG, divergence and estimated VPVR evidence."""
    return service.get_indicator_bundle(subject_id, interval, cutoff_at, instrument_id)


@mcp.tool(name="market_get_indicator_evidence", annotations=READ_ONLY, structured_output=True)
def market_get_indicator_evidence(
    subject_id: Identifier, interval: Interval, instrument_id: Identifier | None = None,
    cutoff_at: datetime | None = None,
) -> IndicatorEvidenceView:
    """Read compact computed evidence, actual history coverage and last bar; no model call."""
    return service.get_indicator_evidence(subject_id, interval, cutoff_at, instrument_id)


@mcp.tool(name="market_get_equity_fundamentals", annotations=READ_ONLY, structured_output=True)
def market_get_equity_fundamentals(subject_id: Identifier) -> EquityFundamentals:
    """Get point-in-time equity fundamentals with explicit missing fields."""
    return service.get_fundamentals(subject_id)


@mcp.tool(name="market_get_crypto_context", annotations=READ_ONLY, structured_output=True)
def market_get_crypto_context(subject_id: Identifier) -> CryptoMarketContext:
    """Get venue-specific public crypto spot, perpetual, funding, OI and basis observations."""
    return service.get_market_context(subject_id)


@mcp.tool(name="macro_get_context", annotations=READ_ONLY, structured_output=True)
def macro_get_context(limit: int = 30, vintage: datetime | None = None) -> MacroSnapshot:
    """Get point-in-time FRED macro context (rates, spread, policy rate, CPI YoY, dollar, VIX).

    Values are the ALFRED vintage visible at `vintage` (default: today), never the latest revision.
    """
    return service.get_macro_context(limit=limit, vintage=vintage)


@mcp.tool(name="market_create_analysis_snapshot", annotations=LOCAL_WRITE, structured_output=True)
def market_create_analysis_snapshot(
    subject_id: Identifier,
    intervals: Annotated[list[Interval], Field(min_length=1, max_length=8)],
    cutoff_at: datetime | None = None,
    include_perpetual: bool = False,
) -> SnapshotRef:
    """Freeze a single-underlying analysis snapshot in the local E-drive audit store."""
    return service.snapshot_ref(
        service.create_snapshot(
            SnapshotRequest(subject_id=subject_id, intervals=intervals, cutoff_at=cutoff_at, include_perpetual=include_perpetual)
        )
    )


@mcp.tool(name="market_run_ai_analysis", annotations=LOCAL_WRITE, structured_output=True)
def market_run_ai_analysis(
    snapshot_id: Identifier,
    idempotency_key: Annotated[str, Field(min_length=16, max_length=128)],
    provider: Literal["DETERMINISTIC_DEMO", "CODEX_CLI"] = "DETERMINISTIC_DEMO",
    model: Annotated[str, StringConstraints(min_length=1, max_length=100)] = "evidence-rules-demo-v1",
    prompt_version: Literal["scenario-v2"] = "scenario-v2",
) -> AiAnalysisTask:
    """Explicitly run one configured analysis provider; this can consume model quota."""
    request = AiAnalysisTaskRequest(
        snapshot_id=snapshot_id,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
    )
    task, created = service.create_task(request, idempotency_key)
    if created:
        service.run_task(task.task_id, request)
    return service.get_task(task.task_id)


@mcp.tool(name="market_get_ai_task", annotations=READ_ONLY, structured_output=True)
def market_get_ai_task(task_id: Identifier) -> AiAnalysisTask:
    """Read AI task state and any typed failure."""
    return service.get_task(task_id)


@mcp.tool(name="market_get_ai_analysis", annotations=READ_ONLY, structured_output=True)
def market_get_ai_analysis(analysis_id: Identifier) -> AiAnalysisResult:
    """Read one validated structured analysis."""
    return service.get_analysis(analysis_id)


@mcp.tool(name="market_get_analysis_outcomes", annotations=READ_ONLY, structured_output=True)
def market_get_analysis_outcomes(
    analysis_id: Identifier,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> OutcomePage:
    """Read paginated post-analysis outcomes; never available to the original analysis prompt."""
    return service.list_outcomes(analysis_id, limit, cursor)


if __name__ == "__main__":
    mcp.run()
