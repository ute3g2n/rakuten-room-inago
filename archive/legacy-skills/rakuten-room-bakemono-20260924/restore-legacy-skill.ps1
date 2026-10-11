param(
    [switch]$Apply,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
if ($Apply -and $WhatIf) { throw '-Apply and -WhatIf cannot be combined' }
$preview = -not $Apply
$package = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$root = (Resolve-Path -LiteralPath (Join-Path $package '../../..')).Path
$skills = Join-Path $root '.agents/skills'
$source = Join-Path $package 'source'
$manifestPath = Join-Path $package 'archive-manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

if (-not $source.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or
    -not $skills.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Restore paths are outside workspace'
}

$files = @($manifest.files)
if ($files.Count -ne $manifest.file_count) { throw 'Archive manifest count mismatch' }
$sum = 0
foreach ($entry in $files) {
    $src = Join-Path $source $entry.archive_path
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "Missing archive file: $src" }
    $info = Get-Item -LiteralPath $src
    if ($info.Length -ne $entry.size_bytes) { throw "Archive size mismatch: $src" }
    if ((Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash -ne $entry.sha256) { throw "Archive hash mismatch: $src" }
    $sum += $info.Length
}
if ($sum -ne $manifest.total_size_bytes) { throw 'Archive total size mismatch' }

$oldName = 'RakutenRoomBakemonoAffiliateDaily'
$newNames = @('RakutenRoomBakemonoKaiDaily', 'RakutenRoomBakemonoKaiHealthCheck')
foreach ($name in @($oldName) + $newNames) {
    Get-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
}

Write-Output "Archive verified: $($files.Count) files, $sum bytes"
Write-Output 'Task switch order: disable new Daily and HealthCheck; restore legacy files; enable old AffiliateDaily.'
foreach ($entry in $files) {
    $src = Join-Path $source $entry.archive_path
    $dst = Join-Path $skills $entry.path
    if (-not $dst.StartsWith($skills, [StringComparison]::OrdinalIgnoreCase)) { throw "Invalid destination: $dst" }
    Write-Output "Restore $src -> $dst"
    if (-not $preview -and (Test-Path -LiteralPath $dst)) { throw "Destination already exists: $dst" }
}
if ($preview) {
    Write-Output 'WhatIf: no files or task states changed. Use -Apply for the live restore.'
    return
}

foreach ($name in $newNames) { Disable-ScheduledTask -TaskName $name | Out-Null }
$copied = [System.Collections.Generic.List[string]]::new()
try {
    foreach ($entry in $files) {
        $src = Join-Path $source $entry.archive_path
        $dst = Join-Path $skills $entry.path
        New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst -ErrorAction Stop
        $copied.Add($dst)
        if ((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "Restored hash mismatch: $dst"
        }
    }
    Enable-ScheduledTask -TaskName $oldName | Out-Null
    Write-Output 'Legacy skill and task restored; new tasks remain disabled.'
} catch {
    foreach ($path in $copied) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    }
    Disable-ScheduledTask -TaskName $oldName -ErrorAction SilentlyContinue | Out-Null
    foreach ($name in $newNames) { Enable-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Out-Null }
    throw
}
