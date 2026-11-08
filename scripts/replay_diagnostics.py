"""Diagnostics on the replay grid: primary-vs-realized confusion and per-freeze-date view.

Prints the conditional outcome of each primary call and a de-correlated view that treats one
freeze date (all eight symbols together) as a single market state.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from app import outlook_registry as registry

    entries = [e for e in registry.load_entries() if e.verification is not None and e.origin == "REPLAY"]
    matrix: dict[str, dict[str, int]] = {p: defaultdict(int) for p in ("A", "B", "C")}
    by_date: dict[str, list] = defaultdict(list)
    for entry in entries:
        matrix[entry.primary][entry.verification.path_realized] += 1
        by_date[entry.as_of.date().isoformat()].append(entry)

    out: dict = {"replay_entries": len(entries), "freeze_dates": len(by_date),
                 "confusion": {primary: dict(counts) for primary, counts in matrix.items()}}
    out["conditional_realized_share"] = {
        primary: {realized: round(count / max(1, sum(counts.values())), 3) for realized, count in counts.items()}
        for primary, counts in matrix.items()
    }
    rows = []
    for day, items in sorted(by_date.items()):
        realized = defaultdict(int)
        for item in items:
            realized[item.verification.path_realized] += 1
        primaries = defaultdict(int)
        for item in items:
            primaries[item.primary] += 1
        rows.append({"as_of": day, "symbols": len(items),
                     "primary": dict(primaries), "realized": dict(realized),
                     "primary_correct": sum(1 for i in items if i.verification.primary_correct is True)})
    out["per_freeze_date"] = rows
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
