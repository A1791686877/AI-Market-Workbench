from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.fixtures import decimal_string
from app.models import Bar, Interval, QualityFlag, RawOhlcv, SessionType
from app.trading_calendar import xnys_session_bounds


NY = ZoneInfo("America/New_York")


INTRADAY_GROUP_SIZE = {
    Interval.HOUR_1: 4,
    Interval.HOUR_2: 8,
    Interval.MIN_195: 13,
    Interval.HOUR_4: 16,
}


def _aggregate(group: list[Bar], interval: Interval, expected_count: int) -> Bar:
    first, last = group[0], group[-1]
    flags = {flag for bar in group for flag in bar.quality_flags}
    partial = len(group) < expected_count
    if partial:
        flags.add(QualityFlag.PARTIAL_INTERVAL)
    trade_counts = [bar.trade_count for bar in group]
    raw = None
    if all(bar.raw_ohlcv is not None for bar in group):
        raw = RawOhlcv(
            open=group[0].raw_ohlcv.open,
            high=decimal_string(max(float(bar.raw_ohlcv.high) for bar in group), 10),
            low=decimal_string(min(float(bar.raw_ohlcv.low) for bar in group), 10),
            close=group[-1].raw_ohlcv.close,
            volume=decimal_string(sum(float(bar.raw_ohlcv.volume) for bar in group), 10),
        )
    return Bar(
        bar_id=f"agg-{interval.value}-{first.bar_id}-{last.bar_id}",
        instrument_id=first.instrument_id,
        interval=interval,
        open_at=first.open_at,
        close_at=last.close_at,
        open=first.open,
        high=decimal_string(max(float(bar.high) for bar in group), 10),
        low=decimal_string(min(float(bar.low) for bar in group), 10),
        close=last.close,
        volume=decimal_string(sum(float(bar.volume) for bar in group), 10),
        trade_count=sum(value for value in trade_counts if value is not None) if all(value is not None for value in trade_counts) else None,
        session=first.session,
        price_basis=first.price_basis,
        raw_ohlcv=raw,
        confirmed=all(bar.confirmed for bar in group),
        partial_interval=partial,
        source_ref=first.source_ref,
        quality_flags=sorted(flags, key=lambda item: item.value),
    )


def _daily_groups(bars: list[Bar]) -> list[list[Bar]]:
    groups: dict[object, list[Bar]] = defaultdict(list)
    for bar in bars:
        if bar.session is SessionType.US_RTH:
            key = bar.open_at.astimezone(NY).date()
        else:
            key = bar.open_at.astimezone(UTC).date()
        groups[key].append(bar)
    return [groups[key] for key in sorted(groups)]


def _calendar_aggregate(daily: list[Bar], interval: Interval) -> list[Bar]:
    groups: dict[object, list[Bar]] = defaultdict(list)
    for bar in daily:
        stamp = bar.open_at.astimezone(NY if bar.session is SessionType.US_RTH else UTC)
        key = (stamp.isocalendar().year, stamp.isocalendar().week) if interval is Interval.WEEK_1 else (stamp.year, stamp.month)
        groups[key].append(bar)
    results = []
    for key in sorted(groups):
        group = groups[key]
        local = group[0].open_at.astimezone(NY if group[0].session is SessionType.US_RTH else UTC)
        start = local.date() - timedelta(days=local.weekday()) if interval is Interval.WEEK_1 else local.date().replace(day=1)
        end = start + timedelta(days=7) if interval is Interval.WEEK_1 else (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        expected_dates = set()
        day = start
        while day < end:
            if group[0].session is not SessionType.US_RTH or xnys_session_bounds(day.isoformat()) is not None:
                expected_dates.add(day)
            day += timedelta(days=1)
        actual_dates = {b.open_at.astimezone(NY if b.session is SessionType.US_RTH else UTC).date() for b in group}
        # A developing calendar bucket or a bucket with missing daily bars is not
        # silently passed to confirmed indicators, even if every input day closed.
        if actual_dates == expected_dates:
            results.append(_aggregate(group, interval, len(expected_dates)))
    return results


def _crypto_intraday_aggregate(
    bars: list[Bar], interval: Interval, size: int
) -> list[Bar]:
    seconds = size * 15 * 60
    groups: dict[object, list[Bar]] = defaultdict(list)
    for bar in bars:
        stamp = bar.open_at.astimezone(UTC)
        # Freeze all 24x7 intraday boundaries to Unix epoch UTC. This avoids
        # request-dependent anchors and avoids a short daily tail for 195m,
        # which does not divide a 24-hour day evenly.
        key = int(stamp.timestamp()) // seconds
        groups[key].append(bar)
    return [_aggregate(groups[key], interval, size) for key in sorted(groups)
            if len(groups[key]) == size
            and int(groups[key][0].open_at.timestamp()) == key * seconds
            and all((b.open_at-a.close_at).total_seconds() == 0 for a,b in zip(groups[key], groups[key][1:]))]


def aggregate_bars(bars: list[Bar], interval: Interval, cutoff_at: datetime | None = None) -> list[Bar]:
    confirmed = [bar for bar in sorted(bars, key=lambda item: item.open_at) if bar.confirmed]
    if cutoff_at is not None:
        confirmed = [bar for bar in confirmed if bar.close_at <= cutoff_at]
    if interval is Interval.MIN_15:
        return confirmed
    if not confirmed:
        return []
    if interval in INTRADAY_GROUP_SIZE:
        size = INTRADAY_GROUP_SIZE[interval]
        if confirmed[0].session is SessionType.US_RTH:
            result: list[Bar] = []
            for day in _daily_groups(confirmed):
                bounds = xnys_session_bounds(day[0].open_at.astimezone(NY).date().isoformat())
                if bounds is None:
                    continue
                opened, closed = bounds
                groups = defaultdict(list)
                for bar in day:
                    if opened <= bar.open_at and bar.close_at <= closed:
                        groups[int((bar.open_at-opened).total_seconds()) // (size*900)].append(bar)
                for index, group in sorted(groups.items()):
                    bucket_start = opened + timedelta(seconds=index*size*900)
                    bucket_end = min(closed, bucket_start+timedelta(seconds=size*900))
                    expected = int((bucket_end-bucket_start).total_seconds()) // 900
                    if len(group) == expected and group[0].open_at == bucket_start and group[-1].close_at == bucket_end:
                        result.append(_aggregate(group, interval, size))
            return result
        return _crypto_intraday_aggregate(confirmed, interval, size)
    if confirmed[0].interval is Interval.DAY_1:
        daily = confirmed
        if interval is Interval.DAY_1:
            return daily
        if interval in {Interval.WEEK_1, Interval.MONTH_1}:
            return _calendar_aggregate(daily, interval)
        raise ValueError(f"cannot aggregate daily bars to {interval.value}")
    daily = [_aggregate(group, Interval.DAY_1, 26 if group[0].session is SessionType.US_RTH else 96) for group in _daily_groups(confirmed)]
    if interval is Interval.DAY_1:
        return daily
    if interval in {Interval.WEEK_1, Interval.MONTH_1}:
        return _calendar_aggregate(daily, interval)
    raise ValueError(f"unsupported interval: {interval}")
