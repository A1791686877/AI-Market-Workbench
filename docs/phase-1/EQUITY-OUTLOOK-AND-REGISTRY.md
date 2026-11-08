# 美股展望 + 结论登记 + 事后校验（可重复流程）

> 状态：2026-09-12 落地并实测。规则引擎版本 `btc-outlook-rules-v1`（`app/outlook.py`），
> 登记/校验层 `outlook-registry-v1`（`app/outlook_registry.py`）。
> 零模型调用、零订阅：整条链路确定性可复算，不花额度、不发 AI 请求。
> 不是投资建议，不构成胜率；权重是**规则排序**，不是校准概率。
> 同一份 registry 同时容纳 `CRYPTO` 与 `EQUITY`：`python -B scripts\btc_outlook.py --record` 登记 BTC，
> `--verify` 按 `CRYPTO:` 前缀自动改用 OKX 日线缓存。

## 1. 这套流程解决什么

把"看行情 → 出结论 → 记录下来 → 事后核对"变成一条**可重复命令**，而不是每次重新讲故事：

1. **冻结**：用截至某一根K线的数据，产出三路径权重（A 先回踩再上 / B 直接上 / C 破位）、关键位、
   触发与作废条件，并把该结论连同**输入数据指纹**写进 registry（只追加）。
2. **校验**：事后只读冻结日之后的日线，按写死的优先级判定实际走成了哪条路径，记账。
3. **排名**：同一时点横向比较多个标的"谁最接近 A 路径"，用于挑选跟踪对象。

## 2. 一键流程

```powershell
# 环境（离线模式示例；联网刷新见下一节）
$env:PYTHONPATH = "E:\AI-Market-Workbench"

# ① 出结论 + 登记（在线增量刷新）
python -B scripts\equity_outlook.py

# ①b 只用本地缓存（离线、可重复）
python -B scripts\equity_outlook.py --no-refresh

# ② 事后校验闭环（把到期结论判成 A/B/C/UNRESOLVED）
python -B scripts\equity_outlook.py --verify --no-refresh

# ③ 只看计数（不发任何请求）
python -B scripts\equity_outlook.py --scoreboard

# 回放：把结论冻结在 N 个交易日之前（只用该日及更早数据，无未来函数）
python -B scripts\equity_outlook.py --no-refresh --as-of-days-ago 63

# 走查回放：从 21 个交易日前起，每隔 21 个交易日冻结一次，共 12 次（REPLAY 样本，8 标的 → 96 条）
python -B scripts\equity_outlook.py --no-refresh --as-of-days-ago 21 --replay-every 21 --replay-count 12

# 回放样本的分解（主判断 × 实际路径 + 独立度）
python -B scripts\replay_diagnostics.py
```

> **回测能让计数飞快长大，但不能让独立样本长大。** `--replay-every/--replay-count` 一次能写 80 条，
> 可它们在 registry 里全部标为 `REPLAY`，与 `FORWARD` 分开统计；独立度指标是 `distinct_freeze_dates`（当前 12）。
> 只有"下达结论时结果还不存在"的那 9 条能回答"这套规则以后管不管用"，而它们必须等。

联网刷新时的环境变量（`scripts\load-provider-env.ps1` 只注入 key，不决定路由）：

```powershell
. .\scripts\load-provider-env.ps1
$env:AMW_DATA_MODE = "live"; $env:AMW_NETWORK_ENABLED = "true"
$env:AMW_EQUITY_PROVIDER = "alpaca"; $env:AMW_EQUITY_EOD_PROVIDER = "alpaca"; $env:AMW_ALPACA_FEED = "sip"
```

产物：

| 文件 | 内容 |
|---|---|
| `reports/equity-outlook-<UTC>.json` / `.md` | 本次全部结论 + 排名 + 数据缺口 |
| `data/outlook/registry.jsonl` | 结论登记簿（每行一条，含 `origin`、冻结关键位与数据指纹） |
| `data/outlook/verification-resets.jsonl` | 被清空的验证结果留痕（例如误用未确认K线的那一条） |
| `data/outlook/origin-migrations.jsonl` | `FORWARD`/`REPLAY` 回填留痕 |
| `scripts/replay_diagnostics.py` | 回放样本的主判断×实际路径分解与独立度 |
| `reports/btc-outlook-<UTC>.json` / `.md` | 加密口径（`scripts\btc_outlook.py`，含 4h/1h/15m；`--record` 可写进同一 registry） |

## 3. 规则口径与"股票 vs 加密"的差异

同一套结构分/动量分/路径公式，**股票口径关闭四年减半周期**：

| 项 | CRYPTO（`market="CRYPTO"`） | EQUITY（`market="EQUITY"`） |
|---|---|---|
| `cycle_bottom_window`（顶后 270–480 天） | 参与打分（±8 分） | **禁用**：记 0 分并在 `structure_hits` 写明 `disabled` |
| `levels.cycle_floor`（顶后最低点） | 给出 | **置空**（无减半周期含义） |
| `triggers.cycle_turn` | "月线收盘 < 周期地板 → 深洗剧本" | "不适用（四年周期规则已禁用）" |
| limitations | OKX 场所级数据等 | 明确写出周期规则不适用 + 财报/宏观未纳入 |
| 输入周期 | 日线 + 4h + 1h + 15m | **只用确认日线**（4h/1h/15m 规则按"跳过（中性）"处理） |

其余规则（价格与 SMA20/50/200、日/周 DI、周/月 MACD 柱、月线 RSI、7/365 日收益、
自身历史的"深跌后修复"基础率、FVG 回踩区、VPVR 辅助）在两种口径下一致。

三路径公式（`app/outlook.py`）：

```
A = 0.55·结构 + 0.45·(100 − 动量)   # 先回踩再上（结构完好 + 短线偏弱）
B = 0.60·结构 + 0.40·动量           # 不回踩直接上
C = 0.75·(100 − 结构) + 0.25·(100 − 动量)  # 破位
```

权重 = 三者归一化后取整（总和 100）。**主判断 = 权重最大者**，同权重比原始分。

## 4. registry 登记条目（`outlook-registry-v1`）

```jsonc
{
  "registry_version": "outlook-registry-v1",
  "outlook_id": "outlook-<hex>",
  "recorded_at": "…Z",
  "rules_version": "btc-outlook-rules-v1",
  "market": "EQUITY",
  "origin": "FORWARD",        // FORWARD=下达时结果还不存在（无偏）；REPLAY=历史回放（样本内、重叠）
  "symbol": "NVDA", "instrument_id": "US:NVDA",
  "as_of": "…Z",              // 冻结所用最后一根日线的 close_at
  "price": "218.29",
  "primary": "A",
  "paths": [{"key":"A","label":"…","score":…,"weight_pct":46}, …],
  "structure_score": 94.0, "momentum_score": 40.0,
  "levels": {"confirm_reclaim":"…","confirm_breakout":"…","dip_zone_low":"…","dip_zone_high":"…",
             "structural_support":"…","swing_low_20d":"…","target_1":"…","target_2":"…","cycle_floor":null},
  "triggers": {"confirm_reclaim":"…","invalidate_A":"…", …},
  "bars_used": 1300,
  "data_fingerprint": "<sha256 of the frozen bar set>",
  "verification": null         // 校验后才有值
}
```

纪律：

- **只追加**：同一 `(instrument_id, as_of, rules_version, primary)` 不重复登记；
- 已登记结论**永不改写**，校验结果单独写进 `verification` 字段（整表原子重写，`os.replace`）；
- `data_fingerprint` 是输入K线集合的 sha256，可复算，防止"事后换数据"。

## 5. 事后校验判定（写死在代码与报告里）

**先到先判**：冻结日之后第一根满足条件的日线决定路径，不回看、不重排。
**同一根K线内**优先级 `C > B > A`。

| 路径 | 判定条件 |
|---|---|
| C | 日线收盘 < 冻结时的 SMA200（`levels.structural_support`） |
| B | 未先触及回踩区，日线收盘 > 冻结时近 3 日高点（`confirm_breakout`） |
| A | 先触及回踩区（最低价 ≤ 回踩区上沿），之后日线收盘 > 冻结时 SMA20（`confirm_reclaim`） |
| UNRESOLVED | 126 个交易日内三条都未成立 |

同时记录：`dip_zone_touched_at` / `confirm_reclaim_at` / `confirm_breakout_at` / `invalidate_at` /
`target_1_hit_at` / `max_close` / `min_low` / `final_close` / `return_pct` / 21-63-126 日区间收益。

边界条款（都在 `notes` / `caveats` 里输出）：

- 没有任何后续K线时，结论保持 **OPEN**，不会写成 UNRESOLVED（避免"没观测"冒充"已判定"）；
- 回踩区在冻结价之上（罕见）时，按"回踩条件视为已满足"处理并写明；
- 只有日线收盘参与判定，不用盘中插针，避免判定口径漂移。

## 6. 记分板纪律（为什么它永远不是"胜率"）

`scoreboard()` 只输出**计数**：已登记/已验证/OPEN、A/B/C/UNRESOLVED 各几条、主路径命中/未命中/未定，
并且**按 `origin` 分开**：`FORWARD`（下达结论时结果还不存在）与 `REPLAY`（用历史数据回放）。
它**不输出** `hit_rate`，因为：

- 回放能飞快把数量堆起来（一条命令 80 条），但那是**样本内 + 高度重叠**：同一冻结日的 8 个标的是
  **同一个市场状态**，规则本身又来自这段历史。`distinct_freeze_dates` 才是回放独立度的上限（当前 12，不是 96）。
- 前向样本才是无偏的，但只能等（当前 9 条，全部 OPEN）。
- 路径权重是规则排序，没有做概率校准，也没有样本外测试。

### 6.1 96 条回放的分解（`scripts/replay_diagnostics.py`）

| 主判断 | 条数 | 实际 A | 实际 B | 实际 C | UNRESOLVED | 描述性命中率 |
|---|---:|---:|---:|---:|---:|---:|
| A | 33 | 14 | 12 | 6 | 1 | 42% |
| B | 42 | 15 | 23 | 4 | 0 | **55%** |
| C | 21 | 0 | 0 | 21 | 0 | 100%（**近乎同义反复**） |

三条必须一起读的结论：

1. **C 的 100% 不是预测力**：21 条里有 20 条在冻结时价格**已经**在 SMA200 下方 2%–19.6%，而 C 的判定条件
   本身就是"后续收盘 < SMA200"——它描述"已经破了"，不是"预测会破"。把 21/21 当命中是错的。
2. **B 是最稳的主判断**：只有 10% 后续转 C（趋势票很少直接崩），所以"高 B 权重 + 已站上 SMA20"是这套规则里
   风险最低的一档（对应 2026-09-12 的 META / AAPL）。
3. **A 是最弱的主判断**：42% 命中且有 **18%** 直接转 C（作废位就在脚下）。A 主判断（如 NVDA）必须带着
   "跌破 SMA200 即认错"的纪律读，不能只取乐观一面。

每个冻结日的命中数在 1/8 ~ 8/8 之间波动（制度依赖），**总命中率 58/96 不具备稳定外推意义**。

## 7. 2026-09-12 实测结果（可直接复现的基线）

- 当日排名（`--no-refresh`，日线截至 2026-09-11 收盘）：

  | 排名 | 标的 | 主路径 | A/B/C | 结构分 | 动量分 | 收盘 | 回踩区 |
  |---:|---|---|---|---:|---:|---:|---:|---|
  | 1 | NVDA | A | 46/43/11 | 94 | 40 | 218.29 | 213.36–216.57 |
  | 2 | AMZN | A | 44/43/13 | 89 | 46 | 256.78 | 226.10–235.21 |
  | 3 | MSFT | A | 38/36/26 | 64 | 44 | 495.63 | 465.96–478.27 |
  | 4 | AAPL | B | 37/49/14 | 80 | 72 | 332.27 | 319.15–326.30 |
  | 5 | GOOGL | A | 35/33/32 | 52 | 46 | 338.50 | 331.85–335.03 |
  | 6 | GOOG | A | 35/33/32 | 52 | 46 | 335.45 | 329.34–332.55 |
  | 7 | META | B | 31/48/21 | 66 | 78 | 648.03 | 624.80–642.14 |
  | 8 | TSLA | C | 19/33/48 | 24 | 72 | 365.44 | 323.00–326.15 |

- registry 现状（`--scoreboard`）：**105 条 = FORWARD 9（全 OPEN）+ REPLAY 96（全已校验）**，
  回放实际 A=29 / B=35 / C=31 / UNRESOLVED=1；独立度只有 **12 个冻结日**。
- 走查回放命令：`python -B scripts\equity_outlook.py --no-refresh --as-of-days-ago 21 --replay-every 21 --replay-count 12`
  → 再用 `--verify` 校验，`scripts\replay_diagnostics.py` 出分解表。
- 只吃**已确认**的日线：缓存里未收盘的当日K线既不能出结论、也不能当验证依据（第一版曾误用它，
  已修 + 已加回归测试，并把受污染的那条 `verification` 清空归档到 `data/outlook/verification-resets.jsonl`）。

## 8. 怎么加新标的 / 复用同一套

1. 目录里加一条股票标的：`app/instruments.py` 的 `EXTRA_EQUITY_SYMBOLS`（或直接给 `build_outlook`
   传 `symbol`/`instrument_id`）；基本面需要 CIK 时加到 `MAG7_CIK`。
2. 加密标的：`scripts\btc_outlook.py` 走 OKX 缓存（15m/1h/4h/日线），`market="CRYPTO"`。
3. 同一条 registry 同时容纳两种市场（`market` 字段区分），记分板按市场分组统计。

## 9. 不适用 / 已知限制

- 规则不含财报、指引、ETF 流、期权持仓；股票口径只用日线。**宏观（FRED）已有但刻意不进规则**：
  它只作背景（`GET /v1/macro/context` / MCP `macro_get_context`），避免把"背景"伪装成"信号"。
- 历史不完整（`history_complete=false`，实际起始 2016-01-04）时，长周期规则基于可得样本。
- VPVR 是"K线高低区间均摊成交量"的估算（`is_estimate=true`），不是逐笔分布。
- 四年减半周期对个股无意义 → 股票口径已禁用，但"周期故事"仍可能被误读，故 report 里显式标注。
- 跨市场/跨周期价格基准（日线总回报复权 vs 15m 拆股复权）未统一换算，股票口径只用日线正是为了规避这点。
- 估值只在单独的基本面接口给出（`TTM_EPS` / `PE_TTM`），**没有进入路径权重**；且 TTM EPS 为 GAAP 口径、
  未剔除一次性项目（GOOG 的 TTM/FY = 1.84 就是典型：TTM 的 17x 不能当"便宜"读）。
- 回放样本的独立度上限是冻结日数量（当前 12），不是行数（96）；任何"命中率"都只能当描述统计。
