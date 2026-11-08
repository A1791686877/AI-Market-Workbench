from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import app.service as service_module
from app.fixtures import BARS, SOURCES
from app.market_cache import MarketCache
from app.models import (
    BarRequestStatus,
    Completeness,
    DataProvider,
    DelayClass,
    Interval,
    MarketScope,
    QualityFlag,
)
from app.service import DataUnavailableError, MarketWorkbenchService
from app.storage import Store


def test_live_failure_uses_only_stale_real_cache_and_never_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_settings = replace(
        service_module.settings,
        data_mode="live",
        network_enabled=True,
        crypto_provider="okx",
        market_cache_ttl_seconds=0,
    )
    monkeypatch.setattr(service_module, "settings", live_settings)

    def fail(*_args, **_kwargs):
        raise RuntimeError("forced upstream failure")

    monkeypatch.setattr(service_module.OkxPublicProvider, "fetch_bars", fail)
    cache = MarketCache(tmp_path / "cache")
    fixture_source = next(iter(SOURCES.values()))
    real_source = fixture_source.model_copy(
        update={
            "source_id": "src-okx-test-real-cache",
            "provider": DataProvider.OKX,
            "dataset": "okx_test_real_cache",
            "endpoint_id": "GET /api/v5/market/history-candles",
            "delay_class": DelayClass.REALTIME_PUBLIC,
            "market_scope": MarketScope.VENUE_SPECIFIC_CRYPTO,
            "completeness": Completeness.COMPLETE_FOR_SCOPE,
            "is_full_market": False,
        }
    )
    real_bars = [
        bar.model_copy(
            update={
                "source_ref": real_source.source_id,
                "quality_flags": [],
            }
        )
        for bar in BARS["CRYPTO:BTCUSDT:SPOT"]
    ]
    cache.save(
        DataProvider.OKX,
        "CRYPTO:BTCUSDT:SPOT",
        Interval.MIN_15,
        real_bars,
        real_source,
    )
    service = MarketWorkbenchService(Store(tmp_path / "with-cache.db"), cache)
    page = service.get_bars("CRYPTO:BTCUSDT:SPOT", Interval.MIN_15, None, 5000, None)
    assert page.quality.request_status is BarRequestStatus.STALE_CACHE
    assert page.sources[0].completeness is Completeness.STALE
    assert all(QualityFlag.STALE_SOURCE in bar.quality_flags for bar in page.bars)
    assert all(QualityFlag.SYNTHETIC_FIXTURE not in bar.quality_flags for bar in page.bars)

    empty_service = MarketWorkbenchService(
        Store(tmp_path / "without-cache.db"), MarketCache(tmp_path / "empty-cache")
    )
    with pytest.raises(DataUnavailableError):
        empty_service.get_bars("CRYPTO:ETHUSDT:SPOT", Interval.MIN_15, None, 5000, None)
