"""Non-destructive code checkpoint and consistent SQLite backup, entirely on E:."""
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def main():
    root = Path(r'E:\AI-Market-Workbench').resolve()
    dest = root / 'backups' / ('pre-quality-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
    dest.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for name in ('app','static','contracts','examples','tests','scripts','docs'):
        for source in (root/name).rglob('*'):
            if not source.is_file() or '__pycache__' in source.parts:
                continue
            relative = source.relative_to(root)
            target = dest/'code'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            manifest[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    for name in ('pyproject.toml','requirements-lock.txt','README.md','config.example.env','.gitignore'):
        shutil.copy2(root/name, dest/'code'/name)
        manifest[name] = hashlib.sha256((dest/'code'/name).read_bytes()).hexdigest()
    with sqlite3.connect(f'file:{(root/"data/workbench.db").as_posix()}?mode=ro',uri=True) as src:
        with sqlite3.connect(dest/'workbench.db') as target:
            src.backup(target)
    report = {'created_at':datetime.now(UTC).isoformat(),'root':str(root),'checkpoint':str(dest),
              'code_sha256':manifest,'secrets_copied':False,
              'database_sha256':hashlib.sha256((dest/'workbench.db').read_bytes()).hexdigest()}
    (dest/'manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with (root/'logs/audit/quality-progress.jsonl').open('a',encoding='utf-8') as log:
        log.write(json.dumps({'event':'pre_quality_checkpoint','path':str(dest),'files':len(manifest)})+'\n')
    print(json.dumps({'checkpoint':str(dest),'files':len(manifest),'db_integrity':sqlite3.connect(dest/'workbench.db').execute('pragma integrity_check').fetchone()[0]}))


if __name__ == '__main__':
    main()
