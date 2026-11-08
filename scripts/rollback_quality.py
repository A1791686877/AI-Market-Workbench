"""Default dry run. Validated code restore; optional DB restore after a fresh checkpoint."""
import argparse,hashlib,json,shutil,sqlite3,subprocess
from pathlib import Path
from scripts.checkpoint_quality import main as checkpoint


def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--restore-database',action='store_true');a=p.parse_args()
    root=Path('E:/AI-Market-Workbench').resolve();backup=root/'backups/pre-quality-20260906T083220Z'
    manifest=json.loads((backup/'manifest.json').read_text(encoding='utf-8'))
    targets=[]
    for relative,digest in manifest['code_sha256'].items():
        source=(backup/'code'/relative).resolve();target=(root/relative).resolve()
        if not source.is_relative_to(backup/'code') or not target.is_relative_to(root):raise RuntimeError('Unsafe restore path')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:raise RuntimeError('Checkpoint integrity failure')
        targets.append((source,target))
    if hashlib.sha256((backup/'workbench.db').read_bytes()).hexdigest()!=manifest['database_sha256']:raise RuntimeError('DB checkpoint integrity failure')
    print({'verified_files':len(targets),'apply':a.apply,'restore_database':a.restore_database})
    if not a.apply:return
    check=subprocess.run(['powershell','-NoProfile','-Command',"if ((Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) -or (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'python.*-m app.mcp_server' })) { exit 1 }"],capture_output=True)
    if check.returncode:raise RuntimeError('Stop project web and MCP services before restoring')
    checkpoint() # Preserves every current code file AND current DB before overwrite.
    for source,target in targets:shutil.copy2(source,target)
    if a.restore_database:
        with sqlite3.connect(f'file:{(backup/"workbench.db").as_posix()}?mode=ro',uri=True) as src:
            with sqlite3.connect(root/'data/workbench.db') as dest:src.backup(dest)
    print('Restored known files only; newer files/cache/audit logs retained. Current DB preserved in newly printed checkpoint.')

if __name__=='__main__':main()
