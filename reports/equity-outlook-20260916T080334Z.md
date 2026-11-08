# 美股 Mag 7 规则展望 — 2026-09-16T08:03:34.711469+00:00

规则版本 `btc-outlook-rules-v1`（股票口径：四年减半周期规则已禁用）· 数据刷新=是

## 谁最接近 A 路径（先回踩再上）

**NVDA**（A 权重 44%，主路径 A）

| 排名 | 标的 | 板块 | 主路径 | A 权重 | B 权重 | C 权重 | 结构分 | 动量分 | 收盘 | 回踩区 | 结构支撑 | 日线顶背离 |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | NVDA | Information Technology | A | 44% | 34% | 22% | 76 | 28 | 212.17 | 201.77–208.82 | 197.42335 | 无（近期且未被否定） |
| 2 | AMZN | Consumer Discretionary | A | 43% | 33% | 24% | 71 | 28 | 248.42 | 226.1–235.21 | 240.02775 | 无（近期且未被否定） |
| 3 | MSFT | Information Technology | A | 38% | 36% | 26% | 64 | 44 | 497.12 | 465.96–478.27 | 429.94165 | 2 个未失效顶背离（MACD_HISTOGRAM+RSI_LINE），11 根前，压制位 517.78 |
| 4 | AAPL | Information Technology | B | 35% | 52% | 13% | 80 | 78 | 331.34 | 319.15–326.3 | 285.1022 | 无（近期且未被否定） |
| 5 | META | Communication Services | B | 31% | 48% | 21% | 66 | 78 | 670.24 | 624.8–642.14 | 623.0271 | 无（近期且未被否定） |
| 6 | GOOG | Communication Services | B | 27% | 44% | 29% | 52 | 78 | 341.43 | 330.89–338.74 | 335.39605 | 无（近期且未被否定） |
| 7 | GOOGL | Communication Services | B | 27% | 44% | 29% | 52 | 78 | 344.98 | 333.23–342.1361 | 336.651 | 无（近期且未被否定） |
| 8 | TSLA | Consumer Discretionary | C | 27% | 23% | 50% | 24 | 44 | 356.58 | 323–326.15 | 398.3173 | 无（近期且未被否定） |

## 数据与缺口

- AAPL: history_complete=false (actual start 2016-01-04 14:30:00+00:00, download_status=UNASSESSED)
- MSFT: history_complete=false (actual start 2016-01-04 14:30:00+00:00, download_status=UNASSESSED)
- GOOGL: history_complete=false (actual start 2016-01-04 14:30:00+00:00, download_status=UNASSESSED)
- AMZN: history_complete=false (actual start 2016-01-04 14:30:00+00:00, download_status=UNASSESSED)
- NVDA: history_complete=false (actual start 2016-01-04 14:30:00+00:00, download_status=UNASSESSED)
- META: history_complete=false (actual start 2016-01-04 14:30:00+00:00, download_status=UNASSESSED)
- TSLA: history_complete=false (actual start 2016-01-04 14:30:00+00:00, download_status=UNASSESSED)
- GOOG: history_complete=false (actual start 2016-01-04 14:30:00+00:00, download_status=UNASSESSED)

## 边界

- 权重是规则排序，不是概率，也没有样本外校准；registry 计数不是胜率。
- 股票口径只使用确认日线；缺少 4h/1h/15m 输入时相关动量规则按“跳过（中性）”处理。
- 板块/行业来自 FinanceDatabase 的快照元数据（MIT，community-maintained），只作分类标签，不是价格证据、也不参与打分。
- 不是投资建议，不保证盈利。
