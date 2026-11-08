from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.instruments import EQUITY_SUBJECTS
from app.models import (
    AccountingBasis,
    AnalysisSubject,
    AssetClass,
    Bar,
    Completeness,
    CryptoMarketContext,
    DataProvider,
    DataStatus,
    DelayClass,
    EquityFundamentals,
    FilingRef,
    FundamentalFact,
    FundamentalMetricName,
    InstrumentRef,
    Interval,
    MarketObservation,
    MarketScope,
    ObservationName,
    PriceBasis,
    QualityFlag,
    SessionType,
    SourceEnvelope,
    SubjectType,
)


NY = ZoneInfo("America/New_York")


def decimal_string(value: float, places: int = 8) -> str:
    rendered = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return rendered if rendered not in {"", "-0"} else "0"


def _source(
    source_id: str,
    dataset: str,
    timezone: str,
    price_basis: PriceBasis | None = None,
) -> SourceEnvelope:
    stamp = datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
    digest = hashlib.sha256(f"{source_id}:{dataset}:v1".encode()).hexdigest()
    return SourceEnvelope(
        source_id=source_id,
        provider=DataProvider.FIXTURE_SYNTHETIC,
        dataset=dataset,
        endpoint_id="local://fixture",
        source_at=stamp,
        received_at=stamp,
        delay_class=DelayClass.SYNTHETIC,
        market_scope=MarketScope.SYNTHETIC_TEST,
        completeness=Completeness.COMPLETE_FOR_SCOPE,
        is_full_market=False,
        price_basis=price_basis,
        timezone=timezone,
        terms_version="fixture-v1",
        raw_sha256=digest,
        limitations=[
            "Synthetic fixture for deterministic tests; not observed market data.",
            "Must never be presented as current, historical, or tradable price information.",
        ],
    )


AAPL = InstrumentRef(
    instrument_id="US:AAPL",
    asset_class=AssetClass.US_EQUITY,
    symbol="AAPL",
    venue="NASDAQ",
    quote_currency="USD",
    timezone="America/New_York",
    contract_type="COMMON_STOCK",
)
BTC_SPOT = InstrumentRef(
    instrument_id="CRYPTO:BTCUSDT:SPOT",
    asset_class=AssetClass.CRYPTO_SPOT,
    symbol="BTCUSDT",
    venue="ROUTED_PUBLIC",
    quote_currency="USDT",
    timezone="UTC",
    contract_type="SPOT",
)
BTC_PERP = InstrumentRef(
    instrument_id="CRYPTO:BTCUSDT:PERP",
    asset_class=AssetClass.CRYPTO_PERPETUAL,
    symbol="BTCUSDT",
    venue="ROUTED_PUBLIC",
    quote_currency="USDT",
    timezone="UTC",
    contract_type="USDT_PERPETUAL",
)
ETH_SPOT = InstrumentRef(
    instrument_id="CRYPTO:ETHUSDT:SPOT",
    asset_class=AssetClass.CRYPTO_SPOT,
    symbol="ETHUSDT",
    venue="ROUTED_PUBLIC",
    quote_currency="USDT",
    timezone="UTC",
    contract_type="SPOT",
)
ETH_PERP = InstrumentRef(
    instrument_id="CRYPTO:ETHUSDT:PERP",
    asset_class=AssetClass.CRYPTO_PERPETUAL,
    symbol="ETHUSDT",
    venue="ROUTED_PUBLIC",
    quote_currency="USDT",
    timezone="UTC",
    contract_type="USDT_PERPETUAL",
)

SUBJECTS = {
    "us-aapl": AnalysisSubject(
        subject_id="us-aapl",
        subject_type=SubjectType.EQUITY,
        symbol="AAPL",
        primary_instrument_id=AAPL.instrument_id,
        instruments=[AAPL],
    ),
    "crypto-btc": AnalysisSubject(
        subject_id="crypto-btc",
        subject_type=SubjectType.CRYPTO_ASSET,
        symbol="BTC",
        primary_instrument_id=BTC_SPOT.instrument_id,
        instruments=[BTC_SPOT, BTC_PERP],
    ),
    "crypto-eth": AnalysisSubject(
        subject_id="crypto-eth",
        subject_type=SubjectType.CRYPTO_ASSET,
        symbol="ETH",
        primary_instrument_id=ETH_SPOT.instrument_id,
        instruments=[ETH_SPOT, ETH_PERP],
    ),
}

# The read-only catalog is the union of synthetic-fixture subjects and the real
# Magnificent Seven (plus explicitly opted-in extra equity symbols such as GOOG).
# Fixture bars exist only for the subjects above; the rest require live mode, and
# asking for them in fixture mode fails closed on purpose.
SUBJECTS = {**EQUITY_SUBJECTS, **SUBJECTS}


def _equity_bars() -> list[Bar]:
    trading_days = [date(2026, 8, 31) + timedelta(days=index) for index in range(5)]
    bars: list[Bar] = []
    previous_close = 229.5
    index = 0
    for trading_day in trading_days:
        for slot in range(26):
            local_open = datetime.combine(trading_day, time(9, 30), NY) + timedelta(minutes=15 * slot)
            open_price = previous_close
            change = 0.16 + 0.48 * math.sin(index / 4.3) - 0.22 * math.cos(index / 9.0)
            close_price = open_price + change
            high = max(open_price, close_price) + 0.28 + 0.04 * (index % 3)
            low = min(open_price, close_price) - 0.24 - 0.03 * (index % 4)
            volume = 850_000 + 75_000 * (slot % 7) + 30_000 * (index % 5)
            bars.append(
                Bar(
                    bar_id=f"bar-aapl-15m-{index:04d}",
                    instrument_id=AAPL.instrument_id,
                    interval=Interval.MIN_15,
                    open_at=local_open.astimezone(UTC),
                    close_at=(local_open + timedelta(minutes=15)).astimezone(UTC),
                    open=decimal_string(open_price, 4),
                    high=decimal_string(high, 4),
                    low=decimal_string(low, 4),
                    close=decimal_string(close_price, 4),
                    volume=decimal_string(volume, 0),
                    trade_count=2_000 + index * 3,
                    session=SessionType.US_RTH,
                    price_basis=PriceBasis.SPLIT_ADJUSTED,
                    confirmed=True,
                    source_ref="src-fixture-aapl-bars",
                    quality_flags=[QualityFlag.SYNTHETIC_FIXTURE],
                )
            )
            previous_close = close_price
            index += 1

    # Create a deterministic bullish three-bar FVG large enough to clear 0.2 ATR.
    anchor = bars[58]
    target = bars[60]
    lower = float(anchor.high) + 1.25
    bars[60] = target.model_copy(
        update={
            "open": decimal_string(lower + 0.20, 4),
            "high": decimal_string(lower + 0.85, 4),
            "low": decimal_string(lower, 4),
            "close": decimal_string(lower + 0.55, 4),
        }
    )
    return bars


def _crypto_bars(instrument: InstrumentRef, base_price: float, premium: float) -> list[Bar]:
    start = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    bars: list[Bar] = []
    previous_close = base_price * (1 + premium)
    for index in range(192):
        open_at = start + timedelta(minutes=15 * index)
        amplitude = base_price * 0.0018
        drift = base_price * 0.00004
        close_price = previous_close + drift + amplitude * math.sin(index / 5.7)
        open_price = previous_close
        high = max(open_price, close_price) + base_price * (0.0005 + (index % 3) * 0.00008)
        low = min(open_price, close_price) - base_price * (0.00045 + (index % 4) * 0.00007)
        volume = (2_000 if base_price > 10_000 else 28_000) * (1 + (index % 8) / 10)
        bars.append(
            Bar(
                bar_id=f"bar-{instrument.instrument_id.replace(':', '-').lower()}-{index:04d}",
                instrument_id=instrument.instrument_id,
                interval=Interval.MIN_15,
                open_at=open_at,
                close_at=open_at + timedelta(minutes=15),
                open=decimal_string(open_price, 8),
                high=decimal_string(high, 8),
                low=decimal_string(low, 8),
                close=decimal_string(close_price, 8),
                volume=decimal_string(volume, 8),
                trade_count=5_000 + index * 11,
                session=SessionType.CRYPTO_24X7,
                price_basis=PriceBasis.RAW,
                confirmed=True,
                source_ref=f"src-fixture-{instrument.instrument_id.replace(':', '-').lower()}",
                quality_flags=[QualityFlag.SYNTHETIC_FIXTURE],
            )
        )
        previous_close = close_price
    return bars


BARS = {
    AAPL.instrument_id: _equity_bars(),
    BTC_SPOT.instrument_id: _crypto_bars(BTC_SPOT, 112_000, 0),
    BTC_PERP.instrument_id: _crypto_bars(BTC_PERP, 112_000, 0.0007),
    ETH_SPOT.instrument_id: _crypto_bars(ETH_SPOT, 4_250, 0),
    ETH_PERP.instrument_id: _crypto_bars(ETH_PERP, 4_250, 0.0009),
}


SOURCES = {
    "src-fixture-aapl-bars": _source(
        "src-fixture-aapl-bars", "aapl_15m_bars", "America/New_York", PriceBasis.SPLIT_ADJUSTED
    ),
    **{
        f"src-fixture-{instrument.instrument_id.replace(':', '-').lower()}": _source(
            f"src-fixture-{instrument.instrument_id.replace(':', '-').lower()}",
            f"{instrument.symbol.lower()}_{instrument.contract_type.lower()}_15m_bars",
            "UTC",
            PriceBasis.RAW,
        )
        for instrument in (BTC_SPOT, BTC_PERP, ETH_SPOT, ETH_PERP)
    },
    "src-fixture-aapl-fundamentals": _source(
        "src-fixture-aapl-fundamentals", "aapl_fundamentals", "UTC"
    ),
    "src-fixture-crypto-context": _source(
        "src-fixture-crypto-context", "crypto_market_context", "UTC"
    ),
}


def _observation(
    subject: AnalysisSubject,
    name: ObservationName,
    value: float | None,
    unit: str,
    completeness: Completeness = Completeness.COMPLETE_FOR_SCOPE,
    missing_reason: str | None = None,
) -> MarketObservation:
    return MarketObservation(
        observation_id=f"obs-{subject.symbol.lower()}-{name.value.lower()}",
        name=name,
        value=decimal_string(value, 10) if value is not None else None,
        unit=unit,
        status=DataStatus.AVAILABLE if value is not None else DataStatus.MISSING,
        as_of=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
        source_ref="src-fixture-crypto-context",
        completeness=completeness,
        missing_reason=missing_reason,
    )


def crypto_context(subject_id: str) -> CryptoMarketContext:
    subject = SUBJECTS[subject_id]
    spot_bars = BARS[subject.primary_instrument_id]
    perp = next(item for item in subject.instruments if item.asset_class is AssetClass.CRYPTO_PERPETUAL)
    perp_bars = BARS[perp.instrument_id]
    spot = float(spot_bars[-1].close)
    mark = float(perp_bars[-1].close)
    basis = mark - spot
    base_supply = 19_900_000 if subject.symbol == "BTC" else 120_700_000
    return CryptoMarketContext(
        subject=subject,
        as_of=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
        spot_price=_observation(subject, ObservationName.SPOT_PRICE, spot, "USDT"),
        perpetual_mark_price=_observation(subject, ObservationName.PERPETUAL_MARK_PRICE, mark, "USDT"),
        index_price=_observation(subject, ObservationName.INDEX_PRICE, spot * 0.9999, "USDT"),
        funding_rate=_observation(subject, ObservationName.FUNDING_RATE, 0.0001, "fraction_per_8h"),
        open_interest=_observation(subject, ObservationName.OPEN_INTEREST, 75_000 if subject.symbol == "BTC" else 950_000, subject.symbol),
        basis_absolute=_observation(subject, ObservationName.BASIS_ABSOLUTE, basis, "USDT"),
        basis_rate=_observation(subject, ObservationName.BASIS_RATE, basis / spot, "fraction"),
        liquidation_notional_observed=_observation(
            subject,
            ObservationName.LIQUIDATION_NOTIONAL_OBSERVED,
            1_250_000 if subject.symbol == "BTC" else 420_000,
            "USDT",
            Completeness.SAMPLED,
        ),
        supply=[
            _observation(subject, ObservationName.CIRCULATING_SUPPLY, base_supply, subject.symbol),
            _observation(subject, ObservationName.TOTAL_SUPPLY, base_supply * 1.01, subject.symbol),
            _observation(subject, ObservationName.MARKET_CAP, spot * base_supply, "USD"),
        ],
        sources=[SOURCES["src-fixture-crypto-context"]],
        limitations=[
            "All values are synthetic fixtures, not live market observations.",
            "Liquidation notional represents a sampled observation and is not full-market liquidation data.",
            "Funding, open interest, mark and basis are venue-specific rather than global.",
        ],
    )


def equity_fundamentals() -> EquityFundamentals:
    accepted = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    source_ref = "src-fixture-aapl-fundamentals"
    values = [
        (FundamentalMetricName.REVENUE, 410_000_000_000, "USD", AccountingBasis.GAAP),
        (FundamentalMetricName.NET_INCOME, 105_000_000_000, "USD", AccountingBasis.GAAP),
        (FundamentalMetricName.OPERATING_CASH_FLOW, 120_000_000_000, "USD", AccountingBasis.GAAP),
        (FundamentalMetricName.CAPITAL_EXPENDITURE, 11_000_000_000, "USD", AccountingBasis.GAAP),
        (FundamentalMetricName.FREE_CASH_FLOW, 109_000_000_000, "USD", AccountingBasis.GAAP),
        (FundamentalMetricName.CASH_AND_EQUIVALENTS, 31_000_000_000, "USD", AccountingBasis.GAAP),
        (FundamentalMetricName.TOTAL_DEBT, 98_000_000_000, "USD", AccountingBasis.GAAP),
        (FundamentalMetricName.DILUTED_SHARES, 15_100_000_000, "shares", AccountingBasis.GAAP),
    ]
    facts = [
        FundamentalFact(
            fact_id=f"fact-aapl-{name.value.lower()}",
            name=name,
            value=decimal_string(value, 2),
            unit=unit,
            status=DataStatus.AVAILABLE,
            accounting_basis=basis,
            fiscal_period="FY",
            period_start=date(2025, 9, 28),
            period_end=date(2026, 9, 26),
            filed_at=date(2026, 8, 1),
            accepted_at=accepted,
            source_ref=source_ref,
            missing_reason=None,
        )
        for name, value, unit, basis in values
    ]
    return EquityFundamentals(
        subject=SUBJECTS["us-aapl"],
        accepted_before=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
        facts=facts,
        filings=[
            FilingRef(
                accession_number="0000320193-26-000001",
                form="10-K",
                filed_at=date(2026, 8, 1),
                accepted_at=accepted,
                report_period=date(2026, 6, 30),
                source_url="https://www.sec.gov/Archives/fixture-only",
                source_ref=source_ref,
            )
        ],
        sources=[SOURCES[source_ref]],
        missing_items=[
            "Fixture mode does not provide a real upcoming earnings date.",
            "Valuation metrics are omitted because fixture prices are synthetic.",
            "Non-GAAP values are missing rather than inferred.",
        ],
    )


def raw_fixture_manifest_hash() -> str:
    payload = {
        key: [item.model_dump(mode="json") for item in value]
        for key, value in sorted(BARS.items())
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
