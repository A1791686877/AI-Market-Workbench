$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\AI-Market-Workbench'
Set-Location $projectRoot
$env:AMW_ROOT = $projectRoot
$env:AMW_DATA_MODE = 'live'
$env:AMW_NETWORK_ENABLED = 'true'
$env:AMW_CRYPTO_PROVIDER = 'okx'
$env:TEMP = Join-Path $projectRoot 'tmp'
$env:TMP = $env:TEMP
# Public crypto needs no provider credentials. stdout is reserved for MCP JSON-RPC.
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -B -m app.mcp_server
exit $LASTEXITCODE
