from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from app.config import settings
from app.logging_config import audit_event, configure_logging
from app.models import DataProvider, Interval, QualityFlag
from app.service import MarketWorkbenchService
from scripts.independent_indicators import compare


CASES = {
    "crypto-btc": ["CRYPTO:BTCUSDT:SPOT", "CRYPTO:BTCUSDT:PERP"],
    "crypto-eth": ["CRYPTO:ETHUSDT:SPOT", "CRYPTO:ETHUSDT:PERP"],
}
INTERVALS = [
    Interval.MIN_15,
    Interval.MIN_195,
    Interval.HOUR_1,
    Interval.HOUR_4,
    Interval.DAY_1,
    Interval.WEEK_1,
    Interval.MONTH_1,
]


def main() -> None:
    if settings.data_mode != "live" or not settings.network_enabled:
        raise RuntimeError("run with AMW_DATA_MODE=live and AMW_NETWORK_ENABLED=true")
    configure_logging()
    service = MarketWorkbenchService()
    started = datetime.now(UTC)
    rows: list[dict] = []
    failures: list[dict] = []
    for subject_id, instrument_ids in CASES.items():
        for instrument_id in instrument_ids:
            for interval in INTERVALS:
                clock = time.perf_counter()
                try:
                    page = service.get_bars(instrument_id, interval, None, 5000, None)
                    bundle = service.get_indicator_bundle(
                        subject_id, interval, cutoff_at=page.bars[-1].close_at, instrument_id=instrument_id
                    )
                    comparisons = compare(page.bars, bundle.indicators)
                    if not all(c['passed'] for c in comparisons):
                        raise AssertionError(json.dumps(comparisons))
                    source = page.sources[0]
                    if source.provider is DataProvider.FIXTURE_SYNTHETIC or any(
                        QualityFlag.SYNTHETIC_FIXTURE in bar.quality_flags for bar in page.bars
                    ):
                        raise AssertionError("synthetic fallback detected in live acceptance")
                    row = {
                        "subject_id": subject_id,
                        "instrument_id": instrument_id,
                        "interval": interval.value,
                        "status": page.quality.request_status.value,
                        "provider": source.provider.value,
                        "dataset": source.dataset,
                        "source_at": source.source_at.isoformat(),
                        "received_at": source.received_at.isoformat(),
                        "delay_class": source.delay_class.value,
                        "market_scope": source.market_scope.value,
                        "is_full_market": source.is_full_market,
                        "bar_count": len(page.bars),
                        "requested_start_at": (
                            page.quality.requested_start_at.isoformat()
                            if page.quality.requested_start_at
                            else None
                        ),
                        "actual_start_at": (
                            page.quality.actual_start_at.isoformat()
                            if page.quality.actual_start_at
                            else None
                        ),
                        "latest_bar_at": (
                            page.quality.latest_bar_at.isoformat()
                            if page.quality.latest_bar_at
                            else None
                        ),
                        "history_complete": page.quality.history_complete,
                        "missing_bar_count": page.quality.missing_bar_count,
                        "reported_gap_count": len(page.quality.gaps),
                        "indicator_names": [item.name.value for item in bundle.indicators],
                        "indicator_count": len(bundle.indicators),
                        "independent_reference": "decimal40-reference-v1 (no production indicator imports)",
                        "pointwise_comparisons": comparisons,
                        "fvg_count": len(bundle.fvg),
                        "divergence_count": len(bundle.divergences),
                        "vpvr": {
                            "calculation_mode": bundle.vpvr.calculation_mode.value,
                            "input_granularity": bundle.vpvr.input_granularity.value,
                            "algorithm_version": bundle.vpvr.algorithm_version,
                            "is_estimate": bundle.vpvr.is_estimate,
                            "poc": str(bundle.vpvr.poc),
                            "vah": str(bundle.vpvr.vah),
                            "val": str(bundle.vpvr.val),
                            "limitations": bundle.vpvr.limitations,
                        },
                        "elapsed_ms": round((time.perf_counter() - clock) * 1000, 2),
                    }
                    rows.append(row)
                    (settings.root/'reports/quality-matrix-progress.json').write_text(json.dumps({'results':rows,'failures':failures},ensure_ascii=False,indent=2),encoding='utf-8')
                    print(
                        f"PASS {instrument_id} {interval.value} "
                        f"bars={len(page.bars)} complete={page.quality.history_complete}"
                    )
                except Exception as exc:
                    failure = {
                        "subject_id": subject_id,
                        "instrument_id": instrument_id,
                        "interval": interval.value,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "elapsed_ms": round((time.perf_counter() - clock) * 1000, 2),
                    }
                    failures.append(failure)
                    print(f"FAIL {instrument_id} {interval.value} {type(exc).__name__}: {exc}")
    finished = datetime.now(UTC)
    report = {
        "phase": "phase-1-live-crypto-acceptance",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "data_mode": settings.data_mode,
        "network_enabled": settings.network_enabled,
        "configured_provider": settings.crypto_provider,
        "monthly_data_subscription_cost_usd": 0,
        "synthetic_fallback_allowed": False,
        "passed": len(rows),
        "failed": len(failures),
        "results": rows,
        "failures": failures,
    }
    target = settings.root / "reports" / "quality-seven-interval-acceptance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    audit_event(
        "live_crypto_acceptance_completed",
        passed=len(rows),
        failed=len(failures),
        report_path=str(target),
    )
    print(f"REPORT {target}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
