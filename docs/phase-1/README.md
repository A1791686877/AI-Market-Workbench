# Phase 1 可运行垂直切片

状态：**BTC/ETH 与美股 Mag 7（含 GOOG）真实数据端到端通过；真实 LLM 已完成 GOOG 全链路验收。**

## 已实现并实测

- OKX 无密钥公共数据：BTC/ETH 现货与 USDT 永续，15m、1h、195m、4h、日、周、月共 28/28 通过，0 缺柱，历史不足全部明确为 `history_complete=false`。
- Binance 公共端点在本机返回 HTTP 451；失败与原始状态保留，未用 fixture 补位。
- 美股：Alpaca（feed `sip`，全市场）日线/15m + Tiingo/十二数据/Alpha Vantage 交叉源 + SEC companyfacts 基本面；
  缓存按需增量，不预下载全市场。GOOG 全链路验收报告见 `reports/goog-acceptance-*.json`。
- 基本面含 `TTM_EPS` / `PE_TTM`（Q4 由 FY − 9M 推导 + 净利润期间校验），8/8 Mag 7 均已算出，见 `docs/phase-1/DATA-SOURCE-STATUS.md`。
- 宏观：FRED 7 条白名单序列（10Y/2Y/利差/政策利率/CPI 同比/美元/VIX），**按 vintage 点时取值**，
  `GET /v1/macro/context` + MCP `macro_get_context`。宏观只作背景，不进入价格证据或 AI 技术结论。
- Massive（Polygon 系）作为第三交叉源接入（仅日线；免费档限近约 2 年、约 5 次/分钟，已在适配器内限速并写入 limitations）。
- CoinGecko demo 提供 BTC/ETH 网络级流通量/总供给/市值，独立 SourceEnvelope，绝不替换 OKX 价格。
- 10 个本地确定性指标：SMA、EMA、RSI、MACD、ATR、布林带、ADX/+DI/-DI、OBV、日内 VWAP、量价关系。
- FVG：三 K 影线、0.2 ATR 过滤、创建时 ATR 冻结、回补与失效拆分。
- RSI/MACD histogram 普通/隐藏背离：3×3 确认枢轴、最大 60 bars、确认后不重绘。
- VPVR：明确为 `BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE`，显示 input granularity、算法版本、POC/VAH/VAL/HVN/LVN 和限制。
- 确认枢轴支撑阻力与 HH/HL/LH/LL 趋势结构；页面叠加 FVG 区、背离线、VPVR 与支撑阻力线。
- XNYS 正式交易日历处理节假日、半日市和 DST；195m 为美股 RTH 默认等长大周期，4h 保留非完整尾柱标志。
- 官方 MCP Python SDK 2.1.1 启动真实 stdio 服务；官方 MCP 客户端完成协议 `2026-07-28` 握手、工具枚举和 OKX live 指标证据调用。
- live 上游失败测试：有旧真实缓存时标 `STALE_CACHE/STALE_SOURCE`；无缓存时失败，禁止 fixture fallback。
- OpenAPI 3.1、JSON/YAML、JSON Schema、MCP projection 同源生成与正式 validator。
- 规则展望（`btc-outlook-rules-v1`）：BTC 三路径权重 + 关键位/触发/作废；美股口径关闭四年周期规则，
  见 `docs/phase-1/EQUITY-OUTLOOK-AND-REGISTRY.md`。
- 结论登记 + 事后校验闭环（`outlook-registry-v1`）：`data/outlook/registry.jsonl` 只追加，
  校验只用冻结日之后的日线，记分板只给计数不给胜率。

## 明确未完成

- 真实 AI 只做过 GOOG（+ 早期 BTC/ETH）全链路验收；模型额度为本地 1 次/24h，未做批量。
- 清算/链上/ETF 流入流出、期权持仓仍缺失；宏观（FRED）已接通但**只作背景**。
- 批量扫描按用户决定暂不做（目录保持 9 个 subject）。
- 6 份被分析引用的旧 schema 快照保留在原地、读不出（API 明确报错，不做猜测性重建）；
  另外 16 份无引用的已归档到 `data/archived-snapshots/`。
- 尚未扩展到几十只自选股或约 500 只扫描；Massive 免费档深度/速率不足以承担默认日线源。
- `history_complete=false` 仍普遍存在：BTC 15m 起 2026-07-07、1h 起 2026-02-10、4h 起 2024-05-26；
  美股日线实际起 2016-01-04（请求 2006-09-17），15m 实际起 2026-03-11。
- outcomes 仍为隔离的 PENDING 记录；到期计算器尚未实现。

## 运行

```powershell
Set-Location E:\AI-Market-Workbench
.\scripts\validate.ps1
.\scripts\audit.ps1
.\scripts\start-live.ps1
```

规则展望（离线可跑）：

```powershell
python -B scripts\btc_outlook.py --no-refresh          # 加密
python -B scripts\equity_outlook.py --no-refresh       # 美股 Mag 7 + GOOG
python -B scripts\equity_outlook.py --verify --no-refresh
```

后台启动/停止：

```powershell
.\scripts\start-live-background.ps1
.\scripts\stop-live.ps1
```

打开 `http://127.0.0.1:8080`，API 文档为 `http://127.0.0.1:8080/docs`。输出不是投资建议，不保证盈利。
