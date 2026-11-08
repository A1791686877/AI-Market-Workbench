# AGENTS.md — AI 开发者速查导航与项目修改日志 (AI Quick-Start & Codebase Map)

> **给后续接手本项目的 AI（Antigravity / Codex / Claude Code / Cursor / Hermes）**：
> 打开本项目时**先读本文件**，无需全库盲搜。本文件直接告诉你：**每个模块在哪、核心函数在第几行、想改某项功能该去哪个文件改、有哪些绝对不能踩的测试/契约/环境红线**。
> 完整架构演进与改动记录同步存放在同目录 [`AI_DEVELOPER_LOG.md`](./AI_DEVELOPER_LOG.md)。

---

## 1. 铁律与环境红线（动手前必看）

1. **绝对只读禁令（最高优先级）**：
   - 本项目**严禁出现任何下单、撤单、改单、平仓、划转、提币、账户私钥/交易签名代码**。
   - [`scripts/validate_contracts.py`](./scripts/validate_contracts.py) 会扫描整个 `contracts/` 与路由，一旦发现 `order`、`trade`、`withdraw`、`transfer`、`execution` 等写操作词汇会直接报错阻断。
2. **Python 环境与命令铁律**：
   - 本机全局裸 `python` 是损坏的环境，且系统环境变量 `PYTHONPATH` 会污染依赖。
   - **一律使用项目虚拟环境并清空 `PYTHONPATH`**：
     ```powershell
     $env:PYTHONPATH=""
     & "E:\AI-Market-Workbench\.venv\Scripts\python.exe" -E -B ...
     ```
   - `.venv\pyvenv.cfg` 已指向本机可用运行时 `D:\xsum\py\cpython-3.12.14-windows-x86_64-none`。
   - `pyproject.toml` 已配置 `cache_dir = ".pytest_cache_local"`（旧 `.pytest_cache` 目录归属 `Administrator` 不可写，切勿改回默认）。
3. **本机代理**：
   - `live` 模式拉取 OKX / Tiingo / Alpaca / FRED / SEC 数据时走本机代理 `http://127.0.0.1:7897`（设置 `NO_PROXY=127.0.0.1,localhost`）。
4. **OpenAPI / MCP 契约门禁铁律**：
   - `contracts/openapi.json`、`contracts/openapi.yaml`、`contracts/ai-analysis.schema.json`、`contracts/mcp-tools.json` 全部由 [`scripts/generate_contracts.py`](./scripts/generate_contracts.py) 从运行时自动生成。
   - **凡是在 `app/main.py` 新增供前端内部使用的聚合路由，如果不打算同步写进 [`app/mcp_server.py`](./app/mcp_server.py) 的 `TOOLS` 列表，必须在 `@app.get(...)` 上加 `include_in_schema=False`**（否则 `scripts/validate_contracts.py` 的 REST↔MCP 1:1 映射检查会直接失败！）。

---

## 2. 「想改啥 → 去哪改」一秒定位表 (Modification Cookbook)

| 你想修改 / 扩展的功能 | 直接去改哪个文件 | 关键函数 / 行号定位 | 修改后必跑验证 |
| :--- | :--- | :--- | :--- |
| **修改用户专属交易策略规则**（周K底背离 A / 周月MACD上行 B / 日K+VPVR主峰头仓 1/3 / 加仓 / 止盈 / 风控核对单） | [`app/strategy_engine.py`](./app/strategy_engine.py) | `evaluate_strategy()` (`L45-L348`) | `pytest tests/test_strategy_and_workbench.py -W error` |
| **修改 VPVR 48 桶主峰(HVN)/真空区(LVN) 聚类阈值或指标斜率算法** | [`app/market_dynamics.py`](./app/market_dynamics.py) | `extract_vpvr_zones()` (`L22-L133`)<br>`compute_indicator_dynamics()` (`L136-L273`) | `pytest tests/test_strategy_and_workbench.py -W error` |
| **修改背离有效/失效规则，或右侧 `3×1` 正在形成候选背离逻辑** | [`app/divergence_audit.py`](./app/divergence_audit.py) | `detect_forming_divergences()` (`L97-L185`)<br>`audit()` (`L188-L265`) | `pytest tests/test_divergence_audit.py tests/test_strategy_and_workbench.py -W error` |
| **修改底层 `3×3` 枢轴背离、VPVR 48 桶分箱、FVG、SMA/EMA/RSI/MACD/ATR/ADX 数学计算** | [`app/indicators.py`](./app/indicators.py) | `compute_indicators()` (`L198-L299`)<br>`compute_fvg()` (`L302-L353`)<br>`compute_divergences()` (`L356-L461`)<br>`compute_vpvr()` (`L510-L569`) | `pytest tests/test_indicators.py -W error` |
| **修改喂给大模型（Codex CLI / 外部 AI）的 Prompt 与公开上下文结构** | [`app/model_provider.py`](./app/model_provider.py) | `public_input(snapshot)` (`L46-L154`)<br>`CodexCliProvider.generate()` (`L202-L295`)<br>⚠️ 注意：`recent_confirmed_bars` 长度必须 `<= 8`（`tests/test_security_and_ semantics.py` 强制校验） | `pytest tests/test_security_and_semantics.py -W error` |
| **修改免费规则演示分析（`DETERMINISTIC_DEMO`）生成的三情景文案与关键位数值** | [`app/service.py`](./app/service.py) | `MarketWorkbenchService._generate_analysis()` (`L317-L420`) | `pytest tests/test_e2e.py -W error` |
| **修改多周期结构分 (`structure_score`) / 动量分 (`momentum_score`) 与 A/B/C 三情景概率权重** | [`app/outlook.py`](./app/outlook.py) | `score_timeframe()` (`L140-L238`)<br>`scenario_weights()` (`L241-L320`)<br>`build_outlook()` (`L410-L515`) | `pytest tests/test_outlook.py -W error` |
| **修改新增/删减展示标的（当前 11 标的：BTC/ETH + 9 美股）** | [`app/fixtures.py`](./app/fixtures.py) + [`app/workbench.py`](./app/workbench.py) + [`static/index.html`](./static/index.html) | `SUBJECTS` (`app/fixtures.py L25-L215`)<br>`ALL_WORKBENCH_SUBJECT_IDS` (`app/workbench.py L54-L94`)<br>`<select id="subject">` (`static/index.html L392-L404`) | `pytest -W error` |
| **修改 Web UI 界面布局、K线/VPVR/MACD/RSI SVG 图表、雷达表、策略作战室** | [`static/index.html`](./static/index.html) + [`static/real-ai.js`](./static/real-ai.js) | 见下方第 4 节《前端结构与不可破坏的 DOM 选择器契约》 | `pytest -W error` |
| **修改 Web 工作台后端聚合数据（11 标的雷达总览、单标的六周期矩阵、基本面双锚、展望记分牌）** | [`app/workbench.py`](./app/workbench.py) | `get_workbench_overview()` (`L499-L577`)<br>`get_subject_workbench_detail()` (`L648-L810`)<br>`get_registry_summary()` (`L406-L435`) | `pytest tests/test_strategy_and_workbench.py -W error` |
| **修改公网分享权限边界（允许/拦截外网访客访问与额度保护）** | [`app/main.py`](./app/main.py) | `local_origin_boundary()` (`L103-L140`)<br>`create_ai_analysis_task()` (`L288-L328`) | `pytest tests/test_e2e.py -W error` |
| **修改公開 API 契约字段或新增对外正式 REST + MCP 接口** | [`app/models.py`](./app/models.py) + [`app/main.py`](./app/main.py) + [`app/mcp_server.py`](./app/mcp_server.py) | 修改后**必须运行** `scripts/generate_contracts.py` 重新生成 `contracts/*` | `scripts/generate_contracts.py` + `scripts/validate_contracts.py` |

---

## 3. 全仓目录与文件地图 (Complete Directory & File Map)

```text
E:\AI-Market-Workbench\
├── AGENTS.md                        # [AI必读] 快速定位地图、修改指南与红线约束（本文件）
├── AI_DEVELOPER_LOG.md              # [AI必读] 详细架构演进日志、数据流图解与排障手册
├── README.md                        # 项目说明与 CLI 脚本速查
├── pyproject.toml                   # Python 包配置（含 cache_dir = ".pytest_cache_local"）
├── config/
│   └── providers.env                # [本地机密，勿打印到聊天] Alpaca/Tiingo/TwelveData/AV/Massive/FRED/SEC/CoinGecko Key
├── app/                             # 核心后端代码（FastAPI + 指标引擎 + 策略引擎 + 数据源适配）
│   ├── main.py                      # FastAPI 入口、Host/Origin 边界中间件、REST 路由、Workbench 聚合路由
│   ├── config.py                    # Settings 配置加载（AMW_DATA_MODE, AMW_NETWORK_ENABLED, AMW_PUBLIC_READONLY_SHARE 等）
│   ├── models.py                    # Pydantic 核心数据契约真源（Bar, IndicatorBundle, AnalysisSnapshot, AIAnalysisResult 等）
│   ├── service.py                   # MarketWorkbenchService：行情调度、快照冻结、DETERMINISTIC_DEMO / CODEX_CLI 任务流转
│   ├── workbench.py                 # [核心聚合] 11 标的缓存优先读取、雷达总览、六周期共振矩阵、PE双锚、宏观与登记簿组装
│   ├── strategy_engine.py           # [核心策略] 用户交易策略引擎（周/月K定方向 A/B + 日K底背离 + VPVR主峰分批建仓/止盈/风控）
│   ├── market_dynamics.py           # [核心动态] 48桶 VPVR 聚类（HVN主峰/LVN真空区）+ 均线/MACD/RSI多棒斜率 + 20棒轨迹
│   ├── divergence_audit.py          # [双轨背离] 3×3 确认背离有效/失效审计 + 3×1 右侧正在形成候选背离检测
│   ├── indicators.py                # 确定性数学指标库：SMA/EMA/RSI/MACD/ATR/ADX、FVG、3×3 枢轴背离、48桶 VPVR
│   ├── bar_builder.py               # 15m/1d 基础 K 线聚合为 1h/2h/195m/4h/1w/1mo（严格按交易日历/自然周月收线确认）
│   ├── outlook.py                   # 多周期结构分 (-100..+100) × 动量分 (-100..+100) 与 A/B/C 三情景概率权重引擎
│   ├── outlook_registry.py          # 展望事前登记簿（FORWARD 事前锁死 vs REPLAY 历史回放，防事后诸葛亮）
│   ├── semantic_review.py           # AI 报告防幻觉语义审计（检查证据ID引用、是否误报已失效背离、VPVR估算声明）
│   ├── model_provider.py            # AI 上下文构建 (`public_input`) 与 `CodexCliProvider` 调用封装
│   ├── store.py                     # SQLite 元数据存储 (`data/workbench.sqlite3`) + 不可变快照/分析 JSON 落盘校验
│   ├── fixtures.py                  # 11 标的定义 (`SUBJECTS`)、离线合成 15m K 线 (`BARS`)、离线财报与宏观数据
│   ├── market_cache.py              # 本地磁盘行情缓存读写与增量合并 (`cache/market/...`)
│   ├── live_providers.py            # OKX 公共行情、Alpaca、Tiingo、Twelve Data、Alpha Vantage、Massive、CoinGecko 适配器
│   ├── sec_edgar.py                 # SEC EDGAR XBRL 点时财报解析与 Q4 = FY - 9M 单季拆分
│   ├── fred_provider.py             # FRED / ALFRED 点时宏观序列适配器（DGS10, DGS2, T10Y2Y, FEDFUNDS, CPI, DTWEXBGS, VIX）
│   └── mcp_server.py                # MCP Server 暴露工具定义（与正式 OpenAPI 路由 1:1 映射）
├── static/                          # 前端交互工作台（零构建依赖，原生 HTML5 + SVG + JS）
│   ├── index.html                   # 11 标的雷达总览 + 三联 SVG 图表(K线/48桶VPVR/FVG/双轨背离/MACD/RSI) + 策略作战室 + 六周期矩阵
│   └── real-ai.js                   # 七周期快照核对 (`#snapshotCheck`) 与本机 Codex CLI 真实大模型触发器 (`#realAiAnalyze`)
├── contracts/                       # [自动生成，勿手改] OpenAPI 3.1 JSON/YAML、JSON Schema、MCP Tools 清单
├── cache/                           # 本地真实行情、SEC 财报、FRED 宏观磁盘缓存（毫秒级加载）
│   ├── market/
│   │   ├── okx-base-volume-v2/      # BTC / ETH Spot & Perp 的 15m/1h/2h/4h/1d/1w/1mo 真实 K 线缓存
│   │   ├── tiingo/                  # 美股 9 标的 20 年全复权日线 (`1d`) 缓存（各 ~5000 根）
│   │   ├── alpaca/                  # 美股 9 标的 15m 日内与日线缓存
│   │   └── massive/                 # 第三交叉验证源日线缓存
│   ├── sec/                         # SEC EDGAR 原始 CompanyFacts JSON 与解析后点时财务快照
│   └── fred/                        # FRED 宏观指标序列点时缓存
├── data/                            # 运行时持久化数据
│   ├── workbench.sqlite3            # 快照、任务、分析结果、额度账本 SQLite 数据库
│   ├── snapshots/                   # 冻结的 AnalysisSnapshot JSON 文件（含 SHA-256 指纹）
│   ├── analyses/                    # 生成的 AIAnalysisResult JSON 文件（含 CODEX_CLI 真实大模型多周期报告）
│   └── outlook/registry.jsonl       # 事前展望登记簿（FORWARD / REPLAY）
├── scripts/                         # 运维、契约生成、验收与批处理脚本
└── tests/                           # 168 个单元与端到端测试（`pytest -W error` 零警告通过）
```

---

## 4. 前端 (`static/index.html` & `static/real-ai.js`) 不可破坏的 DOM 契约

本项目包含两套自动化浏览器验收脚本（[`scripts/accept_browser.cjs`](./scripts/accept_browser.cjs) 与 [`scripts/accept_quality_browser.cjs`](./scripts/accept_quality_browser.cjs)）。在修改前端 UI 时，**必须保留以下 DOM ID、属性与全局变量结构**：

1. **核心控件 ID**：
   - `#subject`（标的下拉框）、`#leg`（`SPOT` / `PERP` 交易腿下拉框）、`#instrumentSelect`（具体品种下拉框）、`#interval`（周期下拉框）、`#load`（加载按钮）、`#refresh`（刷新按钮）、`#analyze`（免费规则演示分析按钮）、`#snapshotCheck`（仅核对七周期快照按钮）、`#realAiAnalyze`（调用真实 AI 按钮）。
2. **状态与摘要节点**：
   - `#status`：加载完成后 `.textContent` 必须包含 `'已加载'`。
   - `#summary`、`.scenario`、`#analysis`、`#evidence`、`#context`。
3. **SVG 图表必含选择器（`renderChart` 内）**：
   - K 线柱：`<g data-bar-id="${b.bar_id}">`
   - VPVR 三条核心线：`<g data-level="POC">`、`<g data-level="VAH">`、`<g data-level="VAL">`，且内部 `<title>` 文本必须以 `${name} ${price}` 开头（验收脚本用正则解析 `<title>` 里的价格并与 API 返回的 `bundle.vpvr` 比对！）。
   - FVG 框：`<rect data-fvg-id="${f.fvg_id}">`，且内部 `<title>` 包含 `f.lower` 和 `f.upper`。
   - `3×3` 已确认背离画线：`<g data-divergence-id="${d.divergence_id}">`，且必须过滤 `Date.parse(d.signal_at) <= Date.parse(bars.at(-1).close_at)`。
   - **`3×1` 右侧正在形成候选背离画线**：必须使用 `data-forming-div-id`（**切勿使用 `data-divergence-id`**，否则验收脚本拿它去 `bundle.divergences` 里反查 ID 会报错）。
4. **全局变量 `window.current`**：
   - `current.bars` 必须是完整的 `barsPage` 对象（即 `current.bars.bars` 是 K 线数组，`current.bars.instrument.instrument_id` 是品种 ID），验收脚本 [`scripts/accept_quality_browser.cjs`](./scripts/accept_quality_browser.cjs) 会直接读取 `window.current.bars.bars.length` 和 `window.current.bars.instrument.instrument_id`。
5. **七周期快照核对格式（[`static/real-ai.js`](./static/real-ai.js)）**：
   - 点击 `#snapshotCheck` 后，`#analysis` 内必须包含 `'公开市场上下文'` 字样，且每条覆盖状态格式为 `${c.instrument_id} / ${c.interval} / ${c.role} / ${c.status} / ...`（确保 BTC/ETH 7 周期 × 2 品种共匹配到 14 个 `/ AVAILABLE /`）。

---

## 5. 常用验证、启动与公网分享命令速查

### 5.1 跑契约生成、契约校验与全量 168 项测试（修改代码后必跑）
```powershell
Set-Location E:\AI-Market-Workbench
$env:PYTHONPATH=""
& "E:\AI-Market-Workbench\.venv\Scripts\python.exe" -E -B scripts/generate_contracts.py
& "E:\AI-Market-Workbench\.venv\Scripts\python.exe" -E -B scripts/validate_contracts.py
& "E:\AI-Market-Workbench\.venv\Scripts\python.exe" -E -B -m pytest -W error -q
```

### 5.2 启动本机 + 公网可分享模式 Web 服务（端口 8080）
```powershell
Set-Location E:\AI-Market-Workbench
$env:PYTHONPATH=""
$env:AMW_DATA_MODE="live"
$env:AMW_NETWORK_ENABLED="true"
$env:AMW_PUBLIC_READONLY_SHARE="true"
$env:HTTP_PROXY="http://127.0.0.1:7897"
$env:HTTPS_PROXY="http://127.0.0.1:7897"
$env:NO_PROXY="127.0.0.1,localhost"
& "E:\AI-Market-Workbench\.venv\Scripts\python.exe" -E -B -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

### 5.3 一键拉起 Cloudflare 公网交互隧道（无需账号）
```powershell
$env:HTTP_PROXY="http://127.0.0.1:7897"
$env:HTTPS_PROXY="http://127.0.0.1:7897"
$env:NO_PROXY="127.0.0.1,localhost"
& "E:\CodexTools\bin\cloudflared.exe" tunnel --url http://127.0.0.1:8080 --protocol http2 --no-autoupdate
```
（启动后控制台输出的 `https://xxxx.trycloudflare.com` 即为可发给他人直接交互的公网链接。）
