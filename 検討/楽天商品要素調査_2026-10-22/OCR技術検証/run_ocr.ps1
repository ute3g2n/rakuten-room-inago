param(
    [int]$Limit = 0,
    [string]$OutputName = 'ocr-results.jsonl'
)

$ErrorActionPreference = 'Stop'
$leafName = [System.IO.Path]::GetFileName($OutputName)
if ([System.IO.Path]::IsPathRooted($OutputName) -or
    $leafName -ne $OutputName -or
    $OutputName.Contains('..')) {
    throw "OutputName must be a single filename inside the study results folder."
}
$studyRoot = (Resolve-Path $PSScriptRoot).Path
$indexPath = (Resolve-Path (Join-Path $studyRoot '..\index.json')).Path
$createdAlias = $false
$locationPushed = $false
$runCode = 1
$environmentNames = @(
    'OCR_STUDY_ROOT', 'PIP_CACHE_DIR', 'TEMP', 'TMP', 'XDG_CACHE_HOME',
    'HF_HOME', 'PADDLE_PDX_CACHE_HOME'
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$mappings = & subst
$mapping = @($mappings | Where-Object { $_.StartsWith('R:\:') })
if ($mapping.Count -eq 0) {
    & subst R: $studyRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not create temporary R: alias for the study folder." }
    $createdAlias = $true
}
else {
    $mappedRunner = 'R:\ocr_runner.py'
    $studyRunner = Join-Path $studyRoot 'ocr_runner.py'
    if (-not (Test-Path -LiteralPath $mappedRunner) -or
        (Get-FileHash -LiteralPath $mappedRunner -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $studyRunner -Algorithm SHA256).Hash) {
        throw "R: is already mapped to another location; no OCR was started."
    }
    $studyPython = Join-Path $studyRoot '.venv-ocr\Scripts\python.exe'
    & $studyPython -c "from pathlib import Path; import sys; sys.exit(0 if Path('R:/').resolve() == Path(sys.argv[1]).resolve() else 1)" $studyRoot
    if ($LASTEXITCODE -ne 0) {
        throw "R: is already mapped to another folder; no OCR was started."
    }
}

try {
    $env:OCR_STUDY_ROOT = 'R:\'
    $env:PIP_CACHE_DIR = 'R:\cache\pip'
    $env:TEMP = 'R:\tmp'
    $env:TMP = 'R:\tmp'
    $env:XDG_CACHE_HOME = 'R:\cache\xdg'
    $env:HF_HOME = 'R:\cache\huggingface'
    $env:PADDLE_PDX_CACHE_HOME = 'R:\models\paddlex'

    $outputPath = "R:\results\$OutputName"
    $arguments = @(
        'R:\ocr_runner.py',
        '--index', $indexPath,
        '--output', $outputPath
    )
    if ($Limit -gt 0) {
        $arguments += @('--limit', "$Limit")
    }

    Push-Location $studyRoot
    $locationPushed = $true
    & R:\.venv-ocr\Scripts\python.exe @arguments
    $runCode = $LASTEXITCODE
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
    if ($createdAlias) {
        & subst R: /d
        $cleanupCode = $LASTEXITCODE
        if ($cleanupCode -ne 0 -and $runCode -eq 0) {
            $runCode = 1
            Write-Warning "OCR completed, but the temporary R: alias could not be removed."
        }
    }
}
exit $runCode
