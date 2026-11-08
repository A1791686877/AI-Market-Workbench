$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\AI-Market-Workbench'
$env:AMW_ROOT = $projectRoot
$env:AMW_DATA_MODE = if ($env:AMW_DATA_MODE) { $env:AMW_DATA_MODE } else { 'fixture' }
$env:AMW_NETWORK_ENABLED = if ($env:AMW_NETWORK_ENABLED) { $env:AMW_NETWORK_ENABLED } else { 'false' }
$env:TEMP = Join-Path $projectRoot 'tmp'
$env:TMP = Join-Path $projectRoot 'tmp'
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'cache\pip'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

Set-Location $projectRoot
& $python -B -m uvicorn app.main:app --host 127.0.0.1 --port 8080

