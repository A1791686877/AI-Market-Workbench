# 免费数据凭据：只在本地填写

数据订阅预算 USD 0/月，不开通实盘账户、不入金、不申请交易或提币权限，不在聊天发送密钥。

编辑 `E:\AI-Market-Workbench\config\providers.env`，每行 `NAME=value`，不加引号。

| 服务 | 官方入口 | 本地字段及用途 |
|---|---|---|
| Alpaca Basic | https://app.alpaca.markets/signup | `ALPACA_MARKET_DATA_KEY_ID` 填 Key ID；`ALPACA_MARKET_DATA_SECRET` 填 Secret Key。仅供 `data.alpaca.markets` 行情 GET 接口使用。优先使用非实盘凭据。 |
| Twelve Data | https://twelvedata.com/ | `TWELVE_DATA_API_KEY` 填 API Key；只调用 `api.twelvedata.com/time_series`。免费层约 8 次/分、800 次/日；日线实测为拆股调整，日内复权未验证（标 RAW）。 |
| Alpha Vantage | https://www.alphavantage.co/support/#api-key | `ALPHA_VANTAGE_API_KEY` 填 API Key；只调用 `www.alphavantage.co/query`。免费层仅原始日线 100 根（`outputsize=full` 与 `TIME_SERIES_DAILY_ADJUSTED` 均为付费端点），约 25 次/日、实测约 5 次/分。 |
| Tiingo Free | https://www.tiingo.com/ 首页 Sign-Up for a Free Account；登录后 https://www.tiingo.com/account/api/token | `TIINGO_API_TOKEN` 填 API Token，用于长期日K、原始/复权价及公司行动，不购买 Power。 |
| Massive (Polygon 系) Basic | https://massive.com/pricing | `MASSIVE_API_KEY` 填 API Key；只调用 `api.massive.com/v2/aggs/...`。**仅作第三交叉源**：免费档实测只能取近约 2 年日线（更早返回 403 `NOT_AUTHORIZED`），限速约 5 次/分钟（适配器内置 12.5s 间隔），且计划窗口内无法验证拆股边界。 |
| CoinGecko Demo | https://www.coingecko.com/en/developers/dashboard | `COINGECKO_DEMO_API_KEY` 填 Demo Key；只调用 `api.coingecko.com/api/v3/coins/markets`，用于 BTC/ETH 网络级流通量、总供给与市值（独立来源，绝不替换 OKX 价格）。demo 档有速率与署名条款要求。 |
| FRED (St. Louis Fed) | https://fredaccount.stlouisfed.org/apikeys | `FRED_API_KEY` 填免费 API Key；只调用 `api.stlouisfed.org/fred/series` 与 `/fred/series/observations`。用于宏观背景（10Y/2Y/利差/政策利率/CPI/美元/VIX）；每次带 `realtime_start=vintage` 取当时可见版本。免费档约 120 次/分钟。 |
| SEC EDGAR | https://www.sec.gov/about/developer-resources | `SEC_USER_AGENT` 填项目名称、联系人姓名及真实可联系邮箱，例如 `AI-Market-Workbench YourName your-real-email@your-domain`；不是密钥，不要照抄示例。此字段仅用于 SEC 请求标识，不发送给模型。 |

## 美股数据源开关（2026-09-12 新增）

- `AMW_EQUITY_PROVIDER`：日内（15m/1h/195m/4h）用，`alpaca`（默认）| `twelve_data` | `alpha_vantage` | `tiingo` | `massive`。
- `AMW_EQUITY_EOD_PROVIDER`：日线/周线/月线用，默认跟随 `AMW_EQUITY_PROVIDER`；本机建议 `tiingo`（长历史全复权）。Tiingo 是 EOD-only，不能用于日内；`massive` 同为 EOD-only 且历史深度只有约 2 年，**不要**设为默认。
- `AMW_ALPACA_FEED`：`sip`（默认，合并行情、免费层延迟≥15 分钟）| `iex`（单交易所实时、非全市场）。
  本机实测两者都可用；`sip` 的 `is_full_market=true`，`iex` 为 `false`。
- `TIINGO_API_TOKEN` 已在本机填写并实测通过（历史到 1980-12-12；与 Alpaca 日线交叉核对 2,688 个交易日、收益相关性 >0.999）。

**注意：不要把密钥贴进聊天。** 七把凭据（Alpaca / Twelve Data / Alpha Vantage / Tiingo / Massive / CoinGecko / FRED）都曾在聊天出现，建议全部到各家后台轮换。凭据只放在 `config/providers.env`（已 gitignore），由 `scripts/load-provider-env.ps1` 白名单注入。

Alpaca 本地字段分别映射请求头 `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY`。凭据本身可能具备纸面交易能力；本项目通过行情主机与 GET 白名单限制用途，不声称服务商已签发独立只读权限。

官方 FAQ 要求免费历史 SIP 查询的 `end` 至少早于当前 15 分钟。代码显式设置 `feed=sip`，并限制 end 至少早 15 分 5 秒。本机已实测通过（`CONSOLIDATED_US_SIP`、`is_full_market=true`、`DELAYED_15M`），但免费额度与条款随时可能变化，不保证长期可用。

填写后运行：

```powershell
Set-Location E:\AI-Market-Workbench
.\scripts\stop-live.ps1
.\scripts\start-live-background.ps1
```

加载脚本仅解析字段白名单，不执行文件内容。`providers.env` 已由 `.gitignore` 排除，不复制到源码、前端或日志。七个字段（Alpaca 两组 / Twelve Data / Alpha Vantage / Tiingo / Massive / CoinGecko / FRED）都已在本机实测通过；SEC 数据标准化与免费额度边界见 `docs/phase-1/DATA-SOURCE-STATUS.md`。

官方依据：

- https://docs.alpaca.markets/us/docs/market-data-faq
- https://www.tiingo.com/account/api/token
- https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- https://polygon.io/docs/rest/stocks/aggregates/custom-bars （Massive 使用同一聚合契约）
