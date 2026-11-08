"""Live smoke: FRED macro snapshot (point-in-time vintage) + the derived CPI YoY series."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service import MarketWorkbenchService


def summarize(snapshot, label: str) -> dict:
    return {
        "label": label,
        "as_of": snapshot.as_of.isoformat(),
        "vintage_date": snapshot.vintage_date.isoformat(),
        "series": [
            {"series_id": item.series_id, "status": item.status.value, "latest_date":
                item.latest_observation_date.isoformat() if item.latest_observation_date else None,
             "latest": item.latest.value if item.latest else None, "units": item.units,
             "chg1": item.change_1_observation, "chg20": item.change_20_observations,
             "pct20": item.percent_change_20_observations, "derivation": item.derivation,
             "observations": len(item.observations)}
            for item in snapshot.series
        ],
        "sources": [s.source_id for s in snapshot.sources],
        "limitations": snapshot.limitations,
    }


def main() -> None:
    service = MarketWorkbenchService()
    today = service.get_macro_context(limit=30)
    out = {"today": summarize(today, "latest vintage")}
    # Point-in-time check: the same series as known ~3 months ago must differ from today's revision.
    vintage = datetime(2026, 6, 11, tzinfo=UTC)
    from app import macros as macro_module
    vintage_snapshot = service.get_macro_context(limit=30, vintage=vintage)
    out["vintage_2026_06_11"] = summarize(vintage_snapshot, "vintage 2026-06-11")
    allowlist = [spec.series_id for spec in macro_module.FRED_SERIES]
    out["allowlist"] = allowlist
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
