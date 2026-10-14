[CmdletBinding()]
param(
    [ValidateSet('setup', 'daily', 'serve')]
    [string]$Mode = 'daily'
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$pythonExe = Join-Path $projectRoot '.venv-inago\Scripts\python.exe'
$runtimeRoot = Join-Path $projectRoot 'rakuten-room-inago\runtime'
$configPath = Join-Path $runtimeRoot 'config\settings.json'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw 'Repository Python environment is missing.'
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw 'Repository production configuration is missing.'
}
Push-Location $projectRoot
try {
    & $pythonExe -B -m automation.inago.cli $Mode --environment production --config $configPath --data-root $runtimeRoot
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
