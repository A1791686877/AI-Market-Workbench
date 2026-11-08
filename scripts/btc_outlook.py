"""One-command BTC outlook: refresh -> deterministic evidence -> rule-based conclusion.

    python -B scripts/btc_outlook.py                 # 增量刷新到最新，然后出结论
    python -B scripts/btc_outlook.py --no-refresh    # 只用本地缓存（离线可跑）

规则真源：docs/phase-1/BTC-OUTLOOK-FRAMEWORK.md（btc-outlook-rules-v1）
产物：reports/btc-outlook-<UTC>.json + .md
零模型调用、零数据订阅。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # keep Chinese output readable on a GBK Windows console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic BTC outlook (rule-based, not a probability).")
    parser.add_argument("--instrument", default="CRYPTO:BTCUSDT:SPOT")
    parser.add_argument("--symbol", default="BTC")
    parser.add_argument("--subject", default="crypto-btc", help="用于取资金费率/基差上下文")
    parser.add_argument("--no-refresh", action="store_true", help="不联网，只用本地缓存")
    parser.add_argument("--outdir", type=Path, default=ROOT / "reports")
    parser.add_argument("--record", action="store_true",
                        help="把本次结论登记进共享 registry（data/outlook/registry.jsonl），供事后校验闭环使用")
    parser.add_argument("--json", action="store_true", help="同时打印完整 JSON")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    refresh = not args.no_refresh
    os.environ.update({
        "AMW_ROOT": str(ROOT),
        "AMW_DATA_MODE": "live" if refresh else "fixture",
        "AMW_NETWORK_ENABLED": "true" if refresh else "false",
        "AMW_CRYPTO_PROVIDER": "okx",
        "TEMP": str(ROOT / "tmp"), "TMP": str(ROOT / "tmp"),
    })

    from app.market_cache import MarketCache
    from app.models import DataProvider, Interval
    from app.outlook import RULES_VERSION, build_outlook
    from app.providers import OkxPublicProvider

    cache = MarketCache()
    rebuild_plan = {Interval.MIN_15: 10, Interval.HOUR_1: 4, Interval.HOUR_4: 3, Interval.DAY_1: 3}
    seconds = {Interval.MIN_15: 900, Interval.HOUR_1: 3600, Interval.HOUR_4: 14400, Interval.DAY_1: 86400}
    seconds_by_name = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

    fills: dict[str, object] = {}
    if refresh:
        provider = OkxPublicProvider()
        try:
            from app.service import MarketWorkbenchService
            instrument = MarketWorkbenchService().get_instrument(args.instrument)
            for interval, pages in rebuild_plan.items():
                try:
                    document = cache.load(DataProvider.OKX, args.instrument, interval)
                    now = datetime.now(UTC)
                    start = (document.bars[-1].open_at - timedelta(seconds=seconds[interval] * 2)
                             if document and document.bars else now - timedelta(days=60))
                    bars, source = provider.fetch_bars(instrument, interval, start, now, max_pages=pages)
                    merged = cache.save(DataProvider.OKX, args.instrument, interval, bars, source)
                    fills[interval.value] = {"new_bars": len(bars), "total": len(merged.bars),
                                             "last": merged.bars[-1].close_at.isoformat()}
                except Exception as exc:
                    fills[interval.value] = {"error": type(exc).__name__, "detail": str(exc)[:160]}
        finally:
            provider.http.close()

    def load(interval):
        document = cache.load(DataProvider.OKX, args.instrument, interval)
        if document is None:
            raise SystemExit(f"no cache for {args.instrument} {interval.value}; run once with --refresh (network) first")
        return [bar for bar in document.bars if bar.confirmed]

    daily = load(Interval.DAY_1)
    h4 = load(Interval.HOUR_4)
    h1 = load(Interval.HOUR_1)
    m15 = load(Interval.MIN_15)

    def continuity(bars, interval_seconds):
        gaps = 0
        for previous, current in zip(bars, bars[1:]):
            if int((current.open_at - previous.open_at).total_seconds()) > interval_seconds * 3 // 2:
                gaps += 1
        return gaps

    data_note = {
        "instrument": args.instrument,
        "refreshed": refresh,
        "fills": fills,
        "bars": {name: {"count": len(bars), "first": bars[0].open_at.isoformat(),
                        "last": bars[-1].close_at.isoformat(),
                        "internal_gaps": continuity(bars, seconds_by_name[name])}
                 for name, bars in (("15m", m15), ("1h", h1), ("4h", h4), ("1d", daily))},
    }

    context = None
    if refresh:
        try:
            from app.service import MarketWorkbenchService
            ctx = MarketWorkbenchService().get_market_context(args.subject)
            context = {"as_of": ctx.as_of.isoformat(), "spot": ctx.spot_price.value,
                       "mark": ctx.perpetual_mark_price.value, "funding_rate": ctx.funding_rate.value,
                       "basis_rate": ctx.basis_rate.value, "open_interest": ctx.open_interest.value,
                       "liquidation": ctx.liquidation_notional_observed.status.value}
        except Exception as exc:
            context = {"error": type(exc).__name__, "detail": str(exc)[:160]}

    report = build_outlook(daily, h4, h1, m15, symbol=args.symbol,
                           instrument_id=args.instrument, context=context, data_note=data_note)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.outdir.mkdir(parents=True, exist_ok=True)
    json_path = args.outdir / f"btc-outlook-{stamp}.json"
    md_path = args.outdir / f"btc-outlook-{stamp}.md"
    json_path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# BTC Outlook — {report.generated_at.isoformat()}",
        "",
        f"规则版本 `{RULES_VERSION}` · 标的 `{args.instrument}` · 数据截止 {data_note['bars']['15m']['last']}",
        "",
        "## 结论",
        "",
        "```",
        report.narrative,
        "```",
        "",
        "## 路径权重（规则排序，非概率）",
        "",
        "| 路径 | 分值 | 权重 |",
        "|---|---:|---:|",
        *[f"| {p.label} | {p.score} | {p.weight_pct}% |" for p in report.paths],
        "",
        "## 关键位",
        "",
        "| 名称 | 值 |",
        "|---|---|",
        *[f"| {key} | {value} |" for key, value in report.levels.model_dump().items()],
        "",
        "## 触发 / 失效",
        "",
        *[f"- **{key}**：{value}" for key, value in report.triggers.items()],
        "",
        "## 打分明细",
        "",
        f"结构分 {report.structure_score} / 动量分 {report.momentum_score}",
        "",
        "| 结构规则 | 得分 | 依据 |",
        "|---|---:|---|",
        *[f"| {h.rule} | {h.points:+.0f} | {h.detail} |" for h in report.structure_hits],
        "",
        "| 动量规则 | 得分 | 依据 |",
        "|---|---:|---|",
        *[f"| {h.rule} | {h.points:+.0f} | {h.detail} |" for h in report.momentum_hits],
        "",
        "## 输入与数据",
        "",
        "```json",
        json.dumps(data_note, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in report.limitations],
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(report.narrative)
    print("\nweights:", {p.key: f"{p.weight_pct}%" for p in report.paths})
    print("levels:", report.levels.model_dump())
    if args.record:
        from app import outlook_registry as registry
        entry = registry.entry_from_report(report, daily)
        added = registry.append_entries([entry])
        print(f"\nregistry: {'已登记 ' + entry.outlook_id if added else '已存在（去重跳过）'} "
              f"-> {registry.registry_path()}")
    print(f"\njson: {json_path}\nmd:   {md_path}")
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
