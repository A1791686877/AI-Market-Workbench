"""Backfill `origin` on registry rows written before the field existed.

Definition (deterministic, no guessing about intent):
    FORWARD  -> the conclusion was frozen within a week of being recorded
    REPLAY   -> the conclusion was frozen on a date materially in the past (walk-forward replay)

A replay row can never be evidence about the future, and a forward row is the only kind that can.
The rewrite is audited to data/outlook/origin-migrations.jsonl and the conclusion fields are untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FORWARD_WINDOW = timedelta(days=7)


def classify(recorded_at: datetime, as_of: datetime) -> str:
    return "REPLAY" if (recorded_at - as_of) > FORWARD_WINDOW else "FORWARD"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the migration (default: report only)")
    args = parser.parse_args()

    from app import outlook_registry as registry

    entries = registry.load_entries()
    changed = []
    updated = []
    for entry in entries:
        origin = classify(entry.recorded_at, entry.as_of)
        if origin != entry.origin:
            changed.append({"outlook_id": entry.outlook_id, "from": entry.origin, "to": origin,
                            "as_of": entry.as_of.isoformat(), "recorded_at": entry.recorded_at.isoformat()})
        updated.append(entry.model_copy(update={"origin": origin}))

    if changed and args.apply:
        archive = registry.registry_path().parent / "origin-migrations.jsonl"
        with archive.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"migrated_at": datetime.now(UTC).isoformat(),
                                     "rule": f"REPLAY iff recorded_at - as_of > {FORWARD_WINDOW}",
                                     "changes": changed}, ensure_ascii=False, sort_keys=True) + "\n")
        registry.save_entries(updated)

    summary = {}
    for entry in updated:
        summary[entry.origin] = summary.get(entry.origin, 0) + 1
    print(json.dumps({"applied": bool(changed and args.apply), "changed": len(changed),
                      "by_origin": summary, "changes": changed[:50]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
