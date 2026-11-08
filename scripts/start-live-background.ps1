$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\AI-Market-Workbench'
$pidFile = Join-Path $projectRoot 'tmp\uvicorn-live.pid'
$logDir = Join-Path $projectRoot 'logs\app'

if (Test-Path -LiteralPath $pidFile) {
    $existingPid = [int](Get-Content -LiteralPath $pidFile -Raw)
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
        Write-Output "Already running: PID $existingPid"
        exit 0
    }
}

. (Join-Path $projectRoot 'scripts\load-provider-env.ps1')
$env:AMW_ROOT = $projectRoot
$env:AMW_DATA_MODE = 'live'
$env:AMW_NETWORK_ENABLED = 'true'
$env:AMW_CRYPTO_PROVIDER = if ($env:AMW_CRYPTO_PROVIDER) { $env:AMW_CRYPTO_PROVIDER } else { 'okx' }
$env:AMW_MARKET_CACHE_TTL_SECONDS = if ($env:AMW_MARKET_CACHE_TTL_SECONDS) { $env:AMW_MARKET_CACHE_TTL_SECONDS } else { '300' }
$env:TEMP = Join-Path $projectRoot 'tmp'
$env:TMP = Join-Path $projectRoot 'tmp'
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'cache\pip'

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }
$launcher = Start-Process -FilePath $python `
    -ArgumentList @('-B','-m','scripts.run_live_server') `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir 'uvicorn-live.stdout.log') `
    -RedirectStandardError (Join-Path $logDir 'uvicorn-live.stderr.log') `
    -PassThru
for ($attempt = 0; $attempt -lt 40 -and -not (Test-Path -LiteralPath $pidFile); $attempt++) {
    Start-Sleep -Milliseconds 250
}
if (-not (Test-Path -LiteralPath $pidFile)) {
    if (-not $launcher.HasExited) { Stop-Process -Id $launcher.Id }
    throw 'Live server did not publish its actual PID'
}
$serverPid = [int](Get-Content -LiteralPath $pidFile -Raw)
Write-Output "Started: PID $serverPid http://127.0.0.1:8080"
