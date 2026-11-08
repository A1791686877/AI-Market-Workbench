# Phase 1 数据源状态与许可边界

更新时间：**2026-09-12**（Asia/Shanghai）；当前数据订阅预算：**USD 0/月**。
所有"已接通"结论都来自真实请求；探针原始证据在 `reports/us-free-source-probe.json`（AAPL/NVDA）。

## 1. 加密货币（已有）

| 来源 | 状态 | 说明 |
|---|---|---|
| OKX 公共接口 | **已接通** | BTC/ETH 现货与 USDT 永续，venue 级、无 key；缓存已补到 2026-09-12 12:45Z，16 个周期视图缺柱 0 |
| Binance 公共接口 | 本机 HTTP 451 | 失败记录保留，不做静默回退 |
| **CoinGecko Demo** | ✅ **已接通** | 网络级供应量/市值：BTC 流通 20,083,337 / 总供给 20,083,390 / 市值 1.5554T USD；独立 `NETWORK_WIDE` SourceEnvelope，**不替换** OKX 价格；失败保持 MISSING 并写明原因（有测试）。demo 档有速率与署名条款，上线前需复核 |

## 2. 美股（2026-09-12 实测新增）

| 来源 | 状态 | 实测事实（机器证据） | 定位 |
|---|---|---|---|
| **Alpaca / feed=sip** | ✅ **默认** | 15m 104 根，`CONSOLIDATED_US_SIP`、`is_full_market=true`、`DELAYED_15M`；日线 `adjustment=all/split/raw` 全部可用，**复权语义已用拆股边界实测**：`all` 在 AAPL 2020-08-31(4:1) 与 NVDA 2024-06-10(10:1) 边界平滑，`raw` 出现 0.2586 / 0.1009 的跳变 | 主力：日内 15m + 日线 |
| **Alpaca / feed=iex** | ✅ 可用 | 15m 104 根，`EXCHANGE_ONLY`、`is_full_market=false`、`REALTIME_PUBLIC`（单交易所实时，非全市场） | 需要"更实时"时切换 |
| **Twelve Data** | ✅ 备用 | 15m 364 根、日线 549 根；**日线为拆股调整**（NVDA 10:1 边界 min ratio 0.9623 平滑），日内复权无法验证（免费 15m 历史不覆盖拆股日）→ 日内标 RAW；`market_scope=UNKNOWN`；限额约 8 次/分、800 次/日 | 日内+日线备用 |
| **Alpha Vantage** | ⚠️ 仅交叉核对 | 原始日线 100 根可用；`outputsize=full` **是付费功能**，`TIME_SERIES_DAILY_ADJUSTED` **是付费端点**（返回 Information 提示）；限额约 25 次/日、实测约 5 次/分 | 少量标的的第三方对照，不做主力 |
| **Tiingo Free EOD** | ✅ **日线长历史默认源** | 真实 token 实测通过；历史深度实测到 **1980-12-12**；主口径为**全复权**（`adjOpen/adjHigh/adjLow/adjClose`）并同时保留原始 OHLCV；拆股边界实测：AAPL 4:1 与 NVDA 10:1 处 `raw/adj` 比值 4.127→1.032、10.028→1.003（调整正确）。当前日历可验证范围 2006-09-12 起 | 日线/周线/月线 |
| **Massive Basic** | ⚠️ 仅交叉核对 | `api.massive.com`（Polygon 系）聚合端点实测可用：AAPL 2025-01 窗口 20 根，`t` 毫秒→UTC 对齐 XNYS session，`adjusted=true`→`SPLIT_ADJUSTED`。**实测边界**：请求 2 年以上历史返回 **403 `NOT_AUTHORIZED`**（"Your plan doesn't include this data timeframe"）；**免费档约 5 次/分钟**（并发探测触发 429）→ 适配器内置 12.5s 限速。因计划窗口不含任何 Mag 7 拆股边界，价格基准标签只依据厂商契约、**未在本窗口实证**，已写入 `limitations` | 第三交叉源；**不能**做默认日线源 |
| **FRED（St. Louis Fed）** | ✅ **已接通** | 7 条白名单序列（DGS10、DGS2、T10Y2Y、FEDFUNDS、CPIAUCSL + 派生 CPI 同比、DTWEXBGS、VIXCLS）。**每次请求带 `realtime_start=realtime_end=<vintage>`**，取当时可见的 ALFRED 版本（实测：2026-06-11 vintage 的 10Y = 4.55，与今日不同）。最新 vintage 实测：10Y 4.95 / 2Y 4.56 / 10Y-2Y +0.33 / FEDFUNDS 3.63 / CPI 同比 3.713 / 美元指数 118.07 / VIX 17.84。缺值（`.`）保持缺失；宏观只作背景，**不进价格证据、不进 AI 技术结论** | 宏观背景（`GET /v1/macro/context`、MCP `macro_get_context`） |
| **SEC EDGAR Company Facts** | ✅ 已接通 | 用真实联系 User-Agent 实测；7 个 CIK 全部通过 SEC submissions 核对（entityName + tickers 全 match）；`us-gaap` XBRL → 点时 `EquityFundamentals`（见下节） | 基本面 |
| **FinanceDatabase（参考目录）** | ✅ 已接入（固定快照） | MIT、community-maintained。快照 commit `a174c97d3bba`（2026-09-13T15:10:38Z）：equities 112,690 行 / etfs 36,481 / indices 91,181；**美国上市股票 27,003 只**（剔除退市）。提供 symbol/name/sector/industry_group/industry/mic/country、`market_cap` 分档等**分类元数据**；以原始 `.bz2` 保存并流式读取（equities 15.3MB），`provenance.json` 记录 sha256；缺失/schema 变化即报错 | **只作标签与目录**：不是价格证据、不进 AI 技术结论、不进估值。详见 `docs/phase-1/REFERENCE-CATALOG.md` |

补充事实：

- **双通道显式配置**：`AMW_EQUITY_PROVIDER`（日内，默认 `alpaca`）+ `AMW_EQUITY_EOD_PROVIDER`（日线及以上，默认跟随前者；本机设为 `tiingo`）。两个值都不做静默回退。
- **Tiingo × Alpaca 交叉核对**（`reports/us-tiingo-history-20260912T143532Z.json`）：七姐妹 2,688 个重叠交易日，**收盘中位绝对差 0.0006%–0.011%**，**日收益相关性 0.9992–0.99999**（差异来自两家的分红调整口径，非数据错误）。Tiingo 侧拿到周线 1,043 根、月线 239 根（META 自 2012 IPO、TSLA 自 2010-06-29）。
- **日历边界**：Tiingo 有 1980 年以来的数据，但本机 `exchange_calendars` 的 XNYS 只覆盖 **2006-09-12 ≈ 2027-09-10**；请求更早的窗口现在会**明确报错**（此前会抛 pandas `DateOutOfBounds` 裸异常），部分覆盖的窗口会被**截断并把限制写进 `limitations`**。

- 用户给的 Alpaca 地址是 `paper-api`（**交易域**），本项目**不使用**它；只调用 `data.alpaca.markets`，密码学能力由主机白名单+GET 限制，代码中没有下单/账户/转账路由（`tests/test_security.py` 会拦）。
- **分页方向是正确性的一部分**：Alpaca 每页实际约 850 行（不是 `limit=10000`），早期实现按"从旧到新"取并在 20 页停下，导致缓存的最右端停在数月前（表现是"数据看起来不实时"）。现在改为**从新往旧取 + 页码预算**：预算只会截断历史，永远不会隐藏最新数据；未覆盖到请求起点时写进 `limitations`，`history_complete=false`。
- **不做全市场预下载**：按需抓取 + 增量合并。已预热"七姐妹"（AAPL/MSFT/GOOGL/AMZN/NVDA/META/TSLA）：日线 2,688 根/标的（2016 起）、15m 2,700–3,300 根/标的（约 7–9 个月 RTH，缺柱 0、最右端 2026-09-11 收盘），新增缓存约 42 MB，证据见 `reports/us-universe-warm-*.json`。

## 3. 美股基本面（2026-09-12 打通）

`app/fundamentals.py` 把 SEC Company Facts 归一化为**点时** `EquityFundamentals`，`GET /v1/subjects/{id}/fundamentals` 与 MCP `market_get_equity_fundamentals` 直接可用。

规则（全部写死在代码里）：
- **点时**：只用 `filed ≤ cutoff` 的事实；`accepted_at` 用申报日 00:00 UTC（Company Facts 无 acceptance 时间戳，已记为 limitation）。
- **跨标签取最新**：公司会换 XBRL 标签（NVDA 营收从 `Revenues` 换到 `RevenueFromContractWithCustomerExcludingAssessedTax`，capex 反之），必须比较所有候选标签的期间终点；"取第一个有数据的标签"会静默拿到废弃标签的旧值。
- **派生必须记录公式与输入**：`FREE_CASH_FLOW`、`REVENUE_GROWTH`、`NET_INCOME_GROWTH`、`MARKET_CAP` 都写 `derivation` + `input_fact_ids`。
- **同期间校验**：现金流与 capex 不同财政期时**拒绝相减**并标 MISSING；同比要求同标签、期间终点相距约一年（XBRL 的 `fy/fp` 是申报年度，同份 10-K 里的对比数也带 `fy=2025`，不能用来判断相邻年度）。
- **不猜**：债务只在同一时点存在可组合的 GAAP 标签时才给合计。
- **TTM EPS / PE_TTM 政策（2026-09-12 用户同意后实现）**：`TTM_EPS = Q1+Q2+Q3+Q4`，Q1–Q3 取单季（80–100 天）GAAP 摊薄 EPS，
  **Q4 = FY − 9M YTD**（XBRL 从不单独披露 Q4），并用**净利润**做期间结构校验（9M NI ≈ Q1+Q2+Q3 NI，±1%；
  无 NI 时退化为 EPS ±10%，因为回购会让"三个单季 EPS 之和 ≠ 9M EPS"）；`PE_TTM = 最近确认收盘 / TTM_EPS`。
  未剔除一次性项目（如按市值计价的投资收益），因此 TTM/FY 比值本身就是"是否有一次性损益"的探针。

实测（`reports/us-fundamentals-20260912T142806Z.json`，全部为 GAAP、点时）：

| 标的 | 期间 | 营收 | 净利 | 自由现金流 | 总债务 | 营收同比 |
|---|---|---:|---:|---:|---:|---:|
| AAPL | FY2025 | 416.2B | 112.0B | 98.8B | 84.3B | +6.4% |
| MSFT | FY2026 | 331.8B | 133.7B | 67.0B | MISSING | +17.8% |
| GOOGL | FY2025 | 402.8B | 132.2B | 73.3B | 100.2B | +15.1% |
| AMZN | FY2025 | 716.9B | 77.7B | 7.7B | 132.5B | +12.4% |
| NVDA | FY2026 | 215.9B | 120.1B | 96.7B | MISSING | +65.5% |
| META | FY2025 | 200.9B | 60.5B | 46.1B | 83.7B | +22.2% |
| TSLA | FY2025 | 94.8B | 3.8B | 6.2B | MISSING | −2.9% |

MSFT/NVDA/TSLA 的总债务为 MISSING：其负债标签在同一时点无法组成完整口径，按纪律不猜。

TTM EPS / PE_TTM（政策落地后实测，`reports/ttm-eps-live-*.json`）：8/8 全部算出（AAPL 8.71、MSFT 17.96、
GOOGL/GOOG 19.91、AMZN 12.43、NVDA 7.91、META 26.54、TSLA 1.08）。配上 2026-09-11 收盘：

| 标的 | 收盘 | TTM EPS | P/E(TTM) | FY EPS | P/E(FY) | TTM/FY |
|---|---:|---:|---:|---:|---:|---:|
| GOOG / GOOGL | 335.45 / 338.50 | 19.91 | 16.8 / 17.0 | 10.81 | 31.0 / 31.3 | 1.84 |
| AMZN | 256.78 | 12.43 | 20.7 | 7.17 | 35.8 | 1.73 |
| NVDA | 218.29 | 7.91 | 27.6 | 4.90 | 44.5 | 1.61 |
| META | 648.03 | 26.54 | 24.4 | 23.49 | 27.6 | 1.13 |
| AAPL | 332.27 | 8.71 | 38.1 | 7.46 | 44.5 | 1.17 |
| MSFT | 495.63 | 17.96 | 27.6 | 17.95 | 27.6 | 1.00 |
| TSLA | 365.44 | 1.08 | 338.4 | 1.08 | 338.4 | 1.00 |

`TTM/FY ≫ 1.5`（GOOG/AMZN）要按一次性损益处理：GOOG 单季净利 2026Q1 62.6B、Q2 112.2B，而 FY2025 全年 132.2B，
说明 TTM 口径的 17x **不能**当成"便宜"；TSLA 的 TTM/FY = 1.00 但绝对水平是上一年（2.04）的 −47%，属盈利塌陷。

## 4. 凭据边界

- 只允许放在 `E:\AI-Market-Workbench\config\providers.env`（`.gitignore` 已排除），由 `scripts/load-provider-env.ps1` 按字段白名单注入进程；日志只记字段名不记值。
- 本轮新增字段：`TWELVE_DATA_API_KEY`、`ALPHA_VANTAGE_API_KEY`；装载脚本白名单已同步。
- **本轮曾有密钥出现在聊天记录里，建议在做完验证后到各家后台轮换一次。**
- 相关开关：`AMW_EQUITY_PROVIDER`（`alpaca` 默认 / `twelve_data` / `alpha_vantage` / `tiingo`）、`AMW_ALPACA_FEED`（`sip` 默认 / `iex`）。**都是显式配置，不做静默 source fallback。**

## 5. 运行

```powershell
Set-Location E:\AI-Market-Workbench
. .\scripts\load-provider-env.ps1
$env:AMW_DATA_MODE='live'; $env:AMW_NETWORK_ENABLED='true'
python -B scripts\probe_us_free_sources.py      # 重新实测数据源
python -B scripts\warm_us_universe.py           # 预热七姐妹（1d + 15m）
python -B scripts\warm_us_universe.py --intervals 1d
```

机器证据：`reports/us-free-source-probe.json`、`reports/us-universe-warm-*.json`、`logs/audit/audit.jsonl`。
