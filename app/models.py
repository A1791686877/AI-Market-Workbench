from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator


DecimalString = Annotated[
    str,
    StringConstraints(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$", min_length=1),
]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$", min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssetClass(str, Enum):
    US_EQUITY = "US_EQUITY"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    CRYPTO_PERPETUAL = "CRYPTO_PERPETUAL"


class SubjectType(str, Enum):
    EQUITY = "EQUITY"
    CRYPTO_ASSET = "CRYPTO_ASSET"


class Interval(str, Enum):
    MIN_15 = "15m"
    HOUR_1 = "1h"
    HOUR_2 = "2h"
    MIN_195 = "195m"
    HOUR_4 = "4h"
    DAY_1 = "1d"
    WEEK_1 = "1w"
    MONTH_1 = "1mo"


class SessionType(str, Enum):
    US_RTH = "US_RTH"
    CRYPTO_24X7 = "CRYPTO_24X7"


class PriceBasis(str, Enum):
    RAW = "RAW"
    SPLIT_ADJUSTED = "SPLIT_ADJUSTED"
    TOTAL_RETURN_ADJUSTED = "TOTAL_RETURN_ADJUSTED"


class DataProvider(str, Enum):
    FIXTURE_SYNTHETIC = "FIXTURE_SYNTHETIC"
    ALPACA = "ALPACA"
    TIINGO = "TIINGO"
    MASSIVE = "MASSIVE"
    SEC_EDGAR = "SEC_EDGAR"
    BINANCE = "BINANCE"
    OKX = "OKX"
    COINGECKO = "COINGECKO"
    TWELVE_DATA = "TWELVE_DATA"
    ALPHA_VANTAGE = "ALPHA_VANTAGE"
    FRED = "FRED"


class DelayClass(str, Enum):
    SYNTHETIC = "SYNTHETIC"
    REALTIME_PUBLIC = "REALTIME_PUBLIC"
    DELAYED_15M = "DELAYED_15M"
    END_OF_DAY = "END_OF_DAY"
    HISTORICAL = "HISTORICAL"
    UNKNOWN = "UNKNOWN"


class MarketScope(str, Enum):
    SYNTHETIC_TEST = "SYNTHETIC_TEST"
    CONSOLIDATED_US_SIP = "CONSOLIDATED_US_SIP"
    EXCHANGE_ONLY = "EXCHANGE_ONLY"
    VENUE_SPECIFIC_CRYPTO = "VENUE_SPECIFIC_CRYPTO"
    NETWORK_WIDE = "NETWORK_WIDE"
    MACRO_ECONOMY = "MACRO_ECONOMY"
    UNKNOWN = "UNKNOWN"


class Completeness(str, Enum):
    COMPLETE_FOR_SCOPE = "COMPLETE_FOR_SCOPE"
    PARTIAL = "PARTIAL"
    SAMPLED = "SAMPLED"
    GAPPED = "GAPPED"
    STALE = "STALE"
    MISSING = "MISSING"
    QUARANTINED = "QUARANTINED"


class DataStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PARTIAL = "PARTIAL"
    STALE = "STALE"


class QualityFlag(str, Enum):
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
    PARTIAL_INTERVAL = "PARTIAL_INTERVAL"
    CORPORATE_ACTION_WINDOW = "CORPORATE_ACTION_WINDOW"
    SOURCE_GAP = "SOURCE_GAP"
    UNRECONCILED = "UNRECONCILED"
    STALE_SOURCE = "STALE_SOURCE"


class SourceEnvelope(StrictModel):
    source_id: Identifier
    provider: DataProvider
    dataset: Identifier
    endpoint_id: str
    source_at: AwareDatetime
    received_at: AwareDatetime
    delay_class: DelayClass
    market_scope: MarketScope
    completeness: Completeness
    is_full_market: bool
    price_basis: PriceBasis | None = None
    timezone: Literal["America/New_York", "UTC"]
    terms_version: str
    raw_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    limitations: list[str] = Field(default_factory=list)


class InstrumentRef(StrictModel):
    instrument_id: Identifier
    asset_class: AssetClass
    symbol: str
    venue: str
    quote_currency: str
    timezone: Literal["America/New_York", "UTC"]
    contract_type: Literal["COMMON_STOCK", "SPOT", "USDT_PERPETUAL"]
    active: bool = True
    market_data_only: Literal[True] = True


class AnalysisSubject(StrictModel):
    subject_id: Identifier
    subject_type: SubjectType
    symbol: str
    primary_instrument_id: Identifier
    instruments: list[InstrumentRef] = Field(min_length=1, max_length=2)
    analysis_scope: Literal["SINGLE_UNDERLYING"] = "SINGLE_UNDERLYING"
    portfolio_analysis: Literal[False] = False

    @model_validator(mode="after")
    def validate_subject_shape(self) -> AnalysisSubject:
        ids = {item.instrument_id for item in self.instruments}
        if self.primary_instrument_id not in ids:
            raise ValueError("primary_instrument_id must reference an instrument in the subject")
        classes = {item.asset_class for item in self.instruments}
        if self.subject_type is SubjectType.EQUITY:
            if len(self.instruments) != 1 or classes != {AssetClass.US_EQUITY}:
                raise ValueError("an equity analysis contains exactly one US equity instrument")
        else:
            expected = {AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERPETUAL}
            if len(self.instruments) != 2 or classes != expected:
                raise ValueError("a crypto analysis contains exactly one spot and one USDT perpetual instrument")
        return self


class PageMeta(StrictModel):
    limit: int = Field(ge=1, le=5000)
    cursor: str | None = None
    next_cursor: str | None = None
    has_more: bool


class InstrumentSearchItem(InstrumentRef):
    subject_id: Identifier


class InstrumentSearchResponse(StrictModel):
    items: list[InstrumentSearchItem]
    page: PageMeta
    as_of: AwareDatetime


class RawOhlcv(StrictModel):
    open: DecimalString
    high: DecimalString
    low: DecimalString
    close: DecimalString
    volume: DecimalString


class Bar(StrictModel):
    bar_id: Identifier
    instrument_id: Identifier
    interval: Interval
    open_at: AwareDatetime
    close_at: AwareDatetime
    open: DecimalString
    high: DecimalString
    low: DecimalString
    close: DecimalString
    volume: DecimalString
    trade_count: int | None = Field(default=None, ge=0)
    session: SessionType
    price_basis: PriceBasis
    raw_ohlcv: RawOhlcv | None = None
    confirmed: bool
    partial_interval: bool = False
    source_ref: Identifier
    quality_flags: list[QualityFlag] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ohlc(self) -> Bar:
        values = [float(self.open), float(self.high), float(self.low), float(self.close), float(self.volume)]
        if values[4] < 0:
            raise ValueError("volume cannot be negative")
        if values[1] < max(values[0], values[3]) or values[2] > min(values[0], values[3]):
            raise ValueError("OHLC relationship is invalid")
        if self.close_at <= self.open_at:
            raise ValueError("close_at must be after open_at")
        if self.price_basis is PriceBasis.RAW and self.raw_ohlcv is not None:
            raise ValueError("raw_ohlcv is only used alongside an adjusted primary price basis")
        return self


class BarRequestStatus(str, Enum):
    SUCCESS = "SUCCESS"
    STALE_CACHE = "STALE_CACHE"
    FIXTURE = "FIXTURE"


class BarGap(StrictModel):
    after_bar_id: Identifier
    before_bar_id: Identifier
    gap_start_at: AwareDatetime
    gap_end_at: AwareDatetime
    missing_bar_count: int = Field(ge=1)


class BarQualitySummary(StrictModel):
    request_status: BarRequestStatus
    expected_interval_seconds: int | None = Field(default=None, ge=1)
    observed_bar_count: int = Field(ge=0)
    missing_bar_count: int = Field(ge=0)
    gaps: list[BarGap]
    requested_start_at: AwareDatetime | None
    actual_start_at: AwareDatetime | None
    latest_bar_at: AwareDatetime | None
    history_complete: bool
    stale_reason: str | None
    history_download_status: Literal['UNASSESSED','LOCAL_PAGE_BUDGET','TARGET_REACHED','PROVIDER_EMPTY_PAGE','REQUEST_FAILED','NOT_STARTED'] = 'UNASSESSED'
    history_cursor_end: AwareDatetime | None = None


class BarPage(StrictModel):
    instrument: InstrumentRef
    interval: Interval
    bars: list[Bar]
    sources: list[SourceEnvelope]
    quality: BarQualitySummary
    page: PageMeta


class CachedBarsDocument(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    provider: DataProvider
    instrument_id: Identifier
    base_interval: Interval
    cached_at: AwareDatetime
    bars: list[Bar]
    source: SourceEnvelope


class ObservationName(str, Enum):
    SPOT_PRICE = "SPOT_PRICE"
    PERPETUAL_MARK_PRICE = "PERPETUAL_MARK_PRICE"
    INDEX_PRICE = "INDEX_PRICE"
    FUNDING_RATE = "FUNDING_RATE"
    OPEN_INTEREST = "OPEN_INTEREST"
    BASIS_ABSOLUTE = "BASIS_ABSOLUTE"
    BASIS_RATE = "BASIS_RATE"
    LIQUIDATION_NOTIONAL_OBSERVED = "LIQUIDATION_NOTIONAL_OBSERVED"
    CIRCULATING_SUPPLY = "CIRCULATING_SUPPLY"
    TOTAL_SUPPLY = "TOTAL_SUPPLY"
    MARKET_CAP = "MARKET_CAP"


class MarketObservation(StrictModel):
    observation_id: Identifier
    name: ObservationName
    value: DecimalString | None
    unit: str | None
    status: DataStatus
    as_of: AwareDatetime
    source_ref: Identifier
    completeness: Completeness
    missing_reason: str | None = None


class CryptoMarketContext(StrictModel):
    subject: AnalysisSubject
    as_of: AwareDatetime
    spot_price: MarketObservation
    perpetual_mark_price: MarketObservation
    index_price: MarketObservation
    funding_rate: MarketObservation
    open_interest: MarketObservation
    basis_absolute: MarketObservation
    basis_rate: MarketObservation
    liquidation_notional_observed: MarketObservation
    supply: list[MarketObservation]
    sources: list[SourceEnvelope]
    limitations: list[str] = Field(min_length=1)


class AccountingBasis(str, Enum):
    GAAP = "GAAP"
    NON_GAAP = "NON_GAAP"
    NETWORK_NATIVE = "NETWORK_NATIVE"
    UNKNOWN = "UNKNOWN"


class FundamentalMetricName(str, Enum):
    REVENUE = "REVENUE"
    NET_INCOME = "NET_INCOME"
    OPERATING_CASH_FLOW = "OPERATING_CASH_FLOW"
    CAPITAL_EXPENDITURE = "CAPITAL_EXPENDITURE"
    FREE_CASH_FLOW = "FREE_CASH_FLOW"
    CASH_AND_EQUIVALENTS = "CASH_AND_EQUIVALENTS"
    TOTAL_DEBT = "TOTAL_DEBT"
    DILUTED_SHARES = "DILUTED_SHARES"
    REVENUE_GROWTH = "REVENUE_GROWTH"
    NET_INCOME_GROWTH = "NET_INCOME_GROWTH"
    MARKET_CAP = "MARKET_CAP"
    TTM_EPS = "TTM_EPS"
    PE_TTM = "PE_TTM"


class FundamentalFact(StrictModel):
    fact_id: Identifier
    name: FundamentalMetricName
    value: DecimalString | None
    unit: str | None
    status: DataStatus
    accounting_basis: AccountingBasis
    fiscal_period: str | None
    period_start: date | None
    period_end: date | None
    filed_at: date | None
    accepted_at: AwareDatetime | None
    source_ref: Identifier
    missing_reason: str | None = None
    # Derived values must record their formula and inputs (PRD 4.5); direct SEC
    # XBRL facts leave these empty.
    derivation: str | None = None
    input_fact_ids: list[Identifier] = Field(default_factory=list)


class FilingRef(StrictModel):
    accession_number: str
    form: str
    filed_at: date
    accepted_at: AwareDatetime
    report_period: date | None
    source_url: str
    source_ref: Identifier


class EquityFundamentals(StrictModel):
    subject: AnalysisSubject
    accepted_before: AwareDatetime
    facts: list[FundamentalFact]
    filings: list[FilingRef]
    sources: list[SourceEnvelope]
    missing_items: list[str]


class IndicatorName(str, Enum):
    SMA = "SMA"
    EMA = "EMA"
    RSI = "RSI"
    MACD = "MACD"
    ATR = "ATR"
    ADX = "ADX"
    OBV = "OBV"
    VWAP = "VWAP"
    BOLLINGER_BANDS = "BOLLINGER_BANDS"
    VOLUME_PRICE_RELATION = "VOLUME_PRICE_RELATION"


class IndicatorStatus(str, Enum):
    READY = "READY"
    WARMING_UP = "WARMING_UP"
    MISSING = "MISSING"


class ConfirmationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    PROVISIONAL = "PROVISIONAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class IndicatorParameters(StrictModel):
    input_field: Literal["close", "ohlcv", "typical_price"]
    length: int | None = Field(default=None, ge=1, le=10000)
    fast: int | None = Field(default=None, ge=1)
    slow: int | None = Field(default=None, ge=2)
    signal: int | None = Field(default=None, ge=1)
    multiplier: DecimalString | None = None
    seed_method: Literal["SMA", "WILDER_SMA", "CUMULATIVE", "NONE"]
    reset_rule: Literal["NONE", "US_RTH_SESSION", "UTC_DAY"] = "NONE"
    stddev_ddof: Literal[0] | None = None


class IndicatorValues(StrictModel):
    primary: DecimalString | None
    signal: DecimalString | None = None
    histogram: DecimalString | None = None
    upper: DecimalString | None = None
    middle: DecimalString | None = None
    lower: DecimalString | None = None
    plus_di: DecimalString | None = None
    minus_di: DecimalString | None = None


class IndicatorPoint(StrictModel):
    at: AwareDatetime
    values: IndicatorValues


class AnchorRef(StrictModel):
    bar_id: Identifier
    at: AwareDatetime
    field: Literal["high", "low", "close", "indicator"]
    value: DecimalString


class Confirmation(StrictModel):
    status: ConfirmationStatus
    confirmed_at: AwareDatetime | None
    left_window: int = Field(ge=0)
    right_window: int = Field(ge=0)
    can_repaint: bool


class EvidenceRefType(str, Enum):
    BAR = "BAR"
    INDICATOR = "INDICATOR"
    FUNDAMENTAL = "FUNDAMENTAL"
    MARKET_CONTEXT = "MARKET_CONTEXT"
    FVG = "FVG"
    DIVERGENCE = "DIVERGENCE"
    VPVR = "VPVR"


class EvidenceObservation(StrictModel):
    ref_type: EvidenceRefType
    ref_id: Identifier
    field: str
    value: DecimalString | None
    unit: str | None
    observed_at: AwareDatetime


class Evidence(StrictModel):
    evidence_id: Annotated[str, StringConstraints(pattern=r"^ev_[A-Za-z0-9_-]+$")]
    claim: str
    rule: str
    observations: list[EvidenceObservation] = Field(min_length=1)
    source_refs: list[Identifier] = Field(min_length=1)
    quality: Literal["HIGH", "MEDIUM", "LOW"]


class IndicatorResult(StrictModel):
    indicator_id: Identifier
    name: IndicatorName
    algorithm_version: str
    parameters: IndicatorParameters
    input_price_basis: PriceBasis
    status: IndicatorStatus
    current: IndicatorValues
    series: list[IndicatorPoint]
    signal_at: AwareDatetime | None
    anchors: list[AnchorRef]
    confirmation: Confirmation
    source_refs: list[Identifier]
    evidence: list[Evidence]
    warmup_bars: int = Field(ge=0)
    missing_reason: str | None = None


class CalculationMode(str, Enum):
    BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE = "BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE"
    TRADE_LEVEL_ACTUAL = "TRADE_LEVEL_ACTUAL"


class VpvrPosition(str, Enum):
    BELOW_VALUE = "BELOW_VALUE"
    IN_VALUE = "IN_VALUE"
    ABOVE_VALUE = "ABOVE_VALUE"


class VpvrBin(StrictModel):
    bin_index: int = Field(ge=0)
    lower: DecimalString
    upper: DecimalString
    allocated_volume: DecimalString
    is_poc: bool
    is_hvn: bool
    is_lvn: bool


class VpvrResult(StrictModel):
    vpvr_id: Identifier
    algorithm_version: str
    calculation_mode: CalculationMode
    input_granularity: Interval
    is_estimate: bool
    window_start: AwareDatetime
    window_end: AwareDatetime
    bin_count: int = Field(ge=2, le=500)
    value_area_fraction: float = Field(gt=0, le=1)
    bins: list[VpvrBin] = Field(min_length=2)
    poc: DecimalString
    vah: DecimalString
    val: DecimalString
    current_price_position: VpvrPosition
    source_refs: list[Identifier]
    market_scope: MarketScope
    completeness: Completeness
    limitations: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def enforce_estimate_label(self) -> VpvrResult:
        expected = self.calculation_mode is CalculationMode.BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE
        if self.is_estimate != expected:
            raise ValueError("is_estimate must match calculation_mode")
        return self


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class FvgFillStatus(str, Enum):
    UNTOUCHED = "UNTOUCHED"
    PARTIAL = "PARTIAL"
    FILLED_50 = "FILLED_50"
    FULLY_FILLED = "FULLY_FILLED"


class FvgValidityStatus(str, Enum):
    VALID = "VALID"
    INVALIDATED = "INVALIDATED"


class FvgResult(StrictModel):
    fvg_id: Identifier
    direction: Direction
    timeframe: Interval
    creation_rule_version: Literal["FVG_WICK_3BAR_ATR_FROZEN_V1"]
    created_at: AwareDatetime
    lower: DecimalString
    upper: DecimalString
    midpoint: DecimalString
    atr_at_creation: DecimalString
    atr_period: Literal[14]
    minimum_gap_atr_multiple: Literal["0.2"]
    fill_status: FvgFillStatus
    max_fill_fraction: float = Field(ge=0, le=1)
    validity_status: FvgValidityStatus
    invalidated_at: AwareDatetime | None
    source_refs: list[Identifier]
    confirmation: Confirmation


class DivergenceKind(str, Enum):
    REGULAR_BULLISH = "REGULAR_BULLISH"
    HIDDEN_BULLISH = "HIDDEN_BULLISH"
    REGULAR_BEARISH = "REGULAR_BEARISH"
    HIDDEN_BEARISH = "HIDDEN_BEARISH"


class OscillatorComponent(str, Enum):
    RSI_LINE = "RSI_LINE"
    MACD_LINE = "MACD_LINE"
    MACD_HISTOGRAM = "MACD_HISTOGRAM"


class DivergenceResult(StrictModel):
    divergence_id: Identifier
    kind: DivergenceKind
    oscillator_component: OscillatorComponent
    algorithm_version: str
    price_anchors: list[AnchorRef] = Field(min_length=2, max_length=2)
    oscillator_anchors: list[AnchorRef] = Field(min_length=2, max_length=2)
    pivot_left: int = Field(ge=1)
    pivot_right: int = Field(ge=1)
    maximum_anchor_gap_bars: int = Field(ge=1)
    signal_at: AwareDatetime
    confirmation: Confirmation
    source_refs: list[Identifier]


class TechnicalLevelKind(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class TechnicalLevel(StrictModel):
    level_id: Annotated[str, StringConstraints(pattern=r"^tech_level_[A-Za-z0-9_-]+$")]
    kind: TechnicalLevelKind
    price: DecimalString
    touch_count: int = Field(ge=1)
    first_touch_at: AwareDatetime
    latest_touch_at: AwareDatetime
    anchor_bar_ids: list[Identifier] = Field(min_length=1)
    confirmed: bool
    role_status: Literal['LEGACY_UNASSESSED','CORRECT_SIDE_ONLY','BROKEN_HISTORICAL','AT_LEVEL'] = 'LEGACY_UNASSESSED'
    role_rule_version: Literal['pivot-role-v1'] = 'pivot-role-v1'
    role_as_of: AwareDatetime | None = None
    reference_close: DecimalString | None = None


class SupportResistanceResult(StrictModel):
    result_id: Identifier
    algorithm_version: Literal["pivot-cluster-3x3-atr025-v1"]
    input_granularity: Interval
    lookback_bars: Literal[120]
    pivot_left: Literal[3]
    pivot_right: Literal[3]
    cluster_tolerance_atr_multiple: Literal["0.25"]
    levels: list[TechnicalLevel]
    source_refs: list[Identifier]
    evidence: list[Evidence]
    limitations: list[str]


class TrendState(str, Enum):
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE_OR_MIXED = "RANGE_OR_MIXED"
    INSUFFICIENT_CONFIRMED_PIVOTS = "INSUFFICIENT_CONFIRMED_PIVOTS"


class TrendStructureResult(StrictModel):
    result_id: Identifier
    algorithm_version: Literal["confirmed-pivot-hh-hl-3x3-v1"]
    input_granularity: Interval
    state: TrendState
    pivot_left: Literal[3]
    pivot_right: Literal[3]
    high_anchors: list[AnchorRef]
    low_anchors: list[AnchorRef]
    signal_at: AwareDatetime | None
    confirmation: Confirmation
    source_refs: list[Identifier]
    evidence: list[Evidence]
    limitations: list[str]


class IndicatorBundle(StrictModel):
    bundle_id: Identifier
    subject: AnalysisSubject
    instrument: InstrumentRef
    interval: Interval
    bars_as_of: AwareDatetime
    indicators: list[IndicatorResult]
    fvg: list[FvgResult]
    divergences: list[DivergenceResult]
    vpvr: VpvrResult
    support_resistance: SupportResistanceResult
    trend_structure: TrendStructureResult
    sources: list[SourceEnvelope]
    missing_items: list[str]


class SnapshotRequest(StrictModel):
    subject_id: Identifier
    intervals: list[Interval] = Field(min_length=1, max_length=8)
    cutoff_at: AwareDatetime | None = None
    include_perpetual: bool = False


class TimeframeCoverage(StrictModel):
    instrument_id: Identifier
    interval: Interval
    role: Literal['BACKGROUND', 'STRUCTURE', 'SIGNAL']
    bars_as_of: AwareDatetime | None
    status: Literal['AVAILABLE', 'MISSING']
    history_complete: bool
    evidence_ids: list[str]
    missing_reason: str | None


class FrozenScenarioRule(StrictModel):
    rule_id: Identifier
    version: Literal['VPVR_15M_CLOSE_ENVELOPE_V1'] = 'VPVR_15M_CLOSE_ENVELOPE_V1'
    scenario: Literal['BULLISH','NEUTRAL','BEARISH']
    instrument_id: Identifier
    lower: DecimalString
    upper: DecimalString
    frozen_at: AwareDatetime
    expires_at: AwareDatetime
    confirmation_interval: Literal['15m'] = '15m'
    trigger: Literal['CLOSE_ABOVE_UPPER','CLOSE_INSIDE_INCLUSIVE','CLOSE_BELOW_LOWER']
    invalidation: Literal['CLOSE_BELOW_LOWER','CLOSE_OUTSIDE','CLOSE_ABOVE_UPPER']
    evidence_ids: list[str]
    limitation: str = 'Platform-defined close-only scenario protocol using estimated VPVR bounds; not a trade, price target or model accuracy measurement.'


class AnalysisSnapshot(StrictModel):
    scenario_rules: list[FrozenScenarioRule] = Field(default_factory=list)
    schema_version: Literal["1.0.0"] = "1.0.0"
    snapshot_id: Identifier
    snapshot_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    subject: AnalysisSubject
    cutoff_at: AwareDatetime
    created_at: AwareDatetime
    bars: list[BarPage] = Field(min_length=1)
    indicator_bundles: list[IndicatorBundle] = Field(min_length=1)
    fundamentals: EquityFundamentals | None
    market_context: CryptoMarketContext | None
    evidence: list[Evidence]
    sources: list[SourceEnvelope]
    missing_items: list[str]
    timeframe_coverage: list[TimeframeCoverage] = Field(default_factory=list)


class SnapshotRef(StrictModel):
    snapshot_id: Identifier
    snapshot_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    subject_id: Identifier
    cutoff_at: AwareDatetime
    created_at: AwareDatetime
    completeness: Completeness
    missing_items: list[str]


class ScenarioType(str, Enum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"


class Condition(StrictModel):
    description: str
    evidence_ids: list[Annotated[str, StringConstraints(pattern=r"^ev_[A-Za-z0-9_-]+$")]]


class Scenario(StrictModel):
    evaluation_rule_id: str | None = None
    type: ScenarioType
    summary: str
    preconditions: list[Condition]
    evidence_ids: list[Annotated[str, StringConstraints(pattern=r"^ev_[A-Za-z0-9_-]+$")]] = Field(min_length=1)
    key_level_ids: list[Annotated[str, StringConstraints(pattern=r"^level_[A-Za-z0-9_-]+$")]]
    invalidation_conditions: list[Condition] = Field(min_length=1)


class KeyLevelKind(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"
    POC = "POC"
    VAH = "VAH"
    VAL = "VAL"
    FVG_BOUNDARY = "FVG_BOUNDARY"
    FVG_MIDPOINT = "FVG_MIDPOINT"
    OTHER = "OTHER"


class KeyLevel(StrictModel):
    level_id: Annotated[str, StringConstraints(pattern=r"^level_[A-Za-z0-9_-]+$")]
    kind: KeyLevelKind
    price: DecimalString
    raw_price_equivalent: DecimalString | None
    price_basis: PriceBasis
    currency: str
    timeframe: Interval
    evidence_ids: list[Annotated[str, StringConstraints(pattern=r"^ev_[A-Za-z0-9_-]+$")]] = Field(min_length=1)
    confirmed: bool


class Impact(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Conflict(StrictModel):
    description: str
    evidence_ids: list[Annotated[str, StringConstraints(pattern=r"^ev_[A-Za-z0-9_-]+$")]] = Field(min_length=2)
    impact: Impact


class MissingReason(str, Enum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    NOT_LICENSED = "NOT_LICENSED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    OUTSIDE_HISTORY = "OUTSIDE_HISTORY"
    QUALITY_QUARANTINE = "QUALITY_QUARANTINE"
    UNRECONCILED = "UNRECONCILED"
    UNKNOWN = "UNKNOWN"


class MissingInformation(StrictModel):
    field: str
    reason_code: MissingReason
    impact: Impact
    detail: str


class Confidence(StrictModel):
    score: float = Field(ge=0, le=1)
    is_calibrated_probability: Literal[False] = False
    basis: str
    limitations: list[str]


class Disclaimer(StrictModel):
    not_investment_advice: Literal[True] = True
    no_profit_guarantee: Literal[True] = True
    data_limitations: str


class AnalysisAudit(StrictModel):
    prompt_version: str
    provider: str
    model: str
    model_revision: str | None
    started_at: AwareDatetime
    finished_at: AwareDatetime
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost: DecimalString | None
    cost_currency: str | None
    raw_response_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class TimeframeSynthesis(StrictModel):
    instrument_id: Identifier
    interval: Interval
    role: Literal['BACKGROUND','STRUCTURE','SIGNAL']
    status: Literal['AVAILABLE','MISSING']
    assessment: str
    evidence_ids: list[str]


class AiAnalysisResult(StrictModel):
    timeframe_synthesis: list[TimeframeSynthesis] = Field(default_factory=list)
    schema_version: Literal["1.1.0"] = "1.1.0"
    analysis_id: Identifier
    snapshot: SnapshotRef
    subject: AnalysisSubject
    scenarios: list[Scenario] = Field(min_length=3, max_length=3)
    key_levels: list[KeyLevel]
    conflicts: list[Conflict]
    missing_information: list[MissingInformation]
    confidence: Confidence
    disclaimer: Disclaimer
    audit: AnalysisAudit

    @model_validator(mode="after")
    def require_exact_scenarios(self) -> AiAnalysisResult:
        actual = [item.type for item in self.scenarios]
        expected = {ScenarioType.BULLISH, ScenarioType.NEUTRAL, ScenarioType.BEARISH}
        if set(actual) != expected or len(actual) != len(set(actual)):
            raise ValueError("scenarios must contain exactly one BULLISH, NEUTRAL, and BEARISH")
        if self.subject.subject_id != self.snapshot.subject_id:
            raise ValueError("analysis subject must match snapshot subject")
        return self


class AiModelOutput(StrictModel):
    timeframe_synthesis: list[TimeframeSynthesis] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(min_length=3, max_length=3)
    key_levels: list[KeyLevel]
    conflicts: list[Conflict]
    missing_information: list[MissingInformation]
    confidence: Confidence
    disclaimer: Disclaimer

    @model_validator(mode="after")
    def require_exact_scenarios(self) -> AiModelOutput:
        actual = [item.type for item in self.scenarios]
        expected = {ScenarioType.BULLISH, ScenarioType.NEUTRAL, ScenarioType.BEARISH}
        if set(actual) != expected or len(actual) != len(set(actual)):
            raise ValueError("scenarios must contain exactly one BULLISH, NEUTRAL, and BEARISH")
        return self


class AiTaskStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    FAILED_PROVIDER = "FAILED_PROVIDER"
    FAILED_INTERNAL = "FAILED_INTERNAL"


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    ANALYSIS_VALIDATION_FAILED = "ANALYSIS_VALIDATION_FAILED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NETWORK_DISABLED = "NETWORK_DISABLED"


class ProblemDetail(StrictModel):
    type: str
    title: str
    status: int = Field(ge=400, le=599)
    code: ErrorCode
    detail: str
    trace_id: Identifier


# Providers that produce local rule-based output. ``DETERMINISTIC_LOCAL`` is the
# legacy name stored in records written before the current enum, kept as an
# explicit alias so an old row is never mistaken for a real external model.
DEMO_PROVIDERS = ("DETERMINISTIC_DEMO", "DETERMINISTIC_LOCAL")
REAL_MODEL_PROVIDERS = ("CODEX_CLI",)


def is_demo_provider(provider: str) -> bool:
    return provider in DEMO_PROVIDERS


def is_real_model_provider(provider: str) -> bool:
    return provider in REAL_MODEL_PROVIDERS


class AiAnalysisTaskRequest(StrictModel):
    snapshot_id: Identifier
    provider: Literal["DETERMINISTIC_DEMO", "CODEX_CLI"] = "DETERMINISTIC_DEMO"
    model: Annotated[str, StringConstraints(min_length=1, max_length=100)] = "evidence-rules-demo-v1"
    prompt_version: Literal["scenario-v2"] = "scenario-v2"

    @model_validator(mode="after")
    def provider_model_pair(self) -> AiAnalysisTaskRequest:
        if self.provider == "DETERMINISTIC_DEMO" and self.model != "evidence-rules-demo-v1":
            raise ValueError("DETERMINISTIC_DEMO requires evidence-rules-demo-v1")
        if self.provider == "CODEX_CLI" and self.model == "evidence-rules-demo-v1":
            raise ValueError("CODEX_CLI requires a real Codex model name")
        return self


class AiAnalysisTask(StrictModel):
    task_id: Identifier
    status: AiTaskStatus
    snapshot_id: Identifier
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    analysis_id: Identifier | None
    error: ProblemDetail | None


class AnalysisSummary(StrictModel):
    analysis_id: Identifier
    subject_id: Identifier
    snapshot_id: Identifier
    created_at: AwareDatetime
    provider: str
    model: str
    confidence_score: float = Field(ge=0, le=1)


class AnalysisPage(StrictModel):
    items: list[AnalysisSummary]
    page: PageMeta


class OutcomeHorizon(str, Enum):
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAY_1 = "1d"
    WEEK_1 = "1w"


class OutcomeStatus(str, Enum):
    PENDING = "PENDING"
    AWAITING_DATA = "AWAITING_DATA"
    MATURED = "MATURED"
    UNAVAILABLE = "UNAVAILABLE"


class AnalysisOutcome(StrictModel):
    outcome_id: Identifier
    analysis_id: Identifier
    horizon: OutcomeHorizon
    status: OutcomeStatus
    matures_at: AwareDatetime
    reference_rule: str
    start_price: DecimalString
    end_price: DecimalString | None
    return_fraction: DecimalString | None
    maximum_favorable_excursion: DecimalString | None
    maximum_adverse_excursion: DecimalString | None
    computed_at: AwareDatetime | None
    source_refs: list[Identifier]
    unavailable_reason: str | None
    observed_end_at: AwareDatetime | None = None
    evaluation_rule_version: str = 'legacy-unfilled'
    scenario_scoring_status: Literal['NOT_PRE_REGISTERED','PLATFORM_RULES_NOT_MODEL_HIT_RATE'] = 'NOT_PRE_REGISTERED'


class OutcomePage(StrictModel):
    items: list[AnalysisOutcome]
    page: PageMeta


class HealthResponse(StrictModel):
    status: Literal["ok", "degraded"]
    checked_at: AwareDatetime
    data_mode: Literal["fixture", "live"]
    network_enabled: bool
    crypto_provider: Literal["binance", "okx"]
    codex_cli_available: bool
    real_ai_cost_boundary: str
    real_ai_model: str
    monthly_data_budget_usd: Literal[0] = 0


class IndicatorEvidenceView(StrictModel):
    subject_id: Identifier
    instrument: InstrumentRef
    interval: Interval
    cutoff_at: AwareDatetime
    latest_bar: Bar
    quality: BarQualitySummary
    sources: list[SourceEnvelope]
    evidence: list[Evidence]
    limitations: list[str]


class MarketSearchInput(StrictModel):
    q: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class MarketBarsInput(StrictModel):
    instrument_id: Identifier
    interval: Interval
    cutoff_at: AwareDatetime | None = None
    limit: int = Field(default=500, ge=1, le=5000)
    cursor: str | None = None


class IndicatorBundleInput(StrictModel):
    subject_id: Identifier
    interval: Interval
    instrument_id: Identifier | None = None
    cutoff_at: AwareDatetime | None = None


class SubjectInput(StrictModel):
    subject_id: Identifier


class AiRunInput(AiAnalysisTaskRequest):
    idempotency_key: Annotated[str, StringConstraints(min_length=16, max_length=128)]


class TaskLookupInput(StrictModel):
    task_id: Identifier


class AnalysisLookupInput(StrictModel):
    analysis_id: Identifier


class OutcomeLookupInput(StrictModel):
    analysis_id: Identifier
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class MacroObservation(StrictModel):
    """One FRED observation date (never a revision-free truth; see the vintage on the snapshot)."""

    observation_date: date
    value: DecimalString | None
    raw_value: str
    is_missing: bool


class MacroSeries(StrictModel):
    series_id: Identifier
    label: str
    title: str | None
    units: str | None
    frequency: str | None
    role: Literal["RISK_FREE_RATE", "RATE_SPREAD", "POLICY_RATE", "INFLATION", "DOLLAR", "VOLATILITY"]
    status: DataStatus
    missing_reason: str | None
    latest: MacroObservation | None
    latest_observation_date: date | None
    change_1_observation: DecimalString | None
    change_5_observations: DecimalString | None
    change_20_observations: DecimalString | None
    percent_change_20_observations: DecimalString | None
    observations: list[MacroObservation] = Field(default_factory=list)
    source_ref: Identifier
    # Derived series (e.g. CPI year-over-year) must record their formula and inputs.
    derivation: str | None = None
    derived_from: list[Identifier] = Field(default_factory=list)


class MacroSnapshot(StrictModel):
    as_of: AwareDatetime
    vintage_date: date
    vintage_note: str
    series: list[MacroSeries]
    sources: list[SourceEnvelope]
    limitations: list[str] = Field(default_factory=list)


class MacroContextInput(StrictModel):
    limit: int = Field(default=30, ge=2, le=200)
    vintage: AwareDatetime | None = None
