# 参考目录（FinanceDatabase）：用法、边界与刷新

> 状态：2026-09-13 评估并接入。**它提供的是"分类元数据"，不是行情，也不是基本面。**
> 上游：[JerBouma/FinanceDatabase](https://github.com/JerBouma/FinanceDatabase)（MIT，community-maintained）。

## 1. 为什么用它（评估结论）

| 需求 | FinanceDatabase 能提供 | 本项目现状 |
|---|---|---|
| 标的清单（未来批量扫描用） | 全部 112,690 只股票 / 36,481 只 ETF / 91,181 个指数；**美国上市股票 27,003 只**（按 MIC/国家过滤、剔除退市） | 目录里只有 9 个硬编码 subject，用户已决定"先不批量" |
| 板块/行业分类 | `sector` / `industry_group` / `industry`（股票）、`category_group` / `category` / `family`（ETF） | 完全没有板块字段 |
| 交易所/MIC/国家/货币 | `exchange`（市场层级，如 `NMS`）/ `mic`（真实场所，如 `XNAS`）/ `country` / `currency` | 只有写死的 `venue="NASDAQ"` |
| 市值分档 | `market_cap` 是**分档字符串**（如 `Mega Cap`），不是数字 | 无 |
| **不提供** | 价格、成交量、财报、ETF 资金流、期权 | 各自另有来源（Alpaca/SEC/OKX/FRED） |

**结论：有帮助——它正好补上"标的目录 + 板块分类"这一层**，而这层是批量扫描和板块相对强弱的前置条件。
它**不能**替代任何行情/基本面来源，也**不进入**估值或打分。

## 2. 接入方式（可复现）

```powershell
$env:PYTHONPATH = "E:\AI-Market-Workbench"
python -B scripts\fetch_financedatabase.py --assets equities,etfs,indices   # 一次性抓取（联网）
python -B scripts\catalog_report.py                                        # 分类 + 总量报告（离线）
```

- 抓取脚本**解析 default branch 的 commit sha 并固定**，写入 `reference/financedatabase/<commit>/provenance.json`：
  repo、commit、commit 日期、每个资产的 URL/字节数/**sha256**、许可证说明。
  本次快照：commit `a174c97d3bba`（2026-09-13T15:10:38Z），MIT。
- 数据以**上游原始的 `.bz2` 保存**（equities 15.3 MB / etfs 1.3 MB / indices 1.2 MB），读取时流式解压，
  不落地解压后的 CSV（避免在 E 盘复制一份几十 MB 的文件）。`.gitignore` 只放行 `provenance.json`。
- `app/catalog.py` 是**纯本地读取器**：`lookup` / `search` / `stats` / `us_universe`，只用白名单列，
  快照缺失或 schema 变化（缺 `symbol` 列）时**显式报错**，不猜、不降级。
- `scripts/catalog_report.py` 产出 `reports/reference-catalog-*.json`：跟踪标的的板块标签 + 可用宇宙规模。

## 3. 怎么用（当前已生效）

1. **展望报告带板块**：`equity_outlook.py` 的排名表新增"板块"列（来自本目录），并在边界里注明
   "分类标签不是价格证据、不参与打分"。Mag 7 只落在 3 个 GICS 板块（IT / 通信服务 / 可选消费）——
   这意味着横截面排名天生集中，不能当成分散投资。
2. **批量扫描的前置**：`ReferenceCatalog.us_universe()` 已能给出美国上市股票清单（27,003 只，
   已剔除退市），批量扫描一旦获准即可直接使用；届时仍受各数据源额度限制（Alpaca 约 200 次/分）。
3. **ETF/指数清单**：`etfs` / `indices` 资产可用于将来的板块代理或基准对照（同样不含资金流）。

## 4. 边界（写进代码与文档的纪律）

- **community-maintained**：个股的板块/行业可能滞后或出错；它是"分类快照"，不是权威事实。
- **不是价格证据、不进 AI 技术结论、不进估值**；只作标签与目录。
- **必须固定 commit**：刷新时重新跑 `fetch_financedatabase.py`（默认 `--ref main` 会解析新 sha 并新建目录），
  旧快照保留，报告里始终写清用的是哪个 commit。
- `exchange` 列是**市场层级代码**（`NMS`/`NYQ`…），不要当成交易所名；判断场所用 `mic`。
- `market_cap` 是**分档字符串**（`Mega Cap` 等），不能当数字做计算。
- 上游仓库约 5 GB（含未压缩 `database/`），**只取需要的 `.bz2`**，不要 clone 整个仓库。

## 5. 刷新与回退

```powershell
# 刷新到最新（会新建 <新commit>/ 目录，旧目录保留）
python -B scripts\fetch_financedatabase.py --assets equities,etfs,indices --force

# 回退：直接指定旧快照目录，或删掉新目录（loader 默认取 mtime 最新的那个）
```

`ReferenceCatalog._discover()` 默认选 `reference/financedatabase/*/provenance.json` 中 **mtime 最新**的快照；
若要钉死某个 commit，直接 `ReferenceCatalog(settings.root / "reference/financedatabase/<commit>")`。
