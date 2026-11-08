"""Show the newest per-symbol acceptance report: task status, model audit, scenarios, key levels."""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, UTC

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
symbol = (sys.argv[1] if len(sys.argv) > 1 else "MCD").lower()
reports = sorted(pathlib.Path("reports").glob(f"{symbol}-acceptance-*.json"), key=lambda p: p.stat().st_mtime)
print("candidates:", [p.name for p in reports][-4:])
data = json.loads(reports[-1].read_text(encoding="utf-8"))
print("file:", reports[-1].name, "| model_call:", data.get("model_call"))
print("snapshot:", {k: data["snapshot"][k] for k in ("snapshot_id", "cutoff_at", "evidence_count",
                                                    "fundamentals_included")})
print("coverage:", [(c["interval"], c["role"], c["status"]) for c in data["snapshot"]["timeframe_coverage"]])
print("missing:", data["snapshot"]["missing_items"])
print("task:", json.dumps(data.get("task"), ensure_ascii=False))
analysis = data.get("analysis") or {}
if analysis.get("error"):
    print("ANALYSIS ERROR:", analysis["error"])
else:
    audit = analysis.get("audit", {})
    print("audit:", {k: audit.get(k) for k in ("provider", "model", "prompt_version", "input_tokens",
                                               "output_tokens", "started_at", "finished_at")})
    print("validation:", analysis.get("evidence_reference_validation"))
    print("\n--- scenarios ---")
    for s in analysis.get("scenarios", []):
        print(f"[{s['type']}] rule={s.get('evaluation_rule_id')}")
        print(f"   summary: {s.get('summary')}")
        for key in ("preconditions", "invalidation_conditions"):
            for c in s.get(key, [])[:2]:
                print(f"   {key}: {c.get('description')}")
        print(f"   evidence: {s.get('evidence_ids')} levels: {s.get('key_level_ids')}")
    print("\n--- timeframe synthesis ---")
    for t in analysis.get("timeframe_synthesis", []):
        print(f"[{t['interval']}/{t['role']}/{t['status']}] {t.get('assessment')}")
    print("\n--- key levels ---")
    for level in analysis.get("key_levels", []):
        print(f"  {level['level_id']:32s} {level['kind']:14s} {level['price']:>10s} {level['timeframe']:4s} "
              f"confirmed={level['confirmed']} evidence={level.get('evidence_ids')}")
    print("\n--- conflicts ---")
    for c in analysis.get("conflicts", []):
        print(f"  [{c.get('impact')}] {c.get('description')}")
    print("\n--- missing information ---")
    for m in analysis.get("missing_information", []):
        print(f"  {m.get('field')}: {m.get('reason_code')} — {m.get('detail')}")
