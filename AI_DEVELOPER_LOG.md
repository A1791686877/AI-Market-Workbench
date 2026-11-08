# AI_DEVELOPER_LOG.md — 项目全量架构日志、函数索引与修改指南

> **用途**：供任何后续 AI 助手或开发者一眼掌握 `E:\AI-Market-Workbench` 的完整设计、模块职责、函数级行号索引、API 路由清单、磁盘缓存格式及历次升级日志，**无需逐个打开文件盲找**。
> 快速速查版见同目录 [`AGENTS.md`](./AGENTS.md)。

---

## 一、 项目整体数据流与五层架构

```text
[数据源层 (Read-Only)]
  ├─ Crypto: OKX Public REST (Spot + USDT Perp: 15m/1h/2h/4h/1d/1w/1mo) + CoinGecko Demo (Supply/MCap)
  ├─ US Equity EOD: Tiingo (20年全复权日线 1d, 聚合生成 1w / 1mo) + Massive (交叉验证)
  ├─ US Equity Intraday: Alpaca SIP/IEX & Twelve Data (15m, 聚合生成 1h / 195m / 4h)
  ├─ Fundamentals: SEC EDGAR XBRL CompanyFacts (点时 filing_date 过滤 + Q4 = FY - 9M 拆分 + PE_TTM/PE_FY 双锚)
  └─ Macro: FRED / ALFRED Vintage 点时宏观序列 (DGS10, DGS2, T10Y2Y, FEDFUNDS, CPIAUCSL, DTWEXBGS, VIXCLS)
        │
        ▼ (磁盘缓存优先 `cache/market/*`, `cache/sec/*`, `cache/fred/*`)
[确定性计算与结构层]
  ├─ `app/bar_builder.py`       : 15m/1d 基础 K 线按市场日历无未来函数聚合为 1h/2h/195m/4h/1w/1mo
  ├─ `app/indicators.py`        : SMA20/50/200、EMA20、RSI14、MACD(12,26,9)、ATR14、ADX14、FVG、3×3 枢轴背离、48桶 VPVR
  ├─ `app/divergence_audit.py`  : 双轨背离雷达 = 3×3 确认背离有效/失效审计 + 3×1 右侧正在形成候选背离检测
  └─ `app/market_dynamics.py`   : 48桶 VPVR 聚类为高量主峰区(HVN)/低量真空区(LVN) + 多棒均线/MACD/RSI斜率 + 近20棒轨迹
        │
        ▼
[策略与多周期展望层]
  ├─ `app/strategy_engine.py`   : 用户专属策略引擎（周/月K定方向 Mode A/B + 日K底背离 + VPVR主峰分批建仓 1/3 + 突破加仓 + 止盈 + 四维风控核对单）
  ├─ `app/outlook.py`           : 多周期结构分 (-100..+100) × 动量分 (-100..+100) → A(上破)/B(震荡)/C(下破) 三情景概率权重
  └─ `app/outlook_registry.py`  : 事前展望登记簿 (`data/outlook/registry.jsonl`，严格分离 FORWARD 事前预测 vs REPLAY 历史回放)
        │
        ▼
[AI 推演与防幻觉审计层]
  ├─ `app/service.py`           : 冻结不可变快照 (`AnalysisSnapshot`) + 免费规则引擎 (`DETERMINISTIC_DEMO`) + 任务调度
  ├─ `app/model_provider.py`    : 构建公开输入上下文 (`public_input`，含 `market_dynamics`) + 调用本机 `CodexCliProvider` (`CODEX_CLI`)
  └─ `app/semantic_review.py`   : 对 AI 输出做确定性防幻觉复核（拦截伪造 Evidence ID、拦截把已失效背离当有效背离、检查 VPVR 估算声明）
        │
        ▼
[接口与前端交互工作台层]
  ├─ `app/main.py`              : FastAPI 正式契约路由 (`/v1/...`) + 工作台内部聚合路由 (`/v1/workbench/...`) + 公网分享额度锁
  ├─ `app/mcp_server.py`        : MCP Server 工具暴露（与正式 OpenAPI 路由 1:1 对应）
  └─ `static/index.html` + `static/real-ai.js` : 11 标的雷达总览 + 三联 SVG 交互图表 + 策略作战室 + 六周期共振矩阵 + 真实 AI 报告检阅器
```

---

## 二、 核心模块逐文件、逐函数精确索引

### 1. [`app/strategy_engine.py`](./app/strategy_engine.py) — 用户专属交易策略引擎
- **核心入口**：`evaluate_strategy(daily_bars, *, symbol, instrument_id, market, weekly_bars=None, monthly_bars=None, intraday_bars=None, h4_bars=None, fundamentals_summary=None, macro_summary=None)` (`L45-L348`)
- **严格实现的策略逻辑（禁止擅自添加“周K死叉清仓”等未授权规则）**：
  1. **大周期方向判定 (`L86-L154`)**：
     - 自动从日线聚合周线 (`1w`) 与月线 (`1mo`)，计算 `compute_indicator_dynamics()`。
     - **条件 A (`cond_a_weekly_bottom_div`)**：周K 存在未失效的 `3×3` 确认底背离 (`live_bottom`) 或 `3×1` 右侧成形底背离 (`forming_bottom`)。
     - **条件 B (`cond_b_macro_up`)**：周K 或 月K 的 MACD 柱状图处于上行状态（`BULL_EXPANDING` / `BEAR_CONVERGING` / `TURNED_POSITIVE` 或金叉）且 `RSI(14) < 70`。
     - 归类为三种大周期模式：`MODE_A_REVERSAL`（满足 A，主升反转模式）、`MODE_B_REBOUND`（仅满足 B 无 A，反弹波段模式）、`MODE_WAIT_HOLD`（均不满足，观望/老仓硬抗等待）。
  2. **日K 建仓、加仓与止盈 (`L156-L310`)**：
     - 读取 `daily_dyn["vpvr_zones"]` 判断当前价格是否处于日K VPVR 高量主峰区 (`in_main_peak_zone`)，以及是否存在日K 底背离 (`has_daily_bottom_div`)。
     - 计算具体价格档位：`entry_tranche_1_zone`（头仓 1/3 VPVR 主峰区间）、`add_trigger_price`（突破上一 VPVR 峰值再加 1/3）、`tp_target_1` / `tp_target_2`（上方下一 VPVR 峰值止盈位）。
     - 检查 `4h` / `1h` 是否出现小级别顶背离 (`small_tf_top_div_warning`)。
     - 生成唯一明确动作代码 (`action_code`) 与操作结论 (`action_verdict`)。
  3. **四项风控核对单 (`risk_checklist`, `L312-L338`)**：
     - `仓位管理底线`（无机械止损硬抗，头仓 1/3 + 突破加仓 1/3）；
     - `高价值标的与催化剂`（结合美股双锚 `PE_TTM`/`PE_FY`、营收增速、FCF 或加密核心标的地位）；
     - `杠杆上限纪律`（杠杆 $\le 10\text{x}$，吸取历史高倍扛单教训）；
     - `右侧确认状态`（区分 `3×3` 已确认 vs `3×1` 右侧候选）。

### 2. [`app/market_dynamics.py`](./app/market_dynamics.py) — VPVR 48 桶聚类与多棒动态特征引擎
- `extract_vpvr_zones(vpvr, current_price)` (`L22-L133`)：
  - 将 `bundle.vpvr.bins`（48 个分桶）按平均桶成交量切分为高量主峰区 (`hvn_zones`, $\ge 1.15\times$ 均量) 与低量真空区 (`lvn_zones`, $\le 0.55\times$ 均量)。
  - 将相邻高量桶合并为连续筹码峰，计算每个筹码峰的 `[lower, upper]`、加权中心价 `center_price`、占全窗口成交量比例 `volume_share_pct`、是否包含 `POC`，以及相对于当前价的角色 (`CURRENT_MAIN_PEAK` / `SUPPORT_PEAK` / `RESISTANCE_PEAK`)。
- `compute_indicator_dynamics(bars, bundle)` (`L136-L273`)：
  - 汇总 `ma`（`SMA20/50/200`、`EMA20` 近 5 棒斜率与多空排列 `BULLISH_STACK` / `BEARISH_STACK`）、`macd`（零轴上下、柱状图扩张/收敛/转正/转负、金叉/死叉）、`rsi`（当前值、5 棒变化量、超买/超卖/多头/空头区间）、`adx_atr`、`trajectory_20b`（近 20 棒高低点、5/10/20 棒涨跌幅、量比、K 线形态）与 `divergence_audit`。

### 3. [`app/divergence_audit.py`](./app/divergence_audit.py) — 双轨背离审计（`3×3` 确认 + `3×1` 成形）
- `detect_forming_divergences(bars, interval)` (`L97-L185`)：
  - 检查最近 3 根已收盘 K 线（索引 `n-3`, `n-2`, `n-1`）是否构成局部高点/低点（左侧严格大于/小于前 3 根，右侧不早于局部极值），并与前一个已确认 `3×3` 枢轴比较价格与 `RSI_14` / `MACD_HIST`，输出 `forming_top` 与 `forming_bottom`。
- `audit(divergences, bars, interval)` (`L188-L265`)：
  - 对 `compute_divergences()` 产出的 `3×3` 确认背离按有效窗口（`1d` 默认 20 根、`1w` 16 根、`4h` 30 根）和锚点突破失效规则进行过滤，返回：
    - `live_top` / `live_bottom`（当前仍有效的确认顶/底背离）
    - `retired_top` / `retired_bottom`（已被价格突破前高/跌破前低而失效的历史背离）
    - `forming_top` / `forming_bottom`（右侧 3 棒内正在形成的候选顶/底背离）
    - `live_top_sentence` / `live_bottom_sentence` / `forming_sentence`（可直接展示在表格或注入 Prompt 的自然语言结论）

### 4. [`app/workbench.py`](./app/workbench.py) — 前端工作台高速缓存聚合层
- `ALL_WORKBENCH_SUBJECT_IDS` (`L54-L66`)：11 个核心标的 ID 列表（`crypto-btc`, `crypto-eth`, `us-nvda`, `us-aapl`, `us-msft`, `us-amzn`, `us-meta`, `us-googl`, `us-goog`, `us-tsla`, `us-mcd`）。
- `load_bars_cache_first(svc, instrument, interval)` (`L101-L159`)：
  - 在 `live` 模式下直接优先读取 `cache/market/{okx-base-volume-v2|tiingo|alpaca}/...` 磁盘 JSON 并按需调用 `aggregate_bars()`，避免每次刷新页面都向外部 API 发数十个请求，保证 11 标的雷达秒级响应。
- `load_fundamentals_summary(svc, subject, latest_close)` (`L201-L355`)：
  - 读取 `cache/sec/{CIK}-companyfacts.json`，按点时 `filing_date` 提取最新 4 个单季摊薄 EPS（含 `Q4 = FY - 9M` 自动推导）与最近完整财年 `10-K` EPS，计算 `pe_ttm`、`pe_fy`、`ttm_fy_ratio` 与盈利质量标记 (`NORMAL` / `ONE_OFF_GAIN_POSSIBLE` / `RECENT_ACCELERATION_OR_CHARGE`)。
- `get_workbench_overview(svc)` (`L499-L577`)：组装 `/v1/workbench/overview` 响应。
- `get_subject_workbench_detail(svc, subject_id, active_interval, instrument_id)` (`L648-L810`)：组装 `/v1/workbench/subjects/{subject_id}` 响应（含 6 周期共振矩阵、当前周期 VPVR 聚类、策略方案、展望权重、基本面双锚、已存档 `CODEX_CLI` 真实 AI 报告列表）。
- `get_registry_summary()` (`L406-L435`)：读取 `data/outlook/registry.jsonl` 并分离统计 `FORWARD` 与 `REPLAY` 记分牌。

### 5. [`app/main.py`](./app/main.py) — FastAPI 路由与安全边界
- `local_origin_boundary(request, call_next)` (`L103-L140`)：
  - 默认仅允许 `127.0.0.1` / `localhost` / `testserver`；
  - 当环境变量 `AMW_PUBLIC_READONLY_SHARE=true` 时，放行外部 `Host`（如 `*.trycloudflare.com`）访问只读查询与免费规则分析。
- `create_ai_analysis_task()` (`L288-L328`)：
  - 当外部访客（非 `LOCAL_HOSTS`）请求 `provider == ModelProviderName.CODEX_CLI` 时，直接抛出 `403 PUBLIC_SHARE_QUOTA_PROTECTED`，保护本机 Codex/ChatGPT 额度。
- **Workbench 内部路由（均带 `include_in_schema=False`，不影响正式 OpenAPI/MCP 契约）**：
  - `GET /v1/workbench/overview` (`L356`)
  - `GET /v1/workbench/subjects/{subject_id}` (`L361`)
  - `GET /v1/workbench/registry` (`L370`)

---

## 三、 历史演进与优化日志 (Changelog)

### 2026-11-09 — 全栈深度优化：VPVR 48 桶聚类 + 双轨背离 + 专属策略引擎 + 11 标的交互工作台 + 公网隧道
1. **修复虚拟环境与 Pytest 权限问题**：
   - 将 `.venv\pyvenv.cfg` 从失效的 `C:\Users\Administrator\...` 修复指向 `D:\xsum\py\cpython-3.12.14-windows-x86_64-none`；
   - 在 `pyproject.toml` 设置 `cache_dir = ".pytest_cache_local"`，消除 `-W error` 下的 `PytestCacheWarning`。
2. **新增市场动态与 48 桶 VPVR 聚类模块（[`app/market_dynamics.py`](./app/market_dynamics.py)）**：
   - 实现 `extract_vpvr_zones()` 与 `compute_indicator_dynamics()`，并将 `market_dynamics` 注入 [`app/model_provider.py`](./app/model_provider.py) 的 `public_input(snapshot)`。
3. **升级双轨背离审计（[`app/divergence_audit.py`](./app/divergence_audit.py)）**：
   - 新增 `detect_forming_divergences()`，在保留 `3×3` 已确认不重绘背离的同时，实时检测右侧 3 根 K 线内的 `3×1` 候选顶/底背离，补齐右侧盲区。
4. **新增用户专属交易策略引擎（[`app/strategy_engine.py`](./app/strategy_engine.py)）**：
   - 严格实现「周K底背离(A) / 周月MACD上行且RSI未超买(B) → 日K底背离+VPVR主峰头仓 1/3 → 突破前峰加仓 1/3 → 下一峰值受阻或小级别顶背离分批止盈 → 无止损硬抗+四维风控核对单」。
5. **新增工作台聚合层（[`app/workbench.py`](./app/workbench.py)）与全功能 Web UI（[`static/index.html`](./static/index.html)、[`static/real-ai.js`](./static/real-ai.js)）**：
   - 支持全市场 11 标的雷达总览、K线+48桶VPVR水平直方图+FVG+双轨背离+MACD+RSI 三联交互图表、策略作战室、六周期共振矩阵、SEC 双锚估值、FRED 点时宏观仪表盘、展望登记簿记分牌、以及本机已存档 `CODEX_CLI` 真实 AI 报告一键检阅。
   - 完整保留 `scripts/accept_browser.cjs` 与 `scripts/accept_quality_browser.cjs` 所需的全部 DOM 选择器与 `window.current` 结构。
6. **新增公网只读分享与额度保护模式（[`app/main.py`](./app/main.py)、[`app/config.py`](./app/config.py)）**：
   - 新增 `AMW_PUBLIC_READONLY_SHARE=true` 配置与 `PUBLIC_SHARE_QUOTA_PROTECTED` 保护锁，配合 `E:\CodexTools\bin\cloudflared.exe` 提供免账号公网可交互链接。
7. **新增单元与集成测试（[`tests/test_strategy_and_workbench.py`](./tests/test_strategy_and_workbench.py)）**：
   - 全仓测试数由 164 项扩充至 **168 项**，`pytest -W error` 全绿通过。

### 2026-09 — Phase 1 & Phase 2 基础建设（原始基线）
- 完成 OKX（BTC/ETH Spot & Perp）、Tiingo（20年美股全复权日线）、Alpaca（美股 15m SIP/IEX）、SEC EDGAR（点时 XBRL 财报与 Q4 推导）、FRED（ALFRED 点时宏观）只读接入与本地缓存。
- 完成 `app/indicators.py`、`app/outlook.py`、`app/outlook_registry.py`、`app/semantic_review.py`、OpenAPI 3.1 / JSON Schema Draft 2020-12 / MCP 1:1 契约生成与校验体系。
