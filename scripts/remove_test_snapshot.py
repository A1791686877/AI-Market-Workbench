"""Remove exactly one test snapshot I just created (archive payload, then delete row + file).

Nothing else is touched: the target is named explicitly on the command line.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_id")
    parser.add_argument("--reason", default="test artifact created by my own smoke run")
    args = parser.parse_args()

    database = ROOT / "data" / "workbench.db"
    snapshot_dir = ROOT / "data" / "snapshots"
    quarantine = ROOT / "data" / "orphaned-snapshots"
    quarantine.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database)
    try:
        row = connection.execute("select payload_json from snapshots where snapshot_id = ?",
                                 (args.snapshot_id,)).fetchone()
        if row is None:
            raise SystemExit(f"{args.snapshot_id} is not in the snapshots table; nothing removed")
        references = connection.execute("select count(*) from analyses where snapshot_id = ?",
                                        (args.snapshot_id,)).fetchone()[0]
        task_references = connection.execute("select count(*) from analysis_tasks where snapshot_id = ?",
                                             (args.snapshot_id,)).fetchone()[0]
        if references or task_references:
            raise SystemExit(f"refusing to remove {args.snapshot_id}: referenced by "
                             f"{references} analyses and {task_references} tasks")
        archive_path = quarantine / f"removed-test-artifact-{args.snapshot_id}.json"
        archive_path.write_text(json.dumps({
            "removed_at": datetime.now(UTC).isoformat(),
            "snapshot_id": args.snapshot_id,
            "reason": args.reason,
            "payload": row[0],
        }, ensure_ascii=False), encoding="utf-8")
        file_path = snapshot_dir / f"{args.snapshot_id}.json"
        moved = None
        if file_path.exists():
            moved = quarantine / f"removed-test-artifact-{args.snapshot_id}-payload.json"
            shutil.move(str(file_path), str(moved))
        connection.execute("delete from snapshots where snapshot_id = ?", (args.snapshot_id,))
        connection.commit()
    finally:
        connection.close()

    print(json.dumps({"snapshot_id": args.snapshot_id, "db_row_deleted": True,
                      "archive": str(archive_path), "payload_moved_to": str(moved) if moved else None},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
