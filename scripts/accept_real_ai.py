"""Explicit, non-scheduled acceptance: two real calls using public snapshots only."""
import json
import uuid
from datetime import UTC, datetime

from app.config import settings
from app.models import AiAnalysisTaskRequest, Interval, SnapshotRequest
from app.service import service


def main():
    rows = []
    for subject in ("crypto-btc", "crypto-eth"):
        snapshot = service.create_snapshot(SnapshotRequest(subject_id=subject, intervals=[Interval.MIN_15]))
        request = AiAnalysisTaskRequest(snapshot_id=snapshot.snapshot_id, provider="CODEX_CLI",
                                        model=settings.codex_default_model, prompt_version="scenario-v2")
        task, _ = service.create_task(request, f"real-acceptance-{uuid.uuid4().hex}")
        print(f"{subject}: snapshot={snapshot.snapshot_id}, task={task.task_id}", flush=True)
        service.run_task(task.task_id, request)
        task = service.get_task(task.task_id)
        row = {"subject": subject, "task": task.model_dump(mode="json"),
               "cutoff_at": snapshot.cutoff_at.isoformat(),
               "quality": snapshot.bars[0].quality.model_dump(mode="json"),
               "evidence_count": len(snapshot.evidence)}
        if task.analysis_id:
            analysis = service.get_analysis(task.analysis_id)
            service._validate_analysis(analysis, snapshot)
            row["audit"] = analysis.audit.model_dump(mode="json")
            row["evidence_reference_validation"] = "PASS: IDs, conflicts, level IDs, exact cited prices"
            row["semantic_claim_validation"] = "MANUAL_REVIEW_REQUIRED: IDs alone do not prove prose claims"
        rows.append(row)
        report = {"checked_at": datetime.now(UTC).isoformat(), "real_model": True,
                  "monthly_data_subscription_usd": 0, "results": rows}
        (settings.root / "reports" / "real-ai-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{subject}: {task.status.value}, analysis={task.analysis_id}, error={task.error}", flush=True)
        if not task.analysis_id:
            break  # Do not spend another call before diagnosing failure.


if __name__ == "__main__":
    main()
