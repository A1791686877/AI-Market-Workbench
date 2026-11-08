# Phase 0 修正与正式校验记录

校验日期：2026-09-05  
真源：`app/models.py`、`app/main.py`、`app/mcp_contract.py`  
生成器：`scripts/generate_contracts.py`

## 对旧结论的更正

旧版仅做 JSON 解析和局部引用检查，却写成“已验证”，且 `bull/neutral/bear` 与 `BULLISH/NEUTRAL/BEARISH` 存在大小写冲突。该结论不成立，现已明确撤回并由下面的正式校验结果取代。`docs/phase-0/openapi.json`、`openapi.yaml`、`ai-analysis.schema.json` 和 `05-AI-PROTOCOL.md` 现在都只是自动生成镜像，禁止手工维护。

## 已执行并通过

- `openapi-spec-validator==0.9.0` 校验 OpenAPI 3.1 JSON 与 YAML，并以文件基址解析外部 `$ref`。
- `jsonschema==4.26.0` 的 Draft 2020-12 validator 校验 AI 分析 schema 及外部引用。
- 合法样例 1 份通过；非法样例 5 份必须失败，覆盖错误大小写、情景重复、缺少必填字段、未知字段和错误哈希格式。
- OpenAPI JSON 与 YAML 由同一内存对象生成，反序列化后深度相等。
- MCP tool 的 `operationId` 必须映射到同一 REST operation，输入输出引用同一强类型 schema。
- Bar、技术指标、基本面、市场上下文、FVG、背离、VPVR、AI 任务、错误、分页和 outcomes 均进入强类型契约；没有 `data: {}` 或任意业务 `object` 占位。
- AI 分析固定为 `SINGLE_UNDERLYING`：股票是一只标的；加密是一种底层资产及其现货+USDT 永续，不是组合分析。
- VPVR 强制标记 `BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE`、`input_granularity`、`algorithm_version`、`is_estimate` 和 `limitations`。
- FVG 的 `fill_status` 与 `validity_status` 分离，并冻结创建时 `atr_at_creation`，规则版本为 `FVG_WICK_3BAR_ATR_FROZEN_V1`。

## 边界

- 上述结论证明当前生成契约与样例一致，不等于证明供应商数据正确、许可证覆盖未来公开部署或项目没有未知漏洞。
- Phase 1 当前只运行明确标记为 `FIXTURE_SYNTHETIC` 的离线测试数据；尚未调用真实行情、模型或账户 API。
- 完整 Phase 1 测试、依赖审计和运行证明记录在 `docs/phase-1/VALIDATION.md`。
