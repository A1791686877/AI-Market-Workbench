from __future__ import annotations

import re
import uuid
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.config import settings
from app.logging_config import audit_event
from app.models import Bar, CachedBarsDocument, DataProvider, Interval, SourceEnvelope
from app.storage import canonical_json
from app.file_lock import exclusive_lock


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()


class MarketCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or settings.root / "cache" / "market"

    def _path(self, provider: DataProvider, instrument_id: str, interval: Interval) -> Path:
        # Versioned namespace preserves the previous records without reusing their
        # inconsistent OKX spot quote-volume normalization.
        namespace = "okx-base-volume-v2" if provider is DataProvider.OKX else provider.value.lower()
        return self.root / namespace / f"{_safe_segment(instrument_id)}-{interval.value}.json"

    def save(self, provider, instrument_id, interval, bars, source) -> CachedBarsDocument:
        with exclusive_lock(self._path(provider, instrument_id, interval).with_suffix('.lock')):
            return self._save_locked(provider, instrument_id, interval, bars, source)

    def _save_locked(
        self,
        provider: DataProvider,
        instrument_id: str,
        interval: Interval,
        bars: list[Bar],
        source: SourceEnvelope,
    ) -> CachedBarsDocument:
        previous = self.load(provider, instrument_id, interval)
        if previous:
            by_time = {b.open_at:b for b in previous.bars}
            by_time.update({b.open_at:b for b in bars})
            bars = [by_time[t] for t in sorted(by_time)]
            source = source.model_copy(update={
                'source_at':max(previous.source.source_at,source.source_at),
                'raw_sha256':hashlib.sha256((previous.source.raw_sha256+source.raw_sha256).encode()).hexdigest(),
                'limitations':list(dict.fromkeys([*source.limitations,*[x for x in previous.source.limitations if 'history endpoint was exhausted' in x],'Incremental merge; raw_sha256 is the ordered prior/page hash chain, not a single response hash.']))})
        document = CachedBarsDocument(
            provider=provider,
            instrument_id=instrument_id,
            base_interval=interval,
            cached_at=datetime.now(UTC),
            bars=bars,
            source=source,
        )
        target = self._path(provider, instrument_id, interval)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f'.{uuid.uuid4().hex}.tmp')
        temporary.write_text(canonical_json(document.model_dump(mode="json")), encoding="utf-8")
        temporary.replace(target)
        audit_event(
            "market_cache_saved",
            provider=provider.value,
            instrument_id=instrument_id,
            interval=interval.value,
            bar_count=len(bars),
            path=str(target),
        )
        return document

    def load(
        self, provider: DataProvider, instrument_id: str, interval: Interval
    ) -> CachedBarsDocument | None:
        target = self._path(provider, instrument_id, interval)
        if not target.exists():
            return None
        try:
            return CachedBarsDocument.model_validate_json(target.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            audit_event(
                "market_cache_invalid",
                provider=provider.value,
                instrument_id=instrument_id,
                interval=interval.value,
                error_type=type(exc).__name__,
            )
            return None


market_cache = MarketCache()
