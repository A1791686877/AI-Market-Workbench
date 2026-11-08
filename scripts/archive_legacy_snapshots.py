"""Archive schema-incompatible legacy snapshots out of the live store (user-approved option C).

For each target snapshot this script:
  1. refuses to touch anything referenced by an analysis or a task;
  2. writes the full original payload to ``data/archived-snapshots/<id>.json`` (never truncated);
  3. moves the payload file (if present) with it;
  4. deletes the database row, and appends a line to ``manifest.jsonl``.

Nothing is reconstructed or guessed: the rows stay unreadable in the archive exactly as they are.
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


def targets_from_report(report_path: Path, error_type: str) -> list[str]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = report.get("problems", {}).get("unreadable_records", [])
    return sorted({row["snapshot_id"] for row in rows if row.get("error_type") == error_type})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-report", type=Path, default=None,
                        help="取该 store-integrity 报告里 unreadable_records 的快照 id")
    parser.add_argument("--error-type", default="ValidationError")
    parser.add_argument("--ids", default="", help="逗号分隔的 snapshot_id（与 --from-report 二选一）")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "workbench.db")
    parser.add_argument("--snapshot-dir", type=Path, default=ROOT / "data" / "snapshots")
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "data" / "archived-snapshots")
    parser.add_argument("--reason", default="schema-incompatible with the current AnalysisSnapshot model")
    parser.add_argument("--apply", action="store_true", help="真正执行（默认只报告）")
    args = parser.parse_args()

    if args.from_report:
        ids = targets_from_report(args.from_report, args.error_type)
    else:
        ids = [item.strip() for item in args.ids.split(",") if item.strip()]
    if not ids:
        raise SystemExit("no snapshot ids selected")

    connection = sqlite3.connect(args.database)
    report = {"selected": len(ids), "archived": [], "skipped": [], "applied": bool(args.apply)}
    try:
        for snapshot_id in ids:
            row = connection.execute(
                "select payload_json, subject_id, cutoff_at, created_at from snapshots where snapshot_id = ?",
                (snapshot_id,)).fetchone()
            if row is None:
                report["skipped"].append({"snapshot_id": snapshot_id, "reason": "not in the snapshots table"})
                continue
            analyses = connection.execute("select count(*) from analyses where snapshot_id = ?",
                                          (snapshot_id,)).fetchone()[0]
            tasks = connection.execute("select count(*) from analysis_tasks where snapshot_id = ?",
                                       (snapshot_id,)).fetchone()[0]
            if analyses or tasks:
                report["skipped"].append({"snapshot_id": snapshot_id,
                                          "reason": f"referenced by {analyses} analyses / {tasks} tasks"})
                continue
            payload_json, subject_id, cutoff_at, created_at = row
            target = args.archive_dir / f"{snapshot_id}.json"
            payload_file = args.snapshot_dir / f"{snapshot_id}.json"
            entry = {"snapshot_id": snapshot_id, "subject_id": subject_id, "cutoff_at": cutoff_at,
                     "created_at": created_at, "archived_at": datetime.now(UTC).isoformat(),
                     "reason": args.reason, "payload_file_present": payload_file.exists(),
                     "payload_json": payload_json}
            moved_to = None
            if args.apply:
                args.archive_dir.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
                if payload_file.exists():
                    moved_to = args.archive_dir / f"{snapshot_id}.payload.json"
                    shutil.move(str(payload_file), str(moved_to))
                connection.execute("delete from snapshots where snapshot_id = ?", (snapshot_id,))
                connection.commit()
            report["archived"].append({"snapshot_id": snapshot_id, "bytes": len(payload_json or ""),
                                       "archive": str(target), "payload_moved_to": str(moved_to) if moved_to else None})
        if args.apply and report["archived"]:
            manifest = args.archive_dir / "manifest.jsonl"
            with manifest.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"archived_at": datetime.now(UTC).isoformat(),
                                         "reason": args.reason,
                                         "ids": [item["snapshot_id"] for item in report["archived"]]},
                                        ensure_ascii=False) + "\n")
    finally:
        connection.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
