# 真实 AI 联调验收（2026-09-06）

这是单用户只读研究垂直切片，不是全部产品验收，不代表预测准确或可盈利。数据订阅 USD 0/月；无新订阅，无交易或账户接口调用。

## 1. 行情与实际覆盖

当前首选可连接源为已配置的 OKX 无密钥公共接口；Binance 先前实测为 HTTP 451，未绕过限制。页面公开现货与永续仍按来源独立标记，不伪称全市场。

本轮 BTC、ETH 真实模型验收均使用现货 15m：各 4,999 根，实际起点 `2026-07-16T01:15:00Z`，最新确认柱 `2026-09-06T03:00:00Z`，观测区间内报告缺柱 0，`history_complete=false`。15m 两年目标未完成。

上一轮历史覆盖记录（只作价格获取范围证据，不能作修正后量指标/聚合验收）：1h 起点约 2026-02-09、4h 约 2024-05-25；日K现货约 2018-01-11，永续约 2020-01-01。195m 由15m聚合，周/月由日K聚合。完整明细保留在 `reports/live-crypto-acceptance.json`。当前修改后的七周期全面再验收尚未完成。

AAPL 缺少凭据，尚未实测 Alpaca 免费延迟 SIP。代码限定行情主机、GET、feed=sip、end至少早15分5秒；这不是权限已验证的证明。Tiingo长期价与 SEC财报标准化仍待联调。配置入口见 LOCAL-CREDENTIALS.md；Massive暂缓。

## 2. 指标与图层

- 十项基础指标、本地支撑阻力/趋势、FVG、RSI/MACD背离、估算VPVR均来自同一引擎。
- OKX SPOT 的成交量从误用 quote volume 修正为 row[5] base volume；PERP 使用 row[6] base volume。旧缓存保留，新缓存采用 `okx-base-volume-v2` 命名空间，避免混用。
- 背离信号时间等待价格与指标枢轴都完成右侧确认；FVG冻结创建ATR，回补与有效性分开。FVG中点使用独立类型 FVG_MIDPOINT，不标成边界。
- 不完整的195m/周/月聚合不再作为已确认输入；美股长周期改按交易日历RTH时钟分桶，缺柱不会平移锚点。美股4h已闭合短尾柱仍明确 partial。
- 浏览器绘制240根OHLC，FVG/背离标记悬停显示ID、数值、确认时间。只画可见时间窗口内的标记，避免历史标记扭曲尺度。截图及ID核对在 `reports/browser-acceptance*.json` 与 `browser-market*.png`。
- 30项回归及正式OpenAPI/JSON Schema正反例、外部引用、JSON/YAML一致性通过。这不是所有指标都已经与第三方独立库逐点对照的声明。

## 3. 真实模型与证据

真实 provider 是 CODEX_CLI，CLI请求模型 `gpt-5.6-terra`，使用本机已有 ChatGPT 登录（本次查询为 Plus）；没有读取/复制 auth.json，没有新增 API 服务。供应商未在此事件协议返回精确模型revision，保存为null。

| 标的 | 实际成功分析ID | 输入tokens | 输出tokens |
|---|---|---:|---:|
| BTC | analysis-effa2b2dec80458fbbc0b46d61dc5b9e | 18,231 | 2,164 |
| ETH | analysis-cb2947321c7d4413bd90b589881179b0 | 17,958 | 2,287 |

两份原文哈希匹配、三情景结构通过、证据与价位类型/数值匹配；人工抽核RSI、MACD、ADX、VWAP、趋势、POC及支撑阻力陈述与冻结数据一致。情景条件仍是模型解释，不是经过收益验证的预测。

用户授权的公开数据经显式白名单投影后发送；只发最近8根K线、覆盖元数据、指标参数和证据，完整冻结快照保存在本地。不同时间抓取的衍生品上下文暂不进入模型，明确列为缺失，避免截止时间之后的信息混入。模型不读取 outcomes、账户、持仓、密钥或无关文件。

一次浏览器调用产生错误证据ID，服务端正确拒绝为 FAILED_VALIDATION。现输出Schema进一步把允许证据ID和价位直接限定为快照枚举；不自动回退、不静默纠正、不无限重试。当前实际提示词版本为 `scenario-v2/codex-public-evidence-v3`，call.json另存提示词SHA256与请求协议版本。

全部调用目录为 `data/model-runs/`；包含 input.json、prompt.txt、output.schema.json、output.json、events.jsonl、stderr.log、call.json。前两次错误成交量口径的调用保留但排除验收。`reports/ai-evidence-verification.json` 汇总包括失败验收在内的已保存调用tokens。

截至最终页面验收，共10个已完成模型回合（包括失败验收）：输入319,065、其中缓存131,584，输出18,980 tokens。此汇总不含开发助手本身的对话用量。最后查询账户5小时已用94%、周已用88%，即剩6%/12%；这是账户共享状态，不是本项目单独扣除比例，不兑换重置信用、不新增付费。

金额不可由CLI tokens推定，cost=null，不写USD0。缓存tokens是输入的子集。账户5小时/周额度与其他任务共享，不能把账户百分比变化归因于这些调用。

## 4. 真实 AI 客户端 MCP

真实 Codex 客户端自主执行两次 market_search_instruments 和两次 market_get_indicator_evidence，分别读取 BTC/ETH 并作答。不是仅Schema生成，也不是仅程序模拟客户端。

成功记录：`data/model-runs/mcp-client-fcbfcf9025b84272ad08d61cc8bc3dc6/answer.md`，完整工具调用与返回见同目录 events.jsonl 和 `reports/real-ai-mcp-acceptance.json`。

本轮输入56,161、其中缓存28,928，输出1,287 tokens；BTC与ETH各引用5个精确证据ID，未知引用0。回答指出趋势均向上而ETH动量较强，与该冻结时点指标相符；不是当前交易建议。早先参数映射失败及工具未暴露的失败轮次保留，不计成功。

MCP按需stdio启动：客户端命令 `powershell.exe`，参数 `-NoProfile -File E:\AI-Market-Workbench\scripts\start-mcp-live.ps1`。只用于本地客户端。独立验收脚本仅向AI开放搜索/精简证据两工具，不开放模型调用或outcomes。

## 本地使用及剩余项

打开 http://127.0.0.1:8080/ ，选择 BTC/ETH、现货、15m，模型模式选“真实 Codex 模型”，点击“AI分析”。显示快照截止时间、实际回答、生成时间、模型、tokens、对应冻结证据及结果记录；不会随着行情刷新自动调用。

最终浏览器点击已通过：2026-09-06T03:25:07Z，`aiSucceeded=true`、一次任务POST返回202、JavaScript错误0；成功分析ID `analysis-1398be9a43b34a66a4321a35aef3845d`。BTC/ETH各240根K线与FVG ID匹配，截图为 `reports/browser-real-ai.png` 和 `reports/browser-market-eth.png`。外域POST被拒绝为403，不触发模型调用。真实调用若遇账号限额或校验错误仍会明确失败，不能保证每次模型请求均成功。

启动：在 E:\AI-Market-Workbench 运行 `scripts/start-live-background.ps1`；停止用 `scripts/stop-live.ps1`。若页面旧缓存，请强制刷新。

仍未完成：AAPL免费源实测与基本面标准化、七周期修正后的全矩阵再验收、全历史补齐、所有指标独立数值基准测试、同步的现货+合约模型上下文、成熟收益自动回填、几十只/500只规模与免费额度验证。当前outcomes为PENDING，不作为AI准确率统计。

## 2026-09-06 后续更正

上文浏览器成功只证明真实调用与渲染链路。保存回答 `analysis-1398be9a43b34a66a4321a35aef3845d` 现被语义检查判定 REJECTED：价格方向及当前阻力角色不成立，不能以旧 aiSucceeded=true 宣称回答正确。原始记录未修改。

后续七周期数值比对、公开多周期快照与成熟价格表现回填已推进，当前证据与仍未完成项以 QUALITY-2026-09-06.md 为准。不将这些价格表现称为模型命中率。
