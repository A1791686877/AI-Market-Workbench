"""Step 4: Massive (Polygon-style) EOD adapter + CoinGecko crypto supply wiring."""
from __future__ import annotations

import time as time_module
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app import providers as providers_module
from app import service as service_module
from app.models import AssetClass, InstrumentRef, Interval
from app.providers import MassiveEodProvider, ProviderConfigurationError
from app.safe_http import JsonResponse


def _aapl() -> InstrumentRef:
    return InstrumentRef(instrument_id="US:AAPL", asset_class=AssetClass.US_EQUITY, symbol="AAPL",
                         venue="NASDAQ", quote_currency="USD", timezone="America/New_York",
                         contract_type="COMMON_STOCK")


@pytest.fixture(autouse=True)
def _no_real_massive_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rate limiter is real production behaviour; tests must not sit out 12.5 seconds per call."""
    monkeypatch.setattr(providers_module, "_massive_last_call", [])
    monkeypatch.setattr(time_module, "sleep", lambda seconds: None)


class _FakeJsonHttp:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls: list[dict] = []
        self.closed = False

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        return JsonResponse(payload=self.payload, sha256="2" * 64, status_code=200)

    def close(self) -> None:
        self.closed = True


def _massive_payload(adjusted: bool = True) -> dict:
    # t is the 09:30 America/New_York session open in epoch milliseconds.
    return {
        "status": "OK",
        "adjusted": adjusted,
        "resultsCount": 2,
        "results": [
            {"v": 55740731.0, "vw": 245.1, "o": 248.93, "c": 243.85, "h": 249.1, "l": 241.8201,
             "t": 1735828200000, "n": 685448},
            {"v": 101075128.0, "vw": 240.0, "o": 247.19, "c": 236.0, "h": 247.19, "l": 233.44,
             "t": 1738333800000, "n": 936710},
        ],
    }


def test_massive_adapter_parses_aggregates_into_daily_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers_module, "settings",
                        replace(providers_module.settings, massive_key="test-key"))
    provider = MassiveEodProvider()
    provider.http = _FakeJsonHttp(_massive_payload())

    bars, source = provider.fetch_daily(_aapl(), datetime(2025, 1, 1, tzinfo=UTC),
                                        datetime(2025, 2, 1, tzinfo=UTC))

    assert [bar.open_at.date().isoformat() for bar in bars] == ["2025-01-02", "2025-01-31"]
    first = bars[0]
    assert first.open_at == datetime(2025, 1, 2, 14, 30, tzinfo=UTC)     # 09:30 EST
    assert first.close_at == datetime(2025, 1, 2, 21, 0, tzinfo=UTC)      # 16:00 EST
    assert (first.open, first.high, first.low, first.close) == ("248.93", "249.1", "241.8201", "243.85")
    assert first.volume == "55740731"          # integral floats are normalised, no ".0"
    assert first.trade_count == 685448
    assert first.session.value == "US_RTH"
    assert first.price_basis.value == "SPLIT_ADJUSTED"
    assert first.confirmed is True
    assert source.provider.value == "MASSIVE"
    assert source.dataset == "us_equity_massive_daily_adjusted"
    assert source.is_full_market is False
    assert source.market_scope.value == "UNKNOWN"
    # Plan limits are declared, not discovered at runtime by the caller.
    assert any("NOT_AUTHORIZED" in item for item in source.limitations)
    assert any("5 requests/minute" in item for item in source.limitations)
    assert provider.http.calls[0]["params"] == {"adjusted": "true", "sort": "asc", "limit": 5000}
    assert provider.http.calls[0]["headers"]["Authorization"] == "Bearer test-key"


def test_massive_adapter_labels_raw_when_vendor_does_not_adjust(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers_module, "settings",
                        replace(providers_module.settings, massive_key="test-key"))
    provider = MassiveEodProvider()
    provider.http = _FakeJsonHttp(_massive_payload(adjusted=False))

    bars, source = provider.fetch_daily(_aapl(), datetime(2025, 1, 1, tzinfo=UTC),
                                        datetime(2025, 2, 1, tzinfo=UTC))

    assert bars[0].price_basis.value == "RAW"
    assert source.dataset == "us_equity_massive_daily_raw"


def test_massive_adapter_is_eod_only_and_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers_module, "settings",
                        replace(providers_module.settings, massive_key="test-key"))
    provider = MassiveEodProvider()
    provider.http = _FakeJsonHttp(_massive_payload())
    with pytest.raises(ValueError):
        provider.fetch_bars(_aapl(), Interval.MIN_15,
                            datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        provider.fetch_daily(
            InstrumentRef(instrument_id="crypto-btc-spot", asset_class=AssetClass.CRYPTO_SPOT, symbol="BTC",
                          venue="OKX", quote_currency="USDT", timezone="UTC", contract_type="SPOT"),
            datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC))

    monkeypatch.setattr(providers_module, "settings",
                        replace(providers_module.settings, massive_key=None))
    with pytest.raises(ProviderConfigurationError):
        MassiveEodProvider().fetch_daily(_aapl(), datetime(2025, 1, 1, tzinfo=UTC),
                                         datetime(2025, 2, 1, tzinfo=UTC))


def test_massive_adapter_throttles_to_the_free_plan_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 from the free plan cannot be retried, so the adapter sleeps instead."""
    monkeypatch.setattr(providers_module, "settings",
                        replace(providers_module.settings, massive_key="test-key"))
    monkeypatch.setattr(providers_module, "_massive_last_call", [])
    slept: list[float] = []
    monkeypatch.setattr(time_module, "sleep", lambda seconds: slept.append(seconds))

    provider = MassiveEodProvider()
    provider.http = _FakeJsonHttp(_massive_payload())
    provider.fetch_raw("AAPL", "2025-01-01", "2025-02-01")   # first call is not throttled
    provider.fetch_raw("AAPL", "2025-01-01", "2025-02-01")   # second one waits out the window

    assert slept and slept[0] > 12.0


class _FakeOkx:
    def __init__(self) -> None:
        stamp = 1_760_000_000_000
        self.http = SimpleNamespace(close=lambda: None)
        self._payloads = {
            "mark": {"data": [{"markPx": "77459.1", "ts": str(stamp)}]},
            "index": {"data": [{"idxPx": "77440.0", "ts": str(stamp)}]},
            "funding": {"data": [{"fundingRate": "0.0001", "ts": str(stamp)}]},
            "open_interest": {"data": [{"oiCcy": "12345", "ts": str(stamp)}]},
            "spot": {"data": [{"last": "77459.1", "ts": str(stamp)}]},
        }

    def fetch_context_raw(self, symbol: str) -> dict:
        return {name: JsonResponse(payload=payload, sha256=f"{index}" * 64, status_code=200)
                for index, (name, payload) in enumerate(self._payloads.items())}


class _FakeGecko:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.http = SimpleNamespace(close=lambda: None)
        self.calls: list[str] = []

    def fetch_raw(self, coin_id: str) -> JsonResponse:
        self.calls.append(coin_id)
        return JsonResponse(payload=self.payload, sha256="3" * 64, status_code=200)


def _crypto_service(monkeypatch: pytest.MonkeyPatch, tmp_path, gecko) -> service_module.MarketWorkbenchService:
    monkeypatch.setattr(service_module, "settings", replace(
        service_module.settings, data_mode="live", crypto_provider="okx", coingecko_demo_key="demo-key"))
    monkeypatch.setattr(service_module, "OkxPublicProvider", _FakeOkx)
    monkeypatch.setattr(service_module, "CoinGeckoDemoProvider", lambda: gecko)
    service = service_module.MarketWorkbenchService(service_module.Store(tmp_path / "step4.db"))
    monkeypatch.setattr(service, "get_bars",
                        lambda *args, **kwargs: SimpleNamespace(bars=[object()], sources=[]))
    return service


_GECKO_ROW = [{
    "id": "bitcoin", "symbol": "btc", "current_price": 77400.0,
    "market_cap": 1_555_402_039_739, "circulating_supply": 20_083_337.0,
    "total_supply": 20_083_378.0, "max_supply": 21_000_000.0,
}]


def test_crypto_supply_is_filled_from_coingecko_with_its_own_source(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    gecko = _FakeGecko(_GECKO_ROW)
    service = _crypto_service(monkeypatch, tmp_path, gecko)

    context = service.get_market_context("crypto-btc")
    supply = {item.name.value: item for item in context.supply}

    assert gecko.calls == ["bitcoin"]
    assert supply["CIRCULATING_SUPPLY"].value == "20083337"
    assert supply["TOTAL_SUPPLY"].value == "20083378"
    assert supply["MARKET_CAP"].value == "1555402039739"
    assert all(item.status.value == "AVAILABLE" for item in supply.values())
    assert all(item.source_ref == "src-coingecko-btc-supply" for item in supply.values())
    # The OKX price is never replaced by the aggregated CoinGecko price.
    assert context.spot_price.value == "77459.1"
    assert context.spot_price.source_ref == "src-okx-btc-context"
    gecko_source = next(item for item in context.sources if item.provider.value == "COINGECKO")
    assert gecko_source.market_scope.value == "NETWORK_WIDE"
    assert gecko_source.is_full_market is True
    assert any("CoinGecko network-wide" in item for item in context.limitations)


def test_crypto_supply_stays_missing_and_says_why_when_coingecko_fails(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class _BrokenGecko(_FakeGecko):
        def fetch_raw(self, coin_id: str) -> JsonResponse:
            raise RuntimeError("demo tier exhausted")

    service = _crypto_service(monkeypatch, tmp_path, _BrokenGecko(_GECKO_ROW))

    context = service.get_market_context("crypto-btc")

    assert all(item.status.value == "MISSING" for item in context.supply)
    assert all(item.missing_reason == "NO_CONFIGURED_FREE_SOURCE" for item in context.supply)
    assert not any(item.provider.value == "COINGECKO" for item in context.sources)
    assert any("CoinGecko supply request failed" in item for item in context.limitations)
    assert context.spot_price.status.value == "AVAILABLE"


def test_crypto_supply_is_missing_when_no_coingecko_key_is_configured(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "settings", replace(
        service_module.settings, data_mode="live", crypto_provider="okx", coingecko_demo_key=None))
    monkeypatch.setattr(service_module, "OkxPublicProvider", _FakeOkx)
    service = service_module.MarketWorkbenchService(service_module.Store(tmp_path / "nokey.db"))
    monkeypatch.setattr(service, "get_bars",
                        lambda *args, **kwargs: SimpleNamespace(bars=[object()], sources=[]))

    context = service.get_market_context("crypto-btc")

    assert all(item.status.value == "MISSING" for item in context.supply)
    assert not any(item.provider.value == "COINGECKO" for item in context.sources)
    assert not any("CoinGecko" in item for item in context.limitations)
