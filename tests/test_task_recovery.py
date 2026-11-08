"""Restart recovery, legacy provider naming, unreadable snapshots and store isolation."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.main import app, get_service
from app.models import AiAnalysisTask, AiAnalysisTaskRequest, AiTaskStatus, Interval, SnapshotRequest
from app.service import AnalysisValidationError, MarketWorkbenchService, SnapshotSchemaError
from app.storage import Store


def _service(tmp_path: Path, name: str = "recovery.db") -> MarketWorkbenchService:
    return MarketWorkbenchService(Store(tmp_path / name))


def _task(task_id: str, snapshot_id: str, status: AiTaskStatus) -> AiAnalysisTask:
    return AiAnalysisTask(
        task_id=task_id,
        status=status,
        snapshot_id=snapshot_id,
        created_at=datetime.now(UTC),
        started_at=None,
        completed_at=None,
        analysis_id=None,
        error=None,
    )


def test_restart_recovery_fails_closed_without_retry_or_model_call(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = service.create_snapshot(
        SnapshotRequest(subject_id="crypto-btc", intervals=[Interval.MIN_15])
    )
    for index, status in enumerate(
        (AiTaskStatus.QUEUED, AiTaskStatus.RUNNING, AiTaskStatus.SUCCEEDED)
    ):
        service.store.create_or_get_task(
            _task(f"task-{index}", snapshot.snapshot_id, status), f"key-{index:0>16}", "sha"
        )

    recovered = service.recover_interrupted_tasks()

    assert sorted(recovered) == ["task-0", "task-1"]
    assert service.get_task("task-0").status is AiTaskStatus.FAILED_INTERNAL
    assert service.get_task("task-1").status is AiTaskStatus.FAILED_INTERNAL
    assert "restart" in (service.get_task("task-0").error.detail or "")
    assert service.get_task("task-2").status is AiTaskStatus.SUCCEEDED
    with service.store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 0
    assert service.recover_interrupted_tasks() == []


def test_legacy_demo_provider_is_treated_as_demo_not_as_a_real_model(tmp_path: Path) -> None:
    service = _service(tmp_path, "legacy-provider.db")
    snapshot = service.create_snapshot(
        SnapshotRequest(subject_id="crypto-btc", intervals=[Interval.MIN_15, Interval.HOUR_1])
    )
    analysis = service._generate_analysis(
        snapshot, AiAnalysisTaskRequest(snapshot_id=snapshot.snapshot_id)
    )
    legacy = analysis.model_copy(
        update={"audit": analysis.audit.model_copy(update={"provider": "DETERMINISTIC_LOCAL"})}
    )
    service._validate_analysis(legacy, snapshot)  # legacy demo output must not hit the strict branch

    strict = analysis.model_copy(
        update={"audit": analysis.audit.model_copy(update={"provider": "CODEX_CLI"})}
    )
    with pytest.raises(AnalysisValidationError):
        service._validate_analysis(strict, snapshot)


def test_unreadable_stored_snapshot_raises_a_typed_error(tmp_path: Path) -> None:
    service = _service(tmp_path, "schema.db")
    with service.store.connection() as connection:
        connection.execute(
            "INSERT INTO snapshots (snapshot_id, subject_id, cutoff_at, created_at, snapshot_sha256, payload_json)"
            " VALUES (?,?,?,?,?,?)",
            ("snapshot-legacy0002", "crypto-btc", "2026-09-05T00:00:00+00:00",
             "2026-09-05T00:00:00+00:00", "0" * 64, "{}"),
        )
    with pytest.raises(SnapshotSchemaError):
        service.get_snapshot("snapshot-legacy0002")


def test_unreadable_snapshot_returns_a_typed_http_error(tmp_path: Path) -> None:
    service = _service(tmp_path, "schema-http.db")
    with service.store.connection() as connection:
        connection.execute(
            "INSERT INTO snapshots (snapshot_id, subject_id, cutoff_at, created_at, snapshot_sha256, payload_json)"
            " VALUES (?,?,?,?,?,?)",
            ("snapshot-legacy0003", "crypto-btc", "2026-09-05T00:00:00+00:00",
             "2026-09-05T00:00:00+00:00", "0" * 64, "{}"),
        )
    app.dependency_overrides[get_service] = lambda: service
    try:
        async def scenario() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get("/v1/analysis-snapshots/snapshot-legacy0003")
                assert response.status_code == 500
                body = response.json()
                assert body["code"] == "INTERNAL_ERROR"
                assert "current schema version" in body["detail"]
        asyncio.run(scenario())
    finally:
        app.dependency_overrides.pop(get_service, None)


def test_snapshot_files_follow_their_own_database(tmp_path: Path) -> None:
    service = _service(tmp_path, "isolated.db")
    snapshot = service.create_snapshot(
        SnapshotRequest(subject_id="crypto-btc", intervals=[Interval.MIN_15])
    )
    assert service.snapshot_dir == tmp_path / "snapshots"
    assert (tmp_path / "snapshots" / f"{snapshot.snapshot_id}.json").exists()
    # A temporary store must never write into the production snapshot directory.
    assert not (settings.snapshot_dir / f"{snapshot.snapshot_id}.json").exists()
