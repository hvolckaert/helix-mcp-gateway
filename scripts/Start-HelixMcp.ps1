[CmdletBinding()]
param(
    [string]$DotenvPath
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

& $Python -m helix_mcp.operations.cli --dotenv $DotenvPath
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $Python -m helix_mcp.server --dotenv $DotenvPath
exit $LASTEXITCODE
