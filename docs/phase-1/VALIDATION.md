# Phase 1 验证记录

> 更新：以下 22 项 / 10 工具 / 28 请求记录为上一轮历史记录，不是当前完整验收结论。当前契约有 11 个 MCP 工具，自动测试 30 项通过；真实模型和真实 AI MCP 结果见 REAL-AI-ACCEPTANCE.md 及 reports/ai-evidence-verification.json。28/28 仅表示当次请求成功；现货旧成交量归一化有误、当前周/月尾柱也已修正，旧轮量相关指标及未闭合聚合不计入本轮验收。

日期：2026-09-06  
环境：Windows、Python 3.12、本机 `127.0.0.1`、项目与运行资产均在 E 盘

| 检查 | 最终结果 |
|---|---|
| 契约生成 | OpenAPI JSON/YAML、AI JSON Schema、MCP projection、协议矩阵、正反样例成功 |
| 正式 validator | OpenAPI 3.1 外部引用、Draft 2020-12、JSON/YAML 深度一致性通过 |
| Schema 样例 | 1 份合法通过；5 份非法按预期失败 |
| 自动测试 | **22 passed**；包含指标、日历、FVG、VPVR、真实 stdio MCP、fail-closed/stale |
| MCP | 官方客户端与真实 stdio 子进程握手；协议 `2026-07-28`；10 个工具；运行时输入/输出字段与真源一致 |
| BTC/ETH live | 4 instruments × 7 intervals = **28 passed, 0 failed**；全部 OKX、0 synthetic fallback |
| Binance | 公共端点 HTTP 451；失败记录保留 |
| AAPL providers | Alpaca/Tiingo/Massive/SEC 探针均 `NOT_CONFIGURED`；没有虚报成功 |
| 依赖一致性 | `pip check`: No broken requirements found |
| 漏洞与 SBOM | 91 个依赖；`pip-audit 2.10.1` 为 **0 known vulnerabilities**；CycloneDX 91 components |

验证中发现并修正：

1. 周/月 K 因变长周期曾被错误判为历史完整；改为周 7 天、月 31 天仅作起点容差后，真实 BTC 日/周/月均正确显示不完整。
2. OKX 每页重建 TLS 连接导致约 1 页/秒；改为单 provider 连接复用。
3. MCP 先返回 5,000 bars 后超过 30 秒缓存 TTL，指标调用重复下载；TTL 改为可配置 300 秒，同时继续显示真实 `source_at/received_at`。
4. 旧 RTH 逻辑只识别工作日和固定时钟；换成 `exchange_calendars` XNYS 并新增 2026-07-03 假日、2026-11-27 半日市验收。
5. 安全测试仍导入旧近似 RTH 函数；更新为正式日历入口后全套通过。

机器证据位于：

- `reports/live-crypto-acceptance.json`
- `reports/mcp-live-stdio-acceptance.json`
- `reports/us-free-source-probe.json`
- `reports/binance-public-probe.json`
- `reports/okx-public-probe.json`
- `reports/pip-audit.json`
- `reports/sbom.cdx.json`
- `logs/audit/audit.jsonl`

当前结论只覆盖已实测范围，不代表 AAPL、真实 LLM、几十只自选股或约 500 标的已经通过。

## 2026-09-06 后续质量修正

上文是历史阶段记录。当前真实 LLM/MCP 链路此前已经完成，但本轮复用其输出发现语义错误，故链路成功不等于回答正确。当前正式契约+44项回归、28组独立逐点比对、多周期公共快照与后验回填证据见 QUALITY-2026-09-06.md；AAPL与完整历史仍未通过，新增多周期模型回答没有追加真实调用验收。

## 2026-09-12（第四轮）：当前有效结论

| 检查 | 结果 |
|---|---|
| 契约生成 + 正式 validator | PASS（OpenAPI JSON/YAML 外部引用、Draft 2020-12、1 合法 + 5 非法样例、MCP/REST 映射） |
| 自动测试 | **118 passed**（`-W error`，`scripts\validate.ps1` 一次跑通） |
| 依赖一致性 | `pip check`：No broken requirements found |
| GOOG 全链路（真实数据 + 1 次真实模型） | Alpaca 日线/15m → 10/10 指标 → SEC 12 项基本面 → 44 条证据快照 → `CODEX_CLI`/`gpt-6-astra` SUCCEEDED，证据校验 PASS；报告 `reports/goog-acceptance-20260912T151838Z.json` |
| 美股展望 + 登记 + 校验 | 25 条 registry（16 已验证 / 9 OPEN，含 1 条 BTC）；回放 16 条：A=3 B=5 C=7 UNRESOLVED=1，主路径命中 7 / 未命中 8 / 未定 1；当日最接近 A 路径 = NVDA |
| 第三源与供应量 | Massive 日线实测通过（含 403/429 边界与 12.5s 限速）；CoinGecko BTC 流通 20,083,337 / 市值 1.5554T USD，独立来源不替换 OKX |

**仍然是未完成/未验收的**：批量（几十~500 标的）扫描、清算/链上/ETF 流/期权/宏观、
`PE_TTM` 政策、2026-09-05 之前 22 份 schema 不兼容的历史快照、以及所有 key 的轮换
（本轮 key 曾在对话明文出现）。计分板数字是**小样本事后计数，不是胜率**。
