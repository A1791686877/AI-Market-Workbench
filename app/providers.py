from __future__ import annotations

import hashlib
import time as time_module
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.config import settings
from app.models import (
    AssetClass,
    Bar,
    DataProvider,
    DelayClass,
    InstrumentRef,
    Interval,
    MarketScope,
    PriceBasis,
    QualityFlag,
    RawOhlcv,
    SessionType,
    SourceEnvelope,
)
from app.safe_http import JsonResponse, ReadOnlyHttpClient
from app.trading_calendar import (
    calendar_first_session,
    calendar_last_session,
    is_complete_xnys_15m_bar,
    xnys_session_bounds,
)


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderHistoryEmpty(RuntimeError):
    """The public endpoint returned an empty page, not a client paging cap."""


class BarsProvider(Protocol):
    def fetch_15m(self, instrument: InstrumentRef, start: datetime, end: datetime) -> tuple[list[Bar], SourceEnvelope]: ...


def _source(
    *,
    source_id: str,
    provider: DataProvider,
    dataset: str,
    endpoint: str,
    source_at: datetime,
    sha256: str,
    delay: DelayClass,
    scope: MarketScope,
    price_basis: PriceBasis,
    timezone: str,
    limitations: list[str],
) -> SourceEnvelope:
    return SourceEnvelope(
        source_id=source_id,
        provider=provider,
        dataset=dataset,
        endpoint_id=endpoint,
        source_at=source_at,
        received_at=datetime.now(UTC),
        delay_class=delay,
        market_scope=scope,
        completeness="COMPLETE_FOR_SCOPE",
        is_full_market=scope is MarketScope.CONSOLIDATED_US_SIP,
        price_basis=price_basis,
        timezone=timezone,
        terms_version="verify-at-enable-time",
        raw_sha256=sha256,
        limitations=limitations,
    )


class AlpacaMarketDataProvider:
    """Market-data-only adapter; deliberately does not import an Alpaca trading SDK.

    ``feed="iex"`` is the free single-venue real-time feed; ``feed="sip"`` is the
    consolidated feed that needs a paid entitlement. This adapter never falls
    back silently between feeds: the caller picks one and the probe records what
    actually worked.
    """

    endpoint = "https://data.alpaca.markets/v2/stocks/bars"

    def __init__(self, feed: str = "iex") -> None:
        if feed not in {"iex", "sip"}:
            raise ValueError("feed must be iex or sip")
        self.feed = feed
        if feed == "sip":
            self.delay_class = DelayClass.DELAYED_15M
            self.market_scope = MarketScope.CONSOLIDATED_US_SIP
            self.scope_note = "Alpaca SIP is the consolidated tape; the free tier is delayed at least 15 minutes."
        else:
            self.delay_class = DelayClass.REALTIME_PUBLIC
            self.market_scope = MarketScope.EXCHANGE_ONLY
            self.scope_note = "Alpaca IEX is a single-venue feed (real-time, NOT consolidated); it is not the full US market."
        self.http = ReadOnlyHttpClient({"data.alpaca.markets"})

    def _fetch_pages(
        self, instrument: InstrumentRef, start: datetime, end: datetime, adjustment: str,
        timeframe: str = "15Min", max_pages: int = 10,
    ) -> tuple[dict[str, dict], list[str], bool]:
        """Fetch bars NEWEST-FIRST so a page budget can never hide the latest data.

        Measured: Alpaca returns roughly 850 rows per page regardless of limit=10000
        (15Min pages include pre/post-market bars). An ascending fetch therefore
        spends its whole page budget on the OLDEST part of a long window and the
        cache ends months in the past. Descending order with an early stop at
        ``start`` guarantees the newest bar is always present; if the budget runs
        out first, ``reached_start`` is False and the caller records that the
        window is shorter than requested (history_complete stays false).
        """
        end = min(end, datetime.now(UTC) - timedelta(minutes=15, seconds=5))
        records: dict[str, dict] = {}
        hashes: list[str] = []
        page_token: str | None = None
        reached_start = False
        for _ in range(max_pages):
            params = {
                "symbols": instrument.symbol,
                "timeframe": timeframe,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "feed": self.feed,
                "adjustment": adjustment,
                "sort": "desc",
                "limit": 10000,
            }
            if page_token:
                params["page_token"] = page_token
            response = self.http.get_json(
                self.endpoint,
                params=params,
                headers={
                    "APCA-API-KEY-ID": settings.alpaca_key_id or "",
                    "APCA-API-SECRET-KEY": settings.alpaca_secret or "",
                },
            )
            hashes.append(response.sha256)
            rows = response.payload.get("bars", {}).get(instrument.symbol, [])
            if not rows:
                break
            for row in rows:
                records[row["t"]] = row
            oldest = datetime.fromisoformat(str(rows[-1]["t"]).replace("Z", "+00:00"))
            if oldest <= start:
                reached_start = True
                break
            next_token = response.payload.get("next_page_token")
            if not next_token or next_token == page_token:
                break
            page_token = next_token
        return records, hashes, reached_start

    def fetch_15m(self, instrument: InstrumentRef, start: datetime, end: datetime) -> tuple[list[Bar], SourceEnvelope]:
        if instrument.asset_class is not AssetClass.US_EQUITY:
            raise ValueError("Alpaca stock adapter only accepts US equities")
        if not settings.alpaca_key_id or not settings.alpaca_secret:
            raise ProviderConfigurationError("Alpaca market-data credentials are not configured")
        adjusted, adjusted_hashes, adjusted_complete = self._fetch_pages(
            instrument, start, end, "split", "15Min", max_pages=10
        )
        raw, raw_hashes, _ = self._fetch_pages(instrument, start, end, "raw", "15Min", max_pages=10)
        bars = []
        for stamp, row in sorted(adjusted.items()):
            opened = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
            if not is_complete_xnys_15m_bar(opened):
                continue
            raw_row = raw.get(stamp)
            bars.append(
                Bar(
                    bar_id=f"alpaca-{instrument.symbol.lower()}-{int(opened.timestamp())}",
                    instrument_id=instrument.instrument_id,
                    interval=Interval.MIN_15,
                    open_at=opened,
                    close_at=opened + timedelta(minutes=15),
                    open=str(row["o"]), high=str(row["h"]), low=str(row["l"]), close=str(row["c"]),
                    volume=str(row["v"]), trade_count=row.get("n"), session=SessionType.US_RTH,
                    price_basis=PriceBasis.SPLIT_ADJUSTED,
                    raw_ohlcv=(
                        RawOhlcv(
                            open=str(raw_row["o"]), high=str(raw_row["h"]),
                            low=str(raw_row["l"]), close=str(raw_row["c"]),
                            volume=str(raw_row["v"]),
                        )
                        if raw_row else None
                    ),
                    confirmed=opened + timedelta(minutes=15) <= datetime.now(UTC) - timedelta(minutes=15),
                    source_ref=f"src-alpaca-{instrument.symbol.lower()}", quality_flags=[],
                )
            )
        if not bars:
            raise RuntimeError(f"Alpaca {self.feed.upper()} returned no complete XNYS 15-minute bars")
        source = _source(
            source_id=f"src-alpaca-{self.feed}-{instrument.symbol.lower()}", provider=DataProvider.ALPACA,
            dataset=f"us_equity_{self.feed}_15m", endpoint=f"GET /v2/stocks/bars feed={self.feed} adjustments=split+raw",
            source_at=max(bar.close_at for bar in bars if bar.confirmed),
            sha256=hashlib.sha256("".join([*adjusted_hashes, *raw_hashes]).encode("ascii")).hexdigest(), delay=self.delay_class,
            scope=self.market_scope, price_basis=PriceBasis.SPLIT_ADJUSTED,
            timezone="America/New_York",
            limitations=[
                self.scope_note,
                "US RTH bars are filtered against exchange_calendars XNYS sessions, including holidays, half-days and DST."
                ,"Primary OHLCV is split-adjusted; matching raw OHLCV is stored on each bar when returned by both queries.",
                (f"Requested 15m window reached back to {start.isoformat()}."
                 if adjusted_complete else
                 f"Page budget (10 x ~850 rows, newest-first) stopped before {start.isoformat()}; "
                 "the cached 15m window is shorter than requested, so history_complete stays false."),
            ],
        )
        return bars, source

    def fetch_bars(
        self, instrument: InstrumentRef, interval: Interval, start: datetime, end: datetime
    ) -> tuple[list[Bar], SourceEnvelope]:
        """Uniform entry point used by the service: daily -> fetch_daily(adjustment=all)."""
        if interval is Interval.DAY_1:
            return self.fetch_daily(instrument, start, end, "all")
        if interval is Interval.MIN_15:
            return self.fetch_15m(instrument, start, end)
        raise ValueError(f"Alpaca adapter does not serve {interval.value} directly")

    def fetch_daily(
        self, instrument: InstrumentRef, start: datetime, end: datetime, adjustment: str = "all"
    ) -> tuple[list[Bar], SourceEnvelope]:
        """Daily US equity bars. adjustment=all -> total-return adjusted, split -> split-adjusted, raw -> raw."""
        if instrument.asset_class is not AssetClass.US_EQUITY:
            raise ValueError("Alpaca stock adapter only accepts US equities")
        if not settings.alpaca_key_id or not settings.alpaca_secret:
            raise ProviderConfigurationError("Alpaca market-data credentials are not configured")
        if adjustment not in {"raw", "split", "all"}:
            raise ValueError("adjustment must be raw, split or all")
        start, end, calendar_note = clamp_to_calendar(start, end)
        if start >= end:
            raise RuntimeError(
                "requested window is outside the installed XNYS calendar coverage "
                f"({calendar_first_session().date()}..{calendar_last_session().date()})"
            )
        records, hashes, _ = self._fetch_pages(instrument, start, end, adjustment, timeframe="1Day", max_pages=6)
        if not records:
            raise RuntimeError(f"Alpaca {self.feed.upper()} returned no daily bars")
        basis = {"raw": PriceBasis.RAW, "split": PriceBasis.SPLIT_ADJUSTED,
                 "all": PriceBasis.TOTAL_RETURN_ADJUSTED}[adjustment]
        source_id = f"src-alpaca-{self.feed}-{instrument.symbol.lower()}-daily"
        bars: list[Bar] = []
        for _, row in sorted(records.items()):
            opened = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
            bounds = xnys_session_bounds(opened.date().isoformat())
            if bounds is None:
                continue
            session_open, session_close = bounds
            bars.append(
                Bar(
                    bar_id=f"alpaca-{self.feed}-{instrument.symbol.lower()}-{opened.date().isoformat()}",
                    instrument_id=instrument.instrument_id,
                    interval=Interval.DAY_1,
                    open_at=session_open,
                    close_at=session_close,
                    open=str(row["o"]), high=str(row["h"]), low=str(row["l"]), close=str(row["c"]),
                    volume=str(row["v"]), trade_count=row.get("n"),
                    session=SessionType.US_RTH,
                    price_basis=basis,
                    confirmed=session_close <= datetime.now(UTC),
                    source_ref=source_id,
                    quality_flags=[],
                )
            )
        if not bars:
            raise RuntimeError(f"Alpaca {self.feed.upper()} returned no validated XNYS daily bars")
        source = _source(
            source_id=source_id, provider=DataProvider.ALPACA,
            dataset=f"us_equity_{self.feed}_daily_{adjustment}",
            endpoint=f"GET /v2/stocks/bars timeframe=1Day feed={self.feed} adjustment={adjustment}",
            source_at=max(bar.close_at for bar in bars if bar.confirmed),
            sha256=hashlib.sha256("".join(hashes).encode("ascii")).hexdigest(),
            delay=self.delay_class, scope=self.market_scope, price_basis=basis,
            timezone="America/New_York",
            limitations=[
                self.scope_note,
                f"Daily bars use adjustment={adjustment} ({basis.value}); weekly/monthly are generated locally from these bars.",
                "Sessions are taken from exchange_calendars XNYS (holidays, half-days and DST included).",
                *([calendar_note] if calendar_note else []),
            ],
        )
        return bars, source


def clamp_to_calendar(start: datetime, end: datetime) -> tuple[datetime, datetime, str | None]:
    """Clamp a requested window to the installed XNYS calendar.

    exchange_calendars only covers e.g. 2006-09-12..2027-09-10; a provider that can
    serve deeper history (Tiingo) would otherwise crash while we validate sessions.
    Returns (start, end, note); the caller must fail clearly if the window is empty.
    """
    first, last = calendar_first_session(), calendar_last_session()
    note = None
    if start < first:
        note = (f"Requested start {start.date()} predates the installed XNYS calendar coverage "
                f"({first.date()}..{last.date()}); the window was clamped.")
        start = first
    if end > last:
        suffix = f"Requested end {end.date()} is beyond the installed XNYS calendar coverage ({last.date()})."
        note = f"{note} {suffix}" if note else suffix
        end = last
    return start, end, note


def _parse_utc_stamp(text: str) -> datetime:
    """Parse a provider timestamp that may be date-only, ISO-T or space separated."""
    value = str(text).strip()
    if " " in value:
        value = value.replace(" ", "T")
    if "T" not in value:
        value = value + "T00:00:00"
    stamp = datetime.fromisoformat(value)
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


class TwelveDataProvider:
    """Market-data-only Twelve Data adapter (free tier is rate limited: ~8 req/min, 800/day).

    Only the ``/time_series`` GET endpoint is used. Prices are recorded as RAW with
    an explicit limitation because adjustment semantics are not assumed: the probe
    checks the AAPL 2020-08-31 4:1 split boundary to decide empirically.
    """

    endpoint = "https://api.twelvedata.com/time_series"
    interval_map = {Interval.MIN_15: "15min", Interval.DAY_1: "1day"}

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({"api.twelvedata.com"})

    def fetch_raw(self, symbol: str, interval: Interval, start: datetime, end: datetime,
                  outputsize: int = 5000) -> JsonResponse:
        if not settings.twelve_data_key:
            raise ProviderConfigurationError("Twelve Data API key is not configured")
        if interval not in self.interval_map:
            raise ValueError(f"Twelve Data adapter does not support {interval.value}")
        return self.http.get_json(self.endpoint, params={
            "symbol": symbol.upper(),
            "interval": self.interval_map[interval],
            "outputsize": outputsize,
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "UTC",
            "order": "asc",
            "apikey": settings.twelve_data_key,
        })

    def fetch_bars(self, instrument: InstrumentRef, interval: Interval, start: datetime, end: datetime,
                   outputsize: int = 5000) -> tuple[list[Bar], SourceEnvelope]:
        if instrument.asset_class is not AssetClass.US_EQUITY:
            raise ValueError("Twelve Data adapter only accepts US equities")
        calendar_note = None
        if interval is Interval.DAY_1:
            start, end, calendar_note = clamp_to_calendar(start, end)
            if start >= end:
                raise RuntimeError(
                    "requested window is outside the installed XNYS calendar coverage "
                    f"({calendar_first_session().date()}..{calendar_last_session().date()})"
                )
        response = self.fetch_raw(instrument.symbol, interval, start, end, outputsize)
        payload = response.payload
        if not isinstance(payload, dict) or payload.get("status") == "error" or "values" not in payload:
            raise RuntimeError(f"Twelve Data returned no bars: {str(payload)[:200]}")
        source_id = f"src-twelve-{instrument.symbol.lower()}-{interval.value}"
        # Probe evidence (NVDA 2024-06-10 10:1 boundary, min ratio 0.9623): Twelve Data
        # DAILY bars are split-adjusted. Intraday adjustment could not be verified
        # (free-tier 15m history does not reach a split), so 15m stays labeled RAW.
        basis = PriceBasis.SPLIT_ADJUSTED if interval is Interval.DAY_1 else PriceBasis.RAW
        bars: list[Bar] = []
        for row in payload["values"]:
            opened = _parse_utc_stamp(row["datetime"])
            if interval is Interval.DAY_1:
                bounds = xnys_session_bounds(opened.date().isoformat())
                if bounds is None:
                    continue
                open_at, close_at = bounds
            else:
                if not is_complete_xnys_15m_bar(opened):
                    continue
                open_at, close_at = opened, opened + timedelta(minutes=15)
            bars.append(Bar(
                bar_id=f"twelve-{instrument.symbol.lower()}-{interval.value}-{int(open_at.timestamp())}",
                instrument_id=instrument.instrument_id,
                interval=interval,
                open_at=open_at, close_at=close_at,
                open=str(row["open"]), high=str(row["high"]), low=str(row["low"]),
                close=str(row["close"]), volume=str(row.get("volume") or 0),
                trade_count=None, session=SessionType.US_RTH, price_basis=basis,
                confirmed=close_at <= datetime.now(UTC), source_ref=source_id, quality_flags=[],
            ))
        if not bars:
            raise RuntimeError("Twelve Data returned no validated XNYS bars")
        source = _source(
            source_id=source_id, provider=DataProvider.TWELVE_DATA,
            dataset=f"us_equity_twelvedata_{interval.value}",
            endpoint=f"GET /time_series interval={self.interval_map[interval]}",
            source_at=max(bar.close_at for bar in bars if bar.confirmed),
            sha256=response.sha256, delay=DelayClass.UNKNOWN, scope=MarketScope.UNKNOWN,
            price_basis=basis, timezone="America/New_York",
            limitations=[
                ("Daily values are split-adjusted (verified on the NVDA 2024-06-10 10:1 boundary)."
                 if interval is Interval.DAY_1 else
                 "Intraday adjustment is unverified (free-tier 15m history does not reach a split); labeled RAW."),
                "Dividend adjustment is not claimed; only split behavior was tested.",
                "Consolidation scope is unknown until documented; this is not labeled a full-market feed.",
                "Free tier is rate limited (~8 requests/min, 800/day); the cache is the safety margin.",
                *([calendar_note] if calendar_note else []),
            ],
        )
        return bars, source


class AlphaVantageProvider:
    """Market-data-only Alpha Vantage adapter. Free tier is ~25 requests/day.

    ``TIME_SERIES_DAILY`` (raw OHLCV) is the supported path. The adjusted endpoint
    only returns an adjusted close without adjusted OHLC, so full adjusted OHLCV
    must come from Tiingo or Alpaca instead of being approximated here.
    """

    endpoint = "https://www.alphavantage.co/query"

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({"www.alphavantage.co"})

    def fetch_raw(self, symbol: str, *, adjusted: bool = False, outputsize: str = "compact") -> JsonResponse:
        if not settings.alpha_vantage_key:
            raise ProviderConfigurationError("Alpha Vantage API key is not configured")
        return self.http.get_json(self.endpoint, params={
            "function": "TIME_SERIES_DAILY_ADJUSTED" if adjusted else "TIME_SERIES_DAILY",
            "symbol": symbol.upper(),
            # outputsize=full is a premium feature for TIME_SERIES_DAILY (verified by probe),
            # so the free tier is limited to the last ~100 daily bars.
            "outputsize": outputsize,
            "apikey": settings.alpha_vantage_key,
        })

    def fetch_bars(
        self, instrument: InstrumentRef, interval: Interval, start: datetime, end: datetime,
        outputsize: int = 5000,
    ) -> tuple[list[Bar], SourceEnvelope]:
        if interval is not Interval.DAY_1:
            raise ValueError("Alpha Vantage adapter is EOD-only; intraday uses Alpaca or Twelve Data")
        return self.fetch_daily(instrument, start, end)

    def fetch_daily(self, instrument: InstrumentRef, start: datetime, end: datetime) -> tuple[list[Bar], SourceEnvelope]:
        if instrument.asset_class is not AssetClass.US_EQUITY:
            raise ValueError("Alpha Vantage adapter only accepts US equities")
        start, end, calendar_note = clamp_to_calendar(start, end)
        if start >= end:
            raise RuntimeError(
                "requested window is outside the installed XNYS calendar coverage "
                f"({calendar_first_session().date()}..{calendar_last_session().date()})"
            )
        response = self.fetch_raw(instrument.symbol, adjusted=False)
        payload = response.payload
        series = payload.get("Time Series (Daily)") if isinstance(payload, dict) else None
        if not isinstance(series, dict) or not series:
            message = str(
                payload.get("Note") or payload.get("Information") or payload.get("Error Message") or payload
            )[:200]
            raise RuntimeError(f"Alpha Vantage returned no daily series: {message}")
        source_id = f"src-alphavantage-{instrument.symbol.lower()}-daily"
        bars: list[Bar] = []
        for session_date in sorted(series):
            if not (start.date().isoformat() <= session_date <= end.date().isoformat()):
                continue
            bounds = xnys_session_bounds(session_date)
            if bounds is None:
                continue
            session_open, session_close = bounds
            row = series[session_date]
            bars.append(Bar(
                bar_id=f"alphavantage-{instrument.symbol.lower()}-{session_date}",
                instrument_id=instrument.instrument_id,
                interval=Interval.DAY_1,
                open_at=session_open, close_at=session_close,
                open=str(row["1. open"]), high=str(row["2. high"]), low=str(row["3. low"]),
                close=str(row["4. close"]), volume=str(row["5. volume"]),
                trade_count=None, session=SessionType.US_RTH, price_basis=PriceBasis.RAW,
                confirmed=session_close <= datetime.now(UTC), source_ref=source_id, quality_flags=[],
            ))
        if not bars:
            raise RuntimeError("Alpha Vantage returned no validated XNYS daily bars")
        source = _source(
            source_id=source_id, provider=DataProvider.ALPHA_VANTAGE,
            dataset="us_equity_alphavantage_daily_raw",
            endpoint="GET /query function=TIME_SERIES_DAILY",
            source_at=max(bar.close_at for bar in bars if bar.confirmed),
            sha256=response.sha256, delay=DelayClass.END_OF_DAY, scope=MarketScope.UNKNOWN,
            price_basis=PriceBasis.RAW, timezone="America/New_York",
            limitations=[
                "Raw split/dividend-unadjusted OHLCV; no adjusted OHLC is derived here.",
                "Consolidation scope is unknown until documented; not labeled a full-market feed.",
                "Free tier verified limits: ~25 requests/day, 1 request/second, outputsize=full is premium "
                "(only the last ~100 daily bars are available). Use Alpaca/Tiingo for long history.",
                *([calendar_note] if calendar_note else []),
            ],
        )
        return bars, source


class TiingoEodProvider:
    endpoint_template = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({"api.tiingo.com"})

    def fetch_raw(self, symbol: str, start: str, end: str) -> JsonResponse:
        if not settings.tiingo_token:
            raise ProviderConfigurationError("Tiingo token is not configured")
        return self.http.get_json(
            self.endpoint_template.format(symbol=symbol.upper()),
            params={"startDate": start, "endDate": end, "resampleFreq": "daily"},
            headers={"Authorization": f"Token {settings.tiingo_token}"},
        )

    def fetch_bars(
        self, instrument: InstrumentRef, interval: Interval, start: datetime, end: datetime
    ) -> tuple[list[Bar], SourceEnvelope]:
        if interval is not Interval.DAY_1:
            raise ValueError("Tiingo adapter is EOD-only; intraday uses Alpaca or Twelve Data")
        return self.fetch_daily(instrument, start, end)

    def fetch_daily(
        self, instrument: InstrumentRef, start: datetime, end: datetime
    ) -> tuple[list[Bar], SourceEnvelope]:
        if instrument.asset_class is not AssetClass.US_EQUITY:
            raise ValueError("Tiingo EOD adapter only accepts US equities")
        start, end, calendar_note = clamp_to_calendar(start, end)
        if start >= end:
            raise RuntimeError(
                "requested window is outside the installed XNYS calendar coverage "
                f"({calendar_first_session().date()}..{calendar_last_session().date()})"
            )
        response = self.fetch_raw(instrument.symbol, start.date().isoformat(), end.date().isoformat())
        source_id = f"src-tiingo-{instrument.symbol.lower()}-daily"
        bars: list[Bar] = []
        for row in response.payload:
            session_date = str(row["date"])[:10]
            bounds = xnys_session_bounds(session_date)
            if bounds is None:
                continue
            opened, closed = bounds
            flags = []
            if float(row.get("splitFactor", 1)) != 1:
                flags.append(QualityFlag.CORPORATE_ACTION_WINDOW)
            bars.append(
                Bar(
                    bar_id=f"tiingo-{instrument.symbol.lower()}-{session_date}",
                    instrument_id=instrument.instrument_id,
                    interval=Interval.DAY_1,
                    open_at=opened,
                    close_at=closed,
                    open=str(row["adjOpen"]), high=str(row["adjHigh"]),
                    low=str(row["adjLow"]), close=str(row["adjClose"]),
                    volume=str(row["adjVolume"]), trade_count=None,
                    session=SessionType.US_RTH,
                    price_basis=PriceBasis.TOTAL_RETURN_ADJUSTED,
                    raw_ohlcv=RawOhlcv(
                        open=str(row["open"]), high=str(row["high"]), low=str(row["low"]),
                        close=str(row["close"]), volume=str(row["volume"]),
                    ),
                    confirmed=closed <= datetime.now(UTC),
                    source_ref=source_id,
                    quality_flags=flags,
                )
            )
        if not bars:
            raise RuntimeError("Tiingo returned no validated XNYS daily bars")
        source = _source(
            source_id=source_id,
            provider=DataProvider.TIINGO,
            dataset="us_equity_eod_adjusted_and_raw",
            endpoint="GET /tiingo/daily/{symbol}/prices",
            source_at=max(bar.close_at for bar in bars if bar.confirmed),
            sha256=response.sha256,
            delay=DelayClass.END_OF_DAY,
            scope=MarketScope.UNKNOWN,
            price_basis=PriceBasis.TOTAL_RETURN_ADJUSTED,
            timezone="America/New_York",
            limitations=[
                "Adjusted OHLCV is primary and raw OHLCV is retained on each bar.",
                "Market scope remains UNKNOWN until Tiingo volume coverage is empirically documented; it is not labeled full-market.",
                "Weekly and monthly bars are generated locally from these validated daily bars.",
                *([calendar_note] if calendar_note else []),
            ],
        )
        return bars, source


_MASSIVE_MIN_INTERVAL_SECONDS = 12.5
_massive_last_call: list[float] = []


class MassiveEodProvider:
    endpoint_template = "https://api.massive.com/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({"api.massive.com"})

    @staticmethod
    def _throttle() -> None:
        """Free plans allow ~5 requests/minute; sleeping beats a 429 we cannot retry."""
        if _massive_last_call:
            remaining = _MASSIVE_MIN_INTERVAL_SECONDS - (time_module.monotonic() - _massive_last_call[0])
            if remaining > 0:
                time_module.sleep(remaining)
        _massive_last_call[:] = [time_module.monotonic()]

    def fetch_raw(self, symbol: str, start: str, end: str) -> JsonResponse:
        if not settings.massive_key:
            raise ProviderConfigurationError("Massive API key is not configured")
        self._throttle()
        return self.http.get_json(
            self.endpoint_template.format(symbol=symbol.upper(), start=start, end=end),
            params={"adjusted": "true", "sort": "asc", "limit": 5000},
            headers={"Authorization": f"Bearer {settings.massive_key}"},
        )

    def fetch_bars(self, instrument: InstrumentRef, interval: Interval, start: datetime, end: datetime):
        if interval is not Interval.DAY_1:
            raise ValueError("Massive adapter is EOD-only; intraday uses Alpaca or Twelve Data")
        return self.fetch_daily(instrument, start, end)

    def fetch_daily(self, instrument: InstrumentRef, start: datetime, end: datetime):
        """Aggregate bars (v/vw/o/h/l/c/t/n) from the Polygon-style aggregates endpoint."""
        if instrument.asset_class is not AssetClass.US_EQUITY:
            raise ValueError("Massive adapter only accepts US equities")
        if not settings.massive_key:
            raise ProviderConfigurationError("Massive API key is not configured")
        start, end, calendar_note = clamp_to_calendar(start, end)
        if start >= end:
            raise RuntimeError(
                "requested window is outside the installed XNYS calendar coverage "
                f"({calendar_first_session().date()}..{calendar_last_session().date()})"
            )
        response = self.fetch_raw(instrument.symbol, start.date().isoformat(), end.date().isoformat())
        payload = response.payload
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not rows:
            raise RuntimeError(f"Massive returned no results: {str(payload)[:200]}")
        adjusted_flag = bool(payload.get("adjusted"))
        basis = PriceBasis.SPLIT_ADJUSTED if adjusted_flag else PriceBasis.RAW
        source_id = f"src-massive-{instrument.symbol.lower()}-daily"
        bars: list[Bar] = []
        for row in rows:
            opened = datetime.fromtimestamp(int(row["t"]) / 1000, UTC)
            bounds = xnys_session_bounds(opened.date().isoformat())
            if bounds is None:
                continue
            session_open, session_close = bounds
            raw_volume = row.get("v") or 0
            volume_text = str(int(raw_volume)) if float(raw_volume).is_integer() else str(raw_volume)
            bars.append(Bar(
                bar_id=f"massive-{instrument.symbol.lower()}-{opened.date().isoformat()}",
                instrument_id=instrument.instrument_id,
                interval=Interval.DAY_1,
                open_at=session_open, close_at=session_close,
                open=str(row["o"]), high=str(row["h"]), low=str(row["l"]), close=str(row["c"]),
                volume=volume_text, trade_count=row.get("n"),
                session=SessionType.US_RTH, price_basis=basis,
                confirmed=session_close <= datetime.now(UTC), source_ref=source_id, quality_flags=[],
            ))
        if not bars:
            raise RuntimeError("Massive returned no validated XNYS daily bars")
        source = _source(
            source_id=source_id, provider=DataProvider.MASSIVE,
            dataset=f"us_equity_massive_daily_{'adjusted' if adjusted_flag else 'raw'}",
            endpoint="GET /v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}",
            source_at=max(bar.close_at for bar in bars if bar.confirmed),
            sha256=response.sha256, delay=DelayClass.DELAYED_15M, scope=MarketScope.UNKNOWN,
            price_basis=basis, timezone="America/New_York",
            limitations=[
                f"Provider reported adjusted={adjusted_flag}; the SPLIT_ADJUSTED label follows the vendor contract, "
                "because the plan window excludes every Mag-7 split boundary, so no within-window boundary was observed.",
                "Observed plan limits: requests older than ~2 years are rejected with NOT_AUTHORIZED "
                "(Polygon: 'Your plan doesn't include this data timeframe'), and the free plan allows ~5 requests/minute; "
                "the adapter therefore sleeps 12.5s between calls.",
                "Consolidation scope is not documented by the payload; never treated as a full-market feed.",
                "Third cross-check source only: not the default intraday or EOD provider.",
                *([calendar_note] if calendar_note else []),
            ],
        )
        return bars, source


class SecCompanyFactsProvider:
    endpoint_template = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    submissions_template = "https://data.sec.gov/submissions/CIK{cik}.json"

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({"data.sec.gov"})

    def fetch_raw(self, cik: int) -> JsonResponse:
        if "example.invalid" in settings.sec_user_agent:
            raise ProviderConfigurationError("a real identifying SEC_USER_AGENT is required for live SEC calls")
        return self.http.get_json(
            self.endpoint_template.format(cik=f"{cik:010d}"),
            headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
        )

    def fetch_submissions(self, cik: int) -> JsonResponse:
        """Smaller endpoint used to verify a CIK -> ticker/entity mapping."""
        if "example.invalid" in settings.sec_user_agent:
            raise ProviderConfigurationError("a real identifying SEC_USER_AGENT is required for live SEC calls")
        return self.http.get_json(
            self.submissions_template.format(cik=f"{cik:010d}"),
            headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
        )


class BinancePublicProvider:
    spot_bars_url = "https://api.binance.com/api/v3/klines"
    futures_bars_url = "https://fapi.binance.com/fapi/v1/klines"
    premium_url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    oi_url = "https://fapi.binance.com/fapi/v1/openInterest"

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({"api.binance.com", "fapi.binance.com"})

    def fetch_15m(self, instrument: InstrumentRef, start: datetime, end: datetime) -> tuple[list[Bar], SourceEnvelope]:
        if instrument.asset_class not in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERPETUAL}:
            raise ValueError("Binance adapter only accepts crypto spot/perpetual instruments")
        endpoint = self.spot_bars_url if instrument.asset_class is AssetClass.CRYPTO_SPOT else self.futures_bars_url
        response = self.http.get_json(
            endpoint,
            params={
                "symbol": instrument.symbol,
                "interval": "15m",
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": 1000,
            },
        )
        source_id = f"src-binance-{instrument.instrument_id.replace(':', '-').lower()}"
        bars = [
            Bar(
                bar_id=f"binance-{instrument.instrument_id.replace(':', '-').lower()}-{row[0]}",
                instrument_id=instrument.instrument_id,
                interval=Interval.MIN_15,
                open_at=datetime.fromtimestamp(row[0] / 1000, UTC),
                close_at=datetime.fromtimestamp((row[0] + 900_000) / 1000, UTC),
                open=str(row[1]), high=str(row[2]), low=str(row[3]), close=str(row[4]), volume=str(row[5]),
                trade_count=int(row[8]), session=SessionType.CRYPTO_24X7, price_basis=PriceBasis.RAW,
                confirmed=row[6] < int(datetime.now(UTC).timestamp() * 1000), source_ref=source_id,
                quality_flags=[],
            )
            for row in response.payload
        ]
        source = _source(
            source_id=source_id, provider=DataProvider.BINANCE, dataset="venue_15m_bars",
            endpoint="GET /api/v3/klines" if instrument.asset_class is AssetClass.CRYPTO_SPOT else "GET /fapi/v1/klines",
            source_at=end, sha256=response.sha256, delay=DelayClass.REALTIME_PUBLIC,
            scope=MarketScope.VENUE_SPECIFIC_CRYPTO, price_basis=PriceBasis.RAW, timezone="UTC",
            limitations=["Venue-specific Binance data; not a global consolidated crypto market."],
        )
        return bars, source

    def fetch_derivatives_raw(self, symbol: str) -> tuple[JsonResponse, JsonResponse]:
        return (
            self.http.get_json(self.premium_url, params={"symbol": symbol}),
            self.http.get_json(self.oi_url, params={"symbol": symbol}),
        )


class OkxPublicProvider:
    """No-key public market-data adapter for spot and USDT-margined swaps."""

    candles_url = "https://www.okx.com/api/v5/market/history-candles"
    mark_url = "https://www.okx.com/api/v5/public/mark-price"
    index_url = "https://www.okx.com/api/v5/market/index-tickers"
    funding_url = "https://www.okx.com/api/v5/public/funding-rate"
    oi_url = "https://www.okx.com/api/v5/public/open-interest"
    interval_map = {
        Interval.MIN_15: "15m",
        Interval.HOUR_1: "1H",
        Interval.HOUR_2: "2H",
        Interval.HOUR_4: "4H",
        Interval.DAY_1: "1Dutc",
    }

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({"www.okx.com"})

    @staticmethod
    def _inst_id(instrument: InstrumentRef) -> str:
        base = instrument.symbol.upper().removesuffix("USDT")
        pair = f"{base}-USDT"
        return f"{pair}-SWAP" if instrument.asset_class is AssetClass.CRYPTO_PERPETUAL else pair

    def fetch_15m(
        self, instrument: InstrumentRef, start: datetime, end: datetime
    ) -> tuple[list[Bar], SourceEnvelope]:
        return self.fetch_bars(instrument, Interval.MIN_15, start, end)

    def fetch_bars(
        self,
        instrument: InstrumentRef,
        interval: Interval,
        start: datetime,
        end: datetime,
        max_pages: int = 50,
    ) -> tuple[list[Bar], SourceEnvelope]:
        if instrument.asset_class not in {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERPETUAL}:
            raise ValueError("OKX adapter only accepts crypto spot/perpetual instruments")
        if interval not in self.interval_map:
            raise ValueError(f"OKX base interval is unsupported: {interval.value}")
        start_ms = int(start.timestamp() * 1000)
        # Window end is exclusive. Adding 1 ms re-fetches the boundary candle;
        # at the provider's oldest candle that can masquerade as a failed page.
        cursor = int(end.timestamp() * 1000)
        rows_by_time: dict[int, list] = {}
        response_hashes: list[str] = []
        provider_history_exhausted = False
        if not 1 <= max_pages <= 50:
            raise ValueError('max_pages must be within 1..50')
        for _ in range(max_pages):
            response = self.http.get_json(
                self.candles_url,
                params={
                    "instId": self._inst_id(instrument),
                    "bar": self.interval_map[interval],
                    "after": cursor,
                    "limit": 100,
                },
            )
            if response.payload.get("code") != "0":
                raise RuntimeError(f"OKX provider error: {response.payload.get('code')}")
            response_hashes.append(response.sha256)
            rows = response.payload.get("data", [])
            if not rows:
                provider_history_exhausted = True
                break
            oldest = cursor
            for row in rows:
                opened_ms = int(row[0])
                oldest = min(oldest, opened_ms)
                if start_ms <= opened_ms < int(end.timestamp() * 1000):
                    rows_by_time[opened_ms] = row
            if oldest <= start_ms or oldest >= cursor:
                break
            cursor = oldest
            time_module.sleep(0.12)
        source_id = f"src-okx-{instrument.instrument_id.replace(':', '-').lower()}-{interval.value}"
        seconds = {
            Interval.MIN_15: 900,
            Interval.HOUR_1: 3600,
            Interval.HOUR_2: 7200,
            Interval.HOUR_4: 14400,
            Interval.DAY_1: 86400,
        }[interval]
        bars = []
        for opened_ms, row in sorted(rows_by_time.items()):
            opened = datetime.fromtimestamp(opened_ms / 1000, UTC)
            bars.append(
                Bar(
                    bar_id=f"okx-{self._inst_id(instrument).lower()}-{interval.value}-{opened_ms}",
                    instrument_id=instrument.instrument_id,
                    interval=interval,
                    open_at=opened,
                    close_at=opened + timedelta(seconds=seconds),
                    open=str(row[1]),
                    high=str(row[2]),
                    low=str(row[3]),
                    close=str(row[4]),
                    volume=str(row[5] if instrument.asset_class is AssetClass.CRYPTO_SPOT else row[6]),
                    trade_count=None,
                    session=SessionType.CRYPTO_24X7,
                    price_basis=PriceBasis.RAW,
                    confirmed=str(row[8]) == "1",
                    source_ref=source_id,
                    quality_flags=[],
                )
            )
        if not bars:
            if provider_history_exhausted:
                raise ProviderHistoryEmpty('OKX endpoint returned an empty page for this requested cursor')
            raise RuntimeError("OKX returned no bars for the requested window")
        combined_hash = hashlib.sha256("".join(response_hashes).encode("ascii")).hexdigest()
        source = _source(
            source_id=source_id,
            provider=DataProvider.OKX,
            dataset=f"okx_{instrument.asset_class.value.lower()}_{interval.value}_bars",
            endpoint="GET /api/v5/market/history-candles",
            source_at=max(
                (bar.close_at for bar in bars if bar.confirmed),
                default=bars[-1].open_at,
            ),
            sha256=combined_hash,
            delay=DelayClass.REALTIME_PUBLIC,
            scope=MarketScope.VENUE_SPECIFIC_CRYPTO,
            price_basis=PriceBasis.RAW,
            timezone="UTC",
            limitations=[
                "OKX venue-specific public data; not a consolidated global crypto market.",
                "Volume normalization v2: base currency; SPOT=vol (row 5), perpetual=volCcy (row 6). Not all-exchange volume.",
                f"Retrieved {len(bars)} bars from the requested window; history completeness is reported separately.",
                (
                    "Provider history endpoint was exhausted before the requested product history target."
                    if provider_history_exhausted and bars[0].open_at > start
                    else f"Local page budget {max_pages} reached or requested start reached; not proof of provider history exhaustion."
                ),
            ],
        )
        return bars, source

    def fetch_context_raw(self, base_symbol: str) -> dict[str, JsonResponse]:
        pair = f"{base_symbol.upper()}-USDT"
        swap = f"{pair}-SWAP"
        return {
            "mark": self.http.get_json(
                self.mark_url, params={"instType": "SWAP", "instId": swap}
            ),
            "index": self.http.get_json(self.index_url, params={"instId": pair}),
            "spot": self.http.get_json('https://www.okx.com/api/v5/market/ticker', params={"instId": pair}),
            "funding": self.http.get_json(self.funding_url, params={"instId": swap}),
            "open_interest": self.http.get_json(
                self.oi_url, params={"instType": "SWAP", "instId": swap}
            ),
        }


class CoinGeckoDemoProvider:
    endpoint = "https://api.coingecko.com/api/v3/coins/markets"

    def __init__(self) -> None:
        self.http = ReadOnlyHttpClient({"api.coingecko.com"})

    def fetch_raw(self, coin_ids: str = "bitcoin,ethereum") -> JsonResponse:
        if not settings.coingecko_demo_key:
            raise ProviderConfigurationError("CoinGecko Demo API key is not configured")
        headers = {"x-cg-demo-api-key": settings.coingecko_demo_key}
        return self.http.get_json(
            self.endpoint,
            params={"vs_currency": "usd", "ids": coin_ids, "sparkline": "false"},
            headers=headers,
        )
