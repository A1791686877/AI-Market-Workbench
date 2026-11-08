# Phase 1 实际进展日志

## 2026-09-16 第七轮：新增 MCD 标的 + 分析 MCD（顺手修掉 2 个真 bug）

详见 `docs/phase-1/CHANGES-2026-09-12.md` 第 16–20 节。

- **新增标的 MCD（先证据后落库）**：`scripts/verify_symbol.py` 从固定快照 MIC 取场所、从 SEC 官方
  `company_tickers.json` 取 CIK → MCD = **NYSE / CIK 63908**；`app/instruments.py` 新增 `EQUITY_VENUE`
  映射（MCD=NYSE），`MAG7_CIK` → `EQUITY_CIK`（保留别名），目录 10 个 subject。
- **修 bug 1（市值尺度）**：MCD 的摊薄股数标签是 **716.4（百万股）**，AAPL/NVDA 是绝对股数；
  旧实现直接相乘 → MCD 市值 181,091 美元（差 6 个数量级）。修好后 **181.1B**；判定写进 derivation + limitations。
- **修 bug 2（股票 C 剧本）**：股票口径 `cycle_floor=None`，但 C 一句话仍在插值它 → 叙述出现 "看 None"；
  已改为引用 **20 日低** 并标注。
- **Codex 链路加固**：①CLI 自更新换 hash 目录导致 "not found" → 新增 `resolve_codex_executable()`
  （解析失败则取 `bin/*/codex.exe` 最新，记录到 run metadata；解析在预算检查之前）；②上游额度拒绝
  （`turn.failed: usage limit`，无 token 消耗）不再占用本地 24h 额度（`counts_against_budget()`）。
- **MCD 结论（截至 09-15 收盘 252.78）**：结构 **DOWNTREND**、结构分 **2**、路径 **C 66%/A 23%/B 11%**；
  RSI 36.9、−DI > +DI、价格在 VPVR 价值区下方（VAL 261.57）；修复位 260.72、3 日高 258.45、
  回踩区 240.63–246.56、20 日低 252.15；日线无"近期且未被否定"的顶背离（也无有效底部信号）；
  估值 **P/E(TTM) 20.5x**、FCF 收益率 ≈4.0%、营收 +3.7%、净利 +4.1%、市值 181.1B；
  09-15 长上影把 09-14 的 +1.96% 反弹几乎吞掉，同期 META 创新高 → **相对弱，不是普跌**。
- **MCD 的真实 AI 分析本轮未产出**：账户额度上限（CLI 提示 "try again at 5:52 PM"）；
  恢复后一条命令补齐：`python -B scripts\accept_symbol.py --symbol MCD`。
- **registry**：新增 MCD 一条 FORWARD；09-11 那批 FORWARD 已有 2 根后续K线 → META(B)✓、TSLA(C)✓
  命中，MSFT/GOOGL/GOOG（原判 A）实际走 **B** 未命中，AAPL/AMZN/NVDA 仍未定。
- 回归：**164 passed**（`-W error`）。

## 2026-09-13 第六轮：顶背离审计（补排名缺口）+ FinanceDatabase 参考目录

详见 `docs/phase-1/CHANGES-2026-09-12.md` 第 13–15 节。

- **顶背离：我上一轮排名里没有这项检查**，所以不能说"靠前的都没有顶背离"。实测有，已补成排名的一部分：
  新增 `app/divergence_audit.py` + 6 项测试；`equity_outlook.py` 排名表新增"日线顶背离"列（离线也跑得通）。
  判定口径：距第二锚 ≤20 根 **且** 第二锚未被价格突破 → 才算"仍有效"。实测结果：**只有 MSFT 有**
  （2 个未失效顶背离：MACD_HISTOGRAM + RSI_LINE，9 根前，压制位 **517.78**）；AAPL 有 31 根前的
  未被否定压制 **344.27**（超窗口）；META/NVDA/GOOG/AMZN/TSLA 无。引擎盲点：只报已确认枢轴，
  最新 3 根内"正在形成"的背离永远不会出现。
- **FinanceDatabase（MIT，community-maintained）评估=有用**，已接入：固定快照 commit `a174c97d3bba`
  （equities 112,690 / etfs 36,481 / indices 91,181；**美国上市股票 27,003 只**）、
  `app/catalog.py`（纯本地流式只读 + 白名单列 + 缺快照即报错）、8 项测试、`docs/phase-1/REFERENCE-CATALOG.md`。
  已生效用途：展望排名表带**板块**列（Mag 7 只落 3 个 GICS 板块 → 集中度提醒）；批量扫描的前置清单就绪。
  边界：只是分类标签，**不是价格证据**，不进打分/估值/AI 结论。
- 回归：**153 passed**（`-W error`）。

## 2026-09-12 第五轮：FRED 宏观 + PE_TTM 落地 + 回放/前向分离 + 旧快照归档

详细改动单见 `docs/phase-1/CHANGES-2026-09-12.md` 第 9–12 节。

- **FRED 宏观接通**（用户授权）：7 条白名单序列（10Y/2Y/利差/政策利率/CPI+派生同比/美元/VIX），
  **每次请求带 `realtime_start=realtime_end=<vintage>`** 取当时可见的 ALFRED 版本（点路口径，
  实测 2026-06-11 vintage 的 10Y=4.55 与今日不同）。新增 `GET /v1/macro/context` 与 MCP `macro_get_context`
  （工具 11→12）。实测：10Y 4.95（20 个观测 +0.27）、2Y 4.56、10Y-2Y +0.33（趋平）、FEDFUNDS 3.63、
  CPI 同比 3.713%（反弹）、美元 118.07（−0.83%）、VIX 17.84（20 日 **+21.9%**）→ 边际风险偏好在收紧。
- **PE_TTM 落地（方案 A）**：`TTM_EPS = Q1+Q2+Q3+Q4`，Q4 由 **FY − 9M YTD** 推导（XBRL 从不单独披露 Q4），
  并用**净利润**做期间结构校验（±1%）——这一步是必须的：EPS 是比值，回购会让"三个单季之和 ≠ 9M EPS"，
  第一版用 EPS 交叉校验把 7/8 标的误判成 MISSING。实测 8/8 全部算出（AAPL 8.71 / MSFT 17.96 /
  GOOGL 19.91 / AMZN 12.43 / NVDA 7.91 / META 26.54 / TSLA 1.08）。
- **双锚估值**：TTM/FY 比值是"一次性损益探针"。GOOG 17.0x(TTM) vs 31.3x(FY2025)，比值 1.84，且 GOOG 单季
  净利 2026Q1 62.6B / Q2 112.2B 而 FY2025 全年仅 132.2B → TTM 的"便宜"不可信；AMZN 同型（1.73）；
  NVDA 1.61 更像真实加速；AAPL 38.1x 是趋势票里最贵；TSLA 338x = 盈利塌陷（上一年 2.04 → 1.08）。
- **回放 vs 前向（修正我上一轮的口误）**：我说过"计划任务是让统计量长大的唯一办法"，**这句是错的**。
  新增 `--replay-every/--replay-count` 一次可写 80 条 REPLAY；但 registry 现在用 `origin` 把
  FORWARD（9 条，全 OPEN）与 REPLAY（96 条）**分开统计**，并给出独立度 `distinct_freeze_dates = 12`
  （96 条里同一冻结日的 8 个标的是同一个市场状态）。96 条分解：A 主判断 42%、B 55%、C 21/21
  （但 C 的 21 条里有 20 条冻结时价格已在 SMA200 下方 2%–19.6% → 近乎同义反复，不是预测力）。
  A 有 18% 转 C、B 只有 10% 转 C → **B 才是最稳的主判断**，支持"高 B 权重 + 已站上 SMA20"的读法。
- **旧快照归档（方案 c）**：22 份旧 schema 里 **6 份被 analyses/tasks 引用 → 拒绝删除**，
  归档 **16 份**（3.8MB payload 全量入 `data/archived-snapshots/` + manifest），随后删行移文件。
  对账 `files=41 rows=41`，孤儿/缺失/哈希/引用全部 OK，`problem_count 34 → 18`（unreadable 22 → 6）。
- **本轮不做**（用户明确）：批量扫描（保持 9 个 subject）、计划任务（registry 只在我手动跑时增长）。
- 回归：**139 passed**（`-W error`，`scripts\validate.ps1` 一次跑通），契约含 12 个 MCP 工具。

## 2026-09-12 第四轮：GOOG 全链路验收 + 股票展望登记/校验闭环 + Massive/CoinGecko

详细改动单（改前计划 → 改后证据 → 回退）见 `docs/phase-1/CHANGES-2026-09-12.md` 第 4–7 节。

- **GOOG 全链路（真实数据 + 1 次真实模型）**：`scripts/accept_goog.py`（先 `--skip-model` 空跑再实跑）。
  验收报告 `reports/goog-acceptance-20260912T151838Z.json`：Alpaca SIP 日线 400 根 + 15m 400 根 →
  10/10 指标就绪 → SEC CIK 1652044 共 12 项基本面（FY2025 营收 402.8B / 净利 132.2B / FCF 73.3B /
  总债务 100.2B / 市值 4.10T，`PE_TTM` 按策略缺失）→ 快照 44 条证据（1d STRUCTURE + 15m SIGNAL）→
  `CODEX_CLI`/`gpt-6-astra` 任务 SUCCEEDED，分析通过证据校验（36,068 in / 4,563 out tokens，约 4m16s）。
  模型主动列出 7 项信息缺口并正确把"已跌破的历史阻力"标为回踩参考而非上方阻力。
- **GOOG 标的登记**：`app/instruments.py` 新增 `EXTRA_EQUITY_SYMBOLS=("GOOG",)`、`EQUITY_SUBJECTS`，
  GOOG 与 GOOGL 共用 SEC CIK 1652044；目录变 9 个 subject。
- **股票展望 + 结论登记 + 事后校验**（`app/outlook_registry.py` + `scripts/equity_outlook.py`，
  说明见 `docs/phase-1/EQUITY-OUTLOOK-AND-REGISTRY.md`）：`market="EQUITY"` 关闭四年减半周期规则
  （`cycle_bottom_window` 记 0 分、`cycle_floor` 置空、触发器写"不适用"）；registry 只追加、
  含输入数据指纹；校验按"先到先判 + 同根 K 线 C>B>A"，只用冻结日之后日线。
- **实测回放**：冻结在 63/21 个交易日前共 16 条 → 实际 A=3 / B=5 / C=7 / UNRESOLVED=1，
  主路径命中 7、未命中 8、未定 1。**方向性发现：规则系统性偏"先回踩"，而样本内多数实际走 B（直接上）或 C（破位）**。
- **当日 Mag 7 排名（谁最接近 A 路径）**：NVDA(A 46%、结构 94) > AMZN(44%) > MSFT(38%) >
  AAPL(37%，主路径 B) > GOOGL/GOOG(35%) > META(31%，B) > TSLA(19%，C)。
  注：GOOG/GOOGL 同一发行人，非独立样本。
- **Massive 第三源接入**：`fetch_daily` 实测（AAPL 2025-01 共 20 根，对齐 XNYS，`adjusted=true`→SPLIT_ADJUSTED）；
  实测边界写进 limitations：**2 年以上历史 403 NOT_AUTHORIZED**、**免费档约 5 次/分钟**（适配器内置 12.5s 限速）；
  因计划窗口不含拆股边界，价格基准只依据厂商契约且已如实标注未实证。
- **CoinGecko 供应量接入**：BTC 流通 20,083,337 / 总供给 20,083,390 / 市值 1.5554T USD，
  独立 `NETWORK_WIDE` SourceEnvelope；OKX 价格/资金费率/持仓量不被替换，失败保持 MISSING 并写明原因。
- **校验脚本可重复运行**：`scripts\validate.ps1` 增加清理历史 `.pytest_cache` / `tmp\pytest` 残留，
  `powershell -File .\scripts\validate.ps1` → 契约 PASS + **118 passed（-W error）** + `pip check` 干净。
- 安全提示：本轮所有 key 曾在对话中明文出现，**建议全部轮换**（代码无需改动，只更新 `config/providers.env`）。

## 2026-09-12 Tiingo 接通 + 双通道数据源（含日历边界修复）

- **Tiingo token 实测通过**：历史深度到 **1980-12-12**；全复权为主、原始 OHLCV 同条保留；拆股边界实测正确（AAPL 4:1、NVDA 10:1 处 raw/adj 比值 4.127→1.032、10.028→1.003）。
- **显式双通道**：新增 `AMW_EQUITY_EOD_PROVIDER`（日线及以上，本机 `tiingo`）与 `AMW_EQUITY_PROVIDER`（日内，`alpaca`）。两者都无静默回退。
- **七姐妹 20 年日线已预热**：AAPL/MSFT/GOOGL/AMZN/NVDA 从 2006-10-25、TSLA 从 2010-06-29、META 从 2012-05-18 起；周线 1,043 根、月线 239 根。
- **Tiingo × Alpaca 交叉核对**：2,688 个重叠交易日，收盘中位绝对差 **0.0006%–0.011%**，日收益相关性 **0.9992–0.99999**（差异源于两家分红调整口径）。证据：`reports/us-tiingo-history-20260912T143532Z.json`。
- **修复日历边界 bug**：Tiingo 有 1980 年起的数据，但 XNYS 日历只覆盖 2006-09-12 起；旧实现直接抛 pandas `DateOutOfBounds`。现在：完全越界**明确报错**，部分越界**截断 + 写入 limitations**（`clamp_to_calendar`）。
- 回归：**95 passed**（新增日历边界与双通道配置测试）。

## 2026-09-12 SEC 基本面链路打通（含两个真 bug 修复）

- **SEC 已接通**：填入真实联系 User-Agent；7 个 CIK 全部经 SEC submissions 官方核对（entityName + tickers 全 match，`reports/us-free-source-probe.json` 的 `cik_verification`）。
- 新增 `app/fundamentals.py`：Company Facts（us-gaap XBRL）→ **点时** `EquityFundamentals`；`GET /v1/subjects/{id}/fundamentals` 与 MCP 工具对七姐妹全部可用，结果按天缓存在 `cache/fundamentals/`。证据：`reports/us-fundamentals-20260912T142806Z.json`（AAPL FY2025 营收 416.2B、NVDA FY2026 营收 215.9B 等）。
- 纪律：只用 `filed ≤ cutoff`；派生值记录公式与输入 fact；同期间不匹配的相减**拒绝**；债务只在可组合时给出；`PE_TTM` 明确 MISSING。
- **修复 bug 1（标签漂移）**：公司换 XBRL 标签（NVDA 营收 `Revenues`↔`RevenueFromContractWithCustomerExcludingAssessedTax`，capex 反之），旧实现"取第一个有数据的标签"会拿到废弃标签 → NVDA 出现 capex=FY2011、FCF=FY2026 现金流 − FY2011 capex 的荒谬值。改为**跨标签取最新期间** + 同期间校验。
- **修复 bug 2（fy 语义）**：XBRL 的 `fy/fp` 是"申报年度"，同一份 10-K 的上年对比数也带 `fy=2025`；按 `fy` 差判断相邻年度必然失败。改为按期间终点相距约一年配对。
- **修复 bug 3（连接未关闭）**：`Store` 用 `with self._connect()` 只提交事务、**不关闭句柄**，Windows 上锁住临时库文件（表现为契约示例临时目录清理 PermissionError、间歇性失败）。改为 `Store.connection()`：成功提交 / 异常回滚 / 始终关闭；93 项回归连续两轮全绿。
- 回归：**93 passed**（含新增 8 项基本面 + 7 项美股链路测试），`pip check` 干净，契约已重生成。

## 2026-09-12 美股链路扩充（真实凭据 + 实测）

- 新增只读适配器：`TwelveDataProvider`（15m/日线）、`AlphaVantageProvider`（原始日线 100 根）；`AlpacaMarketDataProvider` 支持 `feed=iex|sip` 与日线（`adjustment=raw|split|all`）。
- **三源全部真机实测**（`reports/us-free-source-probe.json`，AAPL/NVDA）：Alpaca SIP/IEX 15m 与日线可用；Twelve Data 15m 364 根、日线 549 根；Alpha Vantage 原始日线 100 根可用，`TIME_SERIES_DAILY_ADJUSTED` 与 `outputsize=full` 实测为**付费端点**。
- **复权语义用拆股边界实测**（不是照抄文档）：Alpaca `all` 在 AAPL 4:1、NVDA 10:1 边界平滑；`raw` 出现 0.2586/0.1009 跳变；Twelve Data 日线为拆股调整（min ratio 0.9623）。
- **修正一个会隐藏最新数据的真 bug**：Alpaca 每页实际约 850 行，旧实现按"从旧到新"取并在 20 页停下 → 15m 缓存的最右端停在 2025-10（看起来"不实时"）。改为**从新往旧取 + 页码预算**，预算只截断历史、不隐藏最新；并加回归测试。
- 标的目录扩到**七姐妹**（`app/instruments.py`，9 个 subject），fixture 模式对无合成序列的真实标的**fail closed**（不再 KeyError）。
- 缓存按需增量，**不做全市场预下载**；已预热 Mag7 × {1d,15m}：日线 2,688 根/标的、15m 2,700–3,300 根/标的、缺柱 0、最右端 2026-09-11 收盘，新增约 42 MB（`reports/us-universe-warm-*.json`）。
- 契约随 `DataProvider` 新增枚举重新生成并校验；回归 **84 passed**。
- 安全边界：用户给的 `paper-api` 交易域**未使用**；只调用 `data.alpaca.markets`。密钥只落 `config/providers.env`（gitignore），并已提示轮换。

## 2026-09-12 BTC 可重复分析框架

- 存档手工判断：`docs/phase-1/BTC-OUTLOOK-2026-09-12.md`（当时结论 A 先下后上 45%）。
- 新增规则真源与框架说明：`docs/phase-1/BTC-OUTLOOK-FRAMEWORK.md`（`btc-outlook-rules-v1`：结构分/动量分/三路径权重/关键位/失效条件全部写明）。
- 新增一条命令的流水线：`python -B scripts/btc_outlook.py`（默认增量刷新到最新；`--no-refresh` 离线）→ `app/outlook.py` 规则引擎 → 输出 `reports/btc-outlook-<UTC>.json|.md`。零模型调用、零数据订阅、确定性输出。
- 首次运行复现手工主判断（A 45%，B 33%，C 22%；回踩区 73,405–76,515、确认 78,621/79,896、失效 70,061），联网与离线两次运行结果一致；新增 7 项规则测试通过。
- 边界：权重是规则排序不是概率；无样本外校准；不写库、不下单、不构成投资建议。

## 2026-09-12 实质问题修复轮（无模型调用、无新增数据源）

逐项问题、风险、验证与回退见 `docs/phase-1/CHANGES-2026-09-12.md`。要点：

- 美股成熟时间改用 XNYS 日历交易分钟：修复节假日/半日市被当作交易日（旧实现把 Labor Day 与半日市下午算进 390 分钟）。超出日历覆盖时 fail closed。
- 新增 `scripts/verify_store_integrity.py`：快照文件↔DB 对账、按存储原文复核 `snapshot_sha256`、分析/任务/后验可解析性、引用完整性；默认只读，`--quarantine-orphans` 才移动孤儿文件。
- 孤儿文件根因已修：`create_snapshot` 过去总写全局快照目录，临时库测试与契约生成都在生产目录留下无对应行的文件；现在快照目录跟随所属数据库，测试不再污染生产库（修复后跑完全量测试，生产库行数/文件数不变）。
- 生产库清理：132 份孤儿文件已**移动**到 `data/orphaned-snapshots/`（含 manifest），会前 2 份测试残留已归档后删除；对账结果 `reports/store-integrity-20260912-post-cleanup.json`。57 份快照哈希**全部通过**（51 条在 32MB 上限内、6 条约 87MB 跳过）。
- 22 份 2026-09-05 旧快照当前 schema 无法解析（缺 `bars[].quality`、`indicator_bundles[].instrument`）：原样保留，读取时返回显式 `SnapshotSchemaError`（500 + 明确 detail + 审计），不再裸 traceback；不做凭猜测的迁移。
- 其余：`DETERMINISTIC_LOCAL` 作为 legacy 演示名显式化；`/scenario-outcomes` 查询回到 storage/service 层并去掉 `__import__`；游标实现去重；重启时僵死 QUEUED/RUNNING 任务标记失败且**不自动重试**。
- 回归：全量 **71 项通过**（原 53 + 新增 18）。

## 2026-09-06 真实模型联调增量（取代下方相应旧状态）

- 最终结果：2026-09-06T03:25:07Z 浏览器一次真实AI点击成功，analysis-1398be9a43b34a66a4321a35aef3845d；BTC/ETH图层核对通过，无JS错误。全部用量、失败记录与未完成范围见 REAL-AI-ACCEPTANCE.md。没有新增模型调用来追求盈利或预测胜率。

- 用户已持续授权同范围公开行情、基本面及指标证据外发；不含凭据、账户、持仓、交易或无关文件，不新增订阅。
- CODEX_CLI 已实现真实非交互调用，使用现有 ChatGPT 登录，请求模型 gpt-5.6-terra。禁用 shell、浏览器、插件、记忆及其他主机技能；原始输入、输出、事件、哈希与用量保存在 data/model-runs。
- BTC/ETH 真实结构化回答成功；严格校验三情景、引用 ID、价位及价位类型，不回退规则演示。金额未知以 null 保存，不标零。
- 实测发现并修复 OKX 现货成交量单位：SPOT 使用 row[5] 基础币量，永续使用 row[6]。旧缓存保留，新缓存命名空间 okx-base-volume-v2。修正前两次调用不计入指标验收。
- 背离信号时间改为两个价格/指标枢轴均确认之后；FVG 中点新增独立 FVG_MIDPOINT 类型。
- 实测发现 MCP 搜索缺少 subject_id；新增强类型搜索字段及精简证据 REST/MCP 接口。真实 Codex 客户端完成两次搜索、BTC/ETH 两次证据读取及回答。早先两轮失败记录保留。
- 完整契约由真源重生成并通过正式 validator；30 项回归通过。修复尚未完成的周/月与 195m 尾段误作确认柱、美股缺柱导致聚合锚点漂移的问题，并新增测试。
- 页面真实 AI 按钮、OHLC K线及可核对图层已接入。浏览器任务超过旧 30 秒测试等待窗口的问题已修正为最长 420 秒；最终点击状态以 reports/browser-acceptance.json 为准。
- 15m 最新实测历史仍仅约 52 天，不是两年；此前 28/28 仅代表当次请求成功，不能代表完整历史或全部功能验收。
- AAPL 等待本地凭据；Alpaca/Tiingo/SEC 配置说明见 LOCAL-CREDENTIALS.md，Massive 暂缓。同步合约模型上下文、成熟收益回填及规模扩展仍未完成。

## 2026-09-05 至 2026-09-06

- Binance BTC/ETH 现货、永续、资金费率与 OI 公共请求均返回 HTTP 451；未使用代理或模拟数据冒充成功。
- 启用可替换 OKX public adapter；相同公共类别请求返回 HTTP 200/provider code 0。
- 完成 BTC/ETH spot/perp × 15m/1h/195m/4h/1d/1w/1mo：28/28 成功，0 请求失败，0 检测缺柱；受 5,000 bars 安全分页上限或 provider 历史起点限制的项目均 `history_complete=false`。
- 页面现可显示真实 K 线、来源、数据/接收时间、延迟、市场范围、缺柱、历史完整性、旧缓存状态；live 请求失败时清空旧 UI 并明确失败，不放 fixture。
- 补齐 10 项基础指标、FVG、背离、估算 VPVR、支撑阻力和趋势结构，以及图上标记和结构化证据 ID。
- 安装并审查官方 MCP Python SDK 2.1.1（MIT）；真实 stdio transport 和官方客户端通过，live OKX 证据调用报告已保存。
- 安装 `exchange_calendars 4.13.2`（Apache-2.0），替换美股 RTH 近似逻辑。
- 美股凭据状态集中探测完成：Alpaca、Tiingo、Massive、SEC 当前均未配置；没有真实 AAPL 成功证据。
- 真实 LLM 入口需要把冻结公开市场快照发送到外部模型。安全审批要求用户明确同意该数据外发；在确认前 `CODEX_CLI` fail closed，规则结果标记 `DETERMINISTIC_DEMO`。
- 最终自动测试 22/22；91 个依赖无已知漏洞；数据订阅费用仍 USD 0/月。

## 2026-09-06 数据质量阶段（取代上文作为当前状态）

真实 Codex 与真实 AI MCP 客户端链路已在此前完成；本轮不新增模型调用。正式契约校验与44项离线回归通过，真实28组七周期标准指标逐点比较通过，完整历史仍未通过。保存的浏览器回答存在价格方向/角色错误，语义复核 REJECTED；不得沿用旧“引用ID和数字通过”来声称语义正确。

已加入增量缓存、分页恢复游标与并发锁；BTC/ETH各14项七周期现货/永续冻结快照真实取得，上下文时间不晚于截止。已用现有真实分析记录回填12项成熟价格表现，后验与输入分表，未推导命中率。具体报告、局限、规则及回退方式见 QUALITY-2026-09-06.md。

最终补充：修正并验证排他历史游标；4个日线探测实际返回 PROVIDER_EMPTY_PAGE，而不是将重复边界柱误判请求失败。回归扩展到53项通过；八类 RSI/MACD 普通/隐藏顶底背离几何、历史枢轴角色、零模型预算阻断均包含在内。28组真实七周期已再跑通过，历史仍不完整。
