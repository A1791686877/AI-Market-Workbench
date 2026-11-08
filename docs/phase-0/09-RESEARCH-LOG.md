# Phase 0 调研与安全审查日志

审查时间：2026-09-05（Asia/Shanghai）  
性质：只读、官方来源优先的初筛；未下载/执行候选项目，未连接账户，未输入密钥

## 1. 执行记录

| 项目 | 结果 |
|---|---|
| 工作目录 | 文档仅写入 `E:\AI-Market-Workbench\docs\phase-0`；未在 C 盘创建项目数据 |
| 账户/交易 | 未连接真实账户；未调用私有交易 API；未申请任何权限 |
| 开源审查 | 查看官方仓库的许可证、README、发布记录、安全说明及关键安装/运行文件 |
| 数据源审查 | 查看供应商官方价格、产品说明、API 文档、条款和 SEC 官方开发者说明 |
| 漏洞结论 | 仅为预采用风险筛查；没有完整克隆、锁文件、SBOM 和构建物，不能宣称“无漏洞” |
| 网络/遥测结论 | 根据已核验源码/文档识别已知行为；采用前仍需隔离环境动态观测 |

## 2. 官方证据索引

### 美股与基本面

- Massive Stocks Pricing: https://massive.com/pricing?product=stocks
- Massive Stocks Business: https://www.massive.com/stocks
- Tiingo End-of-Day: https://www.tiingo.com/products/end-of-day-stock-price-data
- Tiingo Documentation / Terms entry: https://www.tiingo.com/documentation/general
- Alpaca market data coverage distinction: https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC developer resources and fair-access limit: https://www.sec.gov/about/developer-resources
- SEC data quality caveat: https://www.sec.gov/data-research/statistics-data-visualizations

### 加密市场与链上

- Binance Spot REST market data: https://developers.binance.com/en/docs/products/spot/rest-api
- Binance USD-M futures market-data catalog: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
- Binance public archive repository: https://github.com/binance/binance-public-data
- Binance liquidation stream implementation note: https://github.com/binance/binance-connector-python/blob/master/clients/derivatives_trading_usds_futures/src/binance_sdk_derivatives_trading_usds_futures/websocket_streams/websocket_streams.py
- Binance archive consistency issue examples: https://github.com/binance/binance-public-data/issues/475 and https://github.com/binance/binance-public-data/issues/484
- Binance archive licensing clarification requests: https://github.com/binance/binance-public-data/issues/473 and https://github.com/binance/binance-public-data/issues/478
- CoinGecko pricing: https://www.coingecko.com/en/api/pricing
- CoinGecko data license: https://www.coingecko.com/en/api/enterprise/data-license
- Coin Metrics API: https://docs.coinmetrics.io/api
- Coin Metrics community-data scope: https://gitbook-docs.coinmetrics.io/packages/coin-metrics-community-data
- Coin Metrics active-address definition: https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/addresses/active-addresses
- OKX API Agreement (public API restrictions reference): https://www.okx.com/en-gb/help/okx-api-agreement
- Bitget API Terms (public data restrictions reference): https://www.bitget.com/support/articles/12560603797947

### 候选开源项目

- OpenBB repository/license: https://github.com/OpenBB-finance/OpenBB
- OpenBB package metadata: https://github.com/OpenBB-finance/OpenBB/blob/develop/openbb_platform/pyproject.toml
- OpenBB security policy/releases: https://github.com/OpenBB-finance/OpenBB/blob/develop/SECURITY.md and https://github.com/OpenBB-finance/OpenBB/releases
- TA-Lib core/wrapper: https://github.com/TA-Lib/ta-lib and https://github.com/TA-Lib/ta-lib-python
- Lightweight Charts license/docs/releases: https://github.com/tradingview/lightweight-charts/blob/master/LICENSE, https://github.com/tradingview/lightweight-charts/blob/master/website/docs/intro.mdx and https://github.com/tradingview/lightweight-charts/releases
- CCXT license/releases: https://github.com/ccxt/ccxt/blob/master/LICENSE.txt and https://github.com/ccxt/ccxt/releases
- Qlib license/package/config/releases: https://github.com/microsoft/qlib/blob/main/LICENSE, https://github.com/microsoft/qlib/blob/main/pyproject.toml, https://github.com/microsoft/qlib/blob/main/qlib/config.py and https://github.com/microsoft/qlib/releases
- RD-Agent repository/environment runner/releases: https://github.com/microsoft/RD-Agent, https://github.com/microsoft/RD-Agent/blob/main/rdagent/utils/env.py and https://github.com/microsoft/RD-Agent/releases
- Freqtrade package/install/releases: https://github.com/freqtrade/freqtrade/blob/develop/pyproject.toml, https://github.com/freqtrade/freqtrade/blob/develop/docs/installation.md and https://github.com/freqtrade/freqtrade/releases
- FinRobot repository/notice/trademark: https://github.com/AI4Finance-Foundation/FinRobot, https://github.com/AI4Finance-Foundation/FinRobot/blob/master/NOTICE and https://github.com/AI4Finance-Foundation/FinRobot/blob/master/TRADEMARK_POLICY.md
- TradingAgents repository/changelog: https://github.com/TauricResearch/TradingAgents and https://github.com/TauricResearch/TradingAgents/blob/main/CHANGELOG.md

## 3. 实现前复核清单

1. 在不含密钥、网络默认关闭的 E 盘隔离环境克隆固定 tag/commit；记录 commit、签名和 SHA-256。
2. 检查 LICENSE、NOTICE、依赖许可证与字体/图标/模型等非代码资产；商业部署单独法务签核。
3. 生成 CycloneDX SBOM；运行 OSV、`pip-audit`、`npm audit`、容器/系统库扫描，并人工判定可达性。
4. 审查安装脚本、postinstall、Dockerfile、CI 下载、动态执行、反序列化、插件加载、shell 调用和权限提升。
5. 动态记录 DNS/HTTP 目的地、遥测、更新检查、文件写入与环境变量读取；确认所有项目路径在 E 盘。
6. 用假密钥运行 secret scan 和日志脱敏测试；验证前端构建物、异常栈、数据库和 Git 均无秘密。
7. 对每个数据供应商保存合同/条款快照、用途、展示/非展示、缓存、衍生数据、再分发、用户数、地区和到期日。
8. 完成上述步骤后，才能把“提议采用”升级为“批准采用”；任何严重未处置项均阻断。

