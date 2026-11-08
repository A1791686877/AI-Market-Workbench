"""Manual probe: a live crypto snapshot must accept the extra CoinGecko supply source.

Writes into a throwaway store under tmp/ so the production database and
data/snapshots directory stay untouched (the service derives its snapshot directory
from the store path).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Interval, SnapshotRequest
from app.service import MarketWorkbenchService
from app.storage import Store


def main() -> None:
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "tmp") as tmp:
        service = MarketWorkbenchService(Store(Path(tmp) / "smoke.db"))
        snapshot = service.create_snapshot(SnapshotRequest(subject_id="crypto-btc",
                                                           intervals=[Interval.MIN_15, Interval.DAY_1]))
        out = {
            "snapshot_id": snapshot.snapshot_id,
            "store": tmp,
            "evidence_count": len(snapshot.evidence),
            "sources": [s.source_id for s in snapshot.sources],
            "supply": [{"name": item.name.value, "status": item.status.value, "value": item.value}
                       for item in (snapshot.market_context.supply if snapshot.market_context else [])],
            "context_present": snapshot.market_context is not None,
            "missing_items": snapshot.missing_items,
            "coverage": [(c.interval.value, c.status) for c in snapshot.timeframe_coverage],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
