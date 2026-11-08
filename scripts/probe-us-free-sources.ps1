$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\AI-Market-Workbench'
. (Join-Path $projectRoot 'scripts\load-provider-env.ps1')
$env:AMW_ROOT = $projectRoot
$env:AMW_DATA_MODE = 'live'
$env:AMW_NETWORK_ENABLED = 'true'
$env:TEMP = Join-Path $projectRoot 'tmp'
$env:TMP = Join-Path $projectRoot 'tmp'
Set-Location $projectRoot
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -B -m scripts.probe_us_free_sources
