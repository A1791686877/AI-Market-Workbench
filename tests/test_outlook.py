"""Rule engine tests: three synthetic regimes must select A / B / C."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.fixtures import decimal_string
from app.models import Bar, Interval, PriceBasis, SessionType
from app.outlook import RULES_VERSION, build_outlook

START = datetime(2024, 1, 1, tzinfo=UTC)
INSTRUMENT = "CRYPTO:BTCUSDT:SPOT"


def make_bars(prices: list[float], instrument: str = INSTRUMENT) -> list[Bar]:
    bars = []
    for index, close in enumerate(prices):
        open_price = prices[index - 1] if index else close
        open_at = START + timedelta(days=index)
        bars.append(Bar(
            bar_id=f"test-{index:04d}",
            instrument_id=instrument,
            interval=Interval.DAY_1,
            open_at=open_at,
            close_at=open_at + timedelta(days=1),
            open=decimal_string(open_price, 6),
            high=decimal_string(max(open_price, close) * 1.001, 6),
            low=decimal_string(min(open_price, close) * 0.999, 6),
            close=decimal_string(close, 6),
            volume="1000",
            session=SessionType.CRYPTO_24X7,
            price_basis=PriceBasis.RAW,
            confirmed=True,
            source_ref="src-test",
        ))
    return bars


def ramp(start: float, end: float, count: int) -> list[float]:
    return [start + (end - start) * i / (count - 1) for i in range(count)]


def structure_above_200d_then_weak() -> list[float]:
    """Long uptrend, then a shallow 20-day slide that keeps price above SMA200."""
    return ramp(50, 100, 300) + ramp(100, 96, 20)


def structure_above_200d_then_strong() -> list[float]:
    """Long uptrend, then a fresh breakout to new highs."""
    return ramp(50, 100, 300) + ramp(100, 107, 6)


def structure_broken() -> list[float]:
    """Long decline: below a falling SMA200, with negative monthly momentum."""
    return ramp(200, 60, 450)


def test_weak_tape_inside_intact_structure_picks_dip_then_up() -> None:
    report = build_outlook(make_bars(structure_above_200d_then_weak()))
    assert report.primary == "A", report.paths
    assert report.structure_score > report.momentum_score


def test_reclaim_to_new_highs_picks_direct_up() -> None:
    report = build_outlook(make_bars(structure_above_200d_then_strong()))
    assert report.primary == "B", report.paths
    assert report.momentum_score > 50


def test_broken_structure_picks_breakdown() -> None:
    report = build_outlook(make_bars(structure_broken()))
    assert report.primary == "C", report.paths
    assert report.structure_score < 40


def test_levels_and_triggers_are_ordered_and_present() -> None:
    report = build_outlook(make_bars(structure_above_200d_then_weak()))
    levels = report.levels
    assert float(levels.dip_zone_low) < float(levels.dip_zone_high)
    assert float(levels.confirm_reclaim) > float(levels.price)          # weak tape: price under SMA20
    assert float(levels.structural_support) < float(levels.price)       # still above SMA200
    assert float(levels.target_1) > float(levels.price)
    assert set(report.triggers) == {"confirm_reclaim", "confirm_breakout", "dip_zone",
                                    "invalidate_A", "warning_weekly", "cycle_turn"}
    assert "日线收盘 < " in report.narrative
    assert RULES_VERSION in report.narrative or RULES_VERSION == report.rules_version


def test_weights_sum_to_100_and_primary_has_the_largest_weight() -> None:
    report = build_outlook(make_bars(structure_above_200d_then_weak()))
    weights = {p.key: p.weight_pct for p in report.paths}
    assert sum(weights.values()) == 100
    assert weights[report.primary] == max(weights.values())


def test_same_input_gives_byte_identical_conclusion() -> None:
    bars = make_bars(structure_above_200d_then_weak())
    first = build_outlook(bars).model_dump(mode="json")
    second = build_outlook(bars).model_dump(mode="json")
    first.pop("generated_at"), second.pop("generated_at")
    assert first == second


def test_missing_intraday_inputs_are_declared_not_penalised() -> None:
    report = build_outlook(make_bars(structure_above_200d_then_weak()))
    skipped = [h.rule for h in report.momentum_hits if h.points == 0 and "skipped" in h.detail]
    assert "h4_DI+>-DI" in skipped and "h1_and_m15_uptrend" in skipped
    assert any("缺少 4h 输入" in item for item in report.limitations)


def test_equity_narrative_never_prints_a_missing_cycle_level() -> None:
    """Regression (found via MCD): the C one-liner interpolated the equity-disabled cycle floor."""
    bars = make_bars(structure_broken(), instrument="US:MCD")
    equity = build_outlook(bars, market="EQUITY", symbol="MCD", instrument_id="US:MCD")
    assert equity.primary == "C"
    assert "None" not in equity.narrative
    assert equity.levels.cycle_floor is None
    assert equity.levels.swing_low_20d in equity.narrative
    assert "20 日低" in equity.narrative

    crypto = build_outlook(make_bars(structure_broken()))
    assert crypto.primary == "C"
    assert "None" not in crypto.narrative
    assert crypto.levels.cycle_floor in crypto.narrative
