"""Inspect the FinanceDatabase repository (metadata, assets, README) before deciding to vendor it.

Read-only GETs to api.github.com / raw.githubusercontent.com. Writes what it learned to a JSON report.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPO = "JerBouma/FinanceDatabase"
HEADERS = {"User-Agent": "AI-Market-Workbench reference-data review", "Accept": "application/vnd.github+json"}


def get(client: httpx.Client, url: str) -> object:
    response = client.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def main() -> None:
    out: dict = {"checked_at": datetime.now(UTC).isoformat(), "repo": REPO}
    with httpx.Client(follow_redirects=True) as client:
        meta = get(client, f"https://api.github.com/repos/{REPO}")
        out["meta"] = {
            "description": meta.get("description"),
            "license": (meta.get("license") or {}).get("spdx_id"),
            "stars": meta.get("stargazers_count"),
            "forks": meta.get("forks_count"),
            "size_kb": meta.get("size"),
            "default_branch": meta.get("default_branch"),
            "pushed_at": meta.get("pushed_at"),
            "created_at": meta.get("created_at"),
            "archived": meta.get("archived"),
            "topics": meta.get("topics"),
            "homepage": meta.get("homepage"),
        }
        default = meta.get("default_branch") or "main"
        root = get(client, f"https://api.github.com/repos/{REPO}/contents/?ref={default}")
        out["root"] = [{"name": item["name"], "type": item["type"],
                        "size": item.get("size")} for item in root]
        # The package ships the data assets; list them with sizes.
        for path in ("financedatabase", "compression"):
            try:
                listing = get(client, f"https://api.github.com/repos/{REPO}/contents/{path}?ref={default}")
            except Exception as exc:
                out.setdefault("errors", []).append(f"{path}: {type(exc).__name__}: {exc}")
                continue
            out[path] = [{"name": item["name"], "type": item["type"], "size": item.get("size")}
                         for item in listing]
        readme = client.get(f"https://raw.githubusercontent.com/{REPO}/{default}/README.md",
                            headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30)
        out["readme_status"] = readme.status_code
        out["readme_head"] = readme.text[:6000] if readme.status_code == 200 else None

    target = ROOT / "reports" / f"financedatabase-probe-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_out = dict(out)
    meta_out.pop("readme_head", None)
    print(json.dumps(meta_out, ensure_ascii=False, indent=2)[:4000])
    print(f"\nreport: {target}")


if __name__ == "__main__":
    main()
