from __future__ import annotations

from datetime import UTC, datetime, timedelta

import exchange_calendars as xcals
import pandas as pd


XNYS = xcals.get_calendar("XNYS")


def is_complete_xnys_15m_bar(opened_at: datetime) -> bool:
    """True only when a 15m bar is wholly inside an actual XNYS session."""
    minute = pd.Timestamp(opened_at)
    if not XNYS.is_trading_minute(minute):
        return False
    session = XNYS.minute_to_session(minute, direction="none")
    session_close = XNYS.session_close(session).to_pydatetime()
    return opened_at + timedelta(minutes=15) <= session_close


def xnys_session_bounds(session_date: str) -> tuple[datetime, datetime] | None:
    try:
        label = pd.Timestamp(session_date)
        if not XNYS.is_session(label):
            return None
    except Exception:
        # Outside the installed calendar range: report "not a valid session here"
        # instead of leaking a pandas DateOutOfBounds traceback.
        return None
    return (
        XNYS.session_open(label).to_pydatetime(),
        XNYS.session_close(label).to_pydatetime(),
    )


def calendar_first_session() -> datetime:
    """First session covered by the installed XNYS calendar (UTC)."""
    return XNYS.first_session.to_pydatetime().astimezone(UTC)


def calendar_last_session() -> datetime:
    """Last session covered by the installed XNYS calendar (UTC)."""
    return XNYS.last_session.to_pydatetime().astimezone(UTC)


class TradingCalendarCoverageError(RuntimeError):
    """The requested instant is outside the installed XNYS calendar range."""


def _ceil_to_minute(moment: datetime) -> datetime:
    aligned = moment.astimezone(UTC).replace(second=0, microsecond=0)
    return aligned if aligned == moment.astimezone(UTC) else aligned + timedelta(minutes=1)


def _next_session_open(current: datetime) -> datetime:
    """First minute mark of the next real session strictly after ``current``."""
    try:
        session = XNYS.minute_to_session(pd.Timestamp(current), direction="next")
        opened = XNYS.session_open(session).to_pydatetime().astimezone(UTC).replace(second=0, microsecond=0)
    except Exception as exc:  # out of installed calendar coverage, never guess a date
        raise TradingCalendarCoverageError(
            f"XNYS calendar does not cover {current.isoformat()}; no fallback approximation is used"
        ) from exc
    if opened <= current:
        raise TradingCalendarCoverageError(
            f"XNYS calendar returned a non-advancing session open for {current.isoformat()}"
        )
    return opened


def maturity_after_trading_minutes(start: datetime, trading_minutes: int) -> datetime:
    """The moment ``trading_minutes`` XNYS trading minutes have elapsed after ``start``.

    Counts only real session minutes: holidays, weekends and the afternoon of a
    half day are never counted, so this cannot drift into a non-session date.
    The partial minute containing ``start`` is skipped, not counted twice.
    """
    if trading_minutes < 0:
        raise ValueError("trading_minutes must not be negative")
    cursor = _ceil_to_minute(start)
    remaining = trading_minutes
    while remaining > 0:
        try:
            is_trading = XNYS.is_trading_minute(pd.Timestamp(cursor))
        except Exception as exc:  # installed calendar does not cover this instant
            raise TradingCalendarCoverageError(
                f"XNYS calendar does not cover {cursor.isoformat()}; no fallback approximation is used"
            ) from exc
        if is_trading:
            remaining -= 1
            cursor += timedelta(minutes=1)
            continue
        cursor = _next_session_open(cursor)
    return cursor
