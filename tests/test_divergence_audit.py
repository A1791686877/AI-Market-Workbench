"""Divergence audit: recency, retirement by price, and the aggregated flag."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.divergence_audit import audit, describe
from app.fixtures import decimal_string
from app.models import (
    AnchorRef,
    Bar,
    Confirmation,
    ConfirmationStatus,
    DivergenceKind,
    DivergenceResult,
    Interval,
    OscillatorComponent,
    PriceBasis,
    SessionType,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def make_bars(rows: list[tuple[float, float, float]]) -> list[Bar]:
    """rows = (high, low, close) per day, oldest first."""
    bars = []
    previous = rows[0][2]
    for index, (high, low, close) in enumerate(rows):
        open_at = START + timedelta(days=index)
        bars.append(Bar(
            bar_id=f"bar-{index:03d}", instrument_id="US:TEST", interval=Interval.DAY_1,
            open_at=open_at, close_at=open_at + timedelta(days=1),
            open=decimal_string(min(max(previous, low), high)), high=decimal_string(high),
            low=decimal_string(low), close=decimal_string(close), volume="1000",
            session=SessionType.US_RTH, price_basis=PriceBasis.RAW, confirmed=True, source_ref="src-test",
        ))
        previous = close
    return bars


def make_divergence(kind: DivergenceKind, oscillator: OscillatorComponent, bars: list[Bar],
                    anchor_1: int, anchor_2: int, signal_index: int,
                    price_1: float = 100.0, price_2: float = 105.0,
                    osc_1: float = 70.0, osc_2: float = 60.0) -> DivergenceResult:
    return DivergenceResult(
        divergence_id=f"div-test-{kind.value.lower()}-{anchor_2}",
        kind=kind, oscillator_component=oscillator, algorithm_version="divergence-3x3-v1",
        price_anchors=[
            AnchorRef(bar_id=bars[anchor_1].bar_id, at=bars[anchor_1].close_at, field="high",
                      value=decimal_string(price_1)),
            AnchorRef(bar_id=bars[anchor_2].bar_id, at=bars[anchor_2].close_at, field="high",
                      value=decimal_string(price_2)),
        ],
        oscillator_anchors=[
            AnchorRef(bar_id=bars[anchor_1].bar_id, at=bars[anchor_1].close_at, field="indicator",
                      value=decimal_string(osc_1)),
            AnchorRef(bar_id=bars[anchor_2].bar_id, at=bars[anchor_2].close_at, field="indicator",
                      value=decimal_string(osc_2)),
        ],
        pivot_left=3, pivot_right=3, maximum_anchor_gap_bars=60,
        signal_at=bars[signal_index].close_at,
        confirmation=Confirmation(status=ConfirmationStatus.CONFIRMED, confirmed_at=bars[signal_index].close_at,
                                  left_window=3, right_window=3, can_repaint=False),
        source_refs=["src-test"],
    )


def _top_divergence_bars(retire: bool = False) -> list[Bar]:
    """Price makes a higher high at index 7 (105); nothing after it exceeds 105 unless `retire`."""
    rows = ([(101, 99, 100)] * 5
            + [(103, 101, 102), (104, 102, 103), (105, 103, 104)]   # index 7 = second anchor
            + [(104.5, 102, 103), (104.0, 101, 102)])
    if retire:
        rows += [(112, 105, 111), (113, 108, 112)]
    return make_bars(rows)


def test_recent_unretired_top_divergence_is_live() -> None:
    bars = _top_divergence_bars()
    divergence = make_divergence(DivergenceKind.REGULAR_BEARISH, OscillatorComponent.RSI_LINE,
                                 bars, anchor_1=2, anchor_2=7, signal_index=9, price_2=105.0)
    audited = describe(divergence, bars, relevance_bars=20)
    assert audited.pressure == "TOP"
    assert audited.recent is True and audited.retired is False and audited.mattered is True
    assert "顶背离" in audited.sentence() and "尚未被否定" in audited.sentence()


def test_price_exceeding_the_second_anchor_retires_a_top_divergence() -> None:
    # After the divergence's second high (105), price prints 112 → the top it flagged was broken.
    bars = _top_divergence_bars(retire=True)
    divergence = make_divergence(DivergenceKind.REGULAR_BEARISH, OscillatorComponent.RSI_LINE,
                                 bars, anchor_1=2, anchor_2=7, signal_index=9, price_2=105.0)
    audited = describe(divergence, bars, relevance_bars=20)
    assert audited.retired is True and audited.mattered is False
    assert "已被价格否定" in audited.sentence()
    summary = audit([divergence], bars, "1d")
    assert summary["retired_top"] == 1
    assert summary["live_top"] == [] and summary["live_top_sentence"] is None


def test_old_divergence_is_not_reported_as_live() -> None:
    bars = make_bars([(101, 99, 100)] * 5 + [(106, 104, 105)] * 40)
    divergence = make_divergence(DivergenceKind.REGULAR_BEARISH, OscillatorComponent.MACD_HISTOGRAM,
                                 bars, anchor_1=2, anchor_2=7, signal_index=9, price_2=106.0)
    audited = describe(divergence, bars, relevance_bars=20)
    assert audited.bars_since_second_anchor > 20
    assert audited.recent is False and audited.mattered is False
    summary = audit([divergence], bars, "1d")
    assert summary["live_top"] == []
    assert summary["most_recent_top"], "history is still reported, just not as live"


def test_macd_histogram_is_treated_as_a_bottom_count_not_a_top() -> None:
    """Regression: MACD *histogram* was inverted once; the first anchor must be the lower low."""
    bars = make_bars([(101, 99, 100)] * 5 + [(101, 90, 95)] * 5)
    bullish = DivergenceResult(
        divergence_id="div-test-bull",
        kind=DivergenceKind.REGULAR_BULLISH, oscillator_component=OscillatorComponent.MACD_HISTOGRAM,
        algorithm_version="divergence-3x3-v1",
        price_anchors=[
            AnchorRef(bar_id=bars[2].bar_id, at=bars[2].close_at, field="low", value=decimal_string(99.0)),
            AnchorRef(bar_id=bars[7].bar_id, at=bars[7].close_at, field="low", value=decimal_string(90.0)),
        ],
        oscillator_anchors=[
            AnchorRef(bar_id=bars[2].bar_id, at=bars[2].close_at, field="indicator", value=decimal_string(-2.0)),
            AnchorRef(bar_id=bars[7].bar_id, at=bars[7].close_at, field="indicator", value=decimal_string(-0.5)),
        ],
        pivot_left=3, pivot_right=3, maximum_anchor_gap_bars=60,
        signal_at=bars[9].close_at,
        confirmation=Confirmation(status=ConfirmationStatus.CONFIRMED, confirmed_at=bars[9].close_at,
                                  left_window=3, right_window=3, can_repaint=False),
        source_refs=["src-test"],
    )
    audited = describe(bullish, bars, relevance_bars=20)
    assert audited.pressure == "BOTTOM"
    summary = audit([bullish], bars, "1d")
    assert summary["counts"] == {"REGULAR_BULLISH": 1}
    assert summary["live_bottom"] and summary["live_top"] == []


def test_audit_reports_two_oscillators_on_the_same_anchor() -> None:
    bars = _top_divergence_bars()
    rsi = make_divergence(DivergenceKind.REGULAR_BEARISH, OscillatorComponent.RSI_LINE,
                          bars, anchor_1=2, anchor_2=7, signal_index=9, price_2=105.0)
    macd = make_divergence(DivergenceKind.REGULAR_BEARISH, OscillatorComponent.MACD_HISTOGRAM,
                           bars, anchor_1=2, anchor_2=7, signal_index=9, price_2=105.0)
    summary = audit([rsi, macd], bars, "1d")
    assert "MACD_HISTOGRAM+RSI_LINE" in summary["live_top_sentence"]
    assert "2 个未失效顶背离" in summary["live_top_sentence"]


def test_caveats_always_state_the_no_repaint_blind_spot() -> None:
    bars = make_bars([(101, 99, 100)] * 5)
    summary = audit([], bars, "1d")
    assert summary["counts"] == {} and summary["live_top_sentence"] is None
    assert any("正在形成" in item for item in summary["caveats"])
