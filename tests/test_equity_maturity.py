"""Equity (US RTH) outcome horizons must follow the real XNYS calendar."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.service import MarketWorkbenchService
from app.trading_calendar import TradingCalendarCoverageError, maturity_after_trading_minutes

NY = ZoneInfo("America/New_York")


def et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NY)


def test_horizon_skips_a_weekday_holiday() -> None:
    # Friday 2026-09-04 15:00 ET + 390 RTH minutes = Tuesday 2026-09-08 15:00 ET.
    # Monday 2026-09-07 is Labor Day and has no session at all.
    result = MarketWorkbenchService._equity_maturity(et(2026, 9, 4, 15), 390)
    assert result.astimezone(NY) == et(2026, 9, 8, 15)


def test_horizon_skips_thanksgiving_and_uses_the_real_half_day_close() -> None:
    # Thursday 2026-11-26 is a holiday; Friday 2026-11-27 closes at 13:00 ET.
    # 390 minutes therefore run 210 minutes on the half day + 180 minutes on Monday.
    result = MarketWorkbenchService._equity_maturity(et(2026, 11, 26, 12), 390)
    assert result.astimezone(NY) == et(2026, 11, 30, 12, 30)


def test_horizon_started_on_a_half_day_afternoon_is_not_trading_time() -> None:
    result = MarketWorkbenchService._equity_maturity(et(2026, 11, 27, 12), 390)
    assert result.astimezone(NY) == et(2026, 11, 30, 15)


def test_horizon_can_end_exactly_at_the_half_day_close() -> None:
    result = MarketWorkbenchService._equity_maturity(et(2026, 11, 27, 12), 60)
    assert result.astimezone(NY) == et(2026, 11, 27, 13)


def test_zero_horizon_is_the_aligned_start() -> None:
    start = et(2026, 9, 4, 15)
    assert maturity_after_trading_minutes(start, 0) == start.astimezone(ZoneInfo("UTC"))


def test_weekend_start_moves_to_the_next_session() -> None:
    result = MarketWorkbenchService._equity_maturity(et(2026, 9, 5, 12), 1)
    assert result.astimezone(NY) == et(2026, 9, 8, 9, 31)


def test_out_of_calendar_coverage_fails_closed_instead_of_guessing() -> None:
    with pytest.raises(TradingCalendarCoverageError):
        maturity_after_trading_minutes(datetime(2028, 1, 3, tzinfo=NY), 1)
