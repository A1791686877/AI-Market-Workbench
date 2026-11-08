from __future__ import annotations

import pytest
from datetime import UTC, datetime

from app.bar_builder import aggregate_bars
from app.fixtures import AAPL, BARS
from app.indicators import (
    atr,
    compute_fvg,
    compute_indicators,
    compute_support_resistance_and_trend,
    compute_vpvr,
    ema,
    rsi,
    sma,
)
from app.models import (
    CalculationMode,
    FvgFillStatus,
    FvgValidityStatus,
    IndicatorName,
    IndicatorStatus,
    Interval,
    QualityFlag,
)
from app.trading_calendar import is_complete_xnys_15m_bar, xnys_session_bounds


def test_sma_and_ema_golden_case() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert sma(values, 3) == [None, None, 2.0, 3.0]
    assert ema(values, 3) == [None, None, 2.0, 3.0]


def test_rsi_golden_case() -> None:
    result = rsi([1, 2, 3, 2, 4], 3)
    assert result[3] == pytest.approx(66.6666666667)
    assert result[4] == pytest.approx(83.3333333333)


def test_atr_golden_case() -> None:
    template = BARS[AAPL.instrument_id][0]
    rows = [(10, 8, 9), (11, 9, 10), (12, 9, 11), (13, 11, 12)]
    bars = [
        template.model_copy(
            update={
                "bar_id": f"bar-atr-{index}",
                "open": str(close),
                "high": str(high),
                "low": str(low),
                "close": str(close),
                "open_at": template.open_at + index * (template.close_at - template.open_at),
                "close_at": template.close_at + index * (template.close_at - template.open_at),
            }
        )
        for index, (high, low, close) in enumerate(rows)
    ]
    result = atr(bars, 3)
    assert result[2] == pytest.approx(7 / 3)
    assert result[3] == pytest.approx(20 / 9)


def test_us_rth_195m_is_two_equal_bars_per_day_and_4h_has_partial_tail() -> None:
    source = BARS[AAPL.instrument_id]
    bars_195 = aggregate_bars(source, Interval.MIN_195)
    bars_4h = aggregate_bars(source, Interval.HOUR_4)
    assert len(bars_195) == 10
    assert all(not bar.partial_interval for bar in bars_195)
    assert all((bar.close_at - bar.open_at).total_seconds() == 195 * 60 for bar in bars_195)
    assert len(bars_4h) == 10
    assert sum(bar.partial_interval for bar in bars_4h) == 5
    assert all(QualityFlag.PARTIAL_INTERVAL in bar.quality_flags for bar in bars_4h[1::2])


def test_vpvr_is_explicitly_an_estimate() -> None:
    vpvr = compute_vpvr(BARS[AAPL.instrument_id])
    assert vpvr.calculation_mode is CalculationMode.BAR_RANGE_UNIFORM_ALLOCATION_ESTIMATE
    assert vpvr.is_estimate is True
    assert vpvr.input_granularity is Interval.MIN_15
    assert vpvr.algorithm_version == "vpvr-bar-range-uniform-v1"
    assert len(vpvr.limitations) >= 3
    assert "not trade-level" in vpvr.limitations[0]


def test_fvg_freezes_creation_atr_and_separates_fill_from_validity() -> None:
    results = compute_fvg(BARS[AAPL.instrument_id])
    assert results
    assert all(item.atr_at_creation for item in results)
    assert all(item.creation_rule_version == "FVG_WICK_3BAR_ATR_FROZEN_V1" for item in results)
    assert all(item.fill_status in set(FvgFillStatus) for item in results)
    assert all(item.validity_status in set(FvgValidityStatus) for item in results)


def test_all_phase_one_base_indicators_are_typed_and_ready() -> None:
    indicators = {item.name: item for item in compute_indicators(BARS[AAPL.instrument_id])}
    assert set(indicators) == set(IndicatorName)
    assert all(item.status is IndicatorStatus.READY for item in indicators.values())
    assert indicators[IndicatorName.MACD].current.signal is not None
    assert indicators[IndicatorName.MACD].current.histogram is not None
    assert indicators[IndicatorName.BOLLINGER_BANDS].current.upper is not None
    assert indicators[IndicatorName.BOLLINGER_BANDS].current.middle is not None
    assert indicators[IndicatorName.BOLLINGER_BANDS].current.lower is not None
    assert indicators[IndicatorName.ADX].current.plus_di is not None
    assert indicators[IndicatorName.ADX].current.minus_di is not None
    assert indicators[IndicatorName.VWAP].parameters.reset_rule == "US_RTH_SESSION"


def test_crypto_195m_uses_stable_epoch_utc_anchor_without_daily_short_tail() -> None:
    source = BARS["CRYPTO:BTCUSDT:SPOT"]
    result = aggregate_bars(source, Interval.MIN_195)
    full = [bar for bar in result if not bar.partial_interval]
    assert full
    assert all((bar.close_at - bar.open_at).total_seconds() == 195 * 60 for bar in full)
    assert all(int(bar.open_at.timestamp()) % (195 * 60) == 0 for bar in full)


def test_support_resistance_and_trend_use_confirmed_non_repainting_pivots() -> None:
    support_resistance, trend = compute_support_resistance_and_trend(BARS[AAPL.instrument_id])
    assert support_resistance.algorithm_version == "pivot-cluster-3x3-atr025-v1"
    assert support_resistance.pivot_right == 3
    assert support_resistance.levels
    assert all(level.confirmed for level in support_resistance.levels)
    assert len(support_resistance.evidence) == len(support_resistance.levels)
    assert trend.algorithm_version == "confirmed-pivot-hh-hl-3x3-v1"
    assert trend.confirmation.can_repaint is False
    if trend.signal_at is not None:
        assert trend.confirmation.confirmed_at == trend.signal_at


def test_xnys_calendar_rejects_holiday_and_observes_half_day() -> None:
    assert xnys_session_bounds("2026-07-03") is None
    half_day = xnys_session_bounds("2026-11-27")
    assert half_day is not None
    assert half_day[0] == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    assert half_day[1] == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert is_complete_xnys_15m_bar(datetime(2026, 11, 27, 17, 45, tzinfo=UTC))
    assert not is_complete_xnys_15m_bar(datetime(2026, 11, 27, 18, 0, tzinfo=UTC))
