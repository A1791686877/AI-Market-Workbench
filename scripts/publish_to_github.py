"""
Publish AI-Market-Workbench to GitHub via pure REST / Git Data API without local git.exe.
Follows the AI Agent automated GitHub publishing SOP:
1. Re-uses cached token if available in cache/github_token.json; otherwise requests Device Code.
2. Displays the 8-character user code and authorization URL.
3. Polls GitHub until authenticated, saves token to cache/github_token.json.
4. Checks / creates the repository.
5. If repo is empty, initializes it with README.md via Contents API (avoids 409 Conflict).
6. Uploads all 265+ sanitized project files via Git Blobs & Tree API.
7. Creates atomic commit and updates refs/heads/main.
8. Outputs final repository URL.
"""

import sys
import os
import time
import json
import base64
from pathlib import Path
import httpx

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

PROXY = "http://127.0.0.1:10808"
CLIENT_ID = "178c6fc778ccc68e1d6a"  # Official GitHub CLI Client ID
SCOPE = "repo,read:org,gist"
ROOT_DIR = Path(__file__).resolve().parent.parent
TOKEN_CACHE = ROOT_DIR / "cache" / "github_token.json"

IGNORED_DIRS = {
    ".git", ".venv", "__pycache__", "cache", "data",
    ".pytest_cache", ".pytest_cache_local", "backups", "tmp",
    ".idea", ".vscode"
}
IGNORED_FILES = {
    "providers.env", ".env", "token.txt", "creds.json"
}
IGNORED_EXTS = {
    ".sqlite3", ".pyc", ".log", ".tmp", ".bak"
}


def get_http_client() -> httpx.Client:
    return httpx.Client(proxy=PROXY, timeout=30.0)


def request_device_code(client: httpx.Client) -> dict:
    url = "https://github.com/login/device/code"
    headers = {"Accept": "application/json"}
    data = {"client_id": CLIENT_ID, "scope": SCOPE}
    resp = client.post(url, headers=headers, data=data)
    resp.raise_for_status()
    return resp.json()


def poll_for_access_token(client: httpx.Client, device_code: str, interval: int = 5, expires_in: int = 900) -> str:
    token_url = "https://github.com/login/oauth/access_token"
    headers = {"Accept": "application/json"}
    data = {
        "client_id": CLIENT_ID,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
    }

    start_time = time.time()
    poll_interval = interval or 5

    while time.time() - start_time < expires_in:
        time.sleep(poll_interval)
        try:
            resp = client.post(token_url, headers=headers, data=data)
            resp.raise_for_status()
            res = resp.json()
        except Exception as e:
            print(f"[POLL_RETRY] {e}", flush=True)
            continue

        if "access_token" in res:
            return res["access_token"]

        error = res.get("error")
        if error == "authorization_pending":
            print(".", end="", flush=True)
            continue
        elif error == "slow_down":
            poll_interval += 5
            print(f"[SLOW_DOWN] Interval increased to {poll_interval}s", flush=True)
        elif error == "expired_token":
            raise RuntimeError("OAuth Device code expired. Please restart the authorization.")
        elif error == "access_denied":
            raise RuntimeError("Authorization was cancelled or denied by user.")
        else:
            print(f"[POLL_STATUS] {res}", flush=True)

    raise TimeoutError("Authorization timed out waiting for user approval.")


def collect_clean_files(root: Path):
    files = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        parts = rel.parts

        # Directory check
        if any(d in parts for d in IGNORED_DIRS):
            continue
        # File name check
        if p.name in IGNORED_FILES:
            continue
        # Extension check
        if p.suffix.lower() in IGNORED_EXTS:
            continue

        files.append(rel)
    return sorted(files, key=lambda x: x.as_posix())


def get_token(client: httpx.Client) -> str:
    # 1. Check cache
    if TOKEN_CACHE.exists():
        try:
            data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            tok = data.get("access_token")
            if tok:
                # Test token validity
                r = client.get("https://api.github.com/user", headers={"Authorization": f"token {tok}"})
                if r.status_code == 200:
                    print(f"[CACHE] 使用已保存的有效 Token (用户: @{r.json().get('login')})", flush=True)
                    return tok
        except Exception as e:
            print(f"[CACHE_NOTICE] 缓存 Token 无效或过期: {e}", flush=True)

    # 2. Request new device code
    print("\n[Step 1] 向 GitHub 申请 OAuth 设备授权码...", flush=True)
    device_info = request_device_code(client)
    user_code = device_info["user_code"]
    device_code = device_info["device_code"]
    verify_url = device_info["verification_uri"]
    interval = device_info.get("interval", 5)
    expires_in = device_info.get("expires_in", 900)

    print("\n" + "=" * 60, flush=True)
    print(f"👉 请在浏览器打开: {verify_url}", flush=True)
    print(f"👉 输入 8 位设备码:  {user_code}", flush=True)
    print(f"👉 并点击【Authorize github】确认授权", flush=True)
    print("=" * 60 + "\n", flush=True)
    print("正在等待授权中（每 5 秒轮询一次，无需额外操作）...", end="", flush=True)

    tok = poll_for_access_token(client, device_code, interval=interval, expires_in=expires_in)
    print(f"\n[OK] 授权成功！获得 Access Token.", flush=True)

    # Save to cache
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({"access_token": tok, "saved_at": time.time()}), encoding="utf-8")
    return tok


def main():
    print("=== AI Agent Automated GitHub Publisher ===", flush=True)
    client = get_http_client()

    token = get_token(client)
    auth_headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AI-Market-Workbench-Publisher"
    }

    # Step 2: Get User Details
    user_resp = client.get("https://api.github.com/user", headers=auth_headers)
    user_resp.raise_for_status()
    user_data = user_resp.json()
    username = user_data["login"]
    print(f"[Step 2] 目标 GitHub 用户: @{username}", flush=True)

    # Step 3: Ensure Repository Exists
    repo_name = "AI-Market-Workbench"
    repo_url = f"https://api.github.com/repos/{username}/{repo_name}"
    check_repo = client.get(repo_url, headers=auth_headers)

    if check_repo.status_code == 404:
        print(f"[Step 3] 正在创建公开仓库 {username}/{repo_name}...", flush=True)
        create_payload = {
            "name": repo_name,
            "description": "Multi-timeframe AI Market Workbench: Radar overview, 6-timeframe matrix, VPVR & dual-track divergences, point-in-time fundamentals, and strategy war room.",
            "private": False,
            "has_issues": True,
            "has_wiki": False,
            "auto_init": False
        }
        create_resp = client.post("https://api.github.com/user/repos", headers=auth_headers, json=create_payload)
        create_resp.raise_for_status()
        print(f"[OK] 仓库创建成功: {create_resp.json().get('html_url')}", flush=True)
    elif check_repo.status_code == 200:
        print(f"[Step 3] 仓库已存在: {check_repo.json().get('html_url')}", flush=True)
    else:
        check_repo.raise_for_status()

    # Step 4: Check if repo is empty. If empty, initialize root commit with README.md via Contents API
    ref_check = client.get(f"https://api.github.com/repos/{username}/{repo_name}/git/refs/heads/main", headers=auth_headers)
    base_commit_sha = None
    base_tree_sha = None

    if ref_check.status_code == 200:
        base_commit_sha = ref_check.json()["object"]["sha"]
        commit_info = client.get(f"https://api.github.com/repos/{username}/{repo_name}/git/commits/{base_commit_sha}", headers=auth_headers).json()
        base_tree_sha = commit_info["tree"]["sha"]
        print(f"[Step 4] 仓库已有基础分支 main (Base Commit: {base_commit_sha[:7]})", flush=True)
    else:
        print("[Step 4] 仓库目前为空，正在通过 Contents API 创建初始根提交 (README.md)...", flush=True)
        readme_path = ROOT_DIR / "README.md"
        readme_b64 = base64.b64encode(readme_path.read_bytes()).decode("ascii")
        init_payload = {
            "message": "Initial commit: Initialize repository with README.md",
            "content": readme_b64,
            "branch": "main"
        }
        init_resp = client.put(
            f"https://api.github.com/repos/{username}/{repo_name}/contents/README.md",
            headers=auth_headers,
            json=init_payload
        )
        init_resp.raise_for_status()
        init_data = init_resp.json()
        base_commit_sha = init_data["commit"]["sha"]
        base_tree_sha = init_data["commit"]["tree"]["sha"] if "tree" in init_data["commit"] else None
        print(f"[OK] 根提交初始化完成 (Root Commit: {base_commit_sha[:7]})", flush=True)

    # Step 5: Gather and Sanitize Files
    print("[Step 5] 正在扫描并审查工程文件...", flush=True)
    files = collect_clean_files(ROOT_DIR)
    print(f"[OK] 找到 {len(files)} 个待发布纯净文件（已排除所有 cache、data、.env、sqlite3 与临时文件）", flush=True)

    # Step 6: Create Blobs & Build Tree
    print("[Step 6] 正在向 GitHub 上传文件数据块 (Git Blobs)...", flush=True)
    tree_items = []
    for idx, rel in enumerate(files, 1):
        full_path = ROOT_DIR / rel
        try:
            content_bytes = full_path.read_bytes()
        except Exception as e:
            print(f"Skipping unreadable file {rel}: {e}")
            continue

        # Try utf-8
        try:
            text_content = content_bytes.decode("utf-8")
            blob_payload = {"content": text_content, "encoding": "utf-8"}
        except UnicodeDecodeError:
            b64_content = base64.b64encode(content_bytes).decode("ascii")
            blob_payload = {"content": b64_content, "encoding": "base64"}

        # Post Blob
        blob_url = f"https://api.github.com/repos/{username}/{repo_name}/git/blobs"
        blob_resp = client.post(blob_url, headers=auth_headers, json=blob_payload)
        blob_resp.raise_for_status()
        blob_sha = blob_resp.json()["sha"]

        mode = "100755" if (rel.suffix in {".sh", ".ps1"} or "scripts" in rel.parts) else "100644"
        tree_items.append({
            "path": rel.as_posix(),
            "mode": mode,
            "type": "blob",
            "sha": blob_sha
        })

        if idx % 25 == 0 or idx == len(files):
            print(f"  已上传 {idx}/{len(files)} 个文件...", flush=True)

    # Step 7: Create Tree
    print("[Step 7] 正在生成全量 Git 树 (Tree)...", flush=True)
    tree_url = f"https://api.github.com/repos/{username}/{repo_name}/git/trees"
    tree_resp = client.post(tree_url, headers=auth_headers, json={"tree": tree_items})
    tree_resp.raise_for_status()
    tree_sha = tree_resp.json()["sha"]
    print(f"[OK] Tree SHA: {tree_sha}", flush=True)

    # Step 8: Create Commit
    print("[Step 8] 正在创建完整版本提交 (Commit)...", flush=True)
    commit_url = f"https://api.github.com/repos/{username}/{repo_name}/git/commits"
    commit_payload = {
        "message": "Release v1.0: Multi-timeframe AI Market Workbench\n\nFeatures:\n- 11-subject radar overview & live market scanner\n- 48-bucket VPVR clustering (HVN/LVN) & dual-track divergences\n- 6-timeframe multi-resolution resonance matrix\n- Point-in-time SEC EDGAR fundamentals & FRED macro indicators\n- User-defined swing trading strategy war room with strict risk controls\n- Responsive bilingual (CN/EN) Web UI\n- Zero-write architecture: 100% read-only market analysis workbench",
        "tree": tree_sha,
        "parents": [base_commit_sha] if base_commit_sha else []
    }
    commit_resp = client.post(commit_url, headers=auth_headers, json=commit_payload)
    commit_resp.raise_for_status()
    commit_sha = commit_resp.json()["sha"]
    print(f"[OK] Commit SHA: {commit_sha}", flush=True)

    # Step 9: Update branch reference (refs/heads/main)
    print("[Step 9] 正在更新主分支 refs/heads/main...", flush=True)
    ref_url = f"https://api.github.com/repos/{username}/{repo_name}/git/refs/heads/main"
    patch_resp = client.patch(ref_url, headers=auth_headers, json={"sha": commit_sha, "force": True})
    patch_resp.raise_for_status()

    # Step 10: Set default branch to main
    client.patch(repo_url, headers=auth_headers, json={"default_branch": "main"})

    final_url = f"https://github.com/{username}/{repo_name}"
    print("\n" + "=" * 60, flush=True)
    print("🎉 发布成功！项目已完整发布至 GitHub：", flush=True)
    print(f"🔗 {final_url}", flush=True)
    print("=" * 60 + "\n", flush=True)


if __name__ == "__main__":
    main()
