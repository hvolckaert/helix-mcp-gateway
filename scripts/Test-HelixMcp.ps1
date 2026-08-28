[CmdletBinding()]
param(
    [string]$DotenvPath,
    [switch]$Live,
    [ValidateSet("dev", "qa", "prod")]
    [string[]]$Environment
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
if (-not $DotenvPath) {
    $DotenvPath = Join-Path $ProjectDir ".env"
}
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Error "Helix MCP virtual environment is not available."
    exit 2
}

$Arguments = @(
    "-m",
    "helix_mcp.operations.cli",
    "--dotenv",
    $DotenvPath
)
if ($Live) {
    $Arguments += "--live"
}
foreach ($Name in $Environment) {
    $Arguments += @("--environment", $Name)
}

& $Python @Arguments
exit $LASTEXITCODE
