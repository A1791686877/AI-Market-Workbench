"""US equity pipeline: catalog, provider routing, fail-closed fixture mode, pagination direction."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.instruments import MAG7_SYMBOLS
from app.models import AssetClass, InstrumentRef, Interval
from app.providers import (
    AlpacaMarketDataProvider,
    AlphaVantageProvider,
    MassiveEodProvider,
    TiingoEodProvider,
    TwelveDataProvider,
)
from app.safe_http import JsonResponse
from app.service import (
    DataUnavailableError,
    MarketWorkbenchService,
    _equity_provider,
)
from app.storage import Store


def test_mag7_catalog_is_available() -> None:
    from app.fixtures import SUBJECTS

    for symbol in MAG7_SYMBOLS:
        subject = SUBJECTS[f"us-{symbol.lower()}"]
        instrument = subject.instruments[0]
        assert subject.subject_type.value == "EQUITY"
        assert instrument.instrument_id == f"US:{symbol}"
        assert instrument.asset_class is AssetClass.US_EQUITY


def test_equity_provider_selection_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    alpaca, alpaca_name = _equity_provider("alpaca")
    assert isinstance(alpaca, AlpacaMarketDataProvider)
    assert alpaca.feed == settings.alpaca_feed
    assert alpaca_name.value == "ALPACA"
    assert isinstance(_equity_provider("twelve_data")[0], TwelveDataProvider)
    assert isinstance(_equity_provider("alpha_vantage")[0], AlphaVantageProvider)
    assert isinstance(_equity_provider("tiingo")[0], TiingoEodProvider)
    with pytest.raises(ValueError):
        _equity_provider("yahoo")


def test_fixture_mode_fails_closed_for_real_equity(tmp_path) -> None:
    service = MarketWorkbenchService(Store(tmp_path / "fixture.db"))
    with pytest.raises(DataUnavailableError):
        service.get_bars("US:NVDA", Interval.DAY_1, None, 100, None)


def test_search_includes_mag7(tmp_path) -> None:
    service = MarketWorkbenchService(Store(tmp_path / "search.db"))
    page = service.search_instruments("nvda", 10, None)
    assert [item.instrument_id for item in page.items] == ["US:NVDA"]


class _FakeHttp:
    """Returns Alpaca-shaped pages newest-first and records the request params."""

    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.requests: list[dict] = []
        self.closed = False

    def get_json(self, url, *, params=None, headers=None):
        self.requests.append(dict(params or {}))
        index = len(self.requests) - 1
        page = self.pages[index] if index < len(self.pages) else {"bars": {"AAPL": []}, "next_page_token": None}
        return JsonResponse(payload=page, sha256="0" * 64, status_code=200)

    def close(self) -> None:
        self.closed = True


def _row(stamp: str) -> dict:
    return {"t": stamp, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 1000, "n": 10}


def test_alpaca_paginator_fetches_newest_first_even_when_budget_runs_out() -> None:
    """Regression guard: an ascending fetch spent the whole budget on the oldest bars."""
    now = datetime.now(UTC)
    newest = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    older = (now - timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
    oldest = (now - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = _FakeHttp([
        {"bars": {"AAPL": [_row(newest), _row(older)]}, "next_page_token": "t1"},
        {"bars": {"AAPL": [_row(oldest)]}, "next_page_token": None},
    ])
    provider = AlpacaMarketDataProvider("iex")
    provider.http = fake
    instrument = InstrumentRef(instrument_id="US:AAPL", asset_class=AssetClass.US_EQUITY, symbol="AAPL",
                              venue="NASDAQ", quote_currency="USD", timezone="America/New_York",
                              contract_type="COMMON_STOCK")
    records, _, reached_start = provider._fetch_pages(
        instrument, now - timedelta(days=2), now, "split", "15Min", max_pages=2
    )
    assert all(request["sort"] == "desc" for request in fake.requests)
    assert newest in records, "the newest bar must be present even with a small page budget"
    assert reached_start is False  # budget stopped before the requested start


def test_alpaca_paginator_reports_reached_start_when_window_is_covered() -> None:
    now = datetime.now(UTC)
    start = now - timedelta(minutes=40)
    newest = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    oldest = (now - timedelta(minutes=55)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = _FakeHttp([{"bars": {"AAPL": [_row(newest), _row(oldest)]}, "next_page_token": "t1"}])
    provider = AlpacaMarketDataProvider("iex")
    provider.http = fake
    instrument = InstrumentRef(instrument_id="US:AAPL", asset_class=AssetClass.US_EQUITY, symbol="AAPL",
                              venue="NASDAQ", quote_currency="USD", timezone="America/New_York",
                              contract_type="COMMON_STOCK")
    _, _, reached_start = provider._fetch_pages(instrument, start, now, "split", "15Min", max_pages=2)
    assert reached_start is True
    assert len(fake.requests) == 1


class _FakeJsonHttp:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def get_json(self, url, *, params=None, headers=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        return JsonResponse(payload=self.payload, sha256="1" * 64, status_code=200)

    def close(self) -> None:
        pass


def test_tiingo_adapter_maps_adjusted_and_raw_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Tiingo token yet, so the mapping is verified against a realistic payload."""
    from dataclasses import replace

    from app import providers as providers_module
    from app.providers import ProviderConfigurationError

    row = {"date": "2026-09-10T00:00:00.000Z", "open": 100.0, "high": 101.5, "low": 99.5, "close": 100.8,
           "volume": 123456, "adjOpen": 50.0, "adjHigh": 50.75, "adjLow": 49.75, "adjClose": 50.4,
           "adjVolume": 246912, "splitFactor": 2.0, "divCash": 0.0}

    monkeypatch.setattr(providers_module, "settings", replace(providers_module.settings, tiingo_token=None))
    with pytest.raises(ProviderConfigurationError):
        TiingoEodProvider().fetch_daily(_aapl(), datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 12, tzinfo=UTC))

    monkeypatch.setattr(providers_module, "settings",
                        replace(providers_module.settings, tiingo_token="test-token"))
    provider = TiingoEodProvider()
    provider.http = _FakeJsonHttp([row])
    bars, source = provider.fetch_daily(_aapl(), datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 12, tzinfo=UTC))

    assert len(bars) == 1
    bar = bars[0]
    assert bar.interval is Interval.DAY_1
    assert bar.price_basis.value == "TOTAL_RETURN_ADJUSTED"
    assert bar.open == "50.0" and bar.close == "50.4"          # adjusted values are primary
    assert bar.raw_ohlcv is not None and bar.raw_ohlcv.open == "100.0"
    assert "CORPORATE_ACTION_WINDOW" in [flag.value for flag in bar.quality_flags]
    assert source.provider.value == "TIINGO"
    assert source.delay_class.value == "END_OF_DAY"
    assert source.market_scope.value == "UNKNOWN"
    assert provider.http.calls[0]["headers"]["Authorization"].startswith("Token ")


def _aapl() -> InstrumentRef:
    return InstrumentRef(instrument_id="US:AAPL", asset_class=AssetClass.US_EQUITY, symbol="AAPL",
                         venue="NASDAQ", quote_currency="USD", timezone="America/New_York",
                         contract_type="COMMON_STOCK")


def test_pre_calendar_window_fails_clearly_and_long_window_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: Tiingo serves 1980+ history but XNYS only starts 2006-09-12."""
    from dataclasses import replace

    from app import providers as providers_module
    from app.trading_calendar import calendar_first_session

    monkeypatch.setattr(providers_module, "settings", replace(providers_module.settings, tiingo_token="t"))
    provider = TiingoEodProvider()
    provider.http = _FakeJsonHttp([])
    with pytest.raises(RuntimeError) as excinfo:
        provider.fetch_daily(_aapl(), datetime(1990, 1, 1, tzinfo=UTC), datetime(2000, 1, 1, tzinfo=UTC))
    assert "outside the installed XNYS calendar coverage" in str(excinfo.value)

    row = {"date": "2010-01-04T00:00:00.000Z", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
           "volume": 100, "adjOpen": 10.0, "adjHigh": 11.0, "adjLow": 9.0, "adjClose": 10.5,
           "adjVolume": 100, "splitFactor": 1.0, "divCash": 0.0}
    provider.http = _FakeJsonHttp([row])
    bars, source = provider.fetch_daily(_aapl(), datetime(1990, 1, 1, tzinfo=UTC), datetime(2015, 1, 1, tzinfo=UTC))
    assert len(bars) == 1
    requested_start = provider.http.calls[0]["params"]["startDate"]
    assert requested_start >= calendar_first_session().date().isoformat()
    assert any("clamped" in item for item in source.limitations)


def test_eod_and_intraday_providers_are_separately_configurable() -> None:
    allowed = {"alpaca", "twelve_data", "alpha_vantage", "tiingo", "massive"}
    assert settings.equity_eod_provider in allowed
    assert settings.equity_provider in allowed
    eod, eod_name = _equity_provider(settings.equity_eod_provider)
    intraday, intraday_name = _equity_provider(settings.equity_provider)
    assert eod_name.value in {"ALPACA", "TWELVE_DATA", "ALPHA_VANTAGE", "TIINGO", "MASSIVE"}
    assert intraday_name.value in {"ALPACA", "TWELVE_DATA", "ALPHA_VANTAGE", "TIINGO", "MASSIVE"}
    assert isinstance(eod, (AlpacaMarketDataProvider, TwelveDataProvider, AlphaVantageProvider,
                            TiingoEodProvider, MassiveEodProvider))
    assert isinstance(intraday, (AlpacaMarketDataProvider, TwelveDataProvider, AlphaVantageProvider,
                                 TiingoEodProvider, MassiveEodProvider))
