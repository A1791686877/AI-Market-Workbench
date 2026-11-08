"""Bounded public-history backfill. Each successful page commits before next request."""
from datetime import UTC, datetime, timedelta
from typing import Literal
from pathlib import Path
from pydantic import AwareDatetime
from app.models import StrictModel, DataProvider, Interval, InstrumentRef
from app.market_cache import market_cache, _safe_segment
from app.config import settings
from app.providers import OkxPublicProvider, ProviderHistoryEmpty
from app.logging_config import audit_event
from app.file_lock import exclusive_lock


class HistoryProgress(StrictModel):
    instrument_id: str
    interval: Interval
    target_start: AwareDatetime
    cursor_end: AwareDatetime
    status: Literal['NOT_STARTED','LOCAL_PAGE_BUDGET','TARGET_REACHED','PROVIDER_EMPTY_PAGE','REQUEST_FAILED']
    committed_pages: int = 0
    last_error_type: str | None = None
    updated_at: AwareDatetime
    limitation: str = 'Provider-empty is only a statement about this endpoint/cursor, not proof of global asset inception. Internal gaps are evaluated separately.'


def backfill(instrument: InstrumentRef, interval: Interval, target_start: datetime, max_pages: int=5) -> HistoryProgress:
    path=settings.root/'cache/history-progress'/f'{_safe_segment(instrument.instrument_id)}-{interval.value}.lock'
    with exclusive_lock(path):
        return _backfill_locked(instrument,interval,target_start,max_pages)


def _backfill_locked(instrument: InstrumentRef, interval: Interval, target_start: datetime, max_pages: int=5) -> HistoryProgress:
    if not 1<=max_pages<=50:raise ValueError('Per-run request budget must be 1..50')
    path=settings.root/'cache/history-progress'/f'{_safe_segment(instrument.instrument_id)}-{interval.value}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    cached=market_cache.load(DataProvider.OKX,instrument.instrument_id,interval)
    current=HistoryProgress.model_validate_json(path.read_text()) if path.exists() else None
    end=cached.bars[0].open_at if cached and cached.bars else datetime.now(UTC)
    if current and current.target_start==target_start:
        end=min(end,current.cursor_end)
        if current.status in ('TARGET_REACHED','PROVIDER_EMPTY_PAGE'):return current
    current=HistoryProgress(instrument_id=instrument.instrument_id,interval=interval,target_start=target_start,
        cursor_end=end,status='NOT_STARTED',committed_pages=current.committed_pages if current else 0,updated_at=datetime.now(UTC))
    def save():
        current.updated_at=datetime.now(UTC)
        temp=path.with_suffix('.tmp');temp.write_text(current.model_dump_json(indent=2),encoding='utf-8');temp.replace(path)
        audit_event('history_checkpoint',instrument_id=instrument.instrument_id,interval=interval.value,
                    status=current.status,cursor_end=current.cursor_end.isoformat(),committed_pages=current.committed_pages)
    provider=OkxPublicProvider()
    try:
        for _ in range(max_pages):
            if current.cursor_end<=target_start:
                current.status='TARGET_REACHED';break
            try:
                bars,source=provider.fetch_bars(instrument,interval,target_start,current.cursor_end,max_pages=1)
                if bars[0].open_at>=current.cursor_end:raise RuntimeError('Non-progressing provider cursor')
                market_cache.save(DataProvider.OKX,instrument.instrument_id,interval,bars,source)
                current.cursor_end=bars[0].open_at;current.committed_pages+=1
                current.status='TARGET_REACHED' if current.cursor_end<=target_start else 'LOCAL_PAGE_BUDGET'
                current.last_error_type=None;save()
            except ProviderHistoryEmpty:
                current.status='PROVIDER_EMPTY_PAGE';break
            except Exception as exc:
                current.status='REQUEST_FAILED';current.last_error_type=type(exc).__name__;break
        save()
        return current
    finally:
        provider.http.close()
