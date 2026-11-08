from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Iterator

from app.config import settings
from app.models import (
    AiAnalysisResult,
    AiAnalysisTask,
    AiTaskStatus,
    AnalysisOutcome,
    AnalysisPage,
    AnalysisSnapshot,
    AnalysisSummary,
    OutcomePage,
    PageMeta,
)
from app.scenario_rules import ScenarioState


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode(padded).decode("ascii"))
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("invalid pagination cursor") from exc
    if value < 0:
        raise ValueError("invalid pagination cursor")
    return value


# Legacy private aliases: older callers imported these names directly.
_decode_cursor = decode_cursor
_encode_cursor = encode_cursor


class Store:
    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or settings.database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Connection that commits on success, rolls back on error and always closes.

        The sqlite3 connection context manager only commits/rolls back the
        transaction; it does NOT close the handle. Leaving handles open kept the
        Windows file locked (observed as a PermissionError when a temporary test
        database was cleaned up), so every caller goes through this helper while
        keeping the commit-on-success semantics callers rely on.
        """
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    cutoff_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_tasks (
                    task_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    analysis_id TEXT,
                    error_json TEXT,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    request_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    analysis_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id),
                    horizon TEXT NOT NULL,
                    matures_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(analysis_id, horizon)
                );
                """
            )

    def save_snapshot(self, snapshot: AnalysisSnapshot) -> None:
        payload = snapshot.model_dump(mode="json")
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO snapshots
                (snapshot_id, subject_id, cutoff_at, created_at, snapshot_sha256, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.snapshot_id,
                    snapshot.subject.subject_id,
                    snapshot.cutoff_at.isoformat(),
                    snapshot.created_at.isoformat(),
                    snapshot.snapshot_sha256,
                    canonical_json(payload),
                ),
            )

    def get_snapshot(self, snapshot_id: str) -> AnalysisSnapshot | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
        return AnalysisSnapshot.model_validate_json(row["payload_json"]) if row else None

    def create_or_get_task(
        self, task: AiAnalysisTask, idempotency_key: str, request_sha256: str
    ) -> tuple[AiAnalysisTask, bool]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_tasks WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row:
                if row["request_sha256"] != request_sha256:
                    raise ValueError("idempotency key was already used with a different request")
                return self._task_from_row(row), False
            connection.execute(
                """INSERT INTO analysis_tasks
                (task_id, snapshot_id, status, created_at, started_at, completed_at,
                 analysis_id, error_json, idempotency_key, request_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.task_id,
                    task.snapshot_id,
                    task.status.value,
                    task.created_at.isoformat(),
                    None,
                    None,
                    None,
                    None,
                    idempotency_key,
                    request_sha256,
                ),
            )
        return task, True

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> AiAnalysisTask:
        return AiAnalysisTask.model_validate(
            {
                "task_id": row["task_id"],
                "status": row["status"],
                "snapshot_id": row["snapshot_id"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "analysis_id": row["analysis_id"],
                "error": json.loads(row["error_json"]) if row["error_json"] else None,
            }
        )

    def list_tasks_by_status(self, statuses: Iterable[str]) -> list[AiAnalysisTask]:
        wanted = [status for status in statuses]
        if not wanted:
            return []
        placeholders = ",".join("?" for _ in wanted)
        with self.connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM analysis_tasks WHERE status IN ({placeholders}) ORDER BY created_at ASC",
                wanted,
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def get_task(self, task_id: str) -> AiAnalysisTask | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return self._task_from_row(row) if row else None

    def update_task(self, task: AiAnalysisTask) -> None:
        with self.connection() as connection:
            connection.execute(
                """UPDATE analysis_tasks
                SET status=?, started_at=?, completed_at=?, analysis_id=?, error_json=?
                WHERE task_id=?""",
                (
                    task.status.value,
                    task.started_at.isoformat() if task.started_at else None,
                    task.completed_at.isoformat() if task.completed_at else None,
                    task.analysis_id,
                    canonical_json(task.error.model_dump(mode="json")) if task.error else None,
                    task.task_id,
                ),
            )

    def save_analysis(self, analysis: AiAnalysisResult) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO analyses
                (analysis_id, subject_id, snapshot_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    analysis.analysis_id,
                    analysis.subject.subject_id,
                    analysis.snapshot.snapshot_id,
                    analysis.audit.finished_at.isoformat(),
                    canonical_json(analysis.model_dump(mode="json")),
                ),
            )

    def get_analysis(self, analysis_id: str) -> AiAnalysisResult | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM analyses WHERE analysis_id=?", (analysis_id,)
            ).fetchone()
        return AiAnalysisResult.model_validate_json(row["payload_json"]) if row else None

    def list_analyses(self, limit: int, cursor: str | None) -> AnalysisPage:
        offset = decode_cursor(cursor)
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM analyses ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        has_more = len(rows) > limit
        items = []
        for row in rows[:limit]:
            analysis = AiAnalysisResult.model_validate_json(row["payload_json"])
            items.append(
                AnalysisSummary(
                    analysis_id=analysis.analysis_id,
                    subject_id=analysis.subject.subject_id,
                    snapshot_id=analysis.snapshot.snapshot_id,
                    created_at=analysis.audit.finished_at,
                    provider=analysis.audit.provider,
                    model=analysis.audit.model,
                    confidence_score=analysis.confidence.score,
                )
            )
        next_cursor = encode_cursor(offset + limit) if has_more else None
        return AnalysisPage(
            items=items,
            page=PageMeta(
                limit=limit,
                cursor=cursor,
                next_cursor=next_cursor,
                has_more=has_more,
            ),
        )

    def save_outcomes(self, outcomes: Iterable[AnalysisOutcome]) -> None:
        with self.connection() as connection:
            for outcome in outcomes:
                connection.execute(
                    """INSERT OR REPLACE INTO outcomes
                    (outcome_id, analysis_id, horizon, matures_at, payload_json)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        outcome.outcome_id,
                        outcome.analysis_id,
                        outcome.horizon.value,
                        outcome.matures_at.isoformat(),
                        canonical_json(outcome.model_dump(mode="json")),
                    ),
                )

    def list_scenario_states(
        self, analysis_id: str, limit: int, cursor: str | None
    ) -> tuple[list[ScenarioState], bool, str | None]:
        """Read the separate post-analysis scenario table; never feeds analysis input."""
        offset = decode_cursor(cursor)
        with self.connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scenario_outcomes'"
            ).fetchone()
            rows = (
                connection.execute(
                    """SELECT payload_json FROM scenario_outcomes WHERE analysis_id=?
                    ORDER BY rule_id ASC LIMIT ? OFFSET ?""",
                    (analysis_id, limit + 1, offset),
                ).fetchall()
                if exists
                else []
            )
        has_more = len(rows) > limit
        items = [ScenarioState.model_validate_json(row["payload_json"]) for row in rows[:limit]]
        return items, has_more, (encode_cursor(offset + limit) if has_more else None)

    def list_outcomes(self, analysis_id: str, limit: int, cursor: str | None) -> OutcomePage:
        offset = decode_cursor(cursor)
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM outcomes WHERE analysis_id=?
                ORDER BY matures_at ASC LIMIT ? OFFSET ?""",
                (analysis_id, limit + 1, offset),
            ).fetchall()
        has_more = len(rows) > limit
        items = [AnalysisOutcome.model_validate_json(row["payload_json"]) for row in rows[:limit]]
        return OutcomePage(
            items=items,
            page=PageMeta(
                limit=limit,
                cursor=cursor,
                next_cursor=encode_cursor(offset + limit) if has_more else None,
                has_more=has_more,
            ),
        )


store = Store()
