$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\AI-Market-Workbench'
$env:AMW_ROOT = $projectRoot
$env:TEMP = Join-Path $projectRoot 'tmp'
$env:TMP = Join-Path $projectRoot 'tmp'
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'cache\pip'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

& $python -B -m pip_audit --local --format json --output (Join-Path $projectRoot 'reports\pip-audit.json') --progress-spinner off
& $python -B -m pip_audit --local --format cyclonedx-json --output (Join-Path $projectRoot 'reports\sbom.cdx.json') --progress-spinner off
