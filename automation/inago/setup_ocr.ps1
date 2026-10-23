[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DataRoot
)

$ErrorActionPreference = 'Stop'
$resolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$assetRoot = Join-Path $resolvedDataRoot 'ocr-assets'
$venvRoot = Join-Path $assetRoot '.venv'
$python = Join-Path $venvRoot 'Scripts\python.exe'
$lockFile = Join-Path $PSScriptRoot 'requirements-ocr.lock'

if ($resolvedDataRoot -notmatch '^[A-Za-z]:\\[A-Za-z0-9._\\-]+$') {
    throw 'DataRoot must be an ASCII Windows path so Paddle model/cache paths remain ASCII.'
}
if (-not (Test-Path -LiteralPath $lockFile -PathType Leaf)) {
    throw "OCR lock file is missing: $lockFile"
}

foreach ($relative in @('cache\pip', 'cache\xdg', 'cache\huggingface', 'models\paddlex', 'tmp')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $assetRoot $relative) | Out-Null
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $projectPython = Join-Path $repositoryRoot '.venv-inago\Scripts\python.exe'
    if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
        & $projectPython -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw "Creating OCR venv failed with exit code $LASTEXITCODE" }
    } else {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($null -eq $launcher) {
            throw 'A CPython 3.12 interpreter is required to create the isolated OCR environment.'
        }
        & $launcher.Source -3.12 -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) { throw "Creating OCR venv failed with exit code $LASTEXITCODE" }
    }
}

$env:PIP_CACHE_DIR = Join-Path $assetRoot 'cache\pip'
$env:TEMP = Join-Path $assetRoot 'tmp'
$env:TMP = Join-Path $assetRoot 'tmp'
$env:XDG_CACHE_HOME = Join-Path $assetRoot 'cache\xdg'
$env:HF_HOME = Join-Path $assetRoot 'cache\huggingface'
$env:PADDLE_PDX_CACHE_HOME = Join-Path $assetRoot 'models\paddlex'

& $python -m pip install --disable-pip-version-check -r $lockFile
if ($LASTEXITCODE -ne 0) { throw "Installing locked OCR dependencies failed with exit code $LASTEXITCODE" }

& $python -c "import sys; from importlib.metadata import version; assert sys.version_info[:2] == (3, 12); assert version('paddleocr') == '3.7.0'; assert version('paddlepaddle') == '3.3.1'; assert version('paddlex') == '3.7.2'"
if ($LASTEXITCODE -ne 0) { throw "Checking the OCR runtime failed with exit code $LASTEXITCODE" }

Write-Output "OCR runtime installed at $venvRoot"
Write-Output 'Paddle models download on first daily OCR use into models\paddlex.'
