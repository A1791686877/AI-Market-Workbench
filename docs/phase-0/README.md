# AI Market Workbench — 第0阶段交付索引

- 版本：0.1.1-corrected
- 基准日期：2026-09-05（Asia/Shanghai）
- 状态：有条件通过；指定修正已进入自动验证，Phase 1 只读垂直切片已开始
- 安全边界：只读；没有真实交易账户、下单、转账、提币或交易权限

## 交付物

1. [01-PRD.md](01-PRD.md)：正式产品需求、范围、验收标准。
2. [02-ARCHITECTURE.md](02-ARCHITECTURE.md)：系统架构、数据流、时间与复权口径、存储和安全边界。
3. [03-OSS-REVIEW.md](03-OSS-REVIEW.md)：候选开源项目的采用、仅参考、拒绝采用清单。
4. [04-DATA-SOURCES.md](04-DATA-SOURCES.md)：数据源、历史覆盖、许可和采购闸门。
5. [05-AI-PROTOCOL.md](05-AI-PROTOCOL.md)：REST/OpenAPI、MCP、模型供应商层和结构化数据协议。
6. [ai-analysis.schema.json](ai-analysis.schema.json)：AI分析结果 JSON Schema（Draft 2020-12）。
7. [openapi.json](openapi.json) 与 [openapi.yaml](openapi.yaml)：均由 `app/models.py`、`app/main.py` 和 `app/mcp_contract.py` 自动生成的镜像，禁止手工双维护；唯一真源在代码模型与路由。
8. [06-INDICATORS.md](06-INDICATORS.md)：指标、FVG、背离、VPVR 的精确算法和验收样例。
9. [07-ROADMAP-COST-RISK.md](07-ROADMAP-COST-RISK.md)：阶段、工期、成本、风险和后续交易隔离路线。
10. [08-DECISIONS.md](08-DECISIONS.md)：已定设计、采购前置条件和待确认项。
11. [09-RESEARCH-LOG.md](09-RESEARCH-LOG.md)：官方证据索引、审查边界和实现前复核清单。
12. [10-VALIDATION.md](10-VALIDATION.md)：语法、引用、只读接口边界的校验结果与未执行项。

## 本阶段结论

- 推荐采用“模块化单体 + 独立采集工作进程”，不在基础版过早拆微服务。
- Phase 1 数据预算冻结为 USD 0/月：先以明确标注的合成 fixture 打通，再按 Alpaca Basic、Tiingo Free、Massive Basic、SEC、Binance 公共接口、CoinGecko Demo 的顺序做真实只读验证；Massive Starter 与 Tiingo Power 均不采购。
- 加密行情暂定 Binance 的无密钥公开/数据专用端点及官方历史归档，但仅作本地个人研究的“有条件采用”；部署前必须通过地区可用性和数据使用条款检查，不绕过地域限制。清算流是采样快照，不能称为完整清算市场。
- 基本面以 SEC EDGAR/XBRL 为事实主源；非 GAAP 项目必须保留公司原始定义，不能与 GAAP 混合。
- 指标全部由版本化确定性程序计算；LLM 只读取冻结快照并输出多情景解释。事后收益写入独立表，禁止进入当时分析输入。
- 195 分钟是美股日内更自然的补充周期（正常 390 分钟交易日正好两根）；保留需求中的 4 小时周期并显式标记第二根仅 150 分钟。2 小时周期仍会留下 30 分钟尾柱，优先级低于 195 分钟。

## 证据边界

开源和许可证审查是截至基准日期的立项初筛，不等于完整法律意见或对任意未来依赖版本的安全背书。Phase 1 已固定直接依赖版本并启用正式 OpenAPI/JSON Schema 校验与依赖审计；许可和供应链审查仍需随每次版本升级复核。
