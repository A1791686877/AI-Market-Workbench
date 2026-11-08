"""Vendor a pinned FinanceDatabase snapshot into reference/financedatabase/<commit>/.

Why a pinned local snapshot instead of pip-installing:
  * the project's rule is "no runtime network dependency for reference data, and pin what you cite";
  * the repo is 5 GB but the packed data files we need are small, so we keep the .bz2 as-is and
    stream it at read time (no double storage);
  * provenance (repo, commit, URL, size, sha256, license) is written next to the data.

Read-only GETs. Nothing is installed into the virtualenv.
"""
from __future__ import annotations

import argparse
import bz2
import csv
import hashlib
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPO = "JerBouma/FinanceDatabase"
ASSETS = ("equities", "etfs", "indices", "funds", "cryptos", "currencies", "moneymarkets")
USER_AGENT = "AI-Market-Workbench reference-data fetch"


def resolve_commit(client: httpx.Client, ref: str) -> tuple[str, str]:
    response = client.get(f"https://api.github.com/repos/{REPO}/commits/{ref}",
                          headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload["sha"], payload["commit"]["committer"]["date"]


def download(client: httpx.Client, commit: str, name: str, target: Path) -> dict:
    url = f"https://raw.githubusercontent.com/{REPO}/{commit}/compression/{name}.bz2"
    response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    data = response.content
    target.write_bytes(data)
    return {"asset": name, "url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def peek(path: Path, limit: int = 3) -> dict:
    """Read the packed CSV header and a few rows so the fetch proves the file is usable."""
    with bz2.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for index, row in enumerate(reader):
            if index >= limit:
                break
            rows.append({key: (value[:60] if isinstance(value, str) else value) for key, value in row.items()})
        return {"columns": reader.fieldnames, "sample": rows}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="main")
    parser.add_argument("--assets", default="equities,etfs,indices")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    names = [item.strip() for item in args.assets.split(",") if item.strip()]
    for name in names:
        if name not in ASSETS:
            raise SystemExit(f"unknown asset {name!r}; known: {', '.join(ASSETS)}")

    report: dict = {"repo": REPO, "ref": args.ref, "fetched_at": datetime.now(UTC).isoformat()}
    with httpx.Client(follow_redirects=True) as client:
        commit, committed_at = resolve_commit(client, args.ref)
        report.update({"commit": commit, "commit_date": committed_at})
        target_dir = ROOT / "reference" / "financedatabase" / commit[:12]
        target_dir.mkdir(parents=True, exist_ok=True)
        assets = []
        for name in names:
            path = target_dir / f"{name}.bz2"
            if path.exists() and not args.force:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                record = {"asset": name, "url": f"(cached) {path.name}", "bytes": path.stat().st_size,
                          "sha256": digest, "cached": True}
            else:
                record = download(client, commit, name, path)
                record["cached"] = False
            record["peek"] = peek(path)
            record["path"] = str(path.relative_to(ROOT))
            assets.append(record)
            print(f"{name:14s} {record['bytes']:>10,} bytes  cols={len(record['peek']['columns'])}  "
                  f"sha256={record['sha256'][:12]}…", flush=True)
    report["assets"] = assets
    report["license"] = "MIT (see https://github.com/JerBouma/FinanceDatabase/blob/main/LICENSE)"
    report["notes"] = [
        "Community-maintained ticker metadata: sector/industry/exchange/currency classification only.",
        "Never price evidence. Never a substitute for SEC/venue data. Pin the commit and re-review on refresh.",
        "Kept as .bz2 and streamed at read time to avoid duplicating a large CSV on disk.",
    ]
    provenance = ROOT / "reference" / "financedatabase" / commit[:12] / "provenance.json"
    provenance.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nprovenance: {provenance}")
    print(json.dumps({"commit": commit, "assets": [a["asset"] for a in assets]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
