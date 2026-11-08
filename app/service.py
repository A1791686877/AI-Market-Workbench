from __future__ import annotations

import json
import uuid
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.bar_builder import aggregate_bars
from app.config import settings
from app.fixtures import BARS, SOURCES, SUBJECTS, crypto_context, decimal_string, equity_fundamentals
from app.indicators import (
    compute_divergences,
    compute_fvg,
    compute_indicators,
    compute_support_resistance_and_trend,
    compute_vpvr,
)
from app.logging_config import audit_event
from app.market_cache import MarketCache, market_cache
from app.models import (
    AiAnalysisResult,
    AiAnalysisTask,
    AiAnalysisTaskRequest,
    AiTaskStatus,
    AnalysisAudit,
    AnalysisOutcome,
    AnalysisPage,
    AnalysisSnapshot,
    AnalysisSubject,
    Bar,
    BarGap,
    BarPage,
    BarQualitySummary,
    BarRequestStatus,
    Completeness,
    Condition,
    Confidence,
    Conflict,
    CryptoMarketContext,
    DataProvider,
    DataStatus,
    DelayClass,
    Disclaimer,
    EquityFundamentals,
    ErrorCode,
    Evidence,
    EvidenceObservation,
    EvidenceRefType,
    Impact,
    IndicatorBundle,
    IndicatorEvidenceView,
    InstrumentRef,
    InstrumentSearchResponse,
    InstrumentSearchItem,
    Interval,
    KeyLevel,
    KeyLevelKind,
    MacroSnapshot,
    MarketScope,
    MarketObservation,
    MissingInformation,
    MissingReason,
    ObservationName,
    OutcomeHorizon,
    OutcomePage,
    OutcomeStatus,
    PageMeta,
    PriceBasis,
    ProblemDetail,
    QualityFlag,
    Scenario,
    ScenarioType,
    SourceEnvelope,
    SnapshotRef,
    SnapshotRequest,
    SubjectType,
    is_demo_provider,
)
from app.providers import (
    AlpacaMarketDataProvider,
    AlphaVantageProvider,
    BinancePublicProvider,
    CoinGeckoDemoProvider,
    MassiveEodProvider,
    OkxPublicProvider,
    TiingoEodProvider,
    TwelveDataProvider,
)
from app.scenario_rules import ScenarioOutcomePage, freeze as freeze_scenario_rules
from app.storage import Store, canonical_json, decode_cursor, encode_cursor, sha256_json, store
from app.trading_calendar import maturity_after_trading_minutes


NY = ZoneInfo("America/New_York")


class NotFoundError(LookupError):
    pass


class AnalysisValidationError(RuntimeError):
    pass


class DataUnavailableError(RuntimeError):
    pass


class ModelProviderUnavailableError(RuntimeError):
    pass


class SnapshotSchemaError(RuntimeError):
    """A stored snapshot cannot be read by the current schema version."""


INTERVAL_SECONDS = {
    Interval.MIN_15: 900,
    Interval.HOUR_1: 3600,
    Interval.HOUR_2: 7200,
    Interval.MIN_195: 11700,
    Interval.HOUR_4: 14400,
    Interval.DAY_1: 86400,
}

CRYPTO_LOOKBACK = {
    Interval.MIN_15: timedelta(days=365 * 2),
    Interval.HOUR_1: timedelta(days=365 * 2),
    Interval.HOUR_2: timedelta(days=365 * 2),
    Interval.MIN_195: timedelta(days=365 * 5),
    Interval.HOUR_4: timedelta(days=365 * 5),
    Interval.DAY_1: timedelta(days=365 * 20),
    Interval.WEEK_1: timedelta(days=365 * 20),
    Interval.MONTH_1: timedelta(days=365 * 20),
}

EQUITY_LOOKBACK = {
    Interval.MIN_15: timedelta(days=365 * 2),
    Interval.HOUR_1: timedelta(days=365 * 2),
    Interval.HOUR_2: timedelta(days=365 * 5),
    Interval.MIN_195: timedelta(days=365 * 5),
    Interval.HOUR_4: timedelta(days=365 * 5),
    Interval.DAY_1: timedelta(days=365 * 20),
    Interval.WEEK_1: timedelta(days=365 * 20),
    Interval.MONTH_1: timedelta(days=365 * 20),
}


def _cursor_to_offset(cursor: str | None) -> int:
    """Pagination cursors have exactly one implementation (see app.storage)."""
    return decode_cursor(cursor)


def _offset_to_cursor(offset: int) -> str:
    return encode_cursor(offset)


def _equity_provider(name: str) -> tuple[object, DataProvider]:
    """Explicit equity source selection (AMW_EQUITY_PROVIDER); never a silent fallback."""
    if name == "alpaca":
        return AlpacaMarketDataProvider(settings.alpaca_feed), DataProvider.ALPACA
    if name == "twelve_data":
        return TwelveDataProvider(), DataProvider.TWELVE_DATA
    if name == "alpha_vantage":
        return AlphaVantageProvider(), DataProvider.ALPHA_VANTAGE
    if name == "tiingo":
        return TiingoEodProvider(), DataProvider.TIINGO
    if name == "massive":
        return MassiveEodProvider(), DataProvider.MASSIVE
    raise ValueError(f"unsupported equity provider: {name}")


class MarketWorkbenchService:
    def __init__(self, database: Store | None = None, cache: MarketCache | None = None,
                 snapshot_dir: Path | None = None) -> None:
        self.store = database or store
        self.cache = cache or market_cache
        # Snapshot files live beside the database they belong to. A temporary or
        # test store therefore never writes into the production snapshot dir,
        # which is what previously produced hundreds of rows-less orphan files.
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir is not None else self.store.database_path.parent / "snapshots"

    def get_subject(self, subject_id: str) -> AnalysisSubject:
        try:
            return SUBJECTS[subject_id]
        except KeyError as exc:
            raise NotFoundError(f"unknown subject: {subject_id}") from exc

    def search_instruments(self, query: str, limit: int, cursor: str | None) -> InstrumentSearchResponse:
        instruments = [InstrumentSearchItem(**item.model_dump(), subject_id=subject.subject_id)
                       for subject in SUBJECTS.values() for item in subject.instruments]
        deduplicated = {item.instrument_id: item for item in instruments}
        query_lower = query.lower()
        matches = [
            item
            for item in deduplicated.values()
            if query_lower in item.symbol.lower() or query_lower in item.instrument_id.lower()
        ]
        matches.sort(key=lambda item: item.instrument_id)
        offset = _cursor_to_offset(cursor)
        page_items = matches[offset : offset + limit]
        has_more = offset + limit < len(matches)
        return InstrumentSearchResponse(
            items=page_items,
            page=PageMeta(
                limit=limit,
                cursor=cursor,
                next_cursor=_offset_to_cursor(offset + limit) if has_more else None,
                has_more=has_more,
            ),
            as_of=datetime.now(UTC),
        )

    @staticmethod
    def get_instrument(instrument_id: str) -> InstrumentRef:
        for subject in SUBJECTS.values():
            for instrument in subject.instruments:
                if instrument.instrument_id == instrument_id:
                    return instrument
        raise NotFoundError(f"unknown instrument: {instrument_id}")

    @staticmethod
    def _crypto_base_interval(requested: Interval) -> Interval:
        if requested is Interval.MIN_195:
            return Interval.MIN_15
        if requested in {Interval.WEEK_1, Interval.MONTH_1}:
            return Interval.DAY_1
        return requested

    def _base_bars(
        self,
        instrument: InstrumentRef,
        requested_interval: Interval,
        cutoff_at: datetime | None = None,
    ) -> tuple[list[Bar], SourceEnvelope, datetime | None, Interval]:
        if settings.data_mode == "fixture":
            bars = BARS.get(instrument.instrument_id)
            if bars is None:
                # Fixture mode only carries synthetic series for its own subjects.
                # Failing closed is better than fabricating bars for a real symbol.
                raise DataUnavailableError(
                    f"{instrument.instrument_id} has no synthetic fixture series; "
                    "start in live mode for real bars (fail closed, no fixture fallback)"
                )
            if cutoff_at:
                bars = [bar for bar in bars if bar.close_at <= cutoff_at]
            source = SOURCES[bars[0].source_ref] if bars else SOURCES[next(iter(SOURCES))]
            return bars, source, bars[0].open_at if bars else None, Interval.MIN_15
        end = cutoff_at or datetime.now(UTC)
        if instrument.asset_class.value == "US_EQUITY":
            start = end - EQUITY_LOOKBACK[requested_interval]
            if requested_interval in {Interval.DAY_1, Interval.WEEK_1, Interval.MONTH_1}:
                # Daily+ uses the EOD provider (long history, adjusted); intraday uses
                # the intraday provider. Both are explicit, neither falls back silently.
                base_interval = Interval.DAY_1
                provider, provider_name = _equity_provider(settings.equity_eod_provider)
            else:
                base_interval = Interval.MIN_15
                provider, provider_name = _equity_provider(settings.equity_provider)
        else:
            base_interval = self._crypto_base_interval(requested_interval)
            start = end - CRYPTO_LOOKBACK[requested_interval]
            if settings.crypto_provider == "okx":
                provider = OkxPublicProvider()
                provider_name = DataProvider.OKX
            else:
                provider = BinancePublicProvider()
                provider_name = DataProvider.BINANCE

        cached = self.cache.load(provider_name, instrument.instrument_id, base_interval)
        # Closed historical windows are immutable; do not refresh from a newer cursor.
        if cached and cached.bars and cutoff_at and end <= cached.bars[-1].close_at:
            provider.http.close()
            return cached.bars, cached.source, start, base_interval
        cache_covers_request = bool(
            cached
            and cached.bars
            and (
                cached.bars[0].open_at
                <= start + timedelta(seconds=INTERVAL_SECONDS[base_interval])
                # OKX history-candles is paged with a deliberately bounded
                # 5,000-bar phase-one cap. Reuse a fresh capped result while
                # continuing to report history_complete=false.
                or len(cached.bars) >= 4900
                or any("history endpoint was exhausted" in item for item in cached.source.limitations)
            )
            and cached.bars[-1].close_at >= end - timedelta(seconds=INTERVAL_SECONDS[base_interval] * 2)
        )
        if (
            cached
            and cache_covers_request
            and (datetime.now(UTC) - cached.cached_at)
            <= timedelta(seconds=settings.market_cache_ttl_seconds)
        ):
            audit_event(
                "market_cache_hit",
                provider=provider_name.value,
                instrument_id=instrument.instrument_id,
                interval=base_interval.value,
                freshness="FRESH",
            )
            return cached.bars, cached.source, start, base_interval
        try:
            if isinstance(provider, OkxPublicProvider):
                incremental_start = max(start,cached.bars[-2].open_at) if cached and len(cached.bars)>1 else start
                bars, source = provider.fetch_bars(instrument, base_interval, incremental_start, end, max_pages=2 if cached else 50)
            else:
                # Equity providers are rate limited (Twelve Data ~8/min, Alpha Vantage ~25/day),
                # so the same incremental-window discipline as OKX applies: fetch the gap, then merge.
                incremental_start = max(start, cached.bars[-2].open_at) if cached and len(cached.bars) > 1 else start
                bars, source = provider.fetch_bars(instrument, base_interval, incremental_start, end)
            merged = self.cache.save(provider_name, instrument.instrument_id, base_interval, bars, source)
            bars, source = merged.bars, merged.source
            audit_event(
                "data_provider_selected",
                provider=provider_name.value,
                instrument_id=instrument.instrument_id,
                interval=base_interval.value,
                reason="EXPLICIT_CONFIGURATION",
            )
            return bars, source, start, base_interval
        except Exception as exc:
            audit_event(
                "data_request_failed_no_fixture_fallback",
                provider=provider_name.value,
                instrument_id=instrument.instrument_id,
                interval=base_interval.value,
                error_type=type(exc).__name__,
                stale_cache_available=cached is not None,
            )
            if cached:
                stale_source = cached.source.model_copy(
                    update={
                        "completeness": Completeness.STALE,
                        "limitations": [
                            *cached.source.limitations,
                            f"Latest {provider_name.value} request failed; serving the last real cached response.",
                        ],
                    }
                )
                stale_bars = [
                    bar.model_copy(
                        update={
                            "quality_flags": sorted(
                                {*bar.quality_flags, QualityFlag.STALE_SOURCE},
                                key=lambda item: item.value,
                            )
                        }
                    )
                    for bar in cached.bars
                ]
                return stale_bars, stale_source, start, base_interval
            raise DataUnavailableError(
                f"{provider_name.value} market-data request failed and no real cache is available"
            ) from exc

    @staticmethod
    def _quality_summary(
        bars: list[Bar], source: SourceEnvelope, requested_start: datetime | None
    ) -> BarQualitySummary:
        expected = INTERVAL_SECONDS.get(bars[0].interval) if bars else None
        gaps: list[BarGap] = []
        if expected:
            for previous, current in zip(bars, bars[1:]):
                if (
                    previous.session.value == "US_RTH"
                    and previous.open_at.astimezone(NY).date() != current.open_at.astimezone(NY).date()
                ):
                    continue
                delta = int((current.open_at - previous.open_at).total_seconds())
                missing = max(delta // expected - 1, 0)
                if missing:
                    gaps.append(
                        BarGap(
                            after_bar_id=previous.bar_id,
                            before_bar_id=current.bar_id,
                            gap_start_at=previous.open_at + timedelta(seconds=expected),
                            gap_end_at=current.open_at,
                            missing_bar_count=missing,
                        )
                    )
        if source.provider is DataProvider.FIXTURE_SYNTHETIC:
            request_status = BarRequestStatus.FIXTURE
        elif source.completeness is Completeness.STALE:
            request_status = BarRequestStatus.STALE_CACHE
        else:
            request_status = BarRequestStatus.SUCCESS
        actual_start = bars[0].open_at if bars else None
        history_tolerance = {
            Interval.WEEK_1: timedelta(days=7),
            Interval.MONTH_1: timedelta(days=31),
        }.get(bars[0].interval if bars else None, timedelta(seconds=expected or 0))
        history_complete = bool(
            requested_start is not None
            and actual_start is not None
            and actual_start <= requested_start + history_tolerance
        )
        return BarQualitySummary(
            request_status=request_status,
            expected_interval_seconds=expected,
            observed_bar_count=len(bars),
            missing_bar_count=sum(gap.missing_bar_count for gap in gaps),
            gaps=gaps[:100],
            requested_start_at=requested_start,
            actual_start_at=actual_start,
            latest_bar_at=bars[-1].close_at if bars else None,
            history_complete=history_complete,
            stale_reason=(
                "Latest provider request failed; last real cached bars returned."
                if request_status is BarRequestStatus.STALE_CACHE
                else None
            ),
        )

    def get_bars(
        self,
        instrument_id: str,
        interval: Interval,
        cutoff_at: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> BarPage:
        instrument = self.get_instrument(instrument_id)
        base, source, requested_start, base_interval = self._base_bars(
            instrument, interval, cutoff_at
        )
        if base_interval is interval:
            bars = [
                bar
                for bar in sorted(base, key=lambda item: item.open_at)
                if bar.confirmed and (cutoff_at is None or bar.close_at <= cutoff_at)
            ]
        else:
            bars = aggregate_bars(base, interval, cutoff_at)
        quality = self._quality_summary(bars, source, requested_start)
        if source.provider is DataProvider.OKX:
            from app.history import HistoryProgress
            from app.market_cache import _safe_segment
            path=settings.root/'cache/history-progress'/f'{_safe_segment(instrument_id)}-{base_interval.value}.json'
            state=None
            if path.exists():
                try:
                    state=HistoryProgress.model_validate_json(path.read_text(encoding='utf-8'))
                except Exception as exc:
                    # A corrupt progress file must not turn a read-only data request
                    # into a 500; fall back to the status derived from the quality summary.
                    audit_event('history_progress_unreadable', instrument_id=instrument_id,
                                interval=base_interval.value, error_type=type(exc).__name__)
            status=state.status if state else 'TARGET_REACHED' if quality.history_complete else 'PROVIDER_EMPTY_PAGE' if any('history endpoint was exhausted' in x for x in source.limitations) else 'LOCAL_PAGE_BUDGET'
            if quality.stale_reason:status='REQUEST_FAILED'
            quality=quality.model_copy(update={'history_download_status':status,'history_cursor_end':state.cursor_end if state else bars[0].open_at if bars else None})
        offset = _cursor_to_offset(cursor)
        # Each page is chronological, but the initial page contains the newest
        # window. Following cursors walks backward; callers freeze cutoff_at.
        end_index=max(len(bars)-offset,0)
        selected=bars[max(end_index-limit,0):end_index]
        has_more = offset + limit < len(bars)
        return BarPage(
            instrument=instrument,
            interval=interval,
            bars=selected,
            sources=[source],
            quality=quality,
            page=PageMeta(
                limit=limit,
                cursor=cursor,
                next_cursor=_offset_to_cursor(offset + limit) if has_more else None,
                has_more=has_more,
            ),
        )

    def get_indicator_bundle(
        self,
        subject_id: str,
        interval: Interval,
        cutoff_at: datetime | None = None,
        instrument_id: str | None = None,
    ) -> IndicatorBundle:
        subject = self.get_subject(subject_id)
        selected_id = instrument_id or subject.primary_instrument_id
        if selected_id not in {item.instrument_id for item in subject.instruments}:
            raise ValueError("instrument_id does not belong to the analysis subject")
        instrument = self.get_instrument(selected_id)
        page = self.get_bars(selected_id, interval, cutoff_at, 5000, None)
        if not page.bars:
            raise NotFoundError("no bars are available at the requested cutoff")
        indicators = compute_indicators(page.bars)
        fvg = compute_fvg(page.bars)
        divergences = compute_divergences(page.bars)
        support_resistance, trend_structure = compute_support_resistance_and_trend(page.bars)
        vpvr = compute_vpvr(
            page.bars,
            market_scope=page.sources[0].market_scope,
            completeness=page.sources[0].completeness,
        )
        return IndicatorBundle(
            bundle_id=f"bundle-{subject.subject_id}-{instrument.instrument_id.replace(':', '-')}-{interval.value}-{int(page.bars[-1].close_at.timestamp())}",
            subject=subject,
            instrument=instrument,
            interval=interval,
            bars_as_of=page.bars[-1].close_at,
            indicators=indicators,
            fvg=fvg,
            divergences=divergences,
            vpvr=vpvr,
            support_resistance=support_resistance,
            trend_structure=trend_structure,
            sources=page.sources,
            missing_items=[],
        )

    def get_fundamentals(self, subject_id: str) -> EquityFundamentals:
        subject = self.get_subject(subject_id)
        if subject.subject_type is not SubjectType.EQUITY:
            raise NotFoundError("equity fundamentals are not applicable to a crypto subject")
        if settings.data_mode == "fixture":
            return equity_fundamentals()
        # Live: SEC EDGAR Company Facts -> point-in-time EquityFundamentals, cached per day.
        from app.fundamentals import normalize_company_facts
        from app.instruments import MAG7_CIK
        from app.providers import SecCompanyFactsProvider

        symbol = subject.symbol.upper()
        cik = MAG7_CIK.get(symbol)
        if cik is None:
            raise NotFoundError(f"no SEC CIK mapping is configured for {symbol}")
        cutoff = datetime.now(UTC)
        cache_path = settings.root / "cache" / "fundamentals" / f"{symbol.lower()}-{cutoff.date().isoformat()}.json"
        if cache_path.exists():
            try:
                return EquityFundamentals.model_validate_json(cache_path.read_text(encoding="utf-8"))
            except Exception as exc:
                audit_event("fundamentals_cache_invalid", symbol=symbol, error_type=type(exc).__name__)
        last_close = None
        try:
            provider_name = _equity_provider(settings.equity_provider)[1]
            daily = self.cache.load(provider_name, subject.primary_instrument_id, Interval.DAY_1)
            if daily and daily.bars:
                last_close = float([bar for bar in daily.bars if bar.confirmed][-1].close)
        except Exception:
            last_close = None
        provider = SecCompanyFactsProvider()
        try:
            response = provider.fetch_raw(cik)
        except Exception as exc:
            audit_event("fundamentals_request_failed", symbol=symbol, cik=cik, error_type=type(exc).__name__)
            raise DataUnavailableError(
                "SEC EDGAR request failed; no fixture fallback was used"
            ) from exc
        finally:
            provider.http.close()
        fundamentals = normalize_company_facts(
            response.payload, response.sha256, subject, cik=cik, cutoff=cutoff, last_close=last_close
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(canonical_json(fundamentals.model_dump(mode="json")), encoding="utf-8")
        audit_event("fundamentals_fetched", symbol=symbol, cik=cik,
                    facts=len(fundamentals.facts), missing=len(fundamentals.missing_items))
        return fundamentals

    def get_macro_context(self, *, limit: int = 30, vintage: datetime | None = None) -> MacroSnapshot:
        """Point-in-time FRED macro snapshot; cached per (vintage day, limit).

        Fixture mode fails closed: there is no synthetic macro series that would be honest to serve.
        """
        from app.macros import (
            CPI_SERIES_ID,
            DEFAULT_LIMIT,
            FRED_SERIES,
            FredProvider,
            build_cpi_yoy,
            build_macro_snapshot,
            build_series,
            parse_metadata,
            parse_observations,
            series_source,
        )

        if settings.data_mode == "fixture":
            raise DataUnavailableError(
                "macro context requires live mode; no synthetic macro series is served in fixture mode"
            )
        limit = max(2, min(limit or DEFAULT_LIMIT, 200))
        vintage_date = (vintage or datetime.now(UTC)).date()
        cache_path = (settings.root / "cache" / "macro"
                      / f"fred-{vintage_date.isoformat()}-limit{limit}.json")
        if cache_path.exists():
            try:
                return MacroSnapshot.model_validate_json(cache_path.read_text(encoding="utf-8"))
            except Exception as exc:
                audit_event("macro_cache_invalid", error_type=type(exc).__name__)

        provider = FredProvider()
        series: list[MacroSeries] = []
        sources: list[SourceEnvelope] = []
        failures: list[str] = []
        try:
            for spec in FRED_SERIES:
                try:
                    observations_response = provider.fetch_observations(spec.series_id, vintage=vintage_date,
                                                                       limit=limit)
                    metadata_response = provider.fetch_metadata(spec.series_id)
                except Exception as exc:
                    failures.append(f"{spec.series_id}: {type(exc).__name__}: {str(exc)[:120]}")
                    continue
                observations = parse_observations(observations_response.payload)
                built = build_series(spec, observations, parse_metadata(metadata_response.payload),
                                     observations_response.sha256)
                series.append(built)
                sources.append(series_source(spec, observations_sha256=observations_response.sha256,
                                             metadata_sha256=metadata_response.sha256,
                                             fetched_at=datetime.now(UTC)))
                if spec.series_id == CPI_SERIES_ID:
                    series.append(build_cpi_yoy(built))
        finally:
            provider.http.close()
        if not series:
            audit_event("macro_request_failed", failures=len(failures))
            raise DataUnavailableError(
                "FRED returned no usable series; no fixture fallback was used "
                f"({'; '.join(failures[:3])})"
            )
        snapshot = build_macro_snapshot(series, sources, vintage=vintage_date,
                                        extra_limitations=[
                                            "FRED 序列为逐条请求；单条失败会明确列出而不是静默补值。",
                                            *([f"部分序列获取失败：{' ; '.join(failures)}"] if failures else []),
                                        ])
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(canonical_json(snapshot.model_dump(mode="json")), encoding="utf-8")
        audit_event("macro_fetched", vintage=vintage_date.isoformat(), series=len(series),
                    available=sum(1 for item in series if item.status is DataStatus.AVAILABLE))
        return snapshot

    def get_market_context(self, subject_id: str):
        subject = self.get_subject(subject_id)
        if subject.subject_type is not SubjectType.CRYPTO_ASSET:
            raise NotFoundError("crypto market context is not applicable to an equity subject")
        if settings.data_mode == "fixture":
            return crypto_context(subject_id)
        if settings.crypto_provider != "okx":
            raise DataUnavailableError(
                "BINANCE public context is unavailable on this host; select an explicitly tested provider"
            )
        spot_instrument = next(
            item for item in subject.instruments if item.asset_class.value == "CRYPTO_SPOT"
        )
        spot_page = self.get_bars(spot_instrument.instrument_id, Interval.MIN_15, None, 5000, None)
        if not spot_page.bars:
            raise DataUnavailableError("OKX spot bars are unavailable for crypto context")
        provider = OkxPublicProvider()
        try:
            raw = provider.fetch_context_raw(subject.symbol)
            rows = {name: response.payload.get("data", []) for name, response in raw.items()}
            if any(not value for value in rows.values()):
                raise ValueError("one or more OKX context datasets returned no rows")
            mark_row = rows["mark"][0]
            index_row = rows["index"][0]
            funding_row = rows["funding"][0]
            oi_row = rows["open_interest"][0]
            mark = Decimal(mark_row["markPx"])
            spot_row = rows['spot'][0]
            spot = Decimal(spot_row['last'])
            index = Decimal(index_row["idxPx"])
            funding = Decimal(funding_row["fundingRate"])
            open_interest = Decimal(oi_row.get("oiCcy") or oi_row["oi"])
            observed_times = [
                datetime.fromtimestamp(int(row['ts']) / 1000, UTC)
                for row in (mark_row, index_row, funding_row, oi_row, spot_row)
            ]
        except Exception as exc:
            audit_event(
                "crypto_context_request_failed",
                provider="OKX",
                subject_id=subject_id,
                error_type=type(exc).__name__,
            )
            raise DataUnavailableError(
                "OKX public crypto context request failed; no fixture fallback was used"
            ) from exc
        finally:
            provider.http.close()

        source_id = f"src-okx-{subject.symbol.lower()}-context"
        context_source = SourceEnvelope(
            source_id=source_id,
            provider=DataProvider.OKX,
            dataset="okx_public_derivatives_context",
            endpoint_id=(
                "GET mark-price + index-tickers + funding-rate + open-interest"
            ),
            source_at=max(observed_times),
            received_at=datetime.now(UTC),
            delay_class=DelayClass.REALTIME_PUBLIC,
            market_scope=MarketScope.VENUE_SPECIFIC_CRYPTO,
            completeness=Completeness.PARTIAL,
            is_full_market=False,
            price_basis=PriceBasis.RAW,
            timezone="UTC",
            terms_version="verify-at-enable-time",
            raw_sha256=hashlib.sha256(
                "".join(response.sha256 for response in raw.values()).encode("ascii")
            ).hexdigest(),
            limitations=[
                "OKX venue-specific observations; not consolidated across exchanges.",
                "Liquidation data is unavailable in this no-key context call; supply/market cap come from a separate CoinGecko source when configured.",
            ],
        )

        def available(
            name: ObservationName,
            value: Decimal,
            unit: str,
            observed_at: datetime,
            source: str = source_id,
        ) -> MarketObservation:
            return MarketObservation(
                observation_id=f"obs-{source.replace('src-', '')}-{name.value.lower()}",
                name=name,
                value=decimal_string(value, 12),
                unit=unit,
                status=DataStatus.AVAILABLE,
                as_of=observed_at,
                source_ref=source,
                completeness=Completeness.COMPLETE_FOR_SCOPE,
                missing_reason=None,
            )

        def missing(name: ObservationName, unit: str) -> MarketObservation:
            return MarketObservation(
                observation_id=f"obs-okx-{subject.symbol.lower()}-{name.value.lower()}",
                name=name,
                value=None,
                unit=unit,
                status=DataStatus.MISSING,
                as_of=max(observed_times),
                source_ref=source_id,
                completeness=Completeness.MISSING,
                missing_reason="NO_CONFIGURED_FREE_SOURCE",
            )

        supply_observations = [
            missing(ObservationName.CIRCULATING_SUPPLY, subject.symbol),
            missing(ObservationName.TOTAL_SUPPLY, subject.symbol),
            missing(ObservationName.MARKET_CAP, "USD"),
        ]
        extra_sources: list[SourceEnvelope] = []
        context_limitations = [
            "Venue-specific OKX public data; funding, OI and basis are not market-wide.",
            "Liquidations, on-chain activity and exchange flows are explicitly missing.",
        ]
        if settings.coingecko_demo_key:
            # Supply/market cap are NETWORK-WIDE estimates from a different provider:
            # they get their own SourceEnvelope and never replace the OKX price.
            try:
                coin_id = {"BTC": "bitcoin", "ETH": "ethereum"}.get(subject.symbol.upper())
                if coin_id is None:
                    raise ValueError(f"no CoinGecko coin id mapped for {subject.symbol}")
                gecko = CoinGeckoDemoProvider()
                try:
                    response = gecko.fetch_raw(coin_id)
                finally:
                    gecko.http.close()
                row = next((item for item in response.payload
                            if str(item.get("symbol", "")).upper() == subject.symbol.upper()), None)
                if row is None:
                    raise ValueError("CoinGecko returned no row for this symbol")
                gecko_source = SourceEnvelope(
                    source_id=f"src-coingecko-{subject.symbol.lower()}-supply",
                    provider=DataProvider.COINGECKO,
                    dataset="coingecko_demo_coins_markets",
                    endpoint_id="GET /api/v3/coins/markets",
                    source_at=datetime.now(UTC),
                    received_at=datetime.now(UTC),
                    delay_class=DelayClass.UNKNOWN,
                    market_scope=MarketScope.NETWORK_WIDE,
                    completeness=Completeness.PARTIAL,
                    is_full_market=True,
                    price_basis=PriceBasis.RAW,
                    timezone="UTC",
                    terms_version="verify-at-enable-time",
                    raw_sha256=response.sha256,
                    limitations=[
                        "Aggregated network-wide supply/market-cap estimate, not a venue observation.",
                        "CoinGecko price is NOT used as the analysis price; OKX remains the price source.",
                        "Demo tier is rate limited and attribution terms must be verified before deployment.",
                    ],
                )

                def gecko_observation(name: ObservationName, key: str, unit: str) -> MarketObservation | None:
                    raw_value = row.get(key)
                    if raw_value is None:
                        return None
                    return available(name, Decimal(str(raw_value)), unit, datetime.now(UTC),
                                     source=gecko_source.source_id)

                resolved = {
                    ObservationName.CIRCULATING_SUPPLY: gecko_observation(
                        ObservationName.CIRCULATING_SUPPLY, "circulating_supply", subject.symbol),
                    ObservationName.TOTAL_SUPPLY: gecko_observation(
                        ObservationName.TOTAL_SUPPLY, "total_supply", subject.symbol),
                    ObservationName.MARKET_CAP: gecko_observation(
                        ObservationName.MARKET_CAP, "market_cap", "USD"),
                }
                if any(item is not None for item in resolved.values()):
                    supply_observations = [item for item in resolved.values() if item is not None]
                    extra_sources.append(gecko_source)
                    context_limitations.append(
                        "Supply and market cap are CoinGecko network-wide estimates; the provider's circulating-supply "
                        "definition applies and is not reconciled with on-chain issuance."
                    )
                    audit_event("coingecko_supply_fetched", subject_id=subject_id,
                                source=gecko_source.source_id)
            except Exception as exc:
                audit_event("coingecko_supply_failed", subject_id=subject_id, error_type=type(exc).__name__)
                context_limitations.append(
                    f"CoinGecko supply request failed ({type(exc).__name__}); supply/market cap stay MISSING."
                )

        basis = mark - spot
        return CryptoMarketContext(
            subject=subject,
            as_of=max(observed_times),
            spot_price=available(
                ObservationName.SPOT_PRICE,
                spot,
                "USDT",
                observed_times[4],
            ),
            perpetual_mark_price=available(
                ObservationName.PERPETUAL_MARK_PRICE, mark, "USDT", observed_times[0]
            ),
            index_price=available(
                ObservationName.INDEX_PRICE, index, "USDT", observed_times[1]
            ),
            funding_rate=available(
                ObservationName.FUNDING_RATE, funding, "fraction", observed_times[2]
            ),
            open_interest=available(
                ObservationName.OPEN_INTEREST,
                open_interest,
                subject.symbol,
                observed_times[3],
            ),
            basis_absolute=available(
                ObservationName.BASIS_ABSOLUTE, basis, "USDT", max(observed_times)
            ) if abs((observed_times[0]-observed_times[4]).total_seconds()) <= 5 else missing(ObservationName.BASIS_ABSOLUTE, 'USDT').model_copy(update={'missing_reason':'SPOT_MARK_TIMESTAMP_SKEW_GT_5_SECONDS'}),
            basis_rate=available(
                ObservationName.BASIS_RATE, basis / spot, "fraction", max(observed_times)
            ) if abs((observed_times[0]-observed_times[4]).total_seconds()) <= 5 else missing(ObservationName.BASIS_RATE, 'fraction').model_copy(update={'missing_reason':'SPOT_MARK_TIMESTAMP_SKEW_GT_5_SECONDS'}),
            liquidation_notional_observed=missing(
                ObservationName.LIQUIDATION_NOTIONAL_OBSERVED, "USDT"
            ),
            supply=supply_observations,
            sources=[*spot_page.sources, context_source, *extra_sources],
            limitations=context_limitations,
        )

    @staticmethod
    def _bundle_evidence(bundle: IndicatorBundle) -> list[Evidence]:
        evidence = [item for indicator in bundle.indicators for item in indicator.evidence]
        evidence.extend(bundle.support_resistance.evidence)
        evidence.extend(bundle.trend_structure.evidence)
        evidence.append(
            Evidence(
                evidence_id=f"ev_vpvr_{bundle.vpvr.vpvr_id}",
                claim=f"Estimated VPVR POC is {bundle.vpvr.poc}.",
                rule=bundle.vpvr.algorithm_version,
                observations=[
                    EvidenceObservation(
                        ref_type=EvidenceRefType.VPVR,
                        ref_id=bundle.vpvr.vpvr_id,
                        field=field,
                        value=getattr(bundle.vpvr, field),
                        unit="price",
                        observed_at=bundle.vpvr.window_end,
                    ) for field in ("poc", "vah", "val")
                ],
                source_refs=bundle.vpvr.source_refs,
                quality="LOW" if bundle.vpvr.is_estimate else "HIGH",
            )
        )
        if bundle.fvg:
            latest = bundle.fvg[-1]
            evidence.append(
                Evidence(
                    evidence_id=f"ev_fvg_{latest.fvg_id}",
                    claim=f"Latest {latest.direction.value} FVG fill={latest.fill_status.value}, validity={latest.validity_status.value}.",
                    rule=latest.creation_rule_version,
                    observations=[
                        EvidenceObservation(
                            ref_type=EvidenceRefType.FVG,
                            ref_id=latest.fvg_id,
                            field="midpoint",
                            value=latest.midpoint,
                            unit="price",
                            observed_at=latest.created_at,
                        )
                    ],
                    source_refs=latest.source_refs,
                    quality="MEDIUM",
                )
            )
        for divergence in bundle.divergences[-3:]:
            evidence.append(
                Evidence(
                    evidence_id=f"ev_divergence_{divergence.divergence_id}",
                    claim=(
                        f"Confirmed {divergence.kind.value} divergence on "
                        f"{divergence.oscillator_component.value}."
                    ),
                    rule=divergence.algorithm_version,
                    observations=[
                        EvidenceObservation(
                            ref_type=EvidenceRefType.DIVERGENCE,
                            ref_id=divergence.divergence_id,
                            field=f"price_anchor_{index + 1}",
                            value=anchor.value,
                            unit="price",
                            observed_at=anchor.at,
                        )
                        for index, anchor in enumerate(divergence.price_anchors)
                    ],
                    source_refs=divergence.source_refs,
                    quality="HIGH" if not divergence.confirmation.can_repaint else "LOW",
                )
            )
        return evidence

    def get_indicator_evidence(self, subject_id, interval, cutoff_at=None, instrument_id=None) -> IndicatorEvidenceView:
        subject = self.get_subject(subject_id)
        instrument_id = instrument_id or subject.primary_instrument_id
        page = self.get_bars(instrument_id, interval, cutoff_at, 5000, None)
        cutoff = page.bars[-1].close_at
        bundle = self.get_indicator_bundle(subject_id, interval, cutoff, instrument_id)
        return IndicatorEvidenceView(
            subject_id=subject_id, instrument=page.instrument, interval=interval, cutoff_at=cutoff,
            latest_bar=page.bars[-1], quality=page.quality, sources=page.sources,
            evidence=self._bundle_evidence(bundle),
            limitations=["Compact view omits historical indicator series; full bundle is available separately.",
                         "VPVR is K-line uniform range allocation, not trade-level volume profile.",
                         "No derivatives/fundamentals are included; historical coverage must be checked in quality.",
                         "Historical pattern evidence need not be active at cutoff; no calibrated prediction accuracy."],
        )

    def create_snapshot(self, request: SnapshotRequest) -> AnalysisSnapshot:
        from app.models import TimeframeCoverage
        subject = self.get_subject(request.subject_id)
        context = None
        context_error = None
        # Collect public context BEFORE freezing cutoff. Historical requests do not fetch live context.
        if subject.subject_type is SubjectType.CRYPTO_ASSET and request.cutoff_at is None:
            try:
                context = self.get_market_context(subject.subject_id)
            except DataUnavailableError as exc:
                context_error = str(exc)
        if request.cutoff_at is not None:
            cutoff = request.cutoff_at
        elif settings.data_mode == "live":
            # A live snapshot is frozen at collection time. Fixture bars are never
            # used as a clock in live mode.
            cutoff = datetime.now(UTC)
        else:
            cutoff = BARS[subject.primary_instrument_id][-1].close_at
        instrument_ids = [subject.primary_instrument_id]
        if request.include_perpetual:
            instrument_ids.extend(i.instrument_id for i in subject.instruments if i.contract_type == 'USDT_PERPETUAL')
        pages, bundles, coverage = [], [], []
        for instrument_id in instrument_ids:
            for interval in dict.fromkeys(request.intervals):
                role = 'BACKGROUND' if interval in (Interval.WEEK_1, Interval.MONTH_1) else 'STRUCTURE' if interval is Interval.DAY_1 else 'SIGNAL'
                try:
                    page = self.get_bars(instrument_id, interval, cutoff, 5000, None)
                    bundle = self.get_indicator_bundle(subject.subject_id, interval, cutoff, instrument_id)
                    pages.append(page); bundles.append(bundle)
                    coverage.append(TimeframeCoverage(instrument_id=instrument_id, interval=interval, role=role,
                        bars_as_of=bundle.bars_as_of,status='AVAILABLE',history_complete=page.quality.history_complete,
                        evidence_ids=[e.evidence_id for e in self._bundle_evidence(bundle)],missing_reason=None))
                except DataUnavailableError as exc:
                    coverage.append(TimeframeCoverage(instrument_id=instrument_id, interval=interval, role=role,
                        bars_as_of=None,status='MISSING',history_complete=False,evidence_ids=[],missing_reason=str(exc)))
        if not pages:
            raise DataUnavailableError('All requested timeframes unavailable; no synthetic fallback')
        evidence = [item for bundle in bundles for item in self._bundle_evidence(bundle)]
        fundamentals = self.get_fundamentals(subject.subject_id) if subject.subject_type is SubjectType.EQUITY else None
        sources_by_id = {
            source.source_id: source
            for source in [
                *(source for page in pages for source in page.sources),
                *(fundamentals.sources if fundamentals else []),
                *(context.sources if context else []),
            ]
        }
        missing = [item for bundle in bundles for item in bundle.missing_items]
        missing.extend(f'{c.instrument_id}/{c.interval.value}: {c.missing_reason}' for c in coverage if c.status=='MISSING')
        if context_error: missing.append(context_error)
        if context is None: missing.append('Market context missing at cutoff; no historical reconstruction inferred')
        for page in pages:
            if not page.quality.history_complete:
                missing.append(f"{page.interval.value}: requested history incomplete; actual start {page.quality.actual_start_at}")
            if page.quality.missing_bar_count:
                missing.append(f"{page.interval.value}: {page.quality.missing_bar_count} internal missing bars")
            if page.quality.stale_reason:
                missing.append(f"{page.interval.value}: stale source: {page.quality.stale_reason}")
        if context and context.as_of > cutoff:
            # A live context fetch occurs after the bar cutoff. Never leak it into
            # the frozen analysis or pretend asynchronously sampled basis is synchronous.
            context = None
            sources_by_id = {s.source_id: s for page in pages for s in page.sources}
            missing.append("Derivatives context omitted: collection is not synchronized with snapshot cutoff")
        if fundamentals:
            missing.extend(fundamentals.missing_items)
        snapshot_id = f"snapshot-{uuid.uuid4().hex}"
        created = datetime.now(UTC)
        snapshot = AnalysisSnapshot(
            scenario_rules=freeze_scenario_rules(cutoff,bundles,subject.primary_instrument_id),
            snapshot_id=snapshot_id,
            snapshot_sha256="0" * 64,
            subject=subject,
            cutoff_at=cutoff,
            created_at=created,
            bars=pages,
            indicator_bundles=bundles,
            fundamentals=fundamentals,
            market_context=context,
            evidence=evidence,
            sources=list(sources_by_id.values()),
            missing_items=sorted(set(missing)),
            timeframe_coverage=coverage,
        )
        payload = snapshot.model_dump(mode="json")
        payload["snapshot_sha256"] = "0" * 64
        snapshot = snapshot.model_copy(update={"snapshot_sha256": sha256_json(payload)})
        self.store.save_snapshot(snapshot)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.snapshot_dir / f"{snapshot.snapshot_id}.json"
        path.write_text(canonical_json(snapshot.model_dump(mode="json")), encoding="utf-8")
        audit_event(
            "snapshot_created",
            snapshot_id=snapshot.snapshot_id,
            subject_id=subject.subject_id,
            cutoff_at=cutoff.isoformat(),
            snapshot_sha256=snapshot.snapshot_sha256,
        )
        return snapshot

    @staticmethod
    def snapshot_ref(snapshot: AnalysisSnapshot) -> SnapshotRef:
        completeness = Completeness.PARTIAL if snapshot.missing_items else Completeness.COMPLETE_FOR_SCOPE
        return SnapshotRef(
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=snapshot.snapshot_sha256,
            subject_id=snapshot.subject.subject_id,
            cutoff_at=snapshot.cutoff_at,
            created_at=snapshot.created_at,
            completeness=completeness,
            missing_items=snapshot.missing_items,
        )

    def get_snapshot(self, snapshot_id: str) -> AnalysisSnapshot:
        try:
            result = self.store.get_snapshot(snapshot_id)
        except ValidationError as exc:
            audit_event("snapshot_unreadable", snapshot_id=snapshot_id, error_type=type(exc).__name__)
            raise SnapshotSchemaError(
                f"stored snapshot {snapshot_id} cannot be read by the current schema version; "
                "it is retained on disk and is not rewritten"
            ) from None
        if not result:
            raise NotFoundError(f"unknown snapshot: {snapshot_id}")
        return result

    def create_task(
        self, request: AiAnalysisTaskRequest, idempotency_key: str
    ) -> tuple[AiAnalysisTask, bool]:
        self.get_snapshot(request.snapshot_id)
        task = AiAnalysisTask(
            task_id=f"task-{uuid.uuid4().hex}",
            status=AiTaskStatus.QUEUED,
            snapshot_id=request.snapshot_id,
            created_at=datetime.now(UTC),
            started_at=None,
            completed_at=None,
            analysis_id=None,
            error=None,
        )
        return self.store.create_or_get_task(
            task, idempotency_key, sha256_json(request.model_dump(mode="json"))
        )

    def _generate_analysis(self, snapshot: AnalysisSnapshot, request: AiAnalysisTaskRequest) -> AiAnalysisResult:
        if request.provider == "CODEX_CLI":
            from app.model_provider import invoke, ProviderError, ProviderValidationError
            try:
                output, audit = invoke(snapshot, request.model, request.prompt_version)
            except ProviderValidationError as exc:
                raise AnalysisValidationError(str(exc)) from None
            except ProviderError as exc:
                raise ModelProviderUnavailableError(str(exc)) from None
            analysis = AiAnalysisResult(
                analysis_id=f"analysis-{uuid.uuid4().hex}",
                snapshot=self.snapshot_ref(snapshot), subject=snapshot.subject,
                audit=audit, **output.model_dump())
            self._validate_analysis(analysis, snapshot)
            return analysis
        started = datetime.now(UTC)
        evidence_ids = [item.evidence_id for item in snapshot.evidence]
        if not evidence_ids:
            raise AnalysisValidationError("snapshot contains no evidence")
        bundle = snapshot.indicator_bundles[0]
        vpvr_evidence = next(item for item in evidence_ids if item.startswith("ev_vpvr_"))
        level_specs = [
            ("level_val", KeyLevelKind.VAL, bundle.vpvr.val),
            ("level_poc", KeyLevelKind.POC, bundle.vpvr.poc),
            ("level_vah", KeyLevelKind.VAH, bundle.vpvr.vah),
        ]
        currency = snapshot.subject.instruments[0].quote_currency
        levels = [
            KeyLevel(
                level_id=level_id,
                kind=kind,
                price=price,
                raw_price_equivalent=price if bundle.indicators[0].input_price_basis is PriceBasis.RAW else None,
                price_basis=bundle.indicators[0].input_price_basis,
                currency=currency,
                timeframe=bundle.interval,
                evidence_ids=[vpvr_evidence],
                confirmed=True,
            )
            for level_id, kind, price in level_specs
        ]
        shared = evidence_ids[: min(4, len(evidence_ids))]
        latest_close = snapshot.bars[0].bars[-1].close if snapshot.bars and snapshot.bars[0].bars else bundle.vpvr.poc
        poc_s, vah_s, val_s = bundle.vpvr.poc, bundle.vpvr.vah, bundle.vpvr.val
        trend_s = bundle.trend_structure.state.value
        scenarios = [
            Scenario(
                type=ScenarioType.BULLISH,
                summary=(
                    f"【看多突破/修复】当前收盘 {latest_close}（{bundle.interval.value} 结构 {trend_s}）："
                    f"若价格守住估算价值区下沿 VAL ({val_s}) 与主峰 POC ({poc_s})，并放量突破 VAH ({vah_s})，则打开向下一筹码峰上攻空间。"
                ),
                preconditions=[
                    Condition(
                        description=f"价格站稳 POC ({poc_s}) 或在 VAL ({val_s})–POC ({poc_s}) 主峰区企稳并获 MACD 柱体回升确认。",
                        evidence_ids=[vpvr_evidence],
                    )
                ],
                evidence_ids=shared,
                key_level_ids=["level_val", "level_poc", "level_vah"],
                invalidation_conditions=[
                    Condition(
                        description=f"确认收盘有效跌破 VAL ({val_s}) 且动量继续恶化。",
                        evidence_ids=shared[:1],
                    )
                ],
            ),
            Scenario(
                type=ScenarioType.NEUTRAL,
                summary=(
                    f"【区间震荡/筹码换手】若价格继续在 VAL ({val_s}) 至 VAH ({vah_s}) 之间围绕 POC ({poc_s}) 窄幅换手，"
                    "大小周期动量尚未形成单边共振，则维持区间震荡判断。"
                ),
                preconditions=[
                    Condition(
                        description=f"收盘价维持在 VAL ({val_s}) 与 VAH ({vah_s}) 价值区内围绕 POC ({poc_s}) 波动。",
                        evidence_ids=[vpvr_evidence],
                    )
                ],
                evidence_ids=shared,
                key_level_ids=["level_poc"],
                invalidation_conditions=[
                    Condition(
                        description=f"确认放量突破 VAH ({vah_s}) 或跌破 VAL ({val_s}) 离开当前价值区。",
                        evidence_ids=[vpvr_evidence],
                    )
                ],
            ),
            Scenario(
                type=ScenarioType.BEARISH,
                summary=(
                    f"【看空受阻/回撤】若价格在 VAH ({vah_s}) 或 POC ({poc_s}) 持续受阻并跌破 VAL ({val_s})，"
                    "则将穿过下方低量区（LVN）向下一级筹码峰寻底。"
                ),
                preconditions=[
                    Condition(
                        description=f"价格确认跌破估算 VAL ({val_s}) 或在 POC ({poc_s}) 下方形成空头排列。",
                        evidence_ids=[vpvr_evidence],
                    )
                ],
                evidence_ids=shared,
                key_level_ids=["level_val", "level_poc"],
                invalidation_conditions=[
                    Condition(
                        description=f"价格重新收复 POC ({poc_s}) 并站稳 VAH ({vah_s}) 上方。",
                        evidence_ids=[vpvr_evidence],
                    )
                ],
            ),
        ]
        missing = [
            MissingInformation(
                field=item,
                reason_code=MissingReason.NOT_CONFIGURED,
                impact=Impact.MEDIUM,
                detail=item,
            )
            for item in snapshot.missing_items
        ]
        finished = datetime.now(UTC)
        provisional = {
            "snapshot_id": snapshot.snapshot_id,
            "subject_id": snapshot.subject.subject_id,
            "scenarios": [item.model_dump(mode="json") for item in scenarios],
            "provider": request.provider,
            "model": request.model,
        }
        analysis = AiAnalysisResult(
            analysis_id=f"analysis-{uuid.uuid4().hex}",
            snapshot=self.snapshot_ref(snapshot),
            subject=snapshot.subject,
            scenarios=scenarios,
            key_levels=levels,
            conflicts=[],
            missing_information=missing,
            confidence=Confidence(
                score=0.35 if settings.data_mode == "fixture" else 0.60,
                basis="Evidence completeness and cross-timeframe consistency; not a probability of profit.",
                limitations=[
                    "The score is not calibrated against out-of-sample trading outcomes.",
                    "Fixture data limits evidence quality." if settings.data_mode == "fixture" else "Free-source coverage may be incomplete.",
                    "VPVR is a K-line range-allocation estimate, not trade-level volume profile.",
                ],
            ),
            disclaimer=Disclaimer(
                data_limitations="Data may be delayed, incomplete, venue-specific, or synthetic; this is not investment advice."
            ),
            audit=AnalysisAudit(
                prompt_version=request.prompt_version,
                provider=request.provider,
                model=request.model,
                model_revision="1",
                started_at=started,
                finished_at=finished,
                input_tokens=None,
                output_tokens=None,
                cost="0",
                cost_currency="USD",
                raw_response_sha256=sha256_json(provisional),
            ),
        )
        self._validate_analysis(analysis, snapshot)
        return analysis

    @staticmethod
    def _validate_analysis(analysis: AiAnalysisResult, snapshot: AnalysisSnapshot) -> None:
        if not is_demo_provider(analysis.audit.provider) and len(snapshot.timeframe_coverage)>1:
            expected={(c.instrument_id,c.interval):c for c in snapshot.timeframe_coverage}
            actual={(c.instrument_id,c.interval):c for c in analysis.timeframe_synthesis}
            if len(actual)!=len(analysis.timeframe_synthesis) or set(actual)!=set(expected):
                raise AnalysisValidationError('Multi-timeframe answer must cover every requested instrument/timeframe, including missing entries')
            for key,c in actual.items():
                source=expected[key]
                if c.role!=source.role or c.status!=source.status or not set(c.evidence_ids)<=set(source.evidence_ids):
                    raise AnalysisValidationError('Timeframe evidence/role/status mismatch')
                if c.status=='AVAILABLE' and not c.evidence_ids:
                    raise AnalysisValidationError('Available timeframe assessment requires its own evidence')
        if not is_demo_provider(analysis.audit.provider) and snapshot.scenario_rules:
            rules={r.scenario:r.rule_id for r in snapshot.scenario_rules}
            if any(s.evaluation_rule_id!=rules.get(s.type.value) for s in analysis.scenarios):
                raise AnalysisValidationError('Scenario must reference its pre-analysis frozen evaluation rule')
        schema = AiAnalysisResult.model_json_schema(mode="serialization")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(
            validator.iter_errors(analysis.model_dump(mode="json")),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            raise AnalysisValidationError(errors[0].message)
        known_evidence = {item.evidence_id for item in snapshot.evidence}
        cited = {
            evidence_id
            for scenario in analysis.scenarios
            for evidence_id in scenario.evidence_ids
        }
        cited |= {
            evidence_id
            for scenario in analysis.scenarios
            for condition in [*scenario.preconditions, *scenario.invalidation_conditions]
            for evidence_id in condition.evidence_ids
        }
        cited |= {evidence_id for level in analysis.key_levels for evidence_id in level.evidence_ids}
        cited |= {evidence_id for conflict in analysis.conflicts for evidence_id in conflict.evidence_ids}
        if not cited <= known_evidence:
            raise AnalysisValidationError(f"unknown evidence IDs: {sorted(cited - known_evidence)}")
        known_levels = {item.level_id for item in analysis.key_levels}
        if len(known_levels) != len(analysis.key_levels):
            raise AnalysisValidationError("duplicate key level IDs")
        evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
        for level in analysis.key_levels:
            observed_prices = {Decimal(o.value) for eid in level.evidence_ids
                               for o in evidence_by_id[eid].observations
                               if o.unit == "price" and o.value is not None}
            if Decimal(level.price) not in observed_prices:
                raise AnalysisValidationError(f"key level {level.level_id} price not supported by cited evidence")
            required_field = {KeyLevelKind.POC: "poc", KeyLevelKind.VAH: "vah", KeyLevelKind.VAL: "val",
                              KeyLevelKind.FVG_MIDPOINT: "midpoint"}.get(level.kind)
            if required_field and not any(o.field == required_field and o.value is not None
                    and Decimal(o.value) == Decimal(level.price) for eid in level.evidence_ids
                    for o in evidence_by_id[eid].observations):
                raise AnalysisValidationError(f"key level {level.level_id} kind does not match observation")
            if level.kind is KeyLevelKind.FVG_BOUNDARY and not any(
                    o.field in {"lower", "upper"} and o.value is not None and Decimal(o.value) == Decimal(level.price)
                    for eid in level.evidence_ids for o in evidence_by_id[eid].observations):
                raise AnalysisValidationError("FVG boundary must cite an actual lower or upper boundary")
        cited_levels = {level_id for scenario in analysis.scenarios for level_id in scenario.key_level_ids}
        if not cited_levels <= known_levels:
            raise AnalysisValidationError(f"unknown key level IDs: {sorted(cited_levels - known_levels)}")
        if not is_demo_provider(analysis.audit.provider):
            from app.semantic_review import review_analysis
            semantic = review_analysis(analysis, snapshot)
            if semantic.issues:
                raise AnalysisValidationError('Semantic check: ' + '; '.join(i.code+': '+i.detail for i in semantic.issues))

    @staticmethod
    def _equity_maturity(cutoff: datetime, trading_minutes: int) -> datetime:
        """US RTH horizons count real XNYS trading minutes only.

        Uses the exchange calendar, so holidays, weekends and the afternoon of a
        half day are never counted; the earlier hand-written 09:30-16:00 walk
        counted a holiday's full session and a half-day afternoon as trading time.
        """
        return maturity_after_trading_minutes(cutoff, trading_minutes)

    def _pending_outcomes(self, analysis: AiAnalysisResult, snapshot: AnalysisSnapshot) -> list[AnalysisOutcome]:
        start_price = snapshot.bars[0].bars[-1].close
        is_equity = snapshot.subject.subject_type is SubjectType.EQUITY
        specs = [
            (OutcomeHorizon.HOUR_1, 60, timedelta(hours=1)),
            (OutcomeHorizon.HOUR_4, 240, timedelta(hours=4)),
            (OutcomeHorizon.DAY_1, 390, timedelta(days=1)),
            (OutcomeHorizon.WEEK_1, 1950, timedelta(days=7)),
        ]
        outcomes = []
        for horizon, minutes, duration in specs:
            matures = self._equity_maturity(snapshot.cutoff_at, minutes) if is_equity else snapshot.cutoff_at + duration
            outcomes.append(
                AnalysisOutcome(
                    outcome_id=f"outcome-{analysis.analysis_id}-{horizon.value}",
                    analysis_id=analysis.analysis_id,
                    horizon=horizon,
                    status=OutcomeStatus.PENDING,
                    matures_at=matures,
                    reference_rule="US_RTH_TRADING_MINUTES_V1" if is_equity else "CRYPTO_ELAPSED_UTC_V1",
                    start_price=start_price,
                    end_price=None,
                    return_fraction=None,
                    maximum_favorable_excursion=None,
                    maximum_adverse_excursion=None,
                    computed_at=None,
                    source_refs=[snapshot.bars[0].sources[0].source_id],
                    unavailable_reason=None,
                )
            )
        return outcomes

    def run_task(self, task_id: str, request: AiAnalysisTaskRequest) -> None:
        task = self.store.get_task(task_id)
        if task is None or task.status is not AiTaskStatus.QUEUED:
            return
        task = task.model_copy(update={"status": AiTaskStatus.RUNNING, "started_at": datetime.now(UTC)})
        self.store.update_task(task)
        try:
            snapshot = self.get_snapshot(task.snapshot_id)
            analysis = self._generate_analysis(snapshot, request)
            self.store.save_analysis(analysis)
            self.store.save_outcomes(self._pending_outcomes(analysis, snapshot))
            task = task.model_copy(
                update={
                    "status": AiTaskStatus.SUCCEEDED,
                    "completed_at": datetime.now(UTC),
                    "analysis_id": analysis.analysis_id,
                }
            )
            audit_event(
                "ai_analysis_succeeded",
                task_id=task.task_id,
                analysis_id=analysis.analysis_id,
                snapshot_id=snapshot.snapshot_id,
                provider=request.provider,
                model=request.model,
                cost=analysis.audit.cost,
                input_tokens=analysis.audit.input_tokens,
                output_tokens=analysis.audit.output_tokens,
            )
        except AnalysisValidationError as exc:
            task = self._failed_task(task, AiTaskStatus.FAILED_VALIDATION, ErrorCode.ANALYSIS_VALIDATION_FAILED, str(exc))
        except ModelProviderUnavailableError as exc:
            task = self._failed_task(
                task,
                AiTaskStatus.FAILED_PROVIDER,
                ErrorCode.PROVIDER_ERROR,
                str(exc),
            )
        except Exception as exc:  # fail closed without persisting secret-bearing exception text
            audit_event(
                "ai_analysis_internal_error",
                task_id=task.task_id,
                error_type=type(exc).__name__,
            )
            task = self._failed_task(
                task,
                AiTaskStatus.FAILED_INTERNAL,
                ErrorCode.INTERNAL_ERROR,
                "Unexpected internal error; inspect the audit trace.",
            )
        self.store.update_task(task)

    def recover_interrupted_tasks(self) -> list[str]:
        """Fail closed on tasks orphaned by a restart.

        FastAPI background tasks only run inside the process that accepted the
        request, so a QUEUED/RUNNING row found at startup can never be resumed.
        Local policy performs no automatic retry and never re-invokes a model;
        the caller must create a new task with a new idempotency key.
        """
        recovered: list[str] = []
        for task in self.store.list_tasks_by_status(
            (AiTaskStatus.QUEUED.value, AiTaskStatus.RUNNING.value)
        ):
            interrupted = self._failed_task(
                task,
                AiTaskStatus.FAILED_INTERNAL,
                ErrorCode.INTERNAL_ERROR,
                "Analysis task was interrupted by a service restart before completion; "
                "no automatic retry was performed.",
            )
            self.store.update_task(interrupted)
            audit_event(
                "task_recovered_after_restart",
                task_id=task.task_id,
                previous_status=task.status.value,
            )
            recovered.append(task.task_id)
        return recovered

    @staticmethod
    def _failed_task(task: AiAnalysisTask, status: AiTaskStatus, code: ErrorCode, detail: str) -> AiAnalysisTask:
        trace_id = f"trace-{uuid.uuid4().hex}"
        audit_event("ai_analysis_failed", task_id=task.task_id, code=code.value, trace_id=trace_id)
        return task.model_copy(
            update={
                "status": status,
                "completed_at": datetime.now(UTC),
                "error": ProblemDetail(
                    type=f"urn:amw:error:{code.value.lower()}",
                    title="AI analysis failed",
                    status=422 if status is AiTaskStatus.FAILED_VALIDATION else 500,
                    code=code,
                    detail=detail,
                    trace_id=trace_id,
                ),
            }
        )

    def get_task(self, task_id: str) -> AiAnalysisTask:
        task = self.store.get_task(task_id)
        if not task:
            raise NotFoundError(f"unknown task: {task_id}")
        return task

    def get_analysis(self, analysis_id: str) -> AiAnalysisResult:
        analysis = self.store.get_analysis(analysis_id)
        if not analysis:
            raise NotFoundError(f"unknown analysis: {analysis_id}")
        return analysis

    def list_analyses(self, limit: int, cursor: str | None) -> AnalysisPage:
        return self.store.list_analyses(limit, cursor)

    def list_outcomes(self, analysis_id: str, limit: int, cursor: str | None) -> OutcomePage:
        self.get_analysis(analysis_id)
        return self.store.list_outcomes(analysis_id, limit, cursor)

    def list_scenario_outcomes(self, analysis_id: str, limit: int, cursor: str | None) -> ScenarioOutcomePage:
        """Scenario rule states live in a separate table and never feed analysis input."""
        analysis = self.get_analysis(analysis_id)
        snapshot = self.get_snapshot(analysis.snapshot.snapshot_id)
        items, has_more, next_cursor = self.store.list_scenario_states(analysis_id, limit, cursor)
        return ScenarioOutcomePage(
            registration_status=(
                'PLATFORM_RULES_NOT_MODEL_HIT_RATE' if snapshot.scenario_rules else 'NOT_PRE_REGISTERED'
            ),
            items=items,
            page=PageMeta(limit=limit, cursor=cursor, has_more=has_more, next_cursor=next_cursor),
        )


service = MarketWorkbenchService()
