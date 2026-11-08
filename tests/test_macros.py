"""FRED macro layer: allowlist, point-in-time vintage, missing values and fail-closed behaviour."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from app import macros as macro_module
from app import service as service_module
from app.macros import (
    CPI_YOY_SERIES_ID,
    FRED_BY_ID,
    FredProvider,
    build_cpi_yoy,
    build_macro_snapshot,
    build_series,
    parse_metadata,
    parse_observations,
    series_source,
)
from app.models import DataStatus, MarketScope
from app.providers import ProviderConfigurationError
from app.safe_http import JsonResponse
from app.service import DataUnavailableError, MarketWorkbenchService
from app.storage import Store

SPEC = FRED_BY_ID["DGS10"]


class _FakeFred:
    def __init__(self, payloads: dict[str, dict], metadata: dict | None = None) -> None:
        self.payloads = payloads
        self.metadata = metadata or {"seriess": [{"title": "10-Year Treasury", "units": "Percent",
                                                  "frequency": "Daily"}]}
        self.calls: list[dict] = []
        self.http = _FakeHttpClient()

    def fetch_observations(self, series_id: str, *, vintage: date, limit: int = 30) -> JsonResponse:
        self.calls.append({"kind": "observations", "series_id": series_id, "vintage": vintage.isoformat(),
                           "limit": limit})
        if series_id not in self.payloads:
            raise RuntimeError(f"boom {series_id}")
        return JsonResponse(payload=self.payloads[series_id], sha256="a" * 64, status_code=200)

    def fetch_metadata(self, series_id: str) -> JsonResponse:
        self.calls.append({"kind": "metadata", "series_id": series_id})
        return JsonResponse(payload=self.metadata, sha256="b" * 64, status_code=200)


class _FakeHttpClient:
    def close(self) -> None:
        pass


def _observations(rows: list[tuple[str, str]]) -> dict:
    """rows are in FRED payload order: newest first (the parser flips them oldest-first)."""
    return {"observations": [{"date": day, "value": value, "realtime_start": "2026-09-12",
                              "realtime_end": "2026-09-12"} for day, value in rows]}


def test_fred_provider_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macro_module, "settings", replace(macro_module.settings, fred_api_key=None))
    with pytest.raises(ProviderConfigurationError):
        FredProvider().fetch_observations("DGS10", vintage=date(2026, 9, 12))


def test_only_allowlisted_series_can_be_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macro_module, "settings", replace(macro_module.settings, fred_api_key="k"))
    provider = FredProvider()
    with pytest.raises(ValueError):
        provider.fetch_observations("GDP", vintage=date(2026, 9, 12))
    with pytest.raises(ValueError):
        provider.fetch_metadata("NOT_A_SERIES")


def test_parse_observations_keeps_missing_values_and_sorts_oldest_first() -> None:
    payload = _observations([("2026-09-10", "4.95"), ("2026-09-09", "."), ("2026-09-08", "4.80")])
    parsed = parse_observations(payload)
    assert [item.observation_date.isoformat() for item in parsed] == ["2026-09-08", "2026-09-09", "2026-09-10"]
    assert parsed[1].is_missing is True and parsed[1].value is None and parsed[1].raw_value == "."
    assert parsed[-1].value == "4.95"


def test_changes_skip_missing_values_and_never_invent_a_percentage_for_a_yield() -> None:
    payload = _observations([("2026-09-10", "4.95"), ("2026-09-09", "."), ("2026-09-08", "4.80"),
                             ("2026-09-05", "4.70")])
    series = build_series(SPEC, parse_observations(payload), parse_metadata({"seriess": [
        {"title": "10-Year Treasury", "units": "Percent", "frequency": "Daily"}]}), "c" * 64)
    assert series.status is DataStatus.AVAILABLE
    assert series.latest.observation_date.isoformat() == "2026-09-10"
    assert series.change_1_observation == "0.15"          # 4.95 - 4.80, the gap is skipped
    assert series.change_5_observations is None
    assert series.percent_change_20_observations is None  # Percent series: a % of a % is meaningless
    assert series.role == "RISK_FREE_RATE"
    assert "利率" in series.label or "收益率" in series.label


def test_index_series_still_reports_a_percent_change() -> None:
    rows = [(f"2026-07-{day:02d}", str(100 + day)) for day in range(1, 26)]
    spec = FRED_BY_ID["DTWEXBGS"]
    series = build_series(spec, parse_observations(_observations(rows)), parse_metadata({"seriess": [
        {"title": "Dollar Index", "units": "Index Jan 2006=100", "frequency": "Daily"}]}), "d" * 64)
    assert series.percent_change_20_observations is not None


def test_empty_payload_is_missing_not_zero() -> None:
    series = build_series(SPEC, parse_observations(_observations([])), parse_metadata({"seriess": []}), "e" * 64)
    assert series.status is DataStatus.MISSING
    assert series.latest is None and series.missing_reason == "PROVIDER_RETURNED_NO_USABLE_OBSERVATIONS"


def test_cpi_yoy_is_derived_with_formula_and_inputs() -> None:
    # 13 monthly observations (newest first, as FRED returns them): the index doubles over the year,
    # so CPI YoY on the last month is 100%.
    rows = [("2026-01-01", "200")] + [(f"2025-{month:02d}-01", "100") for month in range(12, 0, -1)]
    cpi_spec = FRED_BY_ID["CPIAUCSL"]
    cpi = build_series(cpi_spec, parse_observations(_observations(rows)), parse_metadata({"seriess": [
        {"title": "CPI", "units": "Index 1982-1984=100", "frequency": "Monthly"}]}), "f" * 64)
    derived = build_cpi_yoy(cpi)
    assert derived.series_id == CPI_YOY_SERIES_ID
    assert derived.status is DataStatus.AVAILABLE
    assert derived.latest.value == "100"
    assert derived.derivation == "CPI_YOY = CPIAUCSL[t] / CPIAUCSL[t-12] - 1"
    assert derived.derived_from == ["CPIAUCSL"]
    assert derived.units == "percent"


def test_cpi_yoy_without_a_full_year_is_missing() -> None:
    cpi_spec = FRED_BY_ID["CPIAUCSL"]
    rows = [(f"2026-0{month}-01", "100") for month in range(1, 10)]
    cpi = build_series(cpi_spec, parse_observations(_observations(rows)), parse_metadata({"seriess": []}), "g" * 64)
    derived = build_cpi_yoy(cpi)
    assert derived.status is DataStatus.MISSING
    assert derived.missing_reason == "NOT_ENOUGH_CPI_HISTORY_FOR_12_MONTH_COMPARISON"


def test_series_source_is_macro_scoped_and_rejects_a_price_reading() -> None:
    source = series_source(SPEC, observations_sha256="a" * 64, metadata_sha256="b" * 64,
                           fetched_at=datetime(2026, 9, 12, tzinfo=UTC))
    assert source.provider.value == "FRED"
    assert source.market_scope is MarketScope.MACRO_ECONOMY
    assert source.is_full_market is False
    assert "不是投资建议" in " ".join(source.limitations)


def test_macro_snapshot_carries_vintage_and_limitations() -> None:
    series = build_series(SPEC, parse_observations(_observations([("2026-09-10", "4.95")])),
                          parse_metadata({"seriess": []}), "h" * 64)
    source = series_source(SPEC, observations_sha256="a" * 64, metadata_sha256="b" * 64,
                           fetched_at=datetime(2026, 9, 12, tzinfo=UTC))
    snapshot = build_macro_snapshot([series], [source], vintage=date(2026, 6, 11),
                                    fetched_at=datetime(2026, 9, 12, tzinfo=UTC))
    assert snapshot.vintage_date == date(2026, 6, 11)
    assert "realtime_start" in snapshot.vintage_note
    assert any("不是投资建议" in item or "不构成投资建议" in item for item in snapshot.limitations)


def test_service_fails_closed_in_fixture_mode(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(service_module, "settings",
                        replace(service_module.settings, data_mode="fixture", root=tmp_path))
    with pytest.raises(DataUnavailableError):
        MarketWorkbenchService(Store(tmp_path / "macro.db")).get_macro_context(limit=5)


def test_service_builds_and_caches_a_point_in_time_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    payloads = {"DGS10": _observations([("2026-06-10", "4.55"), ("2026-06-09", "4.53")]),
                "CPIAUCSL": _observations([("2026-01-01", "200")]
                                          + [(f"2025-{m:02d}-01", "100") for m in range(12, 0, -1)])}
    fake = _FakeFred(payloads)
    monkeypatch.setattr(macro_module, "FredProvider", lambda: fake)
    monkeypatch.setattr(macro_module, "FRED_SERIES", (FRED_BY_ID["DGS10"], FRED_BY_ID["CPIAUCSL"]))
    monkeypatch.setattr(service_module, "settings", replace(
        service_module.settings, data_mode="live", network_enabled=True, fred_api_key="k", root=tmp_path))

    service = MarketWorkbenchService(Store(tmp_path / "macro.db"))
    snapshot = service.get_macro_context(limit=10, vintage=datetime(2026, 6, 11, tzinfo=UTC))

    assert snapshot.vintage_date == date(2026, 6, 11)
    by_id = {item.series_id: item for item in snapshot.series}
    assert set(by_id) == {"DGS10", "CPIAUCSL", "CPIAUCSL_YOY"}
    assert by_id["DGS10"].latest.value == "4.55"          # the vintage value, not today's revision
    assert [s.source_id for s in snapshot.sources] == ["src-fred-dgs10", "src-fred-cpiaucsl"]
    assert all(call["vintage"] == "2026-06-11" for call in fake.calls if call["kind"] == "observations")
    cached = list((tmp_path / "cache" / "macro").glob("fred-2026-06-11-*.json"))
    assert len(cached) == 1
    # Second read must come from the cache, not from another network call.
    before = len(fake.calls)
    service.get_macro_context(limit=10, vintage=datetime(2026, 6, 11, tzinfo=UTC))
    assert len(fake.calls) == before


def test_one_failing_series_is_reported_and_the_rest_still_served(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    payloads = {"DGS10": _observations([("2026-06-10", "4.55")])}   # CPIAUCSL will raise
    fake = _FakeFred(payloads)
    monkeypatch.setattr(macro_module, "FredProvider", lambda: fake)
    monkeypatch.setattr(macro_module, "FRED_SERIES", (FRED_BY_ID["DGS10"], FRED_BY_ID["CPIAUCSL"]))
    monkeypatch.setattr(service_module, "settings", replace(
        service_module.settings, data_mode="live", network_enabled=True, fred_api_key="k", root=tmp_path))

    snapshot = MarketWorkbenchService(Store(tmp_path / "macro.db")).get_macro_context(limit=5)

    assert [item.series_id for item in snapshot.series] == ["DGS10"]
    assert any("CPIAUCSL" in item for item in snapshot.limitations)
    assert len(snapshot.sources) == 1


def test_service_refuses_when_every_series_fails(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(macro_module, "FredProvider", lambda: _FakeFred({}))
    monkeypatch.setattr(service_module, "settings", replace(
        service_module.settings, data_mode="live", network_enabled=True, fred_api_key="k", root=tmp_path))
    with pytest.raises(DataUnavailableError):
        MarketWorkbenchService(Store(tmp_path / "macro.db")).get_macro_context(limit=5)
