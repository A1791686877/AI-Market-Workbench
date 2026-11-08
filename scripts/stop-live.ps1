$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\AI-Market-Workbench'
$pidFile = Join-Path $projectRoot 'tmp\uvicorn-live.pid'
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'No live PID file found.'
    exit 0
}
$serverPid = [int](Get-Content -LiteralPath $pidFile -Raw)
$process = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
if ($process) {
    if ($process.ProcessName -notlike 'python*') {
        throw "PID $serverPid is not a Python process; refusing to stop it"
    }
    Stop-Process -Id $serverPid
    Write-Output "Stopped PID $serverPid"
}
Remove-Item -LiteralPath $pidFile -Force
