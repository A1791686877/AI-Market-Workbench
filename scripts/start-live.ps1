$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\AI-Market-Workbench'
. (Join-Path $projectRoot 'scripts\load-provider-env.ps1')

$env:AMW_ROOT = $projectRoot
$env:AMW_DATA_MODE = 'live'
$env:AMW_NETWORK_ENABLED = 'true'
$env:AMW_CRYPTO_PROVIDER = if ($env:AMW_CRYPTO_PROVIDER) { $env:AMW_CRYPTO_PROVIDER } else { 'okx' }
$env:TEMP = Join-Path $projectRoot 'tmp'
$env:TMP = Join-Path $projectRoot 'tmp'
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'cache\pip'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

Set-Location $projectRoot
& $python -B -m uvicorn app.main:app --host 127.0.0.1 --port 8080
