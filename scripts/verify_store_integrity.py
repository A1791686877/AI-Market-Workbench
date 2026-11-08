"""Read-only consistency check for the local snapshot / analysis store.

Checks, without modifying stored records or snapshot files:
  1. ``snapshots`` table rows whose JSON file is missing, and files with no row;
  2. ``snapshot_sha256`` recomputed from the stored payload exactly as it was
     written (canonical JSON with the hash field zeroed). This is deliberately
     independent of the current Pydantic schema, so a legacy row that the current
     code can no longer parse is still hash-verifiable;
  3. that stored analyses, tasks, outcomes and scenario states still parse with
     the current models (schema drift makes old rows unreadable to the API);
  4. referential integrity between analyses, tasks, outcomes, scenario_outcomes;
  5. sizes so unbounded growth is visible.

``--quarantine-orphans`` is the only mode that writes: it MOVES unreferenced
snapshot files into ``<snapshot dir>/../orphaned-snapshots/``
(``data/orphaned-snapshots`` in production) and records a manifest, so the
action is reversible and no real asset is ever deleted.

Exit code is 1 only for *actionable* inconsistency (orphan/missing files, hash
mismatch, broken references, unreadable analyses/tasks/outcomes). Legacy
snapshots that the current schema cannot parse, and records skipped by the size
guard, are reported as observations and do not fail the exit code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    AiAnalysisResult,
    AnalysisOutcome,
    AnalysisSnapshot,
)
from app.scenario_rules import ScenarioState  # noqa: E402
from app.storage import Store  # noqa: E402


def canonical_json(payload: object) -> str:
    """Same canonical form as ``app.storage.canonical_json``."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_payload_sha256(payload_json: str) -> str:
    """Hash the stored snapshot payload the way ``create_snapshot`` did.

    Uses the stored JSON as-is instead of a model round-trip: a schema change
    adds defaults on dump and would otherwise look like a hash mismatch.
    """
    payload = json.loads(payload_json)
    payload["snapshot_sha256"] = "0" * 64
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def verify(database: Path, snapshot_dir: Path, hash_check: bool, max_hash_payload_mb: float) -> dict[str, Any]:
    problems: dict[str, list[Any]] = {
        "orphan_snapshot_files": [],
        "missing_snapshot_files": [],
        "snapshot_hash_mismatch": [],
        "snapshot_hash_unchecked": [],
        "snapshot_schema_unchecked": [],
        "broken_references": [],
        "unreadable_records": [],
    }
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT snapshot_id, snapshot_sha256, payload_json FROM snapshots"
        ).fetchall()
        file_names = {path.name: path for path in snapshot_dir.glob("snapshot-*.json")}
        row_names = {f"{row['snapshot_id']}.json": row for row in rows}

        for name in sorted(set(file_names) - set(row_names)):
            problems["orphan_snapshot_files"].append(
                {"file": name, "bytes": file_names[name].stat().st_size}
            )
        for name in sorted(set(row_names) - set(file_names)):
            problems["missing_snapshot_files"].append({"snapshot_id": row_names[name]["snapshot_id"]})

        if hash_check:
            limit_bytes = int(max_hash_payload_mb * 1024 * 1024)
            for row in rows:
                payload_json = row["payload_json"]
                if len(payload_json) > limit_bytes:
                    problems["snapshot_hash_unchecked"].append(
                        {"snapshot_id": row["snapshot_id"], "reason": "payload_over_size_guard",
                         "bytes": len(payload_json)}
                    )
                    continue
                try:
                    actual = snapshot_payload_sha256(payload_json)
                except Exception as exc:
                    problems["unreadable_records"].append(
                        {"table": "snapshots", "kind": "snapshot", "snapshot_id": row["snapshot_id"],
                         "error_type": type(exc).__name__, "detail": str(exc).splitlines()[0][:180]}
                    )
                    continue
                if actual != row["snapshot_sha256"]:
                    problems["snapshot_hash_mismatch"].append(
                        {"snapshot_id": row["snapshot_id"], "stored": row["snapshot_sha256"], "recomputed": actual}
                    )

        readability_limit = int(max_hash_payload_mb * 1024 * 1024)
        for row in rows:
            if len(row["payload_json"]) > readability_limit:
                problems["snapshot_schema_unchecked"].append(
                    {"snapshot_id": row["snapshot_id"], "reason": "payload_over_size_guard",
                     "bytes": len(row["payload_json"])}
                )
                continue
            try:
                AnalysisSnapshot.model_validate_json(row["payload_json"])
            except Exception as exc:
                problems["unreadable_records"].append(
                    {"table": "snapshots", "kind": "snapshot", "snapshot_id": row["snapshot_id"],
                     "error_type": type(exc).__name__, "detail": str(exc).splitlines()[0][:180]}
                )

        def check_readable(table: str, column: str, loader, label: str) -> None:
            if not _table_exists(connection, table):
                return
            for row in connection.execute(f"SELECT {column} AS payload FROM {table}"):
                try:
                    loader(row["payload"])
                except Exception as exc:
                    problems["unreadable_records"].append(
                        {"table": table, "kind": label, "error_type": type(exc).__name__,
                         "detail": str(exc).splitlines()[0][:180]}
                    )

        check_readable("analyses", "payload_json", AiAnalysisResult.model_validate_json, "analysis")
        check_readable("outcomes", "payload_json", AnalysisOutcome.model_validate_json, "outcome")
        check_readable("scenario_outcomes", "payload_json", ScenarioState.model_validate_json, "scenario_state")

        if _table_exists(connection, "analysis_tasks"):
            for row in connection.execute("SELECT * FROM analysis_tasks"):
                try:
                    Store._task_from_row(row)
                except Exception as exc:
                    problems["unreadable_records"].append(
                        {"table": "analysis_tasks", "kind": "task", "error_type": type(exc).__name__,
                         "detail": str(exc).splitlines()[0][:180]}
                    )

        def referenced(child_table: str, child_column: str, parent_table: str, parent_column: str) -> None:
            if not _table_exists(connection, child_table):
                return
            query = (
                f"SELECT c.{child_column} AS value FROM {child_table} c "
                f"LEFT JOIN {parent_table} p ON p.{parent_column} = c.{child_column} "
                f"WHERE c.{child_column} IS NOT NULL AND p.{parent_column} IS NULL LIMIT 50"
            )
            for row in connection.execute(query):
                problems["broken_references"].append(
                    {"child": child_table, "column": child_column, "value": row["value"]}
                )

        referenced("analyses", "snapshot_id", "snapshots", "snapshot_id")
        referenced("analysis_tasks", "snapshot_id", "snapshots", "snapshot_id")
        referenced("analysis_tasks", "analysis_id", "analyses", "analysis_id")
        referenced("outcomes", "analysis_id", "analyses", "analysis_id")
        referenced("scenario_outcomes", "analysis_id", "analyses", "analysis_id")

        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("snapshots", "analyses", "analysis_tasks", "outcomes")
            if _table_exists(connection, table)
        }
        if _table_exists(connection, "scenario_outcomes"):
            counts["scenario_outcomes"] = connection.execute(
                "SELECT COUNT(*) FROM scenario_outcomes"
            ).fetchone()[0]
    finally:
        connection.close()

    file_sizes = sorted(path.stat().st_size for path in snapshot_dir.glob("snapshot-*.json"))
    # Actionable = the store is internally inconsistent or the hash chain is broken.
    # Observations = records that are intact but cannot be read/migrated (legacy
    # schema drift) or were skipped by the size guard; they need a human decision,
    # not an automatic fix, so they do not fail the exit code.
    actionable = (
        len(problems["orphan_snapshot_files"])
        + len(problems["missing_snapshot_files"])
        + len(problems["snapshot_hash_mismatch"])
        + len(problems["broken_references"])
        + len([item for item in problems["unreadable_records"] if item.get("table") != "snapshots"])
    )
    observations = sum(len(items) for items in problems.values()) - actionable
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "database": str(database),
        "database_bytes": database.stat().st_size if database.exists() else 0,
        "snapshot_dir": str(snapshot_dir),
        "snapshot_files": len(file_sizes),
        "snapshot_files_bytes": sum(file_sizes),
        "largest_snapshot_bytes": file_sizes[-1] if file_sizes else 0,
        "table_rows": counts,
        "hash_check_enabled": hash_check,
        "max_hash_payload_mb": max_hash_payload_mb,
        "problems": problems,
        "problem_count": actionable + observations,
        "actionable_problem_count": actionable,
        "observation_count": observations,
    }
    return report


def quarantine_orphans(report: dict[str, Any], snapshot_dir: Path) -> list[str]:
    orphans = report["problems"]["orphan_snapshot_files"]
    if not orphans:
        return []
    target_dir = snapshot_dir.parent / "orphaned-snapshots"
    target_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for item in orphans:
        source = snapshot_dir / item["file"]
        destination = target_dir / item["file"]
        if destination.exists():
            destination = target_dir / f"{source.stem}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        shutil.move(str(source), str(destination))
        moved.append(item["file"])
    manifest = target_dir / "manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as handle:
        for name in moved:
            handle.write(json.dumps({
                "moved_at": datetime.now(UTC).isoformat(),
                "file": name,
                "reason": "snapshot file had no snapshots table row; moved, not deleted",
                "checked_at": report["checked_at"],
            }, ensure_ascii=False) + "\n")
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify snapshot/DB consistency (read-only by default).")
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--snapshot-dir", type=Path, default=settings.snapshot_dir)
    parser.add_argument("--report", type=Path, default=None, help="write the JSON report to this path")
    parser.add_argument("--json", action="store_true", help="print the full JSON report to stdout")
    parser.add_argument("--no-hash-check", action="store_true", help="skip snapshot_sha256 recomputation")
    parser.add_argument("--max-hash-payload-mb", type=float, default=32.0,
                        help="skip hash recomputation for payloads above this size (default 32MB)")
    parser.add_argument("--quarantine-orphans", action="store_true",
                        help="MOVE orphan snapshot files to data/orphaned-snapshots/ (reversible)")
    args = parser.parse_args()

    if not args.database.exists():
        print(f"database not found: {args.database}", file=sys.stderr)
        return 2
    report = verify(
        database=args.database,
        snapshot_dir=args.snapshot_dir,
        hash_check=not args.no_hash_check,
        max_hash_payload_mb=args.max_hash_payload_mb,
    )
    if args.quarantine_orphans:
        report["quarantined"] = quarantine_orphans(report, args.snapshot_dir)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"db={report['database']} rows={report['table_rows']}")
        print(
            f"snapshot files={report['snapshot_files']} "
            f"({report['snapshot_files_bytes'] / 1e6:.1f} MB, largest {report['largest_snapshot_bytes'] / 1e6:.1f} MB)"
        )
        for name, items in report["problems"].items():
            state = "OK" if not items else f"{len(items)} PROBLEM(S)"
            print(f"  {name}: {state}")
            for item in items[:5]:
                print(f"    - {item}")
        if report.get("quarantined"):
            print(f"quarantined {len(report['quarantined'])} orphan file(s) under "
                  f"{args.snapshot_dir.parent / 'orphaned-snapshots'}")
        print(f"problem_count={report['problem_count']} "
              f"actionable={report['actionable_problem_count']} observations={report['observation_count']}")
    return 1 if report["actionable_problem_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
