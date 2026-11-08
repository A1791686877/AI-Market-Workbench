from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel

from app.models import (
    AiAnalysisResult,
    AiAnalysisTask,
    AiRunInput,
    AnalysisLookupInput,
    AnalysisSnapshot,
    BarPage,
    CryptoMarketContext,
    EquityFundamentals,
    IndicatorBundle,
    IndicatorEvidenceView,
    IndicatorBundleInput,
    InstrumentSearchResponse,
    MacroContextInput,
    MacroSnapshot,
    MarketBarsInput,
    MarketSearchInput,
    OutcomeLookupInput,
    OutcomePage,
    SnapshotRef,
    SnapshotRequest,
    SubjectInput,
    TaskLookupInput,
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    rest_operation_id: str
    read_only: bool
    idempotent: bool
    has_cost_side_effect: bool = False


TOOLS = (
    ToolSpec("market_get_indicator_evidence", "Get compact evidence", "Read deterministic evidence with actual history coverage and last bar.", IndicatorBundleInput, IndicatorEvidenceView, "getIndicatorEvidence", True, True),
    ToolSpec("market_search_instruments", "Search instruments", "Search the read-only instrument catalog.", MarketSearchInput, InstrumentSearchResponse, "searchInstruments", True, True),
    ToolSpec("market_get_bars", "Get bars", "Get confirmed bars with provenance, delay and completeness.", MarketBarsInput, BarPage, "getBars", True, True),
    ToolSpec("market_get_indicator_bundle", "Get indicator bundle", "Compute deterministic indicators, FVG, divergence and estimated VPVR.", IndicatorBundleInput, IndicatorBundle, "getIndicatorBundle", True, True),
    ToolSpec("market_get_equity_fundamentals", "Get equity fundamentals", "Get point-in-time equity fundamentals with explicit missing fields.", SubjectInput, EquityFundamentals, "getEquityFundamentals", True, True),
    ToolSpec("market_get_crypto_context", "Get crypto context", "Get venue-specific spot, perpetual, funding, OI, basis and sampled liquidation context.", SubjectInput, CryptoMarketContext, "getCryptoMarketContext", True, True),
    ToolSpec("macro_get_context", "Get macro context", "Get point-in-time FRED macro context (rates, spread, policy rate, CPI YoY, dollar, VIX) at a chosen vintage.", MacroContextInput, MacroSnapshot, "getMacroContext", True, True),
    ToolSpec("market_create_analysis_snapshot", "Create snapshot", "Freeze one single-underlying subject for reproducible analysis.", SnapshotRequest, SnapshotRef, "createAnalysisSnapshot", False, False),
    ToolSpec("market_run_ai_analysis", "Run AI analysis", "Explicitly create a demo or external-model task; CODEX_CLI consumes existing login quota.", AiRunInput, AiAnalysisTask, "createAiAnalysisTask", False, False, True),
    ToolSpec("market_get_ai_task", "Get AI task", "Read AI task status and typed error details.", TaskLookupInput, AiAnalysisTask, "getAiAnalysisTask", True, True),
    ToolSpec("market_get_ai_analysis", "Get AI analysis", "Read a completed structured analysis.", AnalysisLookupInput, AiAnalysisResult, "getAiAnalysis", True, True),
    ToolSpec("market_get_analysis_outcomes", "Get analysis outcomes", "Read paginated post-analysis outcomes; unavailable to the prompt builder.", OutcomeLookupInput, OutcomePage, "getAnalysisOutcomes", True, True),
)


def build_mcp_contract() -> dict:
    return {
        "contract_version": "1.0.0",
        "protocol": "MCP tool schema projection",
        "source": "app.models + app.mcp_contract.TOOLS",
        "security_boundary": {
            "account_tools": False,
            "order_tools": False,
            "withdrawal_tools": False,
            "transfer_tools": False,
        },
        "tools": [
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": tool.input_model.model_json_schema(mode="validation"),
                "outputSchema": tool.output_model.model_json_schema(mode="serialization"),
                "annotations": {
                    "readOnlyHint": tool.read_only,
                    "idempotentHint": tool.idempotent,
                    "destructiveHint": False,
                    "openWorldHint": False,
                    "hasCostSideEffect": tool.has_cost_side_effect,
                    "restOperationId": tool.rest_operation_id,
                },
            }
            for tool in TOOLS
        ],
    }
