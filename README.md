# AI Market Workbench

> ⚠️ **重要风险免责声明 / RISK DISCLAIMER (MUST READ)**:
> - **仅供研究与学习**：本仓库代码与分析工作台仅供量化技术研究、指标建模与编程学习交流，**绝不构成任何投资建议、买卖要约或财务推荐**。
> - **风险自负**：加密货币与股票市场具有极高风险，历史行情与情景推演不代表未来走势。使用者据此作出的任何决策均由其自行承担全部风险。
> - **绝对只读安全保证**：本项目完全采用**只读架构**，代码中严禁且不包含任何实盘下单、撤单、持仓更改、私钥签名或资金划转接口。

> 🤖 **给 AI 助手与开发者的第一入口（免盲搜速查）**：
> - **想改某项功能 / 查哪个模块在哪 / 看防踩坑红线**：直接先读 [`AGENTS.md`](./AGENTS.md)（内含「想改啥 → 去哪改」一秒定位表与 DOM/契约红线）。
> - **查看五层架构数据流、逐文件函数/行号索引与历次升级日志**：请读 [`AI_DEVELOPER_LOG.md`](./AI_DEVELOPER_LOG.md)。

只读、证据优先的股票与加密货币分析平台。当前已跑通 11 大核心标的（BTC、ETH + 美股九大核心标的 NVDA/AAPL/MSFT/AMZN/META/GOOGL/GOOG/TSLA/MCD）：真实多周期行情 → 48 桶 VPVR 主峰/真空区聚类 & 双轨背离（`3×3` 确认 + `3×1` 右侧成形） → 用户专属多周期策略引擎 → SEC 双锚基本面 & FRED 点时宏观 → 冻结快照 → `CODEX_CLI` 真实分析 + 全功能交互式 Web 工作台。

## 安全边界

- 默认 `fixture` 模式且禁止网络；`scripts/start-live.ps1` 才启用公共行情网络。
- 代码中没有下单、撤单、账户、转账或提币接口。
- Alpaca 适配器只允许市场数据域名；OKX/Binance 只允许公共 GET；不使用交易 SDK。
- 输出是条件情景分析，不是投资建议，不保证盈利。
- 数据库、缓存、临时文件和日志均位于 `E:\AI-Market-Workbench`。

## 快速运行

```powershell
Set-Location E:\AI-Market-Workbench
.\scripts\validate.ps1
.\scripts\audit.ps1
& .\.venv\Scripts\python.exe -B -m scripts.run_vertical_slice
.\scripts\start-live.ps1
```

本地快照与后验存储对账（只读；`--quarantine-orphans` 才移动无对应数据库行的快照文件到 `data/orphaned-snapshots/`）：

```powershell
& .\.venv\Scripts\python.exe -B scripts\verify_store_integrity.py
```

BTC 情景区间分析（可重复、零模型调用；规则见 `docs/phase-1/BTC-OUTLOOK-FRAMEWORK.md`）：

```powershell
& .\.venv\Scripts\python.exe -B scripts\btc_outlook.py               # 增量刷新后出结论
& .\.venv\Scripts\python.exe -B scripts\btc_outlook.py --no-refresh  # 只用本地缓存（离线）
```

美股 Mag 7 展望 + 结论登记 + 事后校验（零模型调用；规则见 `docs/phase-1/EQUITY-OUTLOOK-AND-REGISTRY.md`）：

```powershell
& .\.venv\Scripts\python.exe -B scripts\equity_outlook.py                     # 出结论 + 登记 registry
& .\.venv\Scripts\python.exe -B scripts\equity_outlook.py --verify            # 事后校验闭环
& .\.venv\Scripts\python.exe -B scripts\equity_outlook.py --scoreboard        # 只打印计数（不发请求）
```

`data/outlook/registry.jsonl` 只追加登记（含输入数据指纹）；校验只用冻结日之后的日线，判定为
"先到先判 + 同根 K 线内 C > B > A"；记分板只给计数，**不给胜率**，并把 `FORWARD`/`REPLAY` 分开统计。

顶背离审计（已并入排名的"日线顶背离"列，也可单独跑）：

```powershell
& .\.venv\Scripts\python.exe -B scripts\divergence_check.py --intervals 1d
```

口径：距第二锚 ≤20 根（日线）**且**第二锚未被价格突破，才算"仍有效"的顶背离；被价格突破的算已失效。
引擎只报已确认的 3×3 枢轴背离，因此**最新 3 根内正在形成的背离不会出现在报告里**（不重绘的代价）。

参考目录（板块/行业/交易所/MIC 分类 + 可用宇宙清单；只是标签，不是价格证据）：

```powershell
& .\.venv\Scripts\python.exe -B scripts\fetch_financedatabase.py --assets equities,etfs,indices  # 一次性
& .\.venv\Scripts\python.exe -B scripts\catalog_report.py                                      # 离线
```

详见 `docs/phase-1/REFERENCE-CATALOG.md`（MIT、community-maintained、固定 commit、只版本化 provenance）。

打开 `http://127.0.0.1:8080`。API 文档位于 `http://127.0.0.1:8080/docs`。

需要美股免费源时，只在 `E:\AI-Market-Workbench\config\providers.env` 本地填写 market-data 凭据和 SEC 联系 User-Agent，不要发到聊天。然后运行 `.\scripts\probe-us-free-sources.ps1`；只有实测成功后才会把标的标为已接通。

美股（七姐妹）实测与预热：

```powershell
Set-Location E:\AI-Market-Workbench
. .\scripts\load-provider-env.ps1
$env:AMW_DATA_MODE='live'; $env:AMW_NETWORK_ENABLED='true'
& .\.venv\Scripts\python.exe -B scripts\probe_us_free_sources.py   # 三源实测 + 复权语义
& .\.venv\Scripts\python.exe -B scripts\warm_us_universe.py        # 按需预热 Mag7 × {1d,15m}
```

数据源开关：`AMW_EQUITY_PROVIDER=alpaca|twelve_data|alpha_vantage|tiingo|massive`（日内，默认 alpaca）、
`AMW_EQUITY_EOD_PROVIDER=...`（日线及以上，默认跟随上一个；本机用 tiingo 取 20 年全复权日线）、
`AMW_ALPACA_FEED=sip|iex`（默认 sip，合并行情；iex 为单交易所实时）。Massive 只作第三交叉源
（免费档实测仅近约 2 年日线、约 5 次/分钟，适配器内置 12.5s 限速）；CoinGecko demo 只补加密网络级
供应量/市值且不替换 OKX 价格；FRED 提供点时宏观背景（`GET /v1/macro/context`，MCP `macro_get_context`）。
实测状态见 `docs/phase-1/DATA-SOURCE-STATUS.md`。缓存按需增量，不做全市场预下载。

估值与宏观：

```powershell
& .\.venv\Scripts\python.exe -B -c "from app.service import service; print(service.get_macro_context(limit=30).model_dump(mode='json'))"
& .\.venv\Scripts\python.exe -B scripts\probe_fred_macro.py    # 宏观点时快照（含 vintage 对比）
& .\.venv\Scripts\python.exe -B scripts\mag7_valuation.py      # 用 TTM EPS 出 P/E(TTM) 与 P/E(FY) 双锚
```

`TTM_EPS` 口径：Q1–Q3 取单季 GAAP 摊薄 EPS，**Q4 = FY − 9M YTD**（XBRL 从不单独披露 Q4），
并用净利润做期间结构校验（±1%）；未剔除一次性项目，故 `TTM/FY` 比值本身就是"是否有一次性损益"的探针。

## 契约真源

唯一真源是 `app/models.py` 的 Pydantic 类型与 `app/main.py` 的 FastAPI 路由。`scripts/generate_contracts.py` 从运行时对象自动生成：

- `contracts/openapi.json`
- `contracts/openapi.yaml`
- `contracts/ai-analysis.schema.json`
- `contracts/mcp-tools.json`

禁止手工编辑这些生成文件。`scripts/validate_contracts.py` 使用正式 JSON Schema/OpenAPI validator 验证外部引用、正反样例以及 JSON/YAML 深度一致性。

真实验收证据位于 `reports/live-crypto-acceptance.json`、`reports/mcp-live-stdio-acceptance.json`、`reports/us-free-source-probe.json`、`reports/goog-acceptance-*.json`、`reports/step4-evidence.json`、`reports/pip-audit.json` 和 `reports/sbom.cdx.json`。

## 数据源策略

第一轮不购买任何数据服务，预算为 USD 0/月。OKX 公共行情已实测；Binance 本机 HTTP 451；Alpaca（sip/iex）、
Tiingo、Twelve Data、Alpha Vantage、Massive、SEC EDGAR、CoinGecko demo 均已在 E 盘本地配置后实测
（限额与边界见 `docs/phase-1/DATA-SOURCE-STATUS.md`）。模型费用单列，不混入数据订阅预算；
模型调用受本地 1 次/24h 额度限制且**不自动重试**。
