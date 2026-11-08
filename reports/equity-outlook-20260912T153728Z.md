# 美股 Mag 7 规则展望 — 2026-09-12T15:37:28.588263+00:00

规则版本 `btc-outlook-rules-v1`（股票口径：四年减半周期规则已禁用）· 数据刷新=是

## 谁最接近 A 路径（先回踩再上）

**NVDA**（A 权重 46%，主路径 A）

| 排名 | 标的 | 主路径 | A 权重 | B 权重 | C 权重 | 结构分 | 动量分 | 收盘 | 回踩区 | 结构支撑 |
|---:|---|---|---:|---:|---:|---:|---:|---:|---|---:|
| 1 | NVDA | A | 46% | 43% | 11% | 94 | 40 | 218.29 | 213.36–216.57 | 197.1052 |
| 2 | AMZN | A | 44% | 43% | 13% | 89 | 46 | 256.78 | 226.1–235.21 | 239.7977 |
| 3 | MSFT | A | 38% | 36% | 26% | 64 | 44 | 495.63 | 465.96–478.27 | 429.65395 |
| 4 | AAPL | B | 37% | 49% | 14% | 80 | 72 | 332.27 | 319.15–326.3 | 284.53705 |
| 5 | GOOG | A | 35% | 33% | 32% | 52 | 46 | 335.45 | 329.34–332.55 | 335.16255 |
| 6 | GOOGL | A | 35% | 33% | 32% | 52 | 46 | 338.5 | 331.85–335.03 | 336.3809 |
| 7 | META | B | 31% | 48% | 21% | 66 | 78 | 648.03 | 624.8–642.14 | 622.57805 |
| 8 | TSLA | C | 19% | 33% | 48% | 24 | 72 | 365.44 | 323–326.15 | 398.92545 |

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
- 不是投资建议，不保证盈利。
