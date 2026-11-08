from __future__ import annotations

import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from statistics import fmean, pstdev

from app.fixtures import decimal_string
from app.models import (
    AnchorRef,
    Bar,
    CalculationMode,
    Completeness,
    Confirmation,
    ConfirmationStatus,
    Direction,
    DivergenceKind,
    DivergenceResult,
    Evidence,
    EvidenceObservation,
    EvidenceRefType,
    FvgFillStatus,
    FvgResult,
    FvgValidityStatus,
    IndicatorName,
    IndicatorParameters,
    IndicatorPoint,
    IndicatorResult,
    IndicatorStatus,
    IndicatorValues,
    Interval,
    MarketScope,
    OscillatorComponent,
    PriceBasis,
    QualityFlag,
    SupportResistanceResult,
    TechnicalLevel,
    TechnicalLevelKind,
    TrendState,
    TrendStructureResult,
    VpvrBin,
    VpvrPosition,
    VpvrResult,
)


ALGORITHM_VERSION = "deterministic-v1"
NY = ZoneInfo("America/New_York")


def sma(values: list[float], length: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    rolling = 0.0
    for index, value in enumerate(values):
        rolling += value
        if index >= length:
            rolling -= values[index - length]
        if index >= length - 1:
            result[index] = rolling / length
    return result


def ema(values: list[float], length: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < length:
        return result
    seed = fmean(values[:length])
    result[length - 1] = seed
    alpha = 2.0 / (length + 1)
    previous = seed
    for index in range(length, len(values)):
        previous = alpha * values[index] + (1 - alpha) * previous
        result[index] = previous
    return result


def ema_nullable(values: list[float | None], length: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    valid = [(index, value) for index, value in enumerate(values) if value is not None]
    if len(valid) < length:
        return result
    seed_values = [float(item[1]) for item in valid[:length]]
    seed_index = valid[length - 1][0]
    previous = fmean(seed_values)
    result[seed_index] = previous
    alpha = 2.0 / (length + 1)
    for index, value in valid[length:]:
        previous = alpha * float(value) + (1 - alpha) * previous
        result[index] = previous
    return result


def rsi(values: list[float], length: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= length:
        return result
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(value, 0) for value in deltas]
    losses = [max(-value, 0) for value in deltas]
    avg_gain = fmean(gains[:length])
    avg_loss = fmean(losses[:length])

    def value(gain: float, loss: float) -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        if gain == 0:
            return 0.0
        return 100 - 100 / (1 + gain / loss)

    result[length] = value(avg_gain, avg_loss)
    for source_index in range(length, len(deltas)):
        avg_gain = (avg_gain * (length - 1) + gains[source_index]) / length
        avg_loss = (avg_loss * (length - 1) + losses[source_index]) / length
        result[source_index + 1] = value(avg_gain, avg_loss)
    return result


def atr(bars: list[Bar], length: int = 14) -> list[float | None]:
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        high, low = float(bar.high), float(bar.low)
        if index == 0:
            true_ranges.append(high - low)
        else:
            previous_close = float(bars[index - 1].close)
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    result: list[float | None] = [None] * len(bars)
    if len(true_ranges) < length:
        return result
    current = fmean(true_ranges[:length])
    result[length - 1] = current
    for index in range(length, len(true_ranges)):
        current = (current * (length - 1) + true_ranges[index]) / length
        result[index] = current
    return result


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast_line, slow_line = ema(values, fast), ema(values, slow)
    line = [
        float(fast_value) - float(slow_value) if fast_value is not None and slow_value is not None else None
        for fast_value, slow_value in zip(fast_line, slow_line, strict=True)
    ]
    signal_line = ema_nullable(line, signal)
    histogram = [
        float(line_value) - float(signal_value) if line_value is not None and signal_value is not None else None
        for line_value, signal_value in zip(line, signal_line, strict=True)
    ]
    return line, signal_line, histogram


def bollinger(
    values: list[float], length: int = 20, multiplier: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    middle = sma(values, length)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for index in range(length - 1, len(values)):
        deviation = pstdev(values[index - length + 1 : index + 1])
        upper[index] = float(middle[index]) + multiplier * deviation
        lower[index] = float(middle[index]) - multiplier * deviation
    return middle, upper, lower


def obv(bars: list[Bar]) -> list[float | None]:
    if not bars:
        return []
    result: list[float | None] = [0.0]
    running = 0.0
    for previous, current in zip(bars, bars[1:]):
        if float(current.close) > float(previous.close):
            running += float(current.volume)
        elif float(current.close) < float(previous.close):
            running -= float(current.volume)
        result.append(running)
    return result


def session_vwap(bars: list[Bar]) -> list[float | None]:
    result: list[float | None] = []
    active_key: object | None = None
    cumulative_volume = 0.0
    cumulative_value = 0.0
    for bar in bars:
        zone = NY if bar.session.value == "US_RTH" else UTC
        key = bar.open_at.astimezone(zone).date()
        if key != active_key:
            active_key = key
            cumulative_volume = 0.0
            cumulative_value = 0.0
        volume = float(bar.volume)
        typical = (float(bar.high) + float(bar.low) + float(bar.close)) / 3
        cumulative_volume += volume
        cumulative_value += typical * volume
        result.append(cumulative_value / cumulative_volume if cumulative_volume > 0 else None)
    return result


def adx(
    bars: list[Bar], length: int = 14
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    count = len(bars)
    adx_values: list[float | None] = [None] * count
    plus_di: list[float | None] = [None] * count
    minus_di: list[float | None] = [None] * count
    if count <= length:
        return adx_values, plus_di, minus_di
    true_ranges = [0.0] * count
    plus_dm = [0.0] * count
    minus_dm = [0.0] * count
    for index in range(1, count):
        high = float(bars[index].high)
        low = float(bars[index].low)
        previous_high = float(bars[index - 1].high)
        previous_low = float(bars[index - 1].low)
        previous_close = float(bars[index - 1].close)
        up = high - previous_high
        down = previous_low - low
        plus_dm[index] = up if up > down and up > 0 else 0.0
        minus_dm[index] = down if down > up and down > 0 else 0.0
        true_ranges[index] = max(high - low, abs(high - previous_close), abs(low - previous_close))
    smoothed_tr = sum(true_ranges[1 : length + 1])
    smoothed_plus = sum(plus_dm[1 : length + 1])
    smoothed_minus = sum(minus_dm[1 : length + 1])
    dx: list[float | None] = [None] * count
    for index in range(length, count):
        if index > length:
            smoothed_tr = smoothed_tr - smoothed_tr / length + true_ranges[index]
            smoothed_plus = smoothed_plus - smoothed_plus / length + plus_dm[index]
            smoothed_minus = smoothed_minus - smoothed_minus / length + minus_dm[index]
        if smoothed_tr <= 0:
            continue
        plus_di[index] = 100 * smoothed_plus / smoothed_tr
        minus_di[index] = 100 * smoothed_minus / smoothed_tr
        denominator = float(plus_di[index]) + float(minus_di[index])
        dx[index] = (
            100 * abs(float(plus_di[index]) - float(minus_di[index])) / denominator
            if denominator > 0
            else 0.0
        )
    seed_end = length * 2 - 1
    if count > seed_end:
        seed = [float(value) for value in dx[length : seed_end + 1] if value is not None]
        if len(seed) == length:
            current = fmean(seed)
            adx_values[seed_end] = current
            for index in range(seed_end + 1, count):
                if dx[index] is not None:
                    current = (current * (length - 1) + float(dx[index])) / length
                    adx_values[index] = current
    return adx_values, plus_di, minus_di


def _series_points(
    bars: list[Bar],
    primary: list[float | None],
    signal: list[float | None] | None = None,
    histogram: list[float | None] | None = None,
    upper: list[float | None] | None = None,
    middle: list[float | None] | None = None,
    lower: list[float | None] | None = None,
    plus_di: list[float | None] | None = None,
    minus_di: list[float | None] | None = None,
) -> list[IndicatorPoint]:
    signal = signal or [None] * len(bars)
    histogram = histogram or [None] * len(bars)
    upper = upper or [None] * len(bars)
    middle = middle or [None] * len(bars)
    lower = lower or [None] * len(bars)
    plus_di = plus_di or [None] * len(bars)
    minus_di = minus_di or [None] * len(bars)
    return [
        IndicatorPoint(
            at=bar.close_at,
            values=IndicatorValues(
                primary=decimal_string(primary_value, 10) if primary_value is not None else None,
                signal=decimal_string(signal_value, 10) if signal_value is not None else None,
                histogram=decimal_string(histogram_value, 10) if histogram_value is not None else None,
                upper=decimal_string(upper_value, 10) if upper_value is not None else None,
                middle=decimal_string(middle_value, 10) if middle_value is not None else None,
                lower=decimal_string(lower_value, 10) if lower_value is not None else None,
                plus_di=decimal_string(plus_value, 10) if plus_value is not None else None,
                minus_di=decimal_string(minus_value, 10) if minus_value is not None else None,
            ),
        )
        for bar, primary_value, signal_value, histogram_value, upper_value, middle_value, lower_value, plus_value, minus_value in zip(
            bars, primary, signal, histogram, upper, middle, lower, plus_di, minus_di, strict=True
        )
    ]


def _indicator_result(
    bars: list[Bar],
    name: IndicatorName,
    parameters: IndicatorParameters,
    primary: list[float | None],
    *,
    signal: list[float | None] | None = None,
    histogram: list[float | None] | None = None,
    upper: list[float | None] | None = None,
    middle: list[float | None] | None = None,
    lower: list[float | None] | None = None,
    plus_di: list[float | None] | None = None,
    minus_di: list[float | None] | None = None,
    warmup_bars: int,
) -> IndicatorResult:
    current_primary = primary[-1] if primary else None
    current_signal = signal[-1] if signal else None
    current_histogram = histogram[-1] if histogram else None
    current_upper = upper[-1] if upper else None
    current_middle = middle[-1] if middle else None
    current_lower = lower[-1] if lower else None
    current_plus = plus_di[-1] if plus_di else None
    current_minus = minus_di[-1] if minus_di else None
    ready = current_primary is not None and len(bars) >= warmup_bars
    evidence: list[Evidence] = []
    if ready:
        evidence_id = f"ev-{name.value.lower()}-{bars[-1].bar_id}"
        evidence.append(
            Evidence(
                evidence_id=evidence_id.replace("ev-", "ev_", 1),
                claim=f"{name.value} latest confirmed value is {decimal_string(current_primary, 10)}.",
                rule=f"{name.value} computed locally with {ALGORITHM_VERSION}.",
                observations=[
                    EvidenceObservation(
                        ref_type=EvidenceRefType.INDICATOR,
                        ref_id=f"ind-{name.value.lower()}-{bars[-1].instrument_id.replace(':', '-').lower()}-{bars[-1].interval.value}",
                        field="primary",
                        value=decimal_string(current_primary, 10),
                        unit="price" if name in {IndicatorName.SMA, IndicatorName.EMA, IndicatorName.ATR, IndicatorName.VWAP, IndicatorName.BOLLINGER_BANDS} else "indicator",
                        observed_at=bars[-1].close_at,
                    )
                ],
                source_refs=sorted({bar.source_ref for bar in bars}),
                quality="MEDIUM" if QualityFlag.SYNTHETIC_FIXTURE in bars[-1].quality_flags else "HIGH",
            )
        )
    return IndicatorResult(
        indicator_id=f"ind-{name.value.lower()}-{bars[-1].instrument_id.replace(':', '-').lower()}-{bars[-1].interval.value}",
        name=name,
        algorithm_version=ALGORITHM_VERSION,
        parameters=parameters,
        input_price_basis=bars[-1].price_basis,
        status=IndicatorStatus.READY if ready else IndicatorStatus.WARMING_UP,
        current=IndicatorValues(
            primary=decimal_string(current_primary, 10) if current_primary is not None else None,
            signal=decimal_string(current_signal, 10) if current_signal is not None else None,
            histogram=decimal_string(current_histogram, 10) if current_histogram is not None else None,
            upper=decimal_string(current_upper, 10) if current_upper is not None else None,
            middle=decimal_string(current_middle, 10) if current_middle is not None else None,
            lower=decimal_string(current_lower, 10) if current_lower is not None else None,
            plus_di=decimal_string(current_plus, 10) if current_plus is not None else None,
            minus_di=decimal_string(current_minus, 10) if current_minus is not None else None,
        ),
        series=_series_points(
            bars, primary, signal, histogram, upper, middle, lower, plus_di, minus_di
        ),
        signal_at=bars[-1].close_at if ready else None,
        anchors=[],
        confirmation=Confirmation(
            status=ConfirmationStatus.CONFIRMED if ready else ConfirmationStatus.NOT_APPLICABLE,
            confirmed_at=bars[-1].close_at if ready else None,
            left_window=0,
            right_window=0,
            can_repaint=False,
        ),
        source_refs=sorted({bar.source_ref for bar in bars}),
        evidence=evidence,
        warmup_bars=warmup_bars,
        missing_reason=None if ready else "INSUFFICIENT_WARMUP",
    )


def compute_indicators(bars: list[Bar]) -> list[IndicatorResult]:
    if not bars:
        return []
    closes = [float(bar.close) for bar in bars]
    sma20 = sma(closes, 20)
    ema20 = ema(closes, 20)
    rsi14 = rsi(closes, 14)
    atr14 = atr(bars, 14)
    macd_line, signal_line, histogram = macd(closes)
    bb_middle, bb_upper, bb_lower = bollinger(closes)
    adx14, plus_di14, minus_di14 = adx(bars)
    obv_values = obv(bars)
    vwap_values = session_vwap(bars)
    volumes = [float(bar.volume) for bar in bars]
    volume_average = sma(volumes, 20)
    volume_ratio = [
        volume / float(average) if average not in {None, 0} else None
        for volume, average in zip(volumes, volume_average, strict=True)
    ]
    price_return = [
        None
        if index == 0 or float(bars[index - 1].close) == 0
        else float(bar.close) / float(bars[index - 1].close) - 1
        for index, bar in enumerate(bars)
    ]
    return [
        _indicator_result(
            bars,
            IndicatorName.SMA,
            IndicatorParameters(input_field="close", length=20, seed_method="SMA"),
            sma20,
            warmup_bars=20,
        ),
        _indicator_result(
            bars,
            IndicatorName.EMA,
            IndicatorParameters(input_field="close", length=20, seed_method="SMA"),
            ema20,
            warmup_bars=20,
        ),
        _indicator_result(
            bars,
            IndicatorName.RSI,
            IndicatorParameters(input_field="close", length=14, seed_method="WILDER_SMA"),
            rsi14,
            warmup_bars=15,
        ),
        _indicator_result(
            bars,
            IndicatorName.MACD,
            IndicatorParameters(input_field="close", fast=12, slow=26, signal=9, seed_method="SMA"),
            macd_line,
            signal=signal_line,
            histogram=histogram,
            warmup_bars=34,
        ),
        _indicator_result(
            bars,
            IndicatorName.ATR,
            IndicatorParameters(input_field="ohlcv", length=14, seed_method="WILDER_SMA"),
            atr14,
            warmup_bars=14,
        ),
        _indicator_result(
            bars,
            IndicatorName.BOLLINGER_BANDS,
            IndicatorParameters(
                input_field="close",
                length=20,
                multiplier="2",
                seed_method="SMA",
                stddev_ddof=0,
            ),
            bb_middle,
            upper=bb_upper,
            middle=bb_middle,
            lower=bb_lower,
            warmup_bars=20,
        ),
        _indicator_result(
            bars,
            IndicatorName.ADX,
            IndicatorParameters(input_field="ohlcv", length=14, seed_method="WILDER_SMA"),
            adx14,
            plus_di=plus_di14,
            minus_di=minus_di14,
            warmup_bars=28,
        ),
        _indicator_result(
            bars,
            IndicatorName.OBV,
            IndicatorParameters(input_field="ohlcv", seed_method="CUMULATIVE"),
            obv_values,
            warmup_bars=1,
        ),
        _indicator_result(
            bars,
            IndicatorName.VWAP,
            IndicatorParameters(
                input_field="typical_price",
                seed_method="CUMULATIVE",
                reset_rule=(
                    "US_RTH_SESSION" if bars[-1].session.value == "US_RTH" else "UTC_DAY"
                ),
            ),
            vwap_values,
            warmup_bars=1,
        ),
        _indicator_result(
            bars,
            IndicatorName.VOLUME_PRICE_RELATION,
            IndicatorParameters(input_field="ohlcv", length=20, seed_method="SMA"),
            volume_ratio,
            signal=price_return,
            warmup_bars=20,
        ),
    ]


def compute_vpvr(
    bars: list[Bar],
    bin_count: int = 48,
    window_size: int = 120,
    market_scope: MarketScope | None = None,
    completeness: Completeness = Completeness.COMPLETE_FOR_SCOPE,
) -> VpvrResult:
    selected = bars[-window_size:]
    low = min(float(bar.low) for bar in selected)
    high = max(float(bar.high) for bar in selected)
    if high <= low:
        high = low + max(abs(low) * 1e-9, 1e-9)
    width = (high - low) / bin_count
    volumes = [0.0] * bin_count
    for bar in selected:
        bar_low, bar_high, volume = float(bar.low), float(bar.high), float(bar.volume)
        if bar_high == bar_low:
            index = min(int((bar_low - low) / width), bin_count - 1)
            volumes[index] += volume
            continue
        for index in range(bin_count):
            bin_low, bin_high = low + index * width, low + (index + 1) * width
            overlap = max(0.0, min(bar_high, bin_high) - max(bar_low, bin_low))
            if overlap > 0:
                volumes[index] += volume * overlap / (bar_high - bar_low)

    poc_index = max(range(bin_count), key=lambda index: (volumes[index], -index))
    total = sum(volumes)
    included = {poc_index}
    cumulative = volumes[poc_index]
    left, right = poc_index - 1, poc_index + 1
    while cumulative < total * 0.70 and (left >= 0 or right < bin_count):
        left_volume = volumes[left] if left >= 0 else -1
        right_volume = volumes[right] if right < bin_count else -1
        if left_volume >= right_volume:
            included.add(left)
            cumulative += left_volume
            left -= 1
        else:
            included.add(right)
            cumulative += right_volume
            right += 1

    smooth = []
    for index in range(bin_count):
        weights = [(index - 1, 0.25), (index, 0.5), (index + 1, 0.25)]
        valid = [(position, weight) for position, weight in weights if 0 <= position < bin_count]
        scale = sum(weight for _, weight in valid)
        smooth.append(sum(volumes[position] * weight for position, weight in valid) / scale)
    peak = max(smooth) if smooth else 0
    hvn = {
        index
        for index in range(1, bin_count - 1)
        if smooth[index] >= smooth[index - 1]
        and smooth[index] > smooth[index + 1]
        and smooth[index] >= peak * 0.10
    }
    lvn = {
        index
        for index in range(1, bin_count - 1)
        if smooth[index] <= smooth[index - 1]
        and smooth[index] < smooth[index + 1]
        and smooth[index] <= peak * 0.30
        and any(node < index for node in hvn)
        and any(node > index for node in hvn)
    }
    val_index, vah_index = min(included), max(included)
    current = float(selected[-1].close)
    val_price, vah_price = low + val_index * width, low + (vah_index + 1) * width
    position = VpvrPosition.IN_VALUE
    if current < val_price:
        position = VpvrPosition.BELOW_VALUE
    elif current > vah_price:
        position = VpvrPosition.ABOVE_VALUE
    return VpvrResult(
        vpvr_id=f"vpvr-{selected[-1].instrument_id.replace(':', '-').lower()}-{selected[-1].interval.value}",
        algorithm_version="vpvr-bar-range-uniform-v1",
        calculation_mode=CalculationMode.BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE,
        input_granularity=selected[-1].interval,
        is_estimate=True,
        window_start=selected[0].open_at,
        window_end=selected[-1].close_at,
        bin_count=bin_count,
        value_area_fraction=0.70,
        bins=[
            VpvrBin(
                bin_index=index,
                lower=decimal_string(low + index * width, 10),
                upper=decimal_string(low + (index + 1) * width, 10),
                allocated_volume=decimal_string(volumes[index], 10),
                is_poc=index == poc_index,
                is_hvn=index in hvn,
                is_lvn=index in lvn,
            )
            for index in range(bin_count)
        ],
        poc=decimal_string(low + (poc_index + 0.5) * width, 10),
        vah=decimal_string(vah_price, 10),
        val=decimal_string(val_price, 10),
        current_price_position=position,
        source_refs=sorted({bar.source_ref for bar in selected}),
        market_scope=(
            MarketScope.SYNTHETIC_TEST
            if QualityFlag.SYNTHETIC_FIXTURE in selected[-1].quality_flags
            else market_scope or MarketScope.UNKNOWN
        ),
        completeness=completeness,
        limitations=[
            "Estimate derived from K-line high/low range allocation; it is not trade-level Volume Profile.",
            "Each bar's volume is assumed uniformly distributed across its high-low interval.",
            "HVN/LVN depend on bin count, selected window, smoothing and bar granularity.",
        ],
    )


def compute_fvg(bars: list[Bar]) -> list[FvgResult]:
    atr14 = atr(bars, 14)
    results: list[FvgResult] = []
    for index in range(2, len(bars)):
        frozen_atr = atr14[index]
        if frozen_atr is None or any(QualityFlag.CORPORATE_ACTION_WINDOW in bar.quality_flags for bar in bars[index - 2 : index + 1]):
            continue
        first, third = bars[index - 2], bars[index]
        direction: Direction | None = None
        lower = upper = 0.0
        if float(first.high) < float(third.low):
            direction, lower, upper = Direction.BULLISH, float(first.high), float(third.low)
        elif float(first.low) > float(third.high):
            direction, lower, upper = Direction.BEARISH, float(third.high), float(first.low)
        if direction is None or upper - lower < 0.2 * frozen_atr:
            continue

        future = bars[index + 1 :]
        if direction is Direction.BULLISH:
            extreme = min([float(bar.low) for bar in future], default=upper)
            fill = min(max((upper - extreme) / (upper - lower), 0), 1)
            invalidated = next(
                (bar for bar in future if float(bar.close) < lower - 0.1 * frozen_atr), None
            )
        else:
            extreme = max([float(bar.high) for bar in future], default=lower)
            fill = min(max((extreme - lower) / (upper - lower), 0), 1)
            invalidated = next(
                (bar for bar in future if float(bar.close) > upper + 0.1 * frozen_atr), None
            )
        if fill == 0:
            fill_status = FvgFillStatus.UNTOUCHED
        elif fill < 0.5:
            fill_status = FvgFillStatus.PARTIAL
        elif fill < 1:
            fill_status = FvgFillStatus.FILLED_50
        else:
            fill_status = FvgFillStatus.FULLY_FILLED
        results.append(
            FvgResult(
                fvg_id=f"fvg-{third.bar_id}",
                direction=direction,
                timeframe=third.interval,
                creation_rule_version="FVG_WICK_3BAR_ATR_FROZEN_V1",
                created_at=third.close_at,
                lower=decimal_string(lower, 10),
                upper=decimal_string(upper, 10),
                midpoint=decimal_string((lower + upper) / 2, 10),
                atr_at_creation=decimal_string(frozen_atr, 10),
                atr_period=14,
                minimum_gap_atr_multiple="0.2",
                fill_status=fill_status,
                max_fill_fraction=round(fill, 12),
                validity_status=FvgValidityStatus.INVALIDATED if invalidated else FvgValidityStatus.VALID,
                invalidated_at=invalidated.close_at if invalidated else None,
                source_refs=sorted({first.source_ref, third.source_ref}),
                confirmation=Confirmation(
                    status=ConfirmationStatus.CONFIRMED,
                    confirmed_at=third.close_at,
                    left_window=2,
                    right_window=0,
                    can_repaint=False,
                ),
            )
        )
    return results


def _pivots(values: list[float | None], left: int = 3, right: int = 3, high: bool = False) -> list[int]:
    result = []
    for index in range(left, len(values) - right):
        center = values[index]
        window = values[index - left : index + right + 1]
        if center is None or any(value is None for value in window):
            continue
        numeric = [float(value) for value in window if value is not None]
        if high and float(center) == max(numeric) and numeric.count(float(center)) == 1:
            result.append(index)
        if not high and float(center) == min(numeric) and numeric.count(float(center)) == 1:
            result.append(index)
    return result


def compute_divergences(bars: list[Bar]) -> list[DivergenceResult]:
    closes = [float(bar.close) for bar in bars]
    rsi_values = rsi(closes, 14)
    _, _, histogram = macd(closes)
    atr_values = atr(bars, 14)
    results: list[DivergenceResult] = []
    components = [(OscillatorComponent.RSI_LINE, rsi_values), (OscillatorComponent.MACD_HISTOGRAM, histogram)]
    for component, oscillator in components:
        for use_high in (False, True):
            price_values = [float(bar.high if use_high else bar.low) for bar in bars]
            price_pivots = _pivots(price_values, high=use_high)
            osc_pivots = _pivots(oscillator, high=use_high)
            for first_index, second_index in zip(price_pivots, price_pivots[1:]):
                gap = second_index - first_index
                if not 5 <= gap <= 60 or atr_values[second_index] is None:
                    continue
                matches = []
                for target in (first_index, second_index):
                    nearby = [index for index in osc_pivots if abs(index - target) <= 2]
                    if not nearby:
                        matches = []
                        break
                    matches.append(min(nearby, key=lambda index: (abs(index - target), index)))
                if len(matches) != 2:
                    continue
                p1, p2 = price_values[first_index], price_values[second_index]
                o1, o2 = float(oscillator[matches[0]]), float(oscillator[matches[1]])
                if abs(p2 - p1) < 0.1 * float(atr_values[second_index]):
                    continue
                threshold = 3.0 if component is OscillatorComponent.RSI_LINE else 0.1 * (pstdev([float(v) for v in oscillator[max(0, second_index - 59): second_index + 1] if v is not None]) or math.inf)
                if abs(o2 - o1) < threshold:
                    continue
                kind: DivergenceKind | None = None
                if not use_high and p2 < p1 and o2 > o1:
                    kind = DivergenceKind.REGULAR_BULLISH
                elif not use_high and p2 > p1 and o2 < o1:
                    kind = DivergenceKind.HIDDEN_BULLISH
                elif use_high and p2 > p1 and o2 < o1:
                    kind = DivergenceKind.REGULAR_BEARISH
                elif use_high and p2 < p1 and o2 > o1:
                    kind = DivergenceKind.HIDDEN_BEARISH
                if kind is None:
                    continue
                results.append(
                    DivergenceResult(
                        divergence_id=f"div-{component.value.lower()}-{bars[second_index].bar_id}",
                        kind=kind,
                        oscillator_component=component,
                        algorithm_version="divergence-pivot-3x3-v2",
                        price_anchors=[
                            AnchorRef(bar_id=bars[index].bar_id, at=bars[index].close_at, field="high" if use_high else "low", value=decimal_string(price_values[index], 10))
                            for index in (first_index, second_index)
                        ],
                        oscillator_anchors=[
                            AnchorRef(bar_id=bars[index].bar_id, at=bars[index].close_at, field="indicator", value=decimal_string(float(oscillator[index]), 10))
                            for index in matches
                        ],
                        pivot_left=3,
                        pivot_right=3,
                        maximum_anchor_gap_bars=60,
                        signal_at=bars[max(second_index, *matches) + 3].close_at,
                        confirmation=Confirmation(
                            status=ConfirmationStatus.CONFIRMED,
                            confirmed_at=bars[max(second_index, *matches) + 3].close_at,
                            left_window=3,
                            right_window=3,
                            can_repaint=False,
                        ),
                        source_refs=sorted({bars[first_index].source_ref, bars[second_index].source_ref}),
                    )
                )
    return results


def compute_support_resistance_and_trend(
    bars: list[Bar],
) -> tuple[SupportResistanceResult, TrendStructureResult]:
    if not bars:
        raise ValueError("support/resistance requires at least one bar")
    lookback = 120
    left = right = 3
    start = max(0, len(bars) - lookback)
    lows = [float(bar.low) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    low_pivots = [index for index in _pivots(lows, left, right, False) if index >= start]
    high_pivots = [index for index in _pivots(highs, left, right, True) if index >= start]
    atr_values = atr(bars, 14)
    latest_atr = next((value for value in reversed(atr_values) if value is not None), None)
    tolerance = 0.25 * float(latest_atr or max(abs(float(bars[-1].close)) * 0.005, 1e-9))

    def clustered(kind: TechnicalLevelKind, indexes: list[int], values: list[float]) -> list[TechnicalLevel]:
        clusters: list[list[int]] = []
        for index in indexes:
            match = next(
                (
                    cluster
                    for cluster in clusters
                    if abs(values[index] - fmean(values[item] for item in cluster)) <= tolerance
                ),
                None,
            )
            (match if match is not None else clusters.append([index]))
            if match is not None:
                match.append(index)
        ranked = sorted(clusters, key=lambda item: (-len(item), -item[-1]))[:3]
        return [
            TechnicalLevel(
                level_id=(
                    f"tech_level_{kind.value.lower()}_{bars[cluster[-1]].bar_id}"
                ),
                kind=kind,
                price=decimal_string(fmean(values[index] for index in cluster), 10),
                touch_count=len(cluster),
                first_touch_at=bars[cluster[0]].close_at,
                latest_touch_at=bars[cluster[-1]].close_at,
                anchor_bar_ids=[bars[index].bar_id for index in cluster],
                confirmed=True,
            )
            for cluster in ranked
        ]

    levels = [
        *clustered(TechnicalLevelKind.SUPPORT, low_pivots, lows),
        *clustered(TechnicalLevelKind.RESISTANCE, high_pivots, highs),
    ]
    reference=float(bars[-1].close)
    levels=[level.model_copy(update={
        'role_status':'AT_LEVEL' if float(level.price)==reference else 'BROKEN_HISTORICAL'
            if (level.kind is TechnicalLevelKind.SUPPORT and float(level.price)>reference) or
               (level.kind is TechnicalLevelKind.RESISTANCE and float(level.price)<reference) else 'CORRECT_SIDE_ONLY',
        'role_as_of':bars[-1].close_at,'reference_close':bars[-1].close}) for level in levels]
    level_evidence = [
        Evidence(
            evidence_id=f"ev_{level.level_id}",
            claim=(
                f"Historical confirmed {level.kind.value.lower()} pivot cluster at {level.price} "
                f"with {level.touch_count} touch(es); role_status={level.role_status} versus last close {reference}. "
                "Correct price side alone does not confirm a successful retest."
            ),
            rule="pivot-cluster-3x3-atr025-v1",
            observations=[
                EvidenceObservation(
                    ref_type=EvidenceRefType.BAR,
                    ref_id=level.anchor_bar_ids[-1],
                    field="low" if level.kind is TechnicalLevelKind.SUPPORT else "high",
                    value=level.price,
                    unit="price",
                    observed_at=level.latest_touch_at,
                )
            ],
            source_refs=sorted({bars[index].source_ref for index in [*low_pivots, *high_pivots]})
            or [bars[-1].source_ref],
            quality="HIGH" if QualityFlag.SYNTHETIC_FIXTURE not in bars[-1].quality_flags else "MEDIUM",
        )
        for level in levels
    ]
    support_resistance = SupportResistanceResult(
        result_id=f"sr-{bars[-1].instrument_id.replace(':', '-').lower()}-{bars[-1].interval.value}",
        algorithm_version="pivot-cluster-3x3-atr025-v1",
        input_granularity=bars[-1].interval,
        lookback_bars=120,
        pivot_left=3,
        pivot_right=3,
        cluster_tolerance_atr_multiple="0.25",
        levels=levels,
        source_refs=sorted({bar.source_ref for bar in bars[start:]}),
        evidence=level_evidence,
        limitations=[
            "Levels are confirmed pivot clusters, not order-book liquidity guarantees.",
            "The latest three bars cannot form confirmed pivots because pivot_right=3.",
        ],
    )

    selected_highs = high_pivots[-2:]
    selected_lows = low_pivots[-2:]
    state = TrendState.INSUFFICIENT_CONFIRMED_PIVOTS
    if len(selected_highs) == 2 and len(selected_lows) == 2:
        if highs[selected_highs[1]] > highs[selected_highs[0]] and lows[selected_lows[1]] > lows[selected_lows[0]]:
            state = TrendState.UPTREND
        elif highs[selected_highs[1]] < highs[selected_highs[0]] and lows[selected_lows[1]] < lows[selected_lows[0]]:
            state = TrendState.DOWNTREND
        else:
            state = TrendState.RANGE_OR_MIXED
    anchor_indexes = [*selected_highs, *selected_lows]
    signal_index = min(max(anchor_indexes, default=-right) + right, len(bars) - 1)
    confirmed = state is not TrendState.INSUFFICIENT_CONFIRMED_PIVOTS
    high_anchors = [
        AnchorRef(
            bar_id=bars[index].bar_id,
            at=bars[index].close_at,
            field="high",
            value=decimal_string(highs[index], 10),
        )
        for index in selected_highs
    ]
    low_anchors = [
        AnchorRef(
            bar_id=bars[index].bar_id,
            at=bars[index].close_at,
            field="low",
            value=decimal_string(lows[index], 10),
        )
        for index in selected_lows
    ]
    trend_evidence = []
    if confirmed:
        trend_evidence.append(
            Evidence(
                evidence_id=f"ev_trend_{bars[-1].instrument_id.replace(':', '-').lower()}_{bars[-1].interval.value}",
                claim=f"Confirmed swing structure state is {state.value}.",
                rule="confirmed-pivot-hh-hl-3x3-v1",
                observations=[
                    EvidenceObservation(
                        ref_type=EvidenceRefType.BAR,
                        ref_id=anchor.bar_id,
                        field=anchor.field,
                        value=anchor.value,
                        unit="price",
                        observed_at=anchor.at,
                    )
                    for anchor in [*high_anchors, *low_anchors]
                ],
                source_refs=sorted({bars[index].source_ref for index in anchor_indexes}),
                quality="HIGH" if QualityFlag.SYNTHETIC_FIXTURE not in bars[-1].quality_flags else "MEDIUM",
            )
        )
    trend = TrendStructureResult(
        result_id=f"trend-{bars[-1].instrument_id.replace(':', '-').lower()}-{bars[-1].interval.value}",
        algorithm_version="confirmed-pivot-hh-hl-3x3-v1",
        input_granularity=bars[-1].interval,
        state=state,
        pivot_left=3,
        pivot_right=3,
        high_anchors=high_anchors,
        low_anchors=low_anchors,
        signal_at=bars[signal_index].close_at if confirmed else None,
        confirmation=Confirmation(
            status=ConfirmationStatus.CONFIRMED if confirmed else ConfirmationStatus.NOT_APPLICABLE,
            confirmed_at=bars[signal_index].close_at if confirmed else None,
            left_window=3,
            right_window=3,
            can_repaint=False,
        ),
        source_refs=sorted({bar.source_ref for bar in bars[start:]}),
        evidence=trend_evidence,
        limitations=[
            "Trend state compares only the two latest confirmed swing highs and lows.",
            "A mixed higher/lower swing sequence is classified as RANGE_OR_MIXED.",
        ],
    )
    return support_resistance, trend
