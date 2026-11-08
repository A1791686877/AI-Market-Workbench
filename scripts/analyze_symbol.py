"""One-command deterministic analysis for any catalogued equity (no model call, no cost).

    python -B scripts/analyze_symbol.py --symbol MCD

Prints and writes: bars/coverage, indicators, fundamentals (incl. TTM EPS / P/E and the market-cap
scale decision), rule-based outlook (levels + triggers + path weights), live top-divergence audit,
sector label, and a two-anchor valuation (P/E on TTM vs last fiscal year).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _f(value) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--bars", type=int, default=1200)
    parser.add_argument("--outdir", type=Path, default=ROOT / "reports")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from app.divergence_audit import audit
    from app.indicators import compute_divergences
    from app.models import Interval
    from app.outlook import RULES_VERSION, build_outlook
    from app.service import MarketWorkbenchService

    symbol = args.symbol.strip().upper()
    subject_id = f"us-{symbol.lower()}"
    service = MarketWorkbenchService()
    result: dict = {"symbol": symbol, "subject_id": subject_id, "generated_at": datetime.now(UTC).isoformat(),
                    "rules_version": RULES_VERSION}

    # 1) bars
    daily_page = service.get_bars(f"US:{symbol}", Interval.DAY_1, None, args.bars, None)
    intraday_page = service.get_bars(f"US:{symbol}", Interval.MIN_15, None, 400, None)
    daily, intraday = daily_page.bars, intraday_page.bars
    result["bars"] = {
        "1d": {"count": len(daily), "first": daily[0].open_at.isoformat(), "last": daily[-1].close_at.isoformat(),
               "last_close": daily[-1].close, "basis": sorted({b.price_basis.value for b in daily}),
               "history_complete": daily_page.quality.history_complete,
               "actual_start": daily_page.quality.actual_start_at.isoformat() if daily_page.quality.actual_start_at else None},
        "15m": {"count": len(intraday), "first": intraday[0].open_at.isoformat(),
                "last": intraday[-1].close_at.isoformat(), "last_close": intraday[-1].close,
                "basis": sorted({b.price_basis.value for b in intraday}),
                "history_complete": intraday_page.quality.history_complete},
    }
    result["sources"] = [{"source_id": s.source_id, "provider": s.provider.value, "dataset": s.dataset,
                          "delay": s.delay_class.value, "is_full_market": s.is_full_market,
                          "basis": s.price_basis.value if s.price_basis else None} for s in daily_page.sources]

    # 2) indicators
    bundles = {}
    for name, interval in (("1d", Interval.DAY_1), ("15m", Interval.MIN_15)):
        bundle = service.get_indicator_bundle(subject_id, interval)
        bundles[name] = bundle
        result.setdefault("indicators", {})[name] = {
            "trend": bundle.trend_structure.state.value,
            "indicators": {i.name.value: {"status": i.status.value, "primary": i.current.primary,
                                          "signal": i.current.signal, "histogram": i.current.histogram,
                                          "plus_di": i.current.plus_di, "minus_di": i.current.minus_di}
                           for i in bundle.indicators},
            "support_resistance": [{"kind": l.kind.value, "price": l.price, "role": l.role_status,
                                    "touches": l.touch_count} for l in bundle.support_resistance.levels],
            "vpvr": {"poc": bundle.vpvr.poc, "vah": bundle.vpvr.vah, "val": bundle.vpvr.val,
                     "is_estimate": bundle.vpvr.is_estimate},
        }

    # 3) divergences (daily)
    divergence = audit(compute_divergences(daily), daily, "1d")
    result["divergence_1d"] = {
        "counts": divergence["counts"], "retired_top": divergence["retired_top"],
        "live_top_sentence": divergence["live_top_sentence"],
        "live_top": divergence["live_top"], "most_recent_top": divergence["most_recent_top"],
        "most_recent_bottom": divergence["most_recent_bottom"], "caveats": divergence["caveats"],
    }

    # 4) fundamentals
    try:
        fundamentals = service.get_fundamentals(subject_id)
        facts = {item.name.value: item for item in fundamentals.facts}
        result["fundamentals"] = {
            "accepted_before": fundamentals.accepted_before.isoformat(),
            "filings": [{"form": f.form, "filed_at": f.filed_at.isoformat(),
                         "report_period": f.report_period.isoformat()} for f in fundamentals.filings],
            "facts": {name: {"value": item.value, "unit": item.unit, "period_end":
                             item.period_end.isoformat() if item.period_end else None,
                             "status": item.status.value, "derivation": item.derivation}
                      for name, item in facts.items()},
            "missing_items": fundamentals.missing_items,
        }
    except Exception as exc:
        result["fundamentals"] = {"error": f"{type(exc).__name__}: {exc}"}

    # 5) rule-based outlook (equity mode: four-year cycle rules disabled)
    data_note = {"instrument": f"US:{symbol}", "market": "EQUITY", "bars": len(daily),
                 "last": daily[-1].close_at.isoformat(), "intraday_inputs": "omitted by design"}
    report = build_outlook(daily, market="EQUITY", symbol=symbol, instrument_id=f"US:{symbol}",
                           data_note=data_note)
    result["outlook"] = {
        "primary": report.primary,
        "paths": [p.model_dump(mode="json") for p in report.paths],
        "structure_score": report.structure_score, "momentum_score": report.momentum_score,
        "levels": report.levels.model_dump(), "triggers": report.triggers,
        "narrative": report.narrative, "limitations": report.limitations,
        "cycle_rule": [h.model_dump(mode="json") for h in report.structure_hits
                       if h.rule == "cycle_bottom_window"],
    }

    # 6) sector label (reference catalog)
    try:
        from app.catalog import catalog
        record = catalog().lookup(symbol)
        result["reference"] = None if record is None else {
            "name": record.fields["name"], "sector": record.fields["sector"],
            "industry_group": record.fields["industry_group"], "industry": record.fields["industry"],
            "mic": record.fields["mic"], "market_cap_bucket": record.fields["market_cap"],
            "commit": record.commit,
        }
    except Exception as exc:
        result["reference"] = {"error": f"{type(exc).__name__}: {exc}"}

    # 7) two-anchor valuation
    close = _f(daily[-1].close)
    facts = (result.get("fundamentals") or {}).get("facts") or {}
    ttm_eps = _f((facts.get("TTM_EPS") or {}).get("value"))
    pe_ttm = _f((facts.get("PE_TTM") or {}).get("value"))
    result["valuation"] = {"last_close": close, "ttm_eps": ttm_eps, "pe_ttm": pe_ttm,
                          "market_cap_usd": (facts.get("MARKET_CAP") or {}).get("value"),
                          "free_cash_flow_usd": (facts.get("FREE_CASH_FLOW") or {}).get("value"),
                          "revenue_growth": (facts.get("REVENUE_GROWTH") or {}).get("value"),
                          "net_income_growth": (facts.get("NET_INCOME_GROWTH") or {}).get("value")}

    target = args.outdir / f"{symbol.lower()}-analysis-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---- console summary (decisive, short) ----
    ref = result.get("reference") or {}
    print(f"=== {symbol} ({ref.get('name', '?')}) {ref.get('sector', '?')} / {ref.get('industry_group', '?')} "
          f"@{ref.get('mic', '?')}")
    print(f"bars: 1d {len(daily)} 根 -> {daily[-1].close_at.date()} 收盘 {daily[-1].close} "
          f"({'完整' if daily_page.quality.history_complete else 'history_complete=false'}) | "
          f"15m {len(intraday)} 根 -> {intraday[-1].close}")
    print(f"结构: 日线 {result['indicators']['1d']['trend']} / 15m {result['indicators']['15m']['trend']} | "
          f"日线 VPVR POC {result['indicators']['1d']['vpvr']['poc']} "
          f"VAH {result['indicators']['1d']['vpvr']['vah']} VAL {result['indicators']['1d']['vpvr']['val']}")
    print(f"主判断: {report.primary} " + " ".join(f"{p.key}={p.weight_pct}%" for p in report.paths) +
          f" | 结构分 {report.structure_score:.0f} 动量分 {report.momentum_score:.0f}")
    print("一句话: " + report.narrative.splitlines()[1])
    print(f"关键位: 确认 {report.levels.confirm_reclaim} / 突破 {report.levels.confirm_breakout} / "
          f"回踩区 {report.levels.dip_zone_low}–{report.levels.dip_zone_high} / 作废(SMA200) {report.levels.structural_support}")
    print(f"顶背离(日线): {divergence['live_top_sentence'] or '无（近期 ≤20 根且未被否定的）'}")
    if result.get("fundamentals", {}).get("facts"):
        f = result["fundamentals"]["facts"]
        print(f"基本面: 营收 {f['REVENUE']['value']} ({(_f(f['REVENUE_GROWTH']['value']) or 0)*100:.1f}% yoy) | "
              f"净利 {f['NET_INCOME']['value']} | FCF {f['FREE_CASH_FLOW']['value']} | "
              f"TTM EPS {f['TTM_EPS']['value']} | P/E(TTM) {f['PE_TTM']['value']} | "
              f"市值 {f['MARKET_CAP']['value']}")
    print(f"\nreport: {target}")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
