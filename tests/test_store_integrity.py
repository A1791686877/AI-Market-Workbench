"""Store integrity checks: orphans, missing files, tampering, schema drift."""
from __future__ import annotations

import json
from pathlib import Path

from app.models import Interval, SnapshotRequest
from app.service import MarketWorkbenchService
from app.storage import Store, sha256_json
from scripts.verify_store_integrity import quarantine_orphans, snapshot_payload_sha256, verify


def _service(tmp_path: Path) -> MarketWorkbenchService:
    return MarketWorkbenchService(Store(tmp_path / "store.db"))


def _report(service: MarketWorkbenchService) -> dict:
    return verify(
        database=service.store.database_path,
        snapshot_dir=service.snapshot_dir,
        hash_check=True,
        max_hash_payload_mb=32.0,
    )


def test_payload_hash_matches_the_storage_helper() -> None:
    payload = {"b": [1, 2, 3], "a": "text", "snapshot_sha256": "0" * 64}
    assert snapshot_payload_sha256(json.dumps(payload)) == sha256_json(payload)


def test_consistent_store_reports_no_problems(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.create_snapshot(SnapshotRequest(subject_id="crypto-btc", intervals=[Interval.MIN_15]))
    report = _report(service)
    assert report["problem_count"] == 0, report["problems"]
    assert report["snapshot_files"] == 1
    assert report["table_rows"]["snapshots"] == 1


def test_orphan_missing_tampered_and_unreadable_are_each_reported(tmp_path: Path) -> None:
    service = _service(tmp_path)
    kept = service.create_snapshot(SnapshotRequest(subject_id="crypto-btc", intervals=[Interval.MIN_15]))
    removed = service.create_snapshot(SnapshotRequest(subject_id="crypto-eth", intervals=[Interval.MIN_15]))
    (service.snapshot_dir / f"{removed.snapshot_id}.json").unlink()
    (service.snapshot_dir / "snapshot-orphan0000.json").write_text("{}", encoding="utf-8")

    with service.store.connection() as connection:  # tamper with a stored record on purpose
        connection.execute(
            "UPDATE snapshots SET payload_json=? WHERE snapshot_id=?",
            ("{}", kept.snapshot_id),
        )

    report = _report(service)
    problems = report["problems"]
    assert [item["file"] for item in problems["orphan_snapshot_files"]] == ["snapshot-orphan0000.json"]
    assert [item["snapshot_id"] for item in problems["missing_snapshot_files"]] == [removed.snapshot_id]
    assert any(item["snapshot_id"] == kept.snapshot_id for item in problems["unreadable_records"])


def test_tampered_but_parseable_payload_is_a_hash_mismatch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = service.create_snapshot(SnapshotRequest(subject_id="crypto-btc", intervals=[Interval.MIN_15]))
    with service.store.connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM snapshots WHERE snapshot_id=?", (snapshot.snapshot_id,)
        ).fetchone()
    payload = json.loads(row["payload_json"])
    payload["subject"]["symbol"] = "TAMPERED"
    with service.store.connection() as connection:
        connection.execute(
            "UPDATE snapshots SET payload_json=? WHERE snapshot_id=?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), snapshot.snapshot_id),
        )
    report = _report(service)
    assert [item["snapshot_id"] for item in report["problems"]["snapshot_hash_mismatch"]] == [snapshot.snapshot_id]


def test_legacy_row_without_new_fields_is_hash_verifiable_but_not_readable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.create_snapshot(SnapshotRequest(subject_id="crypto-btc", intervals=[Interval.MIN_15]))
    # A payload written by an older schema: valid JSON with an intact hash, but
    # missing fields the current model requires.
    legacy = {
        "schema_version": "1.0.0",
        "snapshot_id": "snapshot-legacy0001",
        "snapshot_sha256": "0" * 64,
        "cutoff_at": "2026-09-05T00:00:00+00:00",
        "created_at": "2026-09-05T00:00:00+00:00",
    }
    legacy["snapshot_sha256"] = snapshot_payload_sha256(json.dumps(legacy))
    legacy_text = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with service.store.connection() as connection:
        connection.execute(
            "INSERT INTO snapshots (snapshot_id, subject_id, cutoff_at, created_at, snapshot_sha256, payload_json)"
            " VALUES (?,?,?,?,?,?)",
            (
                legacy["snapshot_id"], "crypto-btc", legacy["cutoff_at"], legacy["created_at"],
                legacy["snapshot_sha256"], legacy_text,
            ),
        )
    # The row keeps its file, so the only finding is the schema drift itself.
    (service.snapshot_dir / f"{legacy['snapshot_id']}.json").write_text(legacy_text, encoding="utf-8")
    report = _report(service)
    assert report["problems"]["snapshot_hash_mismatch"] == []
    unreadable = [item for item in report["problems"]["unreadable_records"]
                  if item.get("snapshot_id") == "snapshot-legacy0001"]
    assert unreadable and unreadable[0]["table"] == "snapshots"
    # Legacy schema drift is reported as an observation, not as actionable inconsistency.
    assert report["actionable_problem_count"] == 0
    assert report["observation_count"] >= 1


def test_quarantine_moves_orphans_and_keeps_a_manifest(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.create_snapshot(SnapshotRequest(subject_id="crypto-btc", intervals=[Interval.MIN_15]))
    orphan = service.snapshot_dir / "snapshot-orphan0002.json"
    orphan.write_text("{}", encoding="utf-8")
    report = _report(service)
    moved = quarantine_orphans(report, service.snapshot_dir)
    assert moved == ["snapshot-orphan0002.json"]
    assert not orphan.exists()
    target_dir = service.snapshot_dir.parent / "orphaned-snapshots"
    assert (target_dir / "snapshot-orphan0002.json").exists()
    manifest = list(target_dir.glob("manifest.jsonl"))
    assert manifest and "snapshot-orphan0002.json" in manifest[0].read_text(encoding="utf-8")
    assert _report(service)["problems"]["orphan_snapshot_files"] == []
