$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\AI-Market-Workbench'
$env:AMW_ROOT = $projectRoot
$env:AMW_DATA_MODE = 'fixture'
$env:AMW_NETWORK_ENABLED = 'false'
$env:TEMP = Join-Path $projectRoot 'tmp'
$env:TMP = Join-Path $projectRoot 'tmp'
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'cache\pip'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

# A stale cache/basetemp left by a differently-tokened run can be unwritable, which turns
# validation into a permissions error instead of a test result. Clear both before running.
foreach ($stale in @('.pytest_cache', 'tmp\pytest')) {
    $path = Join-Path $projectRoot $stale
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
    }
}

& $python -B (Join-Path $projectRoot 'scripts\generate_contracts.py')
if ($LASTEXITCODE -ne 0) { throw 'Contract generation failed' }
& $python -B (Join-Path $projectRoot 'scripts\validate_contracts.py')
if ($LASTEXITCODE -ne 0) { throw 'Contract validation failed' }
& $python -B -m pytest -q -o addopts='' -W error --basetemp (Join-Path $projectRoot 'tmp\pytest')
if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed' }
& $python -B -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed' }
