# 指标算法、因果约束与验收样例

状态：Phase 0 设计基线  
版本：1.0.0  
适用范围：只读基础版；美股 RTH 与 BTC/ETH 现货、USDT 永续

## 1. 全局计算契约

### 1.1 输入与数值规则

- 输入必须按 `bar_open_at` 严格升序、主键唯一，OHLCV 通过完整性和价格关系校验。
- 原始存储保留十进制定点值；指标运算使用 IEEE-754 float64，数据库/API 不提前按显示精度舍入。
- 缺少足够预热数据时返回 `null` 和 `missing_reason=INSUFFICIENT_WARMUP`，不得用零代替。
- 所有默认信号只使用已收盘 K 线。未收盘值只能放入独立 `preview` 字段，`confirmed=false`，不得写入正式信号历史。
- 指标版本由 `algorithm_id + algorithm_version + params + input_series_hash` 唯一确定；参数变化必须写审计日志。
- 股票默认输入为拆股复权 OHLCV；同时保留原始价格映射。现金分红日、拆股和异常公司行动窗口带标记，FVG 默认排除。
- 股票日内指标只读美东 RTH 09:30–16:00，按交易所日历处理 DST、半日市与停牌；不得把盘前盘后补进缺口。
- 加密市场统一 UTC：15m/1h/4h 从 UTC 00:00 锚定，周一 00:00 为周界，UTC 日历月为月界。
- 相等比较按未舍入值执行。测试比较默认绝对误差 `1e-9`、相对误差 `1e-9`；涉及第三方库交叉验证可放宽至 `1e-7`。

### 1.2 统一返回字段

每项指标至少返回：`name`、`algorithm_version`、`params`、`input_basis`、`current_value`、`series_ref`、`status`、`signal_at`、`anchors`、`confirmed`、`may_repaint`、`source_refs`、`evidence`、`warmup_bars`、`missing_reason`。完整历史序列存储在指标仓中，API 可分页；AI 快照只携带明确窗口和完整序列哈希。

## 2. 精确算法

### 2.1 SMA 与 EMA

默认参数：SMA(20)、EMA(20)，输入 `close`。

- `SMA_n(t) = sum(x[t-n+1:t]) / n`。
- `EMA_n` 的 `alpha = 2/(n+1)`；首个有效值用前 `n` 个输入的 SMA 播种，之后 `EMA(t)=alpha*x(t)+(1-alpha)*EMA(t-1)`。
- 不使用 pandas 默认的从第一点递推口径；不得用未来值反向填充。

### 2.2 MACD

默认参数：`fast=12, slow=26, signal=9`，输入 `close`。

- `macd = EMA_12(close) - EMA_26(close)`，两条 EMA 均按 2.1 播种。
- `signal` 以最早 9 个有效 `macd` 的 SMA 播种，再按 EMA(9) 递推。
- `histogram = macd - signal`。
- 默认状态依据已确认柱的 MACD/Signal 交叉和零轴位置；交叉信号时间是两者符号差首次改变的已收盘 K 线。

### 2.3 RSI

默认参数：RSI(14)，Wilder 口径，输入 `close`。

- `delta=close(t)-close(t-1)`；`gain=max(delta,0)`，`loss=max(-delta,0)`。
- 用前 `n` 个 delta 的简单平均播种 `avg_gain`、`avg_loss`。
- 后续 `avg_x(t)=(avg_x(t-1)*(n-1)+x(t))/n`。
- `RS=avg_gain/avg_loss`，`RSI=100-100/(1+RS)`。
- 边界：两者均为 0 时 RSI=50；仅 `avg_loss=0` 时为 100；仅 `avg_gain=0` 时为 0。

### 2.4 布林带

默认参数：`length=20, multiplier=2`，输入 `close`。

- 中轨为 SMA(20)。
- 标准差使用总体标准差：`sqrt(sum((x-mean)^2)/n)`，即 `ddof=0`。
- 上/下轨为 `middle ± 2*std`。
- `bandwidth=(upper-lower)/middle`；middle=0 时为 null。
- `%B=(close-lower)/(upper-lower)`；带宽为 0 时为 null。

### 2.5 ATR

默认参数：ATR(14)，Wilder 口径。

- 第一根 `TR=high-low`；其后 `TR=max(high-low, abs(high-prev_close), abs(low-prev_close))`。
- 首个 ATR 是前 `n` 个 TR 的简单平均；之后 `ATR(t)=(ATR(t-1)*(n-1)+TR(t))/n`。
- 股票开盘相对上一 RTH 收盘的真实跳空计入 TR；盘前盘后成交不作为上一根 K 线。

### 2.6 ADX

默认参数：ADX(14)，Wilder 口径。

- `up_move=high(t)-high(t-1)`；`down_move=low(t-1)-low(t)`。
- `+DM=up_move` 当且仅当 `up_move>down_move` 且 `up_move>0`，否则 0；`-DM` 对称。
- TR、+DM、-DM 均用 Wilder 平滑；`+DI=100*smoothed(+DM)/smoothed(TR)`，`-DI` 同理。
- `DX=100*abs(+DI--DI)/(+DI+-DI)`；分母为 0 时 DX=0。
- 首个 ADX 是前 `n` 个有效 DX 的平均，此后 Wilder 递推。预计最短预热约 `2n-1` 根；实际有效索引由实现测试锁定。

### 2.7 OBV

默认首值为 0：收盘上涨则加当根成交量，下跌则减，持平不变。股票必须注明成交量是否为完整市场；若不是，状态不得写成“全市场吸筹/派发”。

### 2.8 VWAP

- 典型价 `TP=(high+low+close)/3`，`VWAP=sum(TP*volume)/sum(volume)`。
- 股票日内 VWAP 每个 RTH 开盘重置；加密默认每日 UTC 00:00 重置。
- 周/月或用户自定义锚定 VWAP 必须在参数中返回 `anchor_at` 与重置规则。
- 累计成交量为 0 时返回 null，不允许向前填充。

### 2.9 成交量与量价关系

默认 `RVOL20=volume/SMA20(volume)`，SMA 包括当前已收盘柱。显著价格变化阈值为 `abs(close-prev_close) >= 0.1*ATR14`。状态机：

- 价格上涨且 RVOL≥1.2：`PRICE_UP_VOLUME_EXPAND`；
- 价格下跌且 RVOL≥1.2：`PRICE_DOWN_VOLUME_EXPAND`；
- 价格上涨且 RVOL≤0.8：`PRICE_UP_VOLUME_CONTRACT`；
- 价格下跌且 RVOL≤0.8：`PRICE_DOWN_VOLUME_CONTRACT`；
- 其他：`NEUTRAL`。

这些是描述标签，不直接等于买卖结论。股票半日市按同一时段位置的历史量曲线另做季节性标准化；基础 `RVOL20` 仍明确原式。

### 2.10 VPVR

默认窗口为最近 120 根已确认 K 线，默认 48 个等宽价格箱；必须返回实际起止时间、箱边界、箱内量、样本数和市场完整性。

1. 区间为 `[min(low), max(high)]`，除最后一箱外均为左闭右开，最后一箱闭合。
2. 每根 K 线的成交量按其 `[low,high]` 与各价格箱的重叠长度同比分配，隐含“柱内价格均匀成交”假设；`high=low` 时全部量进入包含该价格的箱。
3. 若有经许可的逐笔或 1 分钟数据，可切换到更精细算法，但 `allocation_algorithm` 必须变化，不能与默认结果混用。
4. POC 是成交量最大箱；并列取价格较低箱。
5. Value Area 目标 70%：从 POC 起，每一步加入相邻两箱中成交量较大者；并列先向低价扩展，直至累计量≥70%。其最低/最高边界分别为 VAL/VAH。
6. HVN/LVN：先对箱量做三点三角平滑 `[0.25,0.5,0.25]`（边缘重新归一）；HVN 是局部极大且突出度≥全局最大箱量 10%；LVN 是相邻两个 HVN 之间的局部极小且量≤全局最大箱量 30%。不足两个 HVN 时 LVN 返回空并说明原因。
7. 当前价低于 VAL、价值区内、高于 VAH 分别返回 `BELOW_VALUE`、`IN_VALUE`、`ABOVE_VALUE`。

### 2.11 枢轴、支撑阻力与趋势结构

默认枢轴窗口 `left=3,right=3`：中心高点不低于左右各 3 个高点、且两侧各至少有一个严格更低值时为候选高点；低点对称。连续等高平台仅保留时间最早的中心候选。枢轴到右侧第 3 根收盘后才确认，`signal_at` 为确认时刻，锚点仍指向中心 K 线。

支撑/阻力聚类：同类枢轴间距不超过 `max(0.25*ATR14_at_pivot, 0.001*price)` 时进入同簇；簇位取按该柱成交量加权中位数，至少 2 次触碰才成为确认水平。默认评分：

`score = 0.40*min(touches/4,1) + 0.25*exp(-age_bars/50) + 0.20*clip(median_rejection/ATR,0,1) + 0.15*clip(mean_RVOL/2,0,1)`。

交易时段高低点、跳空边界、POC/VAH/VAL 可作为独立候选证据，但不得伪装成同一枢轴算法的输出。

趋势结构使用最近两个已确认枢轴高点和低点：更高高点+更高低点为 `BULLISH`；更低高点+更低低点为 `BEARISH`；其余为 `MIXED_OR_RANGE`；任一侧不足两个锚点为 `INSUFFICIENT_STRUCTURE`。

### 2.12 普通/隐藏背离

默认分别在 RSI 线和 MACD 柱状图上运行；可选 MACD 线，但 `oscillator_component` 必须写明。价格使用影线高低点，价格枢轴 `left=3,right=3`；振荡器枢轴允许与价格锚点相差 ±2 根。两个价格锚点间隔 5–60 根。

- 普通看多：价格低点降低、振荡器低点升高；隐藏看多：价格低点升高、振荡器低点降低。
- 普通看空：价格高点升高、振荡器高点降低；隐藏看空：价格高点降低、振荡器高点升高。
- 价格差需至少为第二锚点 ATR14 的 0.1 倍。
- RSI 两锚点差需至少 3 点；MACD/柱状图差需至少为第二锚点前 60 根有效值标准差的 0.1 倍。
- 默认 `confirmation_mode=PIVOT_CLOSE`：第二价格枢轴与匹配振荡器枢轴的右窗口均结束且确认柱收盘后发信号；不额外要求突破中间结构。未完成右窗口的预览标记 `confirmed=false, may_repaint=true`，不能进入 AI 正式证据。
- 每项结果返回两个价格锚点、两个振荡器锚点、匹配偏移、类型、方向、阈值、确认时刻和是否可能重绘。

### 2.13 FVG

对连续三根已收盘 K 线 `(t-2,t-1,t)` 使用影线：

- 看涨：`high[t-2] < low[t]`；边界 `lower=high[t-2]`、`upper=low[t]`。
- 看跌：`low[t-2] > high[t]`；边界 `lower=high[t]`、`upper=low[t-2]`。
- `width=upper-lower`，仅当 `width >= 0.2*ATR14[t]` 才创建；中点 `(lower+upper)/2`。
- 创建/信号时间为第 3 根 K 线收盘；ATR 只能使用截至 t 的数据。前三根之后的数据只更新生命周期，绝不反向改变创建条件。

看涨 FVG 的 `fill_pct=clip((upper-min_future_low)/width,0,1)`；看跌为 `clip((max_future_high-lower)/width,0,1)`。状态：0 为 `UNTOUCHED`；`0<fill<0.5` 为 `PARTIAL`；`0.5≤fill<1` 为 `FILLED_50`；`fill≥1` 为 `FULLY_FILLED`。保留最大历史回补比例，不因反弹回退。

失效规则独立于回补：看涨后续确认收盘 `< lower-0.1*ATR14[current]`，或看跌收盘 `> upper+0.1*ATR14[current]` 时为 `INVALIDATED`，并保留失效前的最大回补状态。公司行动污染窗口、源切换边界、缺柱跨越三根结构时返回排除原因，不创建 FVG。

## 3. 验收样例（Golden Cases）

所有索引从 0 开始，结果按上述容差比较。

| 编号 | 输入 | 预期 |
|---|---|---|
| SMA/EMA-01 | close=`[1,2,3,4]`, n=3 | SMA 有效值=`[2,3]`；EMA 有效值=`[2,3]` |
| RSI-01 | close=`[1,2,3,2,4]`, n=3 | t3=66.666666667；t4=83.333333333 |
| MACD-01 | 40 根 close 均为 100 | 所有有效 MACD、signal、histogram 均为 0 |
| BB-01 | close=`[1,2,3]`, n=3, k=2 | middle=2；std=0.816496581；upper=3.632993162；lower=0.367006838 |
| ATR-01 | `(H,L,C)=[(10,8,9),(11,9,10),(12,9,11),(13,11,12)]`, n=3 | t2 ATR=2.333333333；t3=2.222222222 |
| ADX-01 | 等区间宽度、连续严格上移的足量柱，n=3 | 有效 -DI=0、DX=100，首个及后续 ADX=100 |
| OBV-01 | close=`[10,11,11,9]`, volume=`[100,150,120,200]` | OBV=`[0,150,150,-50]` |
| VWAP-01 | 两柱 TP=10/12、volume=100/200 | VWAP=`[10,11.333333333]` |
| VOL-01 | prev close=100, close=102, ATR=10, RVOL=1.3 | `PRICE_UP_VOLUME_EXPAND` |
| VPVR-01 | 箱 `[0,1),[1,2),[2,3]`；点价 .5/1.5/2.5 的量 20/60/20 | 箱量 20/60/20；POC=第2箱；70% VA 按低价优先为 VAL=0、VAH=2，覆盖80% |
| PIVOT-01 | high=`[1,2,5,2,1]`, left=2,right=2 | t2 为枢轴高，t4 收盘才确认 |
| TREND-01 | 确认高点 10→12、确认低点 8→9 | `BULLISH` |
| DIV-01 | 价格低点 100→95；匹配 RSI 30→35；锚点相隔10根且均确认 | `REGULAR_BULLISH` |
| FVG-01 | 第一根 high=100，第三根 low=102，ATR=5 | 宽2=0.4ATR，接纳；边界100/102，中点101 |
| FVG-02 | 延续 FVG-01，后续最低价101.5/100.9/99.9 | 依次回补25% `PARTIAL`、55% `FILLED_50`、100% `FULLY_FILLED` |
| FVG-03 | 延续 FVG-01，当前 ATR=5 且确认 close=99.4 | 因 99.4<99.5，`INVALIDATED` |

## 4. 必过的系统级正确性测试

1. **未来函数测试**：截断任意时点后的数据，截断点及之前所有已确认结果逐字节一致。
2. **RTH 隔离测试**：注入盘前/盘后极端成交，股票 K 线、指标、FVG、背离和 VPVR 不变。
3. **公司行动测试**：构造 2:1 拆股；复权序列无虚假 FVG，原始序列有行动标记且被排除。
4. **日历测试**：覆盖 DST 切换、节假日和 13:00 ET 半日市；不得跨会话合并 4h 柱。
5. **源完整性测试**：单交易所成交量只能标记 `EXCHANGE_ONLY`，AI 不得称为完整市场。
6. **重采样测试**：周/月从同一验证日 K 聚合；OHLCV 与公司行动口径可复算。
7. **交叉库测试**：SMA/EMA/MACD/RSI/BB/ATR/ADX/OBV 与固定版本 TA-Lib 交叉验证；有口径差异时以本文件为准并记录。
8. **属性测试**：随机合法 OHLCV 满足上下轨次序、RSI∈[0,100]、ATR≥0、FVG 边界/回补比例单调等不变量。
9. **缺失/重复测试**：重复、乱序、间断、零量、坏 OHLC 必须隔离或显式缺失，不得静默插值。
10. **可复现测试**：相同数据哈希、参数和算法版本在 CI 与生产得到相同序列哈希。

## 5. 第一版验收门槛

- 所有 Golden Cases 与系统级测试通过；关键指标语句覆盖与分支覆盖目标分别≥95%/90%。
- 每个正式信号能追溯到原始数据批次、复权因子、交易日历版本、参数、算法版本和证据锚点。
- 任何数据不完整、口径未知或预热不足均 fail closed：显示“缺失/不完整”，不生成确定性结论。
- 未收盘预览、以后结果表和 AI 当时输入在权限与查询路径上分离，通过反泄漏测试。

