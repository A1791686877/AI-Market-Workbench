# 候选开源项目审查

审查基准日：2026-09-05。结论是架构选型，不是对整个仓库的完整源代码审计。维护状态以官方仓库/发布页可见状态为准；依赖漏洞必须在实际锁定版本后重扫。

## 1. 总结清单

| 分类 | 项目 | 结论 |
|---|---|---|
| 采用 | TA-Lib Python/Core | 作为指标交叉验证器，不作为唯一算法真相 |
| 采用 | TradingView Lightweight Charts | 前端图表；保留 NOTICE 和 TradingView attribution |
| 有条件采用 | CCXT | 仅公共加密行情的薄适配层；高级衍生品字段走交易所原生适配器 |
| 仅参考 | OpenBB | 参考 provider 接口和数据标准化，不纳入核心运行时 |
| 仅参考 | Qlib | 后续点时回测/研究参考，不进入只读 MVP |
| 仅参考 | Freqtrade | 参考 dry-run、回测报告和风险控制；不引入运行时 |
| 拒绝采用 | RD-Agent | 会生成/执行代码并挂载/改写工作区，超出只读基础版边界 |
| 拒绝采用 | FinRobot | 以 LLM agent 和多外部数据源为中心，密钥示例与自动安装方式不符合本项目安全/确定性要求 |
| 拒绝采用 | TradingAgents | 近期才修复多项前视偏差，且会联网拉公告和运行多智能体决策，不适合作为事实引擎 |

“拒绝采用”指不进入产品依赖或复制核心代码，不否认其论文/界面可供人工阅读。

## 2. 逐项审查

### 2.1 OpenBB — 仅参考

- 能力：provider 聚合、标准模型、REST/MCP 方向与本项目相近，适合参考 adapter/extension 设计。
- 许可证：官方仓库和当前 platform metadata 标注 `AGPL-3.0-only`；通过网络提供修改版本时可能触发源代码提供义务，需要法律确认。[官方仓库](https://github.com/OpenBB-finance/OpenBB) [当前 pyproject](https://github.com/OpenBB-finance/OpenBB/blob/develop/openbb_platform/pyproject.toml)
- 维护/安全：仓库活跃并有 SECURITY.md；近期发布说明可见多次依赖锁文件安全更新及 credentials model 修复，说明团队在维护，也说明依赖面很大，不能等同“没有漏洞”。[发布记录](https://github.com/OpenBB-finance/OpenBB/releases) [安全政策](https://github.com/OpenBB-finance/OpenBB/blob/develop/SECURITY.md)
- 网络/密钥/文件：默认安装组合包含大量数据 provider，会连接各供应商；各 provider 有自己的 API key 和许可。全量引入扩大 egress、依赖与许可证面。
- 决定：不作为核心运行时；仅参考其标准化模型和插件接口。若未来确需某个 provider，单独重写最小适配器或把未经修改的 OpenBB 隔离为内部服务，并先做 AGPL 法律评估。

### 2.2 TA-Lib — 采用为交叉验证器

- 能力：覆盖 MACD、RSI、SMA/EMA、BBANDS、ATR、ADX、OBV 等；不提供本项目定义的 VPVR、背离、FVG、市场日历和证据协议。
- 许可证：C core 为 BSD-3-Clause，Python wrapper 为 BSD-2-Clause，商用友好并需保留版权/免责声明。[Core](https://github.com/TA-Lib/ta-lib) [Python wrapper](https://github.com/TA-Lib/ta-lib-python)
- 维护/安装：Python wrapper 当前 metadata 标注 0.7.1、Python 3.9–3.14；0.6.5 起官方 wheels 打包底层 C 库，降低 Windows 手工安装风险。[pyproject](https://github.com/TA-Lib/ta-lib-python/blob/master/pyproject.toml) [安装说明](https://github.com/TA-Lib/ta-lib-python)
- 网络/遥测：指标计算本地运行，无业务所需外部网络；未在本次官方安装说明中发现必需遥测。实际 wheel 仍需校验来源、hash 和 SBOM。
- 决定：采用固定版本，用于 golden cross-check；本平台仍实现并版本化自己的公式，明确 seed 差异，避免库升级改变历史结果。

### 2.3 TradingView Lightweight Charts — 采用

- 能力：高性能金融图表，适合 candlestick、volume 和 overlay；指标值仍来自后端。
- 许可证：Apache-2.0；官方文档额外要求公开页面显示 NOTICE 中的 attribution 和 TradingView 链接。[LICENSE](https://github.com/tradingview/lightweight-charts/blob/master/LICENSE) [attribution 指南](https://github.com/tradingview/lightweight-charts/blob/master/website/docs/intro.mdx)
- 维护/测试：官方发布页当前可见 5.x 持续修复；仓库有测试和构建流程。[发布页](https://github.com/tradingview/lightweight-charts/releases)
- 网络/遥测：库本身只负责前端 canvas 绘制；基础版不加载远程脚本/CDN，npm tarball 固定 integrity，CSP 禁止未知域。
- 决定：采用；不使用 TradingView 私有图表数据或把品牌归属隐藏。

### 2.4 CCXT — 有条件采用

- 能力：统一公开 ticker/OHLCV/trades 的调用与 symbol 映射；高级 funding/OI/basis/liquidation 的语义和历史覆盖仍因交易所不同，不能只靠 unified API。
- 许可证：MIT。[LICENSE](https://github.com/ccxt/ccxt/blob/master/LICENSE.txt)
- 维护：官方 releases 高频更新，意味着适配及时，也意味着升级漂移风险高；必须固定精确版本并做回归。[发布页](https://github.com/ccxt/ccxt/releases)
- 安全：同一包也包含下单、账户和密钥能力。基础版 wrapper 不暴露 credentials，静态 allowlist 只允许 `fetch_ohlcv/fetch_ticker/fetch_trades/fetch_order_book` 等经批准公共方法；网络代理再拒绝私有 path、签名 header 和非市场端点。
- 网络/遥测：运行时向所配置交易所 REST/WS 发请求；没有必要启用未知 exchange。只编译 BTC/ETH 所需 venue allowlist。
- 决定：采用最薄公共层；funding/OI/basis/liquidation 使用官方原生 adapter，原始 payload 保留。若 CCXT 行为与官方文档冲突，以原生接口和黄金 fixture 为准。

### 2.5 Qlib — 仅参考

- 能力：强项是数据集、模型、实验和回测，不是当前的可追溯行情展示/指标 API。
- 许可证/维护：MIT；官方发布页当前可见 v0.9.7 并增加 Parquet 支持。[LICENSE](https://github.com/microsoft/qlib/blob/main/LICENSE) [releases](https://github.com/microsoft/qlib/releases)
- 依赖/文件：当前 pyproject 含 MLflow、Redis、Mongo、LightGBM、CVXPY、Jupyter 等较大依赖面；默认 provider URI 曾/仍可落到 `~/.qlib/qlib_data/...`，在 Windows 会写 C 盘用户目录，必须显式重配。[pyproject](https://github.com/microsoft/qlib/blob/main/pyproject.toml) [config](https://github.com/microsoft/qlib/blob/main/qlib/config.py)
- 决定：基础版不装；未来回测阶段参考 point-in-time dataset、experiment recorder 和滚动训练，但优先用本项目自己的无泄漏数据层。

### 2.6 RD-Agent — 拒绝采用

- 能力：自动研究、生成代码、运行实验，适合隔离研究实验室，不适合只读事实平台。
- 许可证/维护：MIT，官方 release 当前可见 v0.8.0；维护并非拒绝原因。[仓库](https://github.com/microsoft/RD-Agent) [releases](https://github.com/microsoft/RD-Agent/releases)
- 关键风险：官方 quick start 要求 Docker/Linux、下载数据并配置工作目录；代码路径会把生成代码写入工作区并执行。环境代码还可递归读取工作区、缓存 pickle/zip，Docker 路径包含递归 `chmod -R 777` 逻辑。[README](https://github.com/microsoft/RD-Agent/blob/main/README.md) [env.py](https://github.com/microsoft/RD-Agent/blob/main/rdagent/utils/env.py)
- 决定：产品运行时拒绝。未来若评估，只能在无生产数据、无密钥、无宿主写权限、无默认网络的临时 VM 中进行。

### 2.7 Freqtrade — 仅参考

- 能力：成熟的加密交易 bot、dry-run、回测、FreqAI、风险与报告；能力远超且冲突于当前只读范围。
- 许可证/维护：GPLv3；官方约月度发布，2026 发布仍活跃。[pyproject](https://github.com/freqtrade/freqtrade/blob/develop/pyproject.toml) [releases](https://github.com/freqtrade/freqtrade/releases)
- 安装/网络/密钥：官方建议 Docker 或 setup 脚本；依赖 CCXT、Telegram、FastAPI 等，正常用途包含交易所账户和订单连接。[安装说明](https://github.com/freqtrade/freqtrade/blob/develop/docs/installation.md)
- 决定：不进入基础版、不复制 GPL 代码；仅参考未来 dry-run、订单幂等、风控和回测报告字段。

### 2.8 FinRobot — 拒绝采用

- 能力：LLM 多 agent 财务报告、DCF 和外部数据整合，可参考报告目录，但其数值事实链不是本项目所需的唯一确定性引擎。
- 许可证：Apache-2.0，但需保留 NOTICE；品牌另有 2026 商标政策。[NOTICE](https://github.com/AI4Finance-Foundation/FinRobot/blob/master/NOTICE) [商标政策](https://github.com/AI4Finance-Foundation/FinRobot/blob/master/TRADEMARK_POLICY.md)
- 安装/密钥/网络：官方 README 提供自动安装 deploy.sh，并指导把 OpenAI、FMP、Finnhub、SEC-API key 写入本地 config 文件；报告生成依赖多个商业 API。[README](https://github.com/AI4Finance-Foundation/FinRobot)
- 决定：不采用运行时和密钥模式；报告结构可由人工阅读后重新设计，不能复制其供应商条款受限数据。

### 2.9 TradingAgents — 拒绝采用

- 能力：多 agent 分析/辩论/交易决策；不是确定性指标引擎。
- 许可证/维护：Apache-2.0，发布活跃；v0.4.0 于 2026-08-31 才集中修复 FRED vintage、社交数据、memory、premature outcome、最新 bar 等前视/点时错误。[仓库](https://github.com/TauricResearch/TradingAgents) [CHANGELOG](https://github.com/TauricResearch/TradingAgents/blob/main/CHANGELOG.md)
- 安全/网络：changelog 记录过 langchain-core 漏洞修补、移除受 CVE-2026-22218 影响的 chainlit、ticker path traversal 修复；CLI 欢迎页还会访问 `api.tauric.ai/v1/announcements`，形成非必要外连。[CHANGELOG](https://github.com/TauricResearch/TradingAgents/blob/main/CHANGELOG.md)
- 决定：基础版拒绝。其近期修复恰好证明“多 agent + 多数据 fallback”不能替代本项目的 point-in-time、无静默切源和确定性证据要求。

## 3. 采用前安全门

任何实际依赖安装都必须：

1. 固定 tag/commit 和 package hash；禁止浮动 `latest`、Git 主分支和安装时任意脚本下载。
2. 人工审阅 package lifecycle scripts、setup hooks、Dockerfile、网络域名和默认写路径。
3. 生成 CycloneDX SBOM，运行 OSV-Scanner、pip-audit/npm audit、Trivy（容器）和许可证扫描。
4. Critical/High 不能只写“可接受”；需升级、移除、隔离或经负责人签字的到期例外。
5. 在无密钥沙箱执行首次安装，记录所有 DNS、网络、进程、注册表和文件写入；确认只写 E 盘项目路径。
6. secret scan 覆盖源码、历史、构建物、日志和测试 fixture。
7. 对每次升级重复指标黄金测试、bar 边界测试、公开端点 egress 测试和 SBOM diff。

## 4. 仍未完成的审计项

本阶段没有 clone 九个完整仓库、没有对每一行安装脚本做人工审计，也没有针对一个锁定依赖树运行漏洞扫描。因此不能声称“无安全隐患”。本文件给出的是是否值得进入下一轮锁定版本审查的架构闸门。
