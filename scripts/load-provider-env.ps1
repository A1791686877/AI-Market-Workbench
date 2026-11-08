$ErrorActionPreference = 'Stop'
$providerFile = 'E:\AI-Market-Workbench\config\providers.env'
$allowedNames = @(
    'ALPACA_MARKET_DATA_KEY_ID',
    'ALPACA_MARKET_DATA_SECRET',
    'TWELVE_DATA_API_KEY',
    'ALPHA_VANTAGE_API_KEY',
    'TIINGO_API_TOKEN',
    'MASSIVE_API_KEY',
    'COINGECKO_DEMO_API_KEY',
    'FRED_API_KEY',
    'SEC_USER_AGENT'
)

if (Test-Path -LiteralPath $providerFile) {
    foreach ($line in Get-Content -LiteralPath $providerFile) {
        if ($line -match '^\s*(#|$)') { continue }
        if ($line -notmatch '^([A-Z0-9_]+)=(.*)$') {
            throw "Invalid providers.env line; expected NAME=value"
        }
        $name = $Matches[1]
        $value = $Matches[2]
        if ($allowedNames -notcontains $name) {
            throw "Environment name is not allowlisted in providers.env"
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}
