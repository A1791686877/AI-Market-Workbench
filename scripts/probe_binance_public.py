from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.fixtures import SUBJECTS
from app.logging_config import audit_event
from app.providers import BinancePublicProvider


def main() -> None:
    if settings.data_mode != "live" or not settings.network_enabled:
        raise RuntimeError("probe requires AMW_DATA_MODE=live and AMW_NETWORK_ENABLED=true")
    provider = BinancePublicProvider()
    ended_at = datetime.now(UTC)
    started_at = ended_at - timedelta(hours=6)
    results: list[dict[str, object]] = []
    for subject_id in ("crypto-btc", "crypto-eth"):
        subject = SUBJECTS[subject_id]
        for instrument in subject.instruments:
            try:
                bars, source = provider.fetch_15m(instrument, started_at, ended_at)
                results.append(
                    {
                        "subject_id": subject_id,
                        "instrument_id": instrument.instrument_id,
                        "status": "SUCCESS",
                        "bar_count": len(bars),
                        "first_open_at": bars[0].open_at.isoformat() if bars else None,
                        "last_close_at": bars[-1].close_at.isoformat() if bars else None,
                        "source": source.model_dump(mode="json"),
                    }
                )
            except Exception as exc:
                audit_event(
                    "provider_probe_failed",
                    provider="BINANCE",
                    instrument_id=instrument.instrument_id,
                    error_type=type(exc).__name__,
                )
                results.append(
                    {
                        "subject_id": subject_id,
                        "instrument_id": instrument.instrument_id,
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "detail": str(exc)[:500],
                    }
                )
        try:
            premium, oi = provider.fetch_derivatives_raw(f"{subject.symbol}USDT")
            results.append(
                {
                    "subject_id": subject_id,
                    "instrument_id": f"BINANCE:{subject.symbol}USDT:DERIVATIVES_CONTEXT",
                    "status": "SUCCESS",
                    "premium_status": premium.status_code,
                    "premium_sha256": premium.sha256,
                    "open_interest_status": oi.status_code,
                    "open_interest_sha256": oi.sha256,
                }
            )
        except Exception as exc:
            audit_event(
                "provider_probe_failed",
                provider="BINANCE",
                instrument_id=f"{subject.symbol}USDT:DERIVATIVES_CONTEXT",
                error_type=type(exc).__name__,
            )
            results.append(
                {
                    "subject_id": subject_id,
                    "instrument_id": f"BINANCE:{subject.symbol}USDT:DERIVATIVES_CONTEXT",
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "detail": str(exc)[:500],
                }
            )
    report = {
        "probed_at": ended_at.isoformat(),
        "network_enabled": settings.network_enabled,
        "account_or_api_key_used": False,
        "results": results,
    }
    path = settings.root / "reports" / "binance-public-probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if any(item["status"] != "SUCCESS" for item in results):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
