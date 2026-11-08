"""Full-chain acceptance for one catalogued equity on live public data.

Chain: live bars (daily + intraday) -> indicator bundle -> SEC fundamentals ->
frozen snapshot -> one real CODEX_CLI analysis -> validation -> JSON report.

Run with --skip-model first: that exercises everything except the model call and
never consumes the local 1-per-24h model budget.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.models import AiAnalysisTaskRequest, Interval, SnapshotRequest
from app.service import service

SUBJECT_ID = ""  # set from --symbol


def _bar_summary(page) -> dict:
    bars = page.bars
    return {
        "instrument_id": page.instrument.instrument_id,
        "interval": page.interval.value,
        "bar_count": len(bars),
        "first": bars[0].open_at.isoformat(),
        "last": bars[-1].close_at.isoformat(),
        "last_close": bars[-1].close,
        "price_basis": sorted({bar.price_basis.value for bar in bars}),
        "all_confirmed": all(bar.confirmed for bar in bars),
        "quality": page.quality.model_dump(mode="json"),
        "bar_quality_flags": sorted({flag.value for bar in bars for flag in bar.quality_flags}),
        "sources": [{"source_id": s.source_id, "provider": s.provider.value, "dataset": s.dataset,
                     "delay": s.delay_class.value, "basis": s.price_basis.value if s.price_basis else None,
                     "is_full_market": s.is_full_market, "limitations": s.limitations}
                    for s in page.sources],
    }


def _bundle_summary(bundle) -> dict:
    return {
        "interval": bundle.interval.value,
        "instrument_id": bundle.instrument.instrument_id,
        "bars_as_of": bundle.bars_as_of.isoformat(),
        "indicator_count": len(bundle.indicators),
        "indicators": [{"name": i.name.value, "status": i.status.value,
                        "current": i.current.model_dump(mode="json"),
                        "algorithm_version": i.algorithm_version, "warmup_bars": i.warmup_bars,
                        "input_price_basis": i.input_price_basis.value,
                        "missing_reason": i.missing_reason} for i in bundle.indicators],
        "fvg_count": len(bundle.fvg),
        "divergence_count": len(bundle.divergences),
        "support_resistance": bundle.support_resistance.model_dump(mode="json"),
        "trend_structure": bundle.trend_structure.model_dump(mode="json"),
        "vpvr": {key: getattr(bundle.vpvr, key) for key in ("poc", "vah", "val", "is_estimate",
                                                            "window_start", "window_end")},
        "missing_items": bundle.missing_items,
    }


def _fundamentals_summary(fundamentals) -> dict:
    return {
        "subject_id": fundamentals.subject.subject_id,
        "accepted_before": fundamentals.accepted_before.isoformat(),
        "fact_count": len(fundamentals.facts),
        "filings": [{"form": f.form, "filed_at": f.filed_at.isoformat(),
                     "report_period": f.report_period.isoformat()} for f in fundamentals.filings],
        "facts": [{"name": fact.name.value, "value": fact.value, "unit": fact.unit,
                   "status": fact.status.value, "period_end": fact.period_end.isoformat() if fact.period_end else None,
                   "basis": fact.accounting_basis.value, "derivation": fact.derivation,
                   "missing_reason": fact.missing_reason} for fact in fundamentals.facts],
        "missing_items": fundamentals.missing_items,
        "sources": [s.source_id for s in fundamentals.sources],
    }


def main() -> None:
    try:  # keep Chinese/mathematical output readable on a GBK Windows console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="catalogued ticker, e.g. MCD")
    parser.add_argument("--skip-model", action="store_true",
                        help="run everything except the CODEX_CLI call (no model budget spent)")
    args = parser.parse_args()
    global SUBJECT_ID
    symbol = args.symbol.strip().upper()
    SUBJECT_ID = f"us-{symbol.lower()}"

    report: dict = {
        "checked_at": datetime.now(UTC).isoformat(),
        "subject_id": SUBJECT_ID,
        "symbol": symbol,
        "data_mode": settings.data_mode,
        "network_enabled": settings.network_enabled,
        "routing": {"equity_provider": settings.equity_provider,
                    "equity_eod_provider": settings.equity_eod_provider,
                    "alpaca_feed": settings.alpaca_feed},
        "model_call": "SKIPPED" if args.skip_model else "ATTEMPTED",
    }

    # 1. Live bars on two timeframes.
    daily = service.get_bars(f"US:{symbol}", Interval.DAY_1, None, 400, None)
    intraday = service.get_bars(f"US:{symbol}", Interval.MIN_15, None, 400, None)
    report["bars"] = {"1d": _bar_summary(daily), "15m": _bar_summary(intraday)}

    # 2. Deterministic indicator bundles.
    report["indicators"] = {
        "1d": _bundle_summary(service.get_indicator_bundle(SUBJECT_ID, Interval.DAY_1)),
        "15m": _bundle_summary(service.get_indicator_bundle(SUBJECT_ID, Interval.MIN_15)),
    }

    # 3. SEC fundamentals (point-in-time).
    try:
        report["fundamentals"] = _fundamentals_summary(service.get_fundamentals(SUBJECT_ID))
    except Exception as exc:  # the report must record a failure, not hide it
        report["fundamentals"] = {"error": f"{type(exc).__name__}: {exc}"}

    # 4. Frozen snapshot.
    snapshot = service.create_snapshot(SnapshotRequest(subject_id=SUBJECT_ID,
                                                       intervals=[Interval.DAY_1, Interval.MIN_15]))
    report["snapshot"] = {
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "cutoff_at": snapshot.cutoff_at.isoformat(),
        "evidence_count": len(snapshot.evidence),
        "timeframe_coverage": [c.model_dump(mode="json") for c in snapshot.timeframe_coverage],
        "missing_items": snapshot.missing_items,
        "scenario_rules": [r.model_dump(mode="json") for r in snapshot.scenario_rules],
        "fundamentals_included": snapshot.fundamentals is not None,
        "sources": [s.source_id for s in snapshot.sources],
    }

    if not args.skip_model:
        # 5. One real model call (consumes the local 1-per-24h budget).
        request = AiAnalysisTaskRequest(snapshot_id=snapshot.snapshot_id, provider="CODEX_CLI",
                                       model=settings.codex_default_model, prompt_version="scenario-v2")
        task, _ = service.create_task(request, f"{symbol.lower()}-acceptance-{uuid.uuid4().hex}")
        service.run_task(task.task_id, request)
        task = service.get_task(task.task_id)
        report["task"] = task.model_dump(mode="json")
        if task.analysis_id:
            analysis = service.get_analysis(task.analysis_id)
            service._validate_analysis(analysis, snapshot)
            report["analysis"] = {
                "analysis_id": analysis.analysis_id,
                "audit": analysis.audit.model_dump(mode="json"),
                "scenarios": [{"type": s.type.value, "summary": s.summary,
                               "evidence_ids": s.evidence_ids, "key_level_ids": s.key_level_ids,
                               "evaluation_rule_id": s.evaluation_rule_id,
                               "preconditions": [c.model_dump(mode="json") for c in s.preconditions],
                               "invalidation_conditions": [c.model_dump(mode="json") for c in s.invalidation_conditions]}
                              for s in analysis.scenarios],
                "timeframe_synthesis": [t.model_dump(mode="json") for t in analysis.timeframe_synthesis],
                "key_levels": [k.model_dump(mode="json") for k in analysis.key_levels],
                "conflicts": [c.model_dump(mode="json") for c in analysis.conflicts],
                "missing_information": [m.model_dump(mode="json") for m in analysis.missing_information],
                "evidence_reference_validation": "PASS: IDs, level IDs and exact cited prices",
                "semantic_claim_validation": "MANUAL_REVIEW_REQUIRED: IDs alone do not prove prose claims",
            }
        else:
            report["analysis"] = {"error": task.error}

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = settings.root / "reports" / f"{symbol.lower()}-acceptance-{stamp}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"\nreport: {target}")


if __name__ == "__main__":
    main()
