# 数据源、覆盖与许可证决策

审查基准日：2026-09-05。价格和条款会变化，采购前必须重新打开官方页面并保存订单/条款快照。当前设计假设第一版是本机、单用户、内部研究；一旦公开展示、多用户、收费或为第三方提供 API，必须重新采购商业/再分发许可。

## 1. Phase 1 已批准的零预算组合

| 数据域 | Phase 1 优先来源 | 当前费用级别 | 许可/覆盖结论 | 状态 |
|---|---|---:|---|---|
| 美股盘中 OHLCV | Alpaca Basic market-data adapter | USD 0 | 只允许 `data.alpaca.markets` 行情接口；官方文档要求 key/secret 且凭据关联 Alpaca 账户，所以当前不配置、不调用；待用户另行授权数据账户后验证延迟 SIP 权限 | 已实现适配器，未启用 |
| 美股长历史日线 | Tiingo Free | USD 0 | raw/adjusted、dividend/split；仅本地内部研究，按免费额度按需获取 | 待无交易权限 token 后实测 |
| 美股免费备用/交叉验证 | Massive Basic | USD 0 | 免费层限速、历史与聚合覆盖受套餐约束，不得标称完整 SIP；按需交叉验证 | 待无交易权限 key 后实测 |
| SEC 申报/GAAP XBRL | SEC data.sec.gov / EDGAR | 免费 | 无 key；公平访问上限 10 req/s；issuer filings 的内容权利仍需尊重 | 采用 |
| 预告盈利日历 | 默认无 | 0 | 只有已发生的 filing/8-K 日期；未来预计日期显示缺失 | 缺失/可选采购 |
| BTC/ETH venue 行情 | Binance public/data-only API + public archive | API 免费 | 无账户/无 key；venue-only；地区与数据用途条款为上线闸门 | 有条件采用 |
| 资金费率/OI/基差 | Binance USDⓈ-M public endpoints | API 免费 | 官方历史窗口有限，启动后持续采集；非全市场 | 有条件采用 |
| 清算 | Binance public WS | API 免费 | 每 symbol 每 1000ms 仅最新快照，非完整数据，只能作 observed floor | 采用但强制 PARTIAL |
| 供应量/市值参考 | CoinGecko Demo | USD 0 | 额度有限；署名、缓存和再分发边界仍适用 | 待 Demo key 后实测 |
| 链上活动/费用 | Coin Metrics Community API | 免费 | 无 key、CC、非商业；只覆盖 Community 子集 | 采用（个人非商业） |
| 交易所流入/流出 | 默认无 | 0 | 免费可靠且许可清楚的统一源未确定 | 显示缺失；以后采购 |

## 2. 美股行情

### 2.1 当前盘中首源：Alpaca Basic（尚未启用）

Phase 1 已实现只允许 `https://data.alpaca.markets` 的 market-data-only adapter，并显式排除 Alpaca Trading API 域名和 SDK。Alpaca 官方说明 Basic 数据访问需要请求头中的 key/secret，且这些凭据与 Alpaca Trading API 账户关联；因此在“不得连接账户”的当前授权下，不能把它宣称为已接通。离线 fixture 通过后，如用户以后明确授权创建纯行情用途的数据账户，再验证 15 分钟延迟 SIP、历史窗口、分页、速率和成交量口径；未授权前保持 `NOT_CONFIGURED`。

### 2.2 付费完整市场候选：Massive Starter（暂不采购）

官方定价页当前列示 Stocks Starter 为 29 美元/月、全部美股 ticker、5 年历史、100% market coverage、15 分钟延迟、minute aggregates、corporate actions、WebSocket 和 flat files。[官方定价](https://massive.com/pricing?product=stocks) 官方股票页进一步说明其 NMS 覆盖包含主要交易所和 FINRA TRF，并称为 100% tape。[覆盖说明](https://www.massive.com/stocks)

若未来免费来源实测无法满足完整市场成交量、历史窗口或稳定性，可用运行日志形成升级理由。当前预算为 USD 0/月，因此不得默认启用或购买。

限制：Starter 是 Individual use。公开 Web 产品或商业显示不能沿用。Massive 当前 business 页面列出 Business 基础计划和 Full Market Delayed add-on，但最终费用、display/non-display、derived data、AI 输入、缓存期限和终止后的保留权必须以书面订单为准，不能从个人定价页推断。[商业说明](https://www.massive.com/stocks)

### 2.3 长历史日线：Tiingo Free 优先

Tiingo 官方 EOD 页面声明 Free 层为 USD 0，并提供有限请求/流量额度；产品包含 raw 和 adjusted OHLCV、dividends、splits。Phase 1 只使用 Free 层并按需缓存，Power 不采购。[EOD 产品页](https://www.tiingo.com/products/end-of-day-stock-price-data) 其文档明确说公开或再分发需另行联系并显示 attribution。[许可说明](https://www.tiingo.com/documentation/general) [Terms](https://api.tiingo.com/tos/)

Tiingo 日线是周/月统一聚合的权威输入；Massive 当日 RTH 聚合只作盘中临时日线。Tiingo 晚间修订到达后进行对账并发布新 revision。

### 2.4 为什么不把 IEX 当完整市场

IEX 或 Nasdaq Basic 等单 venue/proprietary feed 可以给报价，但不能代表所有美股成交量。Alpaca 官方文档也把 IEX 与 CTA/UTP SIP 区分，并说明部分计划是实时 IEX 或 15 分钟延迟 SIP。[Alpaca market data](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api) 因此任何 IEX-only 数据只能标 `EXCHANGE_ONLY`，不得用于声明 FULL_SIP 的 OBV、VWAP、量价和 VPVR。

### 2.4 对账与质量门

- Massive raw response 原样入 Bronze，SHA-256；过滤 RTH 后再聚合。
- 日线发布时校验 `low <= open/close <= high`、非负 volume、无重复、交易日连续性。
- Massive 临时 RTH 日线与 Tiingo 最终日线比较；价格差超过 `max(1 tick, 5 bps)` 或成交量差超过 1% 时 quarantined，人工/次日修订后再放行。阈值是平台质量规则，不是供应商 SLA。
- split/dividend 同时取 Massive 和 Tiingo；factor 或 effective date 不一致时禁止该窗口 FVG。
- flat files 只拉 top-500 增量或用户请求 symbol；禁止全市场分钟历史一键回填。

## 3. 美股基本面与事件

### 3.1 SEC EDGAR/XBRL 主源

SEC 的 data APIs 无认证、提供 submissions 和 Company Facts，涵盖 10-Q、10-K、8-K、20-F、40-F、6-K 等；API 近实时更新并提供 nightly bulk archives。[SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) SEC 公平访问要求声明 User-Agent、总请求不超过 10 req/s。[Developer Resources](https://www.sec.gov/about/developer-resources)

实现规则：

- 每个 fact 保存 taxonomy/tag/unit/start/end/fy/fp/form/filed/accession/frame 和 raw value。
- 三表展示以 filing context 和 period 选择器确定，不从公司之间强行对齐自定义 tag。
- FCF 默认 `NetCashProvidedByUsedInOperatingActivities - PaymentsToAcquirePropertyPlantAndEquipment`；若 capex tag/组成不同，保存映射和公式，否则缺失。
- cash/debt、diluted shares、revenue/net income growth 和估值都是可追溯派生值；amendment 形成新 revision。
- SEC 自身提醒 EDGAR/XBRL 可能有申报者错误，平台不能把“官方入口”误写为“数值绝对正确”。[SEC 数据说明](https://www.sec.gov/data-research/statistics-data-visualizations)

### 3.2 GAAP、非 GAAP 和盈利日期

- GAAP：优先 XBRL 标准 taxonomy；自定义 tag 显示 issuer-specific。
- 非 GAAP：只从 earnings release/8-K/10-Q/10-K 明示的指标和 reconciliation 抽取，保存原名称和调整项；没有 reconciliation 则 `MISSING_UNRECONCILED`。
- 实际盈利发布日期可由相关 filing acceptance/press release 取得；下一次“预计盈利日”不是 SEC 事实。基础版默认缺失，可选采购 Massive 的 TMX corporate events 或其他明确许可的数据集后再启用。
- 估值必须带 price_as_of、price_basis、shares_period 和财务 period；GAAP TTM、issuer non-GAAP TTM 和 forward consensus 不混在一个 P/E 字段。

## 4. 加密交易所数据

### 4.1 暂定主 venue：Binance

官方开发文档提供无需认证的公开 market data，并建议纯行情使用 `data-api.binance.vision`；文档覆盖 spot 和 USDⓈ-M futures。[Spot API](https://developers.binance.com/en/docs/products/spot/rest-api) USDⓈ-M 公开端点提供 funding history、当前/历史 OI 和 basis，但 OI history 仅最近 1 个月、basis 仅最近 30 天，故必须从上线日起持续保存。[USDⓈ-M market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

官方 public-data 仓库提供 spot 和 USD-M 的日/月 kline/trade archives 与 checksum；仓库 MIT 许可明确覆盖下载工具代码，但不能据此自动断言所有市场数据可商业再分发。[官方归档工具](https://github.com/binance/binance-public-data) 2026 年仍有公开 issue 报告月/日归档差异和 derivatives continuity 缺陷，因此 checksum 只证明文件完整，不证明市场内容正确。[归档差异示例](https://github.com/binance/binance-public-data/issues/475) [衍生品连续性示例](https://github.com/binance/binance-public-data/issues/484)

许可/合规闸门：

- 只使用无 key 的公开 market endpoints，不创建 Binance 账户或 API key。
- 启动时检查所在地区可用性；HTTP 451/地域限制立即标记 provider unavailable，不使用代理绕过。
- 基础版仅本地个人分析；公开、收费、提供第三方或训练可发布模型前，必须向 Binance 获得书面数据使用确认或切换到商业数据 vendor。
- 所有输出标 `BINANCE` 和 `EXCHANGE_ONLY`，不称“全网资金费率/OI/成交量”。

### 4.2 清算并不完整

Binance 官方 connector 说明：对每个 symbol，1000ms 内只推最新一条 liquidation order；没有则不推。[官方 connector 源码](https://github.com/binance/binance-connector-python/blob/master/clients/derivatives_trading_usds_futures/src/binance_sdk_derivatives_trading_usds_futures/websocket_streams/websocket_streams.py) 因此：

- 字段命名 `observed_liquidation_notional_floor`；不写 `total_liquidations`。
- coverage=SAMPLED，completeness=PARTIAL，保留采样规则版本。
- 断线窗口记 gap，不用 0 填充；历史回补不可得时显示缺失。

### 4.3 venue adapter 与 fallback

CCXT 仅做公开 OHLCV/ticker 的便捷层；funding/OI/basis/liquidation 使用原生适配器。Bitget、OKX、Bybit 可作为将来候选，但它们的 API 条款同样限制复制、竞争、再分发或第三方用途。例如 OKX 2026 API Agreement 明确把 public endpoint data 也纳入非商业和再分发限制。[OKX API Agreement](https://www.okx.com/en-gb/help/okx-api-agreement) Bitget API Key Terms 也限制衍生、复制产品和转售 API-related data。[Bitget API Terms](https://www.bitget.com/support/articles/12560603797947)

fallback 不能静默混成一条序列。若主源故障，创建新的 source segment；跨 venue 数据默认只用于验证，不自动拼接 K 线。用户必须看到切源时间。

## 5. 供应量、市值和链上

### 5.1 CoinGecko

用于 BTC/ETH circulating supply、total supply、market cap 和跨 venue 参考价，不替代交易所执行行情。当前 Demo 免费；Basic 月付 35 美元、年付折算 29 美元，页面列明商业许可层级和 attribution；原始数据再分发/white label 需要 custom license。[官方定价](https://www.coingecko.com/en/api/pricing) [许可说明](https://www.coingecko.com/en/api/enterprise/data-license)

存储时保留 supplied market cap 和平台重算 `reference_price × circulating_supply` 两个字段；差异不强行覆盖。ETH 没有类似 BTC 的固定最大供应量，`total_supply` 与 `max_supply` 必须分开，max 不适用时为 null + `NOT_APPLICABLE`。

### 5.2 Coin Metrics Community

Community API 无 key、采用 Creative Commons、限非商业，并明确只是 Network/Market Data Pro 的子集；当前限速为每 IP 6 秒 10 次。[API conventions](https://docs.coinmetrics.io/api) [Community limits](https://gitbook-docs.coinmetrics.io/packages/coin-metrics-community-data) 可用于 availability 允许的 active addresses、transaction count、fees、supply 等；每个 metric 读取官方 definition，不把 active addresses 当唯一用户数。[Active address definition](https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/addresses/active-addresses)

“网络收入”必须按资产定义：BTC 展示 miner transaction fees 和 block rewards 分项；ETH 展示 transaction fees、burn、priority fees/validator revenue 中实际可得的分项。没有对应 metric 或许可时显示缺失，不构造一个模糊的统一 revenue 数字。

### 5.3 交易所流入/流出

地址标签决定准确度，免费公开源难以保证。基础版字段存在但状态为 `MISSING_NO_LICENSED_SOURCE`。未来候选为 Coin Metrics Pro、Glassnode 或 CryptoQuant；采购时核验资产覆盖、标签修订、历史回溯、派生数据/AI权利和多用户显示许可。

## 6. 源切换和许可注册表

数据库 `source_license` 保存 provider、product、order_id、allowed_use、display_rights、derived_data_rights、ai_use_rights、retention、redistribution、territory、effective_at、expires_at、terms_hash、reviewed_by。任何 adapter 启用前由 policy engine 比对部署模式：

- `LOCAL_PERSONAL`：可使用明确允许个人内部分析的计划。
- `INTERNAL_BUSINESS`：需 commercial internal-use。
- `PUBLIC_DISPLAY`：需 display/redistribution。
- `PAID_SAAS/API`：需商业、derived data、AI 和相应再分发权。

不匹配时 fail closed；不能只显示免责声明后继续用。

## 7. 成本基线

Phase 1 当前刚性数据订阅成本为 **USD 0/月**：Alpaca Basic、Tiingo Free、Massive Basic、SEC、Binance 公共接口和 CoinGecko Demo 只有在各自凭据/地区/条款边界允许时才逐个实测。Massive Starter、Tiingo Power、CoinGecko Basic 与盈利事件数据均不购买。USD 59/月仅保留为过去的候选上限，不是当前成本或采购决定。模型费用另列；当前确定性本地 provider 成本为 0，也不代表未来外部模型免费。

商业化成本不是这个数字：美股 display/SIP、Tiingo redistribution、CoinGecko custom、链上 Pro 和交易所书面许可可能成为最大成本。未取得正式报价前只给区间，不写成已确定预算。
