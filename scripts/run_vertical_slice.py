from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.models import AiAnalysisTaskRequest, AiTaskStatus, Interval, SnapshotRequest
from app.service import service


CASES = (
    ("us-aapl", [Interval.MIN_15, Interval.MIN_195, Interval.HOUR_4]),
    ("crypto-btc", [Interval.MIN_15, Interval.HOUR_4]),
    ("crypto-eth", [Interval.MIN_15, Interval.HOUR_4]),
)


def main() -> None:
    if settings.data_mode != "fixture" or settings.network_enabled:
        raise RuntimeError("vertical-slice proof must run in offline fixture mode")

    report: list[dict[str, object]] = []
    for subject_id, intervals in CASES:
        snapshot = service.create_snapshot(
            SnapshotRequest(subject_id=subject_id, intervals=intervals)
        )
        request = AiAnalysisTaskRequest(snapshot_id=snapshot.snapshot_id)
        task, created = service.create_task(
            request,
            idempotency_key=f"offline-proof-{subject_id}-{snapshot.snapshot_sha256}",
        )
        if created:
            service.run_task(task.task_id, request)
        task = service.get_task(task.task_id)
        if task.status is not AiTaskStatus.SUCCEEDED or task.analysis_id is None:
            raise RuntimeError(f"vertical slice failed for {subject_id}: {task.status.value}")
        analysis = service.get_analysis(task.analysis_id)
        outcomes = service.list_outcomes(task.analysis_id, 100, None)
        report.append(
            {
                "subject_id": subject_id,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "analysis_id": analysis.analysis_id,
                "scenario_types": [scenario.type.value for scenario in analysis.scenarios],
                "outcome_states": [outcome.status.value for outcome in outcomes.items],
                "data_mode": settings.data_mode,
                "network_enabled": settings.network_enabled,
                "ai_cost_usd": analysis.audit.cost,
            }
        )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    report_path = settings.root / "reports" / "vertical-slice-proof.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
