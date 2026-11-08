"""Reset one stored verification that was computed from an unconfirmed candle (audited write).

Why this exists: the first version of the verification loader read every cached daily bar,
including the in-progress candle, so one BTC conclusion got a bogus 1-bar verdict. The
conclusion row itself stays untouched (registry is append-only); only the derived
`verification` field is cleared, and the reason is archived next to the registry.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("outlook_id")
    parser.add_argument("--reason", default="verification was derived from an unconfirmed candle")
    args = parser.parse_args()

    from app import outlook_registry as registry

    entries = registry.load_entries()
    target = next((item for item in entries if item.outlook_id == args.outlook_id), None)
    if target is None:
        raise SystemExit(f"{args.outlook_id} is not in the registry")
    if target.verification is None:
        raise SystemExit(f"{args.outlook_id} has no stored verification; nothing to reset")

    archive = registry.registry_path().parent / "verification-resets.jsonl"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "reset_at": datetime.now(UTC).isoformat(),
            "outlook_id": target.outlook_id,
            "reason": args.reason,
            "previous_verification": target.verification.model_dump(mode="json"),
        }, ensure_ascii=False, sort_keys=True) + "\n")

    updated = [item.model_copy(update={"verification": None}) if item.outlook_id == args.outlook_id else item
               for item in entries]
    path = registry.save_entries(updated)
    print(json.dumps({"outlook_id": args.outlook_id, "verification_cleared": True,
                      "archive": str(archive), "registry": str(path),
                      "previous_bars_observed": target.verification.bars_observed,
                      "previous_path": target.verification.path_realized}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
