from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.main import app
from app.mcp_contract import TOOLS, build_mcp_contract
from app.models import (
    AiAnalysisResult,
    AiAnalysisTaskRequest,
    AiTaskStatus,
    ErrorCode,
    FvgFillStatus,
    FvgValidityStatus,
    Interval,
    OutcomeStatus,
    ScenarioType,
    SnapshotRequest,
)
from app.service import MarketWorkbenchService


def _replace_analysis_refs(node: object) -> None:
    if isinstance(node, dict):
        if node.get("$ref") == "#/components/schemas/AiAnalysisResult":
            node["$ref"] = "./ai-analysis.schema.json"
        for value in node.values():
            _replace_analysis_refs(value)
    elif isinstance(node, list):
        for value in node:
            _replace_analysis_refs(value)


def build_contracts() -> tuple[dict, dict, dict, dict]:
    openapi = copy.deepcopy(app.openapi())
    openapi["x-generated-by"] = "scripts/generate_contracts.py"
    openapi["x-contract-source"] = ["app/models.py", "app/main.py"]
    openapi["x-analysis-scope"] = "SINGLE_UNDERLYING"
    openapi["x-monthly-data-budget-usd"] = 0
    _replace_analysis_refs(openapi["paths"])

    ai_schema = AiAnalysisResult.model_json_schema(mode="serialization")
    ai_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    ai_schema["$id"] = "https://ai-market-workbench.local/schemas/ai-analysis/1.1.0"
    ai_schema["title"] = "AI Market Workbench Single-Underlying Analysis"
    ai_schema["x-generated-by"] = "app.models.AiAnalysisResult"

    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://ai-market-workbench.local/schemas/analysis-record/1.0.0",
        "title": "External reference validation wrapper",
        "$ref": "./ai-analysis.schema.json",
    }
    return openapi, ai_schema, wrapper, build_mcp_contract()


def build_protocol_markdown(openapi: dict) -> str:
    operations = []
    for path, path_item in openapi["paths"].items():
        for method, operation in path_item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                operations.append((method.upper(), path, operation["operationId"]))
    enum_lines = [
        ("ScenarioType", ScenarioType),
        ("AiTaskStatus", AiTaskStatus),
        ("OutcomeStatus", OutcomeStatus),
        ("ErrorCode", ErrorCode),
        ("Interval", Interval),
        ("FvgFillStatus", FvgFillStatus),
        ("FvgValidityStatus", FvgValidityStatus),
    ]
    lines = [
        "# AI 结构化数据协议与通用接口",
        "",
        "> 本文件由 `scripts/generate_contracts.py` 从 `app/models.py`、`app/main.py` 和 `app/mcp_contract.py` 自动生成，禁止手工维护路径、字段或枚举。",
        "",
        "## 分析单位",
        "",
        "一次 AI 分析严格对应一个 `SINGLE_UNDERLYING` 主体，不支持投资组合分析。美股主体恰好包含一个证券；BTC/ETH 主体各包含一个现货和一个 USDT 永续，用于同一底层资产的联合上下文。",
        "",
        "AI 只读取冻结快照。指标由本地确定性程序计算；模型不得从截图猜数值。事后 outcomes 不属于快照，提示词构建路径不能读取 outcome 表。",
        "",
        "## REST/OpenAPI 路径",
        "",
        "| Method | Path | operationId |",
        "|---|---|---|",
        *[f"| {method} | `{path}` | `{operation_id}` |" for method, path, operation_id in operations],
        "",
        "POST `/v1/ai-analysis-tasks` 是唯一可触发模型或演示规则生成器的路由，需要 `Idempotency-Key`。`DETERMINISTIC_DEMO` 明确为零成本规则演示；`CODEX_CLI` 为真实模型入口，消耗现有 Codex/ChatGPT 配额且美元金额未知。行情刷新不会调用任何分析提供者。",
        "",
        "分页统一使用 `limit`、不透明 `cursor`、`next_cursor`、`has_more`。错误统一返回 `application/problem+json` 的强类型 `ProblemDetail`。",
        "",
        "## 统一枚举",
        "",
        *[f"- `{name}`：" + ", ".join(f"`{item.value}`" for item in enum_type) for name, enum_type in enum_lines],
        "",
        "## 强类型数据块",
        "",
        "`Bar`、`IndicatorResult`、`EquityFundamentals`、`CryptoMarketContext`、`FvgResult`、`DivergenceResult`、`VpvrResult`、`AnalysisSnapshot`、`AiAnalysisTask`、`AiAnalysisResult` 和 `AnalysisOutcome` 均由 Pydantic 模型生成 JSON Schema，未知字段默认拒绝。不存在 `data:{}` 或任意业务 object。",
        "",
        "VPVR 默认 `BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE`，必须输出 `calculation_mode`、`input_granularity`、`algorithm_version`、`is_estimate` 和 `limitations`，不得称为逐笔成交画像。",
        "",
        "FVG 使用 `FVG_WICK_3BAR_ATR_FROZEN_V1`：创建时冻结 ATR14；`fill_status` 与 `validity_status` 分离，失效缓冲使用创建时 ATR 的 0.1 倍。",
        "",
        "## MCP 工具投影",
        "",
        "| Tool | REST operationId | Read only | Cost side effect |",
        "|---|---|---:|---:|",
        *[f"| `{tool.name}` | `{tool.rest_operation_id}` | {str(tool.read_only).lower()} | {str(tool.has_cost_side_effect).lower()} |" for tool in TOOLS],
        "",
        "MCP input/output schema 与 REST 共用同一 Pydantic 模型。基础版不发布 account/order/withdraw/transfer 工具。`market_get_analysis_outcomes` 仅用于事后研究，不可被分析提示词工具链调用。",
        "",
        "## 生成与验证",
        "",
        "- JSON/YAML OpenAPI 均从同一个内存对象生成并进行深度一致性比较。",
        "- OpenAPI 对 AI 结果使用 `./ai-analysis.schema.json` 外部引用。",
        "- Draft 2020-12 validator 验证 AI Schema、外部引用、合法样例和多份非法样例。",
        "- OpenAPI 3.1 validator 分别验证 JSON 与 YAML，并解析外部引用。",
        "- MCP 的 `restOperationId` 必须存在于 OpenAPI；禁止工具和路由名称由测试阻断。",
        "",
    ]
    return "\n".join(lines)


def generate_examples() -> tuple[dict, dict[str, dict]]:
    # Example generation must never write into the production snapshot store:
    # a temporary store places its snapshot files beside its own database.
    from app.storage import Store

    with tempfile.TemporaryDirectory(prefix="amw-contract-examples-") as temporary:
        svc = MarketWorkbenchService(Store(Path(temporary) / "contract-examples.db"))
        snapshot = svc.create_snapshot(
            SnapshotRequest(subject_id="us-aapl", intervals=[Interval.MIN_15, Interval.MIN_195])
        )
        valid = svc._generate_analysis(
            snapshot,
            AiAnalysisTaskRequest(
                snapshot_id=snapshot.snapshot_id,
                provider="DETERMINISTIC_DEMO",
                model="evidence-rules-demo-v1",
                prompt_version="scenario-v2",
            ),
        ).model_dump(mode="json")

    invalid: dict[str, dict] = {}
    wrong_case = copy.deepcopy(valid)
    wrong_case["scenarios"][0]["type"] = "bull"
    invalid["wrong-case-scenario.json"] = wrong_case

    duplicate = copy.deepcopy(valid)
    duplicate["scenarios"][2]["type"] = "BULLISH"
    invalid["duplicate-scenario.json"] = duplicate

    unknown_property = copy.deepcopy(valid)
    unknown_property["untrusted_extra"] = "must be rejected"
    invalid["unknown-property.json"] = unknown_property

    malformed_hash = copy.deepcopy(valid)
    malformed_hash["snapshot"]["snapshot_sha256"] = "not-a-sha256"
    invalid["malformed-hash.json"] = malformed_hash

    missing_required = copy.deepcopy(valid)
    del missing_required["confidence"]
    invalid["missing-required.json"] = missing_required
    return valid, invalid


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    openapi, ai_schema, wrapper, mcp = build_contracts()
    settings.contracts_dir.mkdir(parents=True, exist_ok=True)
    _write_json(settings.contracts_dir / "openapi.json", openapi)
    (settings.contracts_dir / "openapi.yaml").write_text(
        yaml.safe_dump(openapi, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    _write_json(settings.contracts_dir / "ai-analysis.schema.json", ai_schema)
    _write_json(settings.contracts_dir / "analysis-record.schema.json", wrapper)
    _write_json(settings.contracts_dir / "mcp-tools.json", mcp)

    # Phase 0 legacy locations are generated mirrors, never manually maintained.
    phase0 = settings.root / "docs" / "phase-0"
    _write_json(phase0 / "openapi.json", openapi)
    (phase0 / "openapi.yaml").write_text(
        yaml.safe_dump(openapi, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    _write_json(phase0 / "ai-analysis.schema.json", ai_schema)
    (phase0 / "05-AI-PROTOCOL.md").write_text(build_protocol_markdown(openapi), encoding="utf-8")

    valid, invalid = generate_examples()
    _write_json(settings.root / "examples" / "valid" / "ai-analysis.json", valid)
    for name, payload in invalid.items():
        _write_json(settings.root / "examples" / "invalid" / name, payload)
    print("Generated OpenAPI JSON/YAML, JSON Schemas, MCP projection, protocol matrix, and examples.")


if __name__ == "__main__":
    main()
