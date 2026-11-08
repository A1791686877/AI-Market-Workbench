# AI 结构化数据协议与通用接口

> 本文件由 `scripts/generate_contracts.py` 从 `app/models.py`、`app/main.py` 和 `app/mcp_contract.py` 自动生成，禁止手工维护路径、字段或枚举。

## 分析单位

一次 AI 分析严格对应一个 `SINGLE_UNDERLYING` 主体，不支持投资组合分析。美股主体恰好包含一个证券；BTC/ETH 主体各包含一个现货和一个 USDT 永续，用于同一底层资产的联合上下文。

AI 只读取冻结快照。指标由本地确定性程序计算；模型不得从截图猜数值。事后 outcomes 不属于快照，提示词构建路径不能读取 outcome 表。

## REST/OpenAPI 路径

| Method | Path | operationId |
|---|---|---|
| GET | `/v1/health` | `getHealth` |
| GET | `/v1/subjects/{subject_id}/indicator-evidence` | `getIndicatorEvidence` |
| GET | `/v1/instruments` | `searchInstruments` |
| GET | `/v1/instruments/{instrument_id}/bars` | `getBars` |
| GET | `/v1/subjects/{subject_id}/indicator-bundle` | `getIndicatorBundle` |
| GET | `/v1/subjects/{subject_id}/fundamentals` | `getEquityFundamentals` |
| GET | `/v1/subjects/{subject_id}/market-context` | `getCryptoMarketContext` |
| GET | `/v1/macro/context` | `getMacroContext` |
| POST | `/v1/analysis-snapshots` | `createAnalysisSnapshot` |
| GET | `/v1/analysis-snapshots/{snapshot_id}` | `getAnalysisSnapshot` |
| POST | `/v1/ai-analysis-tasks` | `createAiAnalysisTask` |
| GET | `/v1/ai-analysis-tasks/{task_id}` | `getAiAnalysisTask` |
| GET | `/v1/ai-analyses/{analysis_id}/semantic-review` | `getAnalysisSemanticReview` |
| GET | `/v1/ai-analyses` | `listAiAnalyses` |
| GET | `/v1/ai-analyses/{analysis_id}` | `getAiAnalysis` |
| GET | `/v1/ai-analyses/{analysis_id}/outcomes` | `getAnalysisOutcomes` |
| GET | `/v1/ai-analyses/{analysis_id}/scenario-outcomes` | `getScenarioOutcomes` |

POST `/v1/ai-analysis-tasks` 是唯一可触发模型或演示规则生成器的路由，需要 `Idempotency-Key`。`DETERMINISTIC_DEMO` 明确为零成本规则演示；`CODEX_CLI` 为真实模型入口，消耗现有 Codex/ChatGPT 配额且美元金额未知。行情刷新不会调用任何分析提供者。

分页统一使用 `limit`、不透明 `cursor`、`next_cursor`、`has_more`。错误统一返回 `application/problem+json` 的强类型 `ProblemDetail`。

## 统一枚举

- `ScenarioType`：`BULLISH`, `NEUTRAL`, `BEARISH`
- `AiTaskStatus`：`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED_VALIDATION`, `FAILED_PROVIDER`, `FAILED_INTERNAL`
- `OutcomeStatus`：`PENDING`, `AWAITING_DATA`, `MATURED`, `UNAVAILABLE`
- `ErrorCode`：`VALIDATION_ERROR`, `NOT_FOUND`, `DATA_UNAVAILABLE`, `DATA_INCOMPLETE`, `UPSTREAM_RATE_LIMITED`, `UPSTREAM_UNAVAILABLE`, `ANALYSIS_VALIDATION_FAILED`, `PROVIDER_ERROR`, `BUDGET_EXCEEDED`, `IDEMPOTENCY_CONFLICT`, `INTERNAL_ERROR`, `NETWORK_DISABLED`
- `Interval`：`15m`, `1h`, `2h`, `195m`, `4h`, `1d`, `1w`, `1mo`
- `FvgFillStatus`：`UNTOUCHED`, `PARTIAL`, `FILLED_50`, `FULLY_FILLED`
- `FvgValidityStatus`：`VALID`, `INVALIDATED`

## 强类型数据块

`Bar`、`IndicatorResult`、`EquityFundamentals`、`CryptoMarketContext`、`FvgResult`、`DivergenceResult`、`VpvrResult`、`AnalysisSnapshot`、`AiAnalysisTask`、`AiAnalysisResult` 和 `AnalysisOutcome` 均由 Pydantic 模型生成 JSON Schema，未知字段默认拒绝。不存在 `data:{}` 或任意业务 object。

VPVR 默认 `BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE`，必须输出 `calculation_mode`、`input_granularity`、`algorithm_version`、`is_estimate` 和 `limitations`，不得称为逐笔成交画像。

FVG 使用 `FVG_WICK_3BAR_ATR_FROZEN_V1`：创建时冻结 ATR14；`fill_status` 与 `validity_status` 分离，失效缓冲使用创建时 ATR 的 0.1 倍。

## MCP 工具投影

| Tool | REST operationId | Read only | Cost side effect |
|---|---|---:|---:|
| `market_get_indicator_evidence` | `getIndicatorEvidence` | true | false |
| `market_search_instruments` | `searchInstruments` | true | false |
| `market_get_bars` | `getBars` | true | false |
| `market_get_indicator_bundle` | `getIndicatorBundle` | true | false |
| `market_get_equity_fundamentals` | `getEquityFundamentals` | true | false |
| `market_get_crypto_context` | `getCryptoMarketContext` | true | false |
| `macro_get_context` | `getMacroContext` | true | false |
| `market_create_analysis_snapshot` | `createAnalysisSnapshot` | false | false |
| `market_run_ai_analysis` | `createAiAnalysisTask` | false | true |
| `market_get_ai_task` | `getAiAnalysisTask` | true | false |
| `market_get_ai_analysis` | `getAiAnalysis` | true | false |
| `market_get_analysis_outcomes` | `getAnalysisOutcomes` | true | false |

MCP input/output schema 与 REST 共用同一 Pydantic 模型。基础版不发布 account/order/withdraw/transfer 工具。`market_get_analysis_outcomes` 仅用于事后研究，不可被分析提示词工具链调用。

## 生成与验证

- JSON/YAML OpenAPI 均从同一个内存对象生成并进行深度一致性比较。
- OpenAPI 对 AI 结果使用 `./ai-analysis.schema.json` 外部引用。
- Draft 2020-12 validator 验证 AI Schema、外部引用、合法样例和多份非法样例。
- OpenAPI 3.1 validator 分别验证 JSON 与 YAML，并解析外部引用。
- MCP 的 `restOperationId` 必须存在于 OpenAPI；禁止工具和路由名称由测试阻断。
