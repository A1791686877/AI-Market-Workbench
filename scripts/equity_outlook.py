"""Mag 7 (plus GOOG) rule-based outlooks with a conclusion registry and verification loop.

    python -B scripts/equity_outlook.py              # 增量取数 → 出结论 → 登记 registry → 排名
    python -B scripts/equity_outlook.py --verify     # 事后校验：用冻结日之后的日线判定路径 → 记账
    python -B scripts/equity_outlook.py --scoreboard # 只打印计数（不发请求）

规则真源：app/outlook.py（btc-outlook-rules-v1）+ 股票口径关闭四年减半周期规则。
零模型调用、零订阅：确定性规则，无 AI、无成本。
产物：reports/equity-outlook-<UTC>.json/.md、data/outlook/registry.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # keep Chinese output readable on a GBK Windows console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mag 7 equity outlooks (rule-based, not a probability).")
    parser.add_argument("--symbols", default="AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,GOOG")
    parser.add_argument("--bars", type=int, default=1300, help="每日线取数上限（默认约 5 年）")
    parser.add_argument("--as-of-days-ago", type=int, default=0,
                        help="把结论冻结在 N 个交易日之前（只用该日线及更早的数据，无未来函数）；"
                             "配合 --verify 可演示事后校验闭环")
    parser.add_argument("--replay-every", type=int, default=0,
                        help="走查回放：从 --as-of-days-ago 起每隔 N 个交易日再冻结一次（REPLAY 样本）")
    parser.add_argument("--replay-count", type=int, default=1,
                        help="走查回放的冻结次数（默认 1）")
    parser.add_argument("--no-refresh", action="store_true", help="不联网，只读本地缓存")
    parser.add_argument("--verify", action="store_true", help="跑事后校验闭环（可离线）")
    parser.add_argument("--scoreboard", action="store_true", help="只打印 registry 计数")
    parser.add_argument("--no-record", action="store_true", help="只出报告，不写 registry")
    parser.add_argument("--outdir", type=Path, default=ROOT / "reports")
    return parser.parse_args()


def _load_daily(symbols: list[str], *, refresh: bool, limit: int) -> tuple[dict[str, list], dict[str, str]]:
    """Return {instrument_id: [Bar]} plus per-symbol error notes (never a silent empty list)."""
    from app.models import DataProvider, Interval

    bars_by_instrument: dict[str, list] = {}
    notes: dict[str, str] = {}
    if refresh:
        from app.service import MarketWorkbenchService
        service = MarketWorkbenchService()
        for symbol in symbols:
            instrument_id = f"US:{symbol}"
            try:
                page = service.get_bars(instrument_id, Interval.DAY_1, None, limit, None)
            except Exception as exc:
                notes[symbol] = f"{type(exc).__name__}: {str(exc)[:160]}"
                continue
            bars_by_instrument[instrument_id] = [b for b in page.bars if b.confirmed]
            if not page.quality.history_complete:
                notes[symbol] = (f"history_complete=false (actual start {page.quality.actual_start_at}, "
                                 f"download_status={page.quality.history_download_status})")
    else:
        from app.market_cache import MarketCache
        cache = MarketCache()
        for symbol in symbols:
            instrument_id = f"US:{symbol}"
            document = cache.load(DataProvider.ALPACA, instrument_id, Interval.DAY_1)
            if document is None:
                notes[symbol] = "no local cache for this symbol; run once without --no-refresh"
                continue
            # Only confirmed bars: an unclosed daily candle must never drive a conclusion.
            bars_by_instrument[instrument_id] = [b for b in document.bars if b.confirmed][-limit:]
    return bars_by_instrument, notes


def _load_registry_bars(instrument_ids: list[str], *, refresh: bool, limit: int) -> tuple[dict[str, list], dict[str, str]]:
    """Load daily bars for whatever the registry holds (US: equities and CRYPTO: OKX instruments)."""
    from app.models import DataProvider, Interval

    bars_by_instrument: dict[str, list] = {}
    notes: dict[str, str] = {}
    cache = None
    service = None
    if refresh:
        from app.service import MarketWorkbenchService
        service = MarketWorkbenchService()
    else:
        from app.market_cache import MarketCache
        cache = MarketCache()
    for instrument_id in instrument_ids:
        provider = DataProvider.OKX if instrument_id.startswith("CRYPTO:") else DataProvider.ALPACA
        try:
            if service is not None:
                page = service.get_bars(instrument_id, Interval.DAY_1, None, limit, None)
                bars = [b for b in page.bars if b.confirmed]
            else:
                document = cache.load(provider, instrument_id, Interval.DAY_1)
                # Only confirmed bars: verification must not read an unclosed candle.
                bars = [b for b in document.bars if b.confirmed][-limit:] if document else []
        except Exception as exc:
            notes[instrument_id] = f"{type(exc).__name__}: {str(exc)[:160]}"
            continue
        if not bars:
            notes[instrument_id] = "no cached daily bars; run once without --no-refresh"
            continue
        bars_by_instrument[instrument_id] = bars
    return bars_by_instrument, notes


def _divergence_flags(bars_by_instrument: dict, symbols: list[str]) -> tuple[dict[str, dict], str | None]:
    """Daily divergence audit for the ranked names (live = recent AND not retired by price).

    Runs directly on the already-loaded (confirmed) daily bars via the deterministic engine, so it
    works in `--no-refresh` mode too — no service round-trip and no fixture/live switch involved.
    """
    from app.divergence_audit import audit
    from app.indicators import compute_divergences

    flags: dict[str, dict] = {}
    notes: list[str] = []
    for symbol in symbols:
        bars = bars_by_instrument.get(f"US:{symbol}")
        if not bars:
            flags[symbol] = {"checked": False, "error": "no confirmed daily bars loaded for this symbol"}
            notes.append(f"{symbol}: no bars")
            continue
        try:
            divergences = compute_divergences(bars)
        except Exception as exc:
            flags[symbol] = {"checked": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
            notes.append(f"{symbol}: {type(exc).__name__}")
            continue
        summary = audit(divergences, bars, "1d")
        flags[symbol] = {
            "checked": True,
            "live_top": summary["live_top_sentence"],
            "live_top_count": len(summary["live_top"]),
            "retired_top": summary["retired_top"],
            "counts": summary["counts"],
            "most_recent_top": summary["most_recent_top"][:1],
            "relevance_bars": summary["relevance_bars"],
            "note": "只把「距今 ≤ relevance_bars 且第二锚未被价格突破」的顶背离算作仍有效；引擎只报已确认枢轴。",
        }
    return flags, ("; ".join(notes[:3]) if notes else None)


def _sector_metadata(symbols: list[str]) -> tuple[dict[str, dict], str | None]:
    """Best-effort sector/industry labels from the pinned reference catalog (metadata only)."""
    try:
        from app.catalog import catalog
        reference = catalog()
    except Exception as exc:  # no snapshot / unreadable provenance: keep going, and say why
        return {}, f"{type(exc).__name__}: {str(exc)[:140]}"
    labels: dict[str, dict] = {}
    for symbol in symbols:
        try:
            record = reference.lookup(symbol)
        except Exception:
            record = None
        if record is not None:
            labels[symbol] = {"sector": record.fields.get("sector") or None,
                              "industry_group": record.fields.get("industry_group") or None,
                              "mic": record.fields.get("mic") or None,
                              "catalog_commit": record.commit}
    return labels, None


def _rank_markdown(rows: list[dict]) -> list[str]:
    lines = ["| 排名 | 标的 | 板块 | 主路径 | A 权重 | B 权重 | C 权重 | 结构分 | 动量分 | 收盘 | 回踩区 | 结构支撑 | 日线顶背离 |",
             "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|"]
    for index, row in enumerate(rows, start=1):
        dip = "–" if not row["dip_zone"][0] else f'{row["dip_zone"][0]}–{row["dip_zone"][1]}'
        divergence = row.get("divergence_live_top") or ("未检查" if not row.get("divergence_checked") else "无（近期且未被否定）")
        lines.append(f'| {index} | {row["symbol"]} | {row.get("sector") or "–"} | {row["primary"]} | '
                     f'{row["a_weight"]}% | {row["b_weight"]}% | {row["c_weight"]}% | {row["structure_score"]:.0f} | '
                     f'{row["momentum_score"]:.0f} | {row["price"]} | {dip} | {row["structural_support"]} | '
                     f'{divergence} |')
    return lines


def main() -> int:
    args = _parse_args()
    refresh = not args.no_refresh
    if refresh or args.verify:
        os.environ.update({"AMW_ROOT": str(ROOT), "AMW_DATA_MODE": "live", "AMW_NETWORK_ENABLED": "true",
                           "TEMP": str(ROOT / "tmp"), "TMP": str(ROOT / "tmp")})
    else:
        os.environ.update({"AMW_ROOT": str(ROOT), "AMW_DATA_MODE": "fixture", "AMW_NETWORK_ENABLED": "false",
                           "TEMP": str(ROOT / "tmp"), "TMP": str(ROOT / "tmp")})

    from app import outlook_registry as registry

    if args.scoreboard:
        print(json.dumps(registry.scoreboard(registry.load_entries()), ensure_ascii=False, indent=2))
        return 0

    if args.verify:
        entries = registry.load_entries()
        if not entries:
            print("registry 为空：先跑一次 scripts/equity_outlook.py 生成结论。")
            return 0
        symbols = sorted({entry.instrument_id for entry in entries})
        bars_by_instrument, notes = _load_registry_bars(symbols, refresh=refresh, limit=args.bars)
        updated, skipped = registry.verify_entries(entries, bars_by_instrument)
        path = registry.save_entries(updated)
        board = registry.scoreboard(updated)
        print("校验完成：")
        for entry in updated:
            verification = entry.verification
            if verification is None:
                continue
            print(f"  {entry.symbol:6s} as_of={entry.as_of.date()} 主路径={entry.primary} "
                  f"→ 实际={verification.path_realized:10s} bars={verification.bars_observed:4d} "
                  f"主路径命中={verification.primary_correct} 区间收益={verification.return_pct}")
        for symbol, note in notes.items():
            print(f"  [跳过] {symbol}: {note}")
        for item in skipped:
            print(f"  [跳过] {item}")
        print(json.dumps(board, ensure_ascii=False, indent=2))
        print(f"\nregistry: {path}")
        return 0

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    bars_by_instrument, notes = _load_daily(symbols, refresh=refresh, limit=args.bars)

    from app.outlook import RULES_VERSION, build_outlook

    reports = []
    new_entries = []
    offsets = [args.as_of_days_ago]
    if args.replay_every > 0 and args.replay_count > 1:
        offsets = [args.as_of_days_ago + args.replay_every * step for step in range(args.replay_count)]
    for symbol in symbols:
        instrument_id = f"US:{symbol}"
        full_series = bars_by_instrument.get(instrument_id)
        if not full_series:
            reports.append({"symbol": symbol, "error": notes.get(symbol, "no bars")})
            continue
        for offset in offsets:
            if offset > 0 and len(full_series) <= offset + 60:
                continue  # not enough history for this freeze point; the others still run
            # Point-in-time slice: every input predates the freeze bar, so no look-ahead is possible.
            bars = full_series[:-offset] if offset > 0 else full_series
            data_note = {
                "instrument": instrument_id,
                "market": "EQUITY",
                "refreshed": refresh,
                "as_of_days_ago": offset,
                "bars": {"count": len(bars), "first": bars[0].open_at.isoformat(),
                         "last": bars[-1].close_at.isoformat()},
                "note": notes.get(symbol),
                "intraday_inputs": "omitted by design: equity ranking uses confirmed daily bars only",
            }
            try:
                report = build_outlook(bars, market="EQUITY", symbol=symbol, instrument_id=instrument_id,
                                       data_note=data_note)
            except Exception as exc:
                reports.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
                continue
            reports.append(report.model_dump(mode="json"))
            if not args.no_record:
                origin = "REPLAY" if offset > 0 else "FORWARD"
                new_entries.append(registry.entry_from_report(report, bars, origin=origin))

    latest_entries = [entry for entry in new_entries if entry.as_of == max(
        item.as_of for item in new_entries)] if new_entries else []
    ranking = registry.rank_closest_to_path_a(latest_entries) if latest_entries else []
    if not ranking:
        # Rank from whatever is already recorded so the command stays useful offline.
        ranking = registry.rank_closest_to_path_a(
            [e for e in registry.load_entries() if e.symbol in symbols]) if not args.no_record else []
    added = registry.append_entries(new_entries) if new_entries else []

    # Reference metadata (sector/industry) from the pinned catalog; never price evidence.
    sector_labels, catalog_note = _sector_metadata(symbols)
    for row in ranking:
        labels = sector_labels.get(row["symbol"], {})
        row["sector"] = labels.get("sector")
        row["industry_group"] = labels.get("industry_group")
        row["mic"] = labels.get("mic")
        row["catalog_commit"] = labels.get("catalog_commit")

    # Divergence audit for the ranked names (daily): a ranking that ignores a live top divergence
    # would be incomplete, so the flag travels with the row.
    divergence_flags, divergence_note = _divergence_flags(bars_by_instrument, [row["symbol"] for row in ranking])
    for row in ranking:
        flag = divergence_flags.get(row["symbol"], {})
        row["divergence_live_top"] = flag.get("live_top")
        row["divergence_retired_top"] = flag.get("retired_top")
        row["divergence_counts"] = flag.get("counts")
        row["divergence_note"] = flag.get("note")
        row["divergence_error"] = flag.get("error")
        row["divergence_checked"] = bool(flag.get("checked"))

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.outdir.mkdir(parents=True, exist_ok=True)
    json_path = args.outdir / f"equity-outlook-{stamp}.json"
    md_path = args.outdir / f"equity-outlook-{stamp}.md"
    payload = {"generated_at": datetime.now(UTC).isoformat(), "rules_version": RULES_VERSION,
               "market": "EQUITY", "refreshed": refresh, "symbols": symbols,
               "freeze_offsets": offsets,
               "history_notes": notes, "recorded_entries": [e.outlook_id for e in added],
               "recorded_by_origin": {"FORWARD": sum(1 for e in added if e.origin == "FORWARD"),
                                      "REPLAY": sum(1 for e in added if e.origin == "REPLAY")},
               "sector_metadata": sector_labels or None,
               "sector_metadata_note": catalog_note,
               "divergence_audit": divergence_flags or None,
               "divergence_audit_note": divergence_note,
               "closest_to_path_a": ranking[0]["symbol"] if ranking else None,
               "ranking": ranking, "reports": reports}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [f"# 美股 Mag 7 规则展望 — {payload['generated_at']}", "",
             f"规则版本 `{RULES_VERSION}`（股票口径：四年减半周期规则已禁用）· 数据刷新={'是' if refresh else '否（本地缓存）'}", "",
             "## 谁最接近 A 路径（先回踩再上）", ""]
    if ranking:
        lines += [f"**{ranking[0]['symbol']}**（A 权重 {ranking[0]['a_weight']}%，主路径 {ranking[0]['primary']}）", "",
                  *_rank_markdown(ranking), ""]
    else:
        lines += ["本次没有可排名的结论（见下方错误）。", ""]
    lines += ["## 数据与缺口", ""]
    if notes:
        lines += [f"- {symbol}: {note}" for symbol, note in notes.items()]
    else:
        lines += ["- 全部标的日内数据完整（history_complete 见 JSON）。"]
    lines += ["", "## 边界", "",
              "- 权重是规则排序，不是概率，也没有样本外校准；registry 计数不是胜率。",
              "- 股票口径只使用确认日线；缺少 4h/1h/15m 输入时相关动量规则按“跳过（中性）”处理。",
              "- 板块/行业来自 FinanceDatabase 的快照元数据（MIT，community-maintained），只作分类标签，"
              "不是价格证据、也不参与打分。"
              + (f"（本次未取到：{catalog_note}）" if catalog_note else ""),
              "- 不是投资建议，不保证盈利。", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    for row in ranking:
        print(f"{row['symbol']:6s} {(row.get('sector') or '—'):24s} primary={row['primary']} "
              f"A={row['a_weight']}% B={row['b_weight']}% C={row['c_weight']}% "
              f"structure={row['structure_score']:.0f} momentum={row['momentum_score']:.0f} "
              f"price={row['price']} dip={row['dip_zone']}")
        print(f"       顶背离(日线): {row.get('divergence_live_top') or ('未检查' if not row.get('divergence_checked') else '无（近期且未被否定）')}")
    for symbol, note in notes.items():
        print(f"  [note] {symbol}: {note}")
    print(f"记录新结论 {len(added)} 条；最接近 A 路径：{payload['closest_to_path_a']}")
    print(f"\njson: {json_path}\nmd:   {md_path}\nregistry: {registry.registry_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
