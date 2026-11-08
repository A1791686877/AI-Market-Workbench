"""FRED macro layer: a small, explicit set of free series turned into a point-in-time snapshot.

Discipline
----------
* Only an allowlisted host (`api.stlouisfed.org`) over HTTPS GET, no key in logs.
* **Point-in-time by construction**: every request carries `realtime_start=<vintage>`, so FRED
  returns the value as it was *known at that date* (ALFRED vintage), not the latest revision.
  The vintage date is part of the snapshot and of the cache key.
* Missing values ("." in FRED) stay missing; they are never interpolated or carried forward.
* Derived values (CPI year-over-year) record their formula and inputs.
* Macro data is context, never a signal on its own, and is not mixed into price evidence.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Literal

from app.config import settings
from app.models import (
    Completeness,
    DataProvider,
    DataStatus,
    DelayClass,
    MacroObservation,
    MacroSeries,
    MacroSnapshot,
    MarketScope,
    PriceBasis,
    SourceEnvelope,
)
from app.providers import ProviderConfigurationError
from app.safe_http import JsonResponse, ReadOnlyHttpClient

FRED_HOST = "api.stlouisfed.org"
FRED_BASE = "https://api.stlouisfed.org/fred"
OBSERVATION_LIMIT_MAX = 200
DEFAULT_LIMIT = 30

MacroRole = Literal["RISK_FREE_RATE", "RATE_SPREAD", "POLICY_RATE", "INFLATION", "DOLLAR", "VOLATILITY"]


@dataclass(frozen=True)
class FredSeriesSpec:
    series_id: str
    label: str
    role: MacroRole
    why: str


# The explicit allowlist. Adding a series here is a deliberate, reviewable act.
FRED_SERIES: tuple[FredSeriesSpec, ...] = (
    FredSeriesSpec("DGS10", "10 年期美债收益率", "RISK_FREE_RATE", "全球风险资产定价的贴现率锚"),
    FredSeriesSpec("DGS2", "2 年期美债收益率", "RISK_FREE_RATE", "市场对政策利率路径的定价"),
    FredSeriesSpec("T10Y2Y", "10Y-2Y 利差", "RATE_SPREAD", "倒挂/陡峭化 = 衰退与降息预期"),
    FredSeriesSpec("FEDFUNDS", "有效联邦基金利率（月）", "POLICY_RATE", "政策利率现状"),
    FredSeriesSpec("CPIAUCSL", "CPI 指数（季调，月）", "INFLATION", "通胀水平（同比由本模块派生）"),
    FredSeriesSpec("DTWEXBGS", "名义广义美元指数（日）", "DOLLAR", "美元强弱对风险资产与大宗的影响"),
    FredSeriesSpec("VIXCLS", "VIX 收盘（日）", "VOLATILITY", "市场恐慌/风险偏好"),
)
FRED_BY_ID = {spec.series_id: spec for spec in FRED_SERIES}
CPI_SERIES_ID = "CPIAUCSL"
CPI_YOY_SERIES_ID = "CPIAUCSL_YOY"
CPI_YOY_LOOKBACK = 12          # monthly series → 12 observations back ≈ one year


def _decimal(raw: str | None) -> Decimal | None:
    if raw is None or raw.strip() in {"", "."}:
        return None
    try:
        return Decimal(raw.strip())
    except InvalidOperation:
        return None


def _decimal_string(value: Decimal | None, places: int = 6) -> str | None:
    if value is None:
        return None
    quantized = value.quantize(Decimal(1).scaleb(-places))
    text = format(quantized, "f").rstrip("0").rstrip(".")
    return text or "0"


class FredProvider:
    """Read-only FRED client. The API key travels in the query string, as FRED requires."""

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({FRED_HOST})

    def _key(self) -> str:
        if not settings.fred_api_key:
            raise ProviderConfigurationError("FRED API key is not configured")
        return settings.fred_api_key

    def fetch_observations(self, series_id: str, *, vintage: date, limit: int = DEFAULT_LIMIT) -> JsonResponse:
        if series_id not in FRED_BY_ID:
            raise ValueError(f"series {series_id!r} is not in the reviewed FRED allowlist")
        return self.http.get_json(
            f"{FRED_BASE}/series/observations",
            params={
                "series_id": series_id,
                "api_key": self._key(),
                "file_type": "json",
                "sort_order": "desc",
                "limit": max(2, min(limit, OBSERVATION_LIMIT_MAX)),
                # Vintage request: the value as known on that date, not the latest revision.
                "realtime_start": vintage.isoformat(),
                "realtime_end": vintage.isoformat(),
            },
        )

    def fetch_metadata(self, series_id: str) -> JsonResponse:
        if series_id not in FRED_BY_ID:
            raise ValueError(f"series {series_id!r} is not in the reviewed FRED allowlist")
        return self.http.get_json(
            f"{FRED_BASE}/series",
            params={"series_id": series_id, "api_key": self._key(), "file_type": "json"},
        )


def parse_observations(payload: dict) -> list[MacroObservation]:
    rows = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("FRED payload has no observations list")
    parsed: list[MacroObservation] = []
    for row in rows:
        raw = str(row.get("value", "."))
        parsed.append(MacroObservation(
            observation_date=date.fromisoformat(str(row["date"])),
            value=_decimal_string(_decimal(raw)),
            raw_value=raw,
            is_missing=_decimal(raw) is None,
        ))
    # FRED returns newest-first; the engine works oldest-first and always computes changes forward.
    parsed.reverse()
    return parsed


def parse_metadata(payload: dict) -> dict[str, str | None]:
    rows = payload.get("seriess") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"title": None, "units": None, "frequency": None}
    row = rows[0]
    return {"title": row.get("title"), "units": row.get("units"), "frequency": row.get("frequency")}


def _available(observations: Iterable[MacroObservation]) -> list[MacroObservation]:
    return [item for item in observations if not item.is_missing]


def _change(available: list[MacroObservation], steps: int) -> Decimal | None:
    if len(available) <= steps:
        return None
    return (_decimal(available[-1].value) or Decimal(0)) - (_decimal(available[-1 - steps].value) or Decimal(0))


def build_series(spec: FredSeriesSpec, observations: list[MacroObservation], metadata: dict,
                 raw_sha256: str) -> MacroSeries:
    available = _available(observations)
    latest = available[-1] if available else None
    change_20 = _change(available, 20)
    base_20 = _decimal(available[-21].value) if len(available) > 20 else None
    # Percent change only makes sense for level series (indexes); a spread or a yield has no
    # meaningful percentage change, so it stays None instead of being invented.
    units = (metadata.get("units") or "")
    level_series = "index" in units.lower() or spec.series_id == CPI_SERIES_ID
    percent_20 = (change_20 / base_20 * 100) if (level_series and change_20 is not None
                                                and base_20 not in (None, Decimal(0))) else None
    return MacroSeries(
        series_id=spec.series_id,
        label=spec.label,
        title=metadata.get("title"),
        units=metadata.get("units"),
        frequency=metadata.get("frequency"),
        role=spec.role,
        status=DataStatus.AVAILABLE if latest is not None else DataStatus.MISSING,
        missing_reason=None if latest is not None else "PROVIDER_RETURNED_NO_USABLE_OBSERVATIONS",
        latest=latest,
        latest_observation_date=latest.observation_date if latest else None,
        change_1_observation=_decimal_string(_change(available, 1)),
        change_5_observations=_decimal_string(_change(available, 5)),
        change_20_observations=_decimal_string(change_20),
        percent_change_20_observations=_decimal_string(percent_20, 4),
        observations=observations,
        source_ref=f"src-fred-{spec.series_id.lower()}",
    )


def build_cpi_yoy(cpi: MacroSeries) -> MacroSeries:
    """CPI year-over-year, derived from the CPI index series (formula and inputs recorded)."""
    available = _available(cpi.observations)
    if len(available) <= CPI_YOY_LOOKBACK:
        return MacroSeries(
            series_id=CPI_YOY_SERIES_ID, label="CPI 同比（派生）", title=None, units="percent",
            frequency="Monthly", role="INFLATION", status=DataStatus.MISSING,
            missing_reason="NOT_ENOUGH_CPI_HISTORY_FOR_12_MONTH_COMPARISON", latest=None,
            latest_observation_date=None, change_1_observation=None, change_5_observations=None,
            change_20_observations=None, percent_change_20_observations=None, observations=[],
            source_ref=cpi.source_ref,
            derivation="CPI_YOY = CPIAUCSL[t] / CPIAUCSL[t-12] - 1",
            derived_from=[cpi.series_id],
        )
    derived: list[MacroObservation] = []
    for index in range(CPI_YOY_LOOKBACK, len(available)):
        current, previous = _decimal(available[index].value), _decimal(available[index - CPI_YOY_LOOKBACK].value)
        if current is None or previous in (None, Decimal(0)):
            continue
        value = (current / previous - 1) * 100
        derived.append(MacroObservation(observation_date=available[index].observation_date,
                                        value=_decimal_string(value, 4), raw_value=str(value),
                                        is_missing=False))
    latest = derived[-1] if derived else None
    change_1 = (_decimal(derived[-1].value) - _decimal(derived[-2].value)) if len(derived) >= 2 else None
    changes_5 = (_decimal(derived[-1].value) - _decimal(derived[-6].value)) if len(derived) >= 6 else None
    changes_20 = (_decimal(derived[-1].value) - _decimal(derived[-21].value)) if len(derived) >= 21 else None
    return MacroSeries(
        series_id=CPI_YOY_SERIES_ID, label="CPI 同比（派生）", title=None, units="percent",
        frequency="Monthly", role="INFLATION",
        status=DataStatus.AVAILABLE if latest else DataStatus.MISSING,
        missing_reason=None if latest else "NOT_ENOUGH_CPI_HISTORY_FOR_12_MONTH_COMPARISON",
        latest=latest, latest_observation_date=latest.observation_date if latest else None,
        change_1_observation=_decimal_string(change_1), change_5_observations=_decimal_string(changes_5),
        change_20_observations=_decimal_string(changes_20),
        percent_change_20_observations=None, observations=derived, source_ref=cpi.source_ref,
        derivation="CPI_YOY = CPIAUCSL[t] / CPIAUCSL[t-12] - 1",
        derived_from=[cpi.series_id],
    )


def series_source(spec: FredSeriesSpec, *, observations_sha256: str, metadata_sha256: str,
                  fetched_at: datetime) -> SourceEnvelope:
    return SourceEnvelope(
        source_id=f"src-fred-{spec.series_id.lower()}",
        provider=DataProvider.FRED,
        dataset=f"fred_{spec.series_id.lower()}_observations",
        endpoint_id="GET /fred/series/observations (realtime_start=vintage)",
        source_at=fetched_at,
        received_at=datetime.now(UTC),
        delay_class=DelayClass.END_OF_DAY,
        market_scope=MarketScope.MACRO_ECONOMY,
        completeness=Completeness.COMPLETE_FOR_SCOPE,
        is_full_market=False,
        price_basis=PriceBasis.RAW,
        timezone="UTC",
        terms_version="verify-at-enable-time",
        raw_sha256=sha256_of(observations_sha256, metadata_sha256),
        limitations=[
            "官方宏观统计，非行情数据；观测日期 ≠ 发布时刻，本快照用 realtime_start 取当时可见的修订版本。",
            "不是投资建议，也不单独构成买卖信号；只能作为风险偏好/贴现率的背景。",
            f"用途：{spec.why}",
        ],
    )


def build_macro_snapshot(series: list[MacroSeries], sources: list[SourceEnvelope], *,
                         vintage: date, fetched_at: datetime | None = None,
                         extra_limitations: list[str] | None = None) -> MacroSnapshot:
    now = fetched_at or datetime.now(UTC)
    limitations = [
        "FRED 为官方统计发布，月度序列（FEDFUNDS/CPIAUCSL）天然滞后 1–2 个月，日度序列也要等发布。",
        "ALFRED 版本化按 realtime_start 取值，但同一版本内的后续修订不可见；不做 vintage 回填。",
        "宏观只作背景：不参与价格证据、不进入 AI 快照的技术结论，也不构成投资建议。",
    ]
    limitations.extend(extra_limitations or [])
    return MacroSnapshot(
        as_of=now,
        vintage_date=vintage,
        vintage_note=f"所有序列按 realtime_start=realtime_end={vintage.isoformat()}（当时可见的版本）取值",
        series=series,
        sources=sources,
        limitations=limitations,
    )


def sha256_of(*digests: str) -> str:
    return hashlib.sha256("".join(digests).encode("ascii")).hexdigest()
