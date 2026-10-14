[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
if (-not $env:USERPROFILE) { throw 'USERPROFILE is unavailable.' }
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$bootstrapAllowedSids = @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
    'S-1-5-18',
    'S-1-5-32-544'
)
$bootstrapTrustedOwners = @($bootstrapAllowedSids)
try {
    $bootstrapTrustedOwners += [Security.Principal.NTAccount]::new(
        'NT SERVICE\TrustedInstaller'
    ).Translate([Security.Principal.SecurityIdentifier]).Value
} catch {
    # TrustedInstaller is unavailable on some Windows editions.
}
$bootstrapWriteMask = [int]([System.Security.AccessControl.FileSystemRights]::WriteData -bor
    [System.Security.AccessControl.FileSystemRights]::AppendData -bor
    [System.Security.AccessControl.FileSystemRights]::CreateFiles -bor
    [System.Security.AccessControl.FileSystemRights]::CreateDirectories -bor
    [System.Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
    [System.Security.AccessControl.FileSystemRights]::WriteAttributes -bor
    [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
    [System.Security.AccessControl.FileSystemRights]::Delete -bor
    [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [System.Security.AccessControl.FileSystemRights]::TakeOwnership)

function Assert-InstallerBootstrapPath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Installer bootstrap path contains a reparse point.'
            }
            $acl = Get-Acl -LiteralPath $cursor -ErrorAction Stop
            $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
            if ($bootstrapTrustedOwners -notcontains $owner) {
                throw 'Installer bootstrap path has an unapproved owner.'
            }
            foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
                if ($rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
                    $bootstrapAllowedSids -notcontains $rule.IdentityReference.Value -and
                    (([int]$rule.FileSystemRights -band $bootstrapWriteMask) -ne 0)) {
                    throw 'Installer source is writable by an unapproved principal.'
                }
            }
        }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
}

$securityModule = Join-Path $PSScriptRoot 'storage_security.ps1'
$prepareScript = Join-Path $PSScriptRoot 'prepare_runtime_storage.ps1'
Assert-InstallerBootstrapPath $PSCommandPath
Assert-InstallerBootstrapPath $securityModule
Assert-InstallerBootstrapPath $prepareScript
. $securityModule

$requiredSourcePaths = @(
    'automation',
    'scripts\room-likes-lib.mjs',
    'scripts\affiliate-report-lib.mjs',
    'package.json',
    'package-lock.json',
    '.agents\skills\rakuten-room-inago',
    '.agents\skills\rr-inago-observation',
    '.agents\skills\rr-inago-keywords',
    '.agents\skills\rr-inago-copywriter',
    '.agents\skills\rr-inago-insight',
    '.agents\skills\rr-inago-config',
    '.agents\skills\rr-inago-recovery',
    '.codex\agents\rr-inago-observation.toml',
    '.codex\agents\rr-inago-keywords.toml',
    '.codex\agents\rr-inago-copywriter.toml',
    '.codex\agents\rr-inago-insight.toml',
    '.codex\agents\rr-inago-config.toml',
    '.codex\agents\rr-inago-recovery.toml',
    'rakuten-room-inago\dashboard-assets',
    'rakuten-room-kai'
)
foreach ($relative in $requiredSourcePaths) {
    $source = Join-Path $ProjectRoot $relative
    if (-not (Test-Path -LiteralPath $source)) { throw "Required application source is missing: $relative" }
    Assert-InagoTrustedSourceTree -Root $source -AllowedSids $bootstrapAllowedSids
}

$prepareScript = Join-Path $PSScriptRoot 'prepare_runtime_storage.ps1'
& $prepareScript | Out-Null

$runtimeRoot = Join-Path $env:USERPROFILE 'InagoRuntime'
$appName = 'app-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$appRoot = Join-Path $runtimeRoot $appName
if (Test-Path -LiteralPath $appRoot) { throw 'Versioned application path already exists.' }

$allowedSids = @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
    'S-1-5-18',
    'S-1-5-32-544'
)
function Assert-TrustedEntry([string]$Path, [bool]$RequireProtected = $false) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'A protected runtime entry is a reparse point.'
    }
    Assert-InagoStorageAcl -Path $Path -AllowedSids $allowedSids `
        -Mode 'private' -RequireProtected $RequireProtected
}

function Assert-TrustedTree([string]$Root, [bool]$RequireProtectedRoot = $true) {
    Assert-TrustedEntry $Root $RequireProtectedRoot
    foreach ($entry in Get-ChildItem -LiteralPath $Root -Force -Recurse -ErrorAction Stop) {
        Assert-TrustedEntry $entry.FullName
    }
}

function Assert-TrustedReadOnlyTree([string]$Root) {
    Assert-SourceHasNoReparsePoint $Root
    Assert-InagoTrustedSourceTree -Root $Root -AllowedSids $allowedSids
}

function Resolve-TrustedApplication([string]$Name) {
    $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $command -or $command.CommandType.ToString() -ne 'Application') {
        throw "Required external application is unavailable: $Name"
    }
    $source = [IO.Path]::GetFullPath([string]$command.Source)
    $systemLauncherPaths = @()
    if ($env:WINDIR) {
        $systemLauncherPaths = @(
            [IO.Path]::GetFullPath((Join-Path $env:WINDIR 'py.exe')),
            [IO.Path]::GetFullPath((Join-Path $env:WINDIR 'System32\py.exe')),
            [IO.Path]::GetFullPath((Join-Path $env:WINDIR 'SysWOW64\py.exe'))
        )
    }
    if ($Name -ieq 'py.exe' -and $systemLauncherPaths -contains $source) {
        # Check the launcher and its ancestors, without recursively walking all of Windows.
        Assert-InagoTrustedSourceTree -Root $source -AllowedSids $allowedSids
    } else {
        # Non-system shims may load adjacent files, so validate their complete tool directory.
        Assert-InagoTrustedSourceTree -Root (Split-Path -Parent $source) -AllowedSids $allowedSids
    }
    return $source
}

$runtimeAcl = Get-Acl -LiteralPath $runtimeRoot
[void](New-Item -ItemType Directory -Path $appRoot)
Set-Acl -LiteralPath $appRoot -AclObject $runtimeAcl
function Assert-SourceHasNoReparsePoint([string]$Path) {
    $sourceEntries = @((Get-Item -LiteralPath $Path -Force))
    if ((Get-Item -LiteralPath $Path -Force).PSIsContainer) {
        $sourceEntries += @(Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop)
    }
    foreach ($sourceEntry in $sourceEntries) {
        if (($sourceEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Application source cannot contain reparse points.'
        }
    }
}

foreach ($relative in @('automation', 'scripts\room-likes-lib.mjs', 'scripts\affiliate-report-lib.mjs', 'package.json', 'package-lock.json')) {
    $source = Join-Path $ProjectRoot $relative
    if (-not (Test-Path -LiteralPath $source)) { throw "Required application source is missing: $relative" }
    Assert-SourceHasNoReparsePoint $source
    $destination = Join-Path $appRoot $relative
    $parent = Split-Path -Parent $destination
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
    }
    Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
}

foreach ($skill in @(
    'rakuten-room-inago',
    'rr-inago-observation',
    'rr-inago-keywords',
    'rr-inago-copywriter',
    'rr-inago-insight',
    'rr-inago-config',
    'rr-inago-recovery'
)) {
    $source = Join-Path $ProjectRoot ".agents\skills\$skill"
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "Required AI skill is missing: $skill" }
    Assert-SourceHasNoReparsePoint $source
    $destination = Join-Path $appRoot ".agents\skills\$skill"
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force)
    Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
}

$agentSource = Join-Path $ProjectRoot '.codex\agents'
$agentDestination = Join-Path $appRoot '.codex\agents'
[void](New-Item -ItemType Directory -Path $agentDestination -Force)
foreach ($agent in @('rr-inago-observation.toml', 'rr-inago-keywords.toml', 'rr-inago-copywriter.toml', 'rr-inago-insight.toml', 'rr-inago-config.toml', 'rr-inago-recovery.toml')) {
    $source = Join-Path $agentSource $agent
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required AI role definition is missing: $agent" }
    Assert-SourceHasNoReparsePoint $source
    Copy-Item -LiteralPath $source -Destination (Join-Path $agentDestination $agent)
}

$dashboardSource = Join-Path $ProjectRoot 'rakuten-room-inago\dashboard-assets'
if (-not (Test-Path -LiteralPath $dashboardSource -PathType Container)) { throw 'Dashboard assets are missing.' }
Assert-SourceHasNoReparsePoint $dashboardSource
$dashboardDestination = Join-Path $appRoot 'rakuten-room-inago\dashboard-assets'
[void](New-Item -ItemType Directory -Path (Split-Path -Parent $dashboardDestination) -Force)
Copy-Item -LiteralPath $dashboardSource -Destination $dashboardDestination -Recurse -Force

$legacySchemaSource = Join-Path $ProjectRoot 'rakuten-room-kai\orchestrator\schemas'
if (-not (Test-Path -LiteralPath $legacySchemaSource -PathType Container)) { throw 'Read-only legacy migration schemas are missing.' }
Assert-SourceHasNoReparsePoint $legacySchemaSource
$legacySchemaDestination = Join-Path $appRoot 'rakuten-room-kai\orchestrator\schemas'
[void](New-Item -ItemType Directory -Path $legacySchemaDestination -Force)
Get-ChildItem -LiteralPath $legacySchemaSource -Force | Copy-Item -Destination $legacySchemaDestination -Recurse -Force

Assert-TrustedTree $appRoot

$pythonLauncher = Resolve-TrustedApplication 'py.exe'
$pythonBase = (& $pythonLauncher -V:Astral/CPython3.12.13 -c 'import sys; print(sys.executable)').Trim()
if (-not (Test-Path -LiteralPath $pythonBase -PathType Leaf)) { throw 'The trusted Python 3.12 base runtime is unavailable.' }
Assert-InagoTrustedSourceTree -Root (Split-Path -Parent $pythonBase) -AllowedSids $allowedSids
& $pythonBase -m venv (Join-Path $appRoot '.venv-inago')
if ($LASTEXITCODE -ne 0) { throw 'Could not create the protected Python environment.' }
$pythonExe = Join-Path $appRoot '.venv-inago\Scripts\python.exe'
& $pythonExe -m pip install --disable-pip-version-check -r (Join-Path $appRoot 'automation\requirements.txt') -r (Join-Path $appRoot 'automation\inago\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Inago Python runtime requirements.' }

$nodePath = Resolve-TrustedApplication 'node.exe'
$npmPath = Resolve-TrustedApplication 'npm.cmd'
$codexPath = Resolve-TrustedApplication 'codex.exe'
$nodeVersion = (& $nodePath --version).Trim().TrimStart('v')
$stableNodeRoot = Join-Path $env:APPDATA "fnm\node-versions\v$nodeVersion\installation"
$stableNode = Join-Path $stableNodeRoot 'node.exe'
$stableNpm = Join-Path $stableNodeRoot 'npm.cmd'
if ((Test-Path -LiteralPath $stableNode -PathType Leaf) -and (Test-Path -LiteralPath $stableNpm -PathType Leaf)) {
    Assert-InagoTrustedSourceTree -Root $stableNodeRoot -AllowedSids $allowedSids
    $nodePath = $stableNode
    $npmPath = $stableNpm
}
$toolsRoot = Join-Path $appRoot 'tools'
[void](New-Item -ItemType Directory -Path $toolsRoot)
$nodeExe = Join-Path $toolsRoot 'node.exe'
$codexExe = Join-Path $toolsRoot 'codex.exe'
Copy-Item -LiteralPath $nodePath -Destination $nodeExe
Copy-Item -LiteralPath $codexPath -Destination $codexExe
Assert-TrustedTree $appRoot
Push-Location $appRoot
try {
    & $npmPath ci --omit=dev --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the protected Playwright runtime dependency.' }
    & $nodeExe --input-type=module -e "import('playwright-core').then(() => process.exit(0)).catch(() => process.exit(1))"
    if ($LASTEXITCODE -ne 0) { throw 'The protected Playwright dependency did not load.' }
} finally {
    Pop-Location
}
& $pythonExe -m automation.inago.cli setup --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'The protected Inago CLI did not load.' }
Assert-TrustedTree $appRoot

$legacySourceRoot = Join-Path $ProjectRoot 'rakuten-room-kai'
$manifestPath = Join-Path $runtimeRoot 'config\install-manifest.json'
$manifest = [ordered]@{
    schema_version = 1
    application_root = [IO.Path]::GetFullPath($appRoot)
    legacy_source_root = [IO.Path]::GetFullPath($legacySourceRoot)
}
$manifestJson = $manifest | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($manifestPath, $manifestJson, [Text.UTF8Encoding]::new($false))
Assert-TrustedEntry $manifestPath

[pscustomobject]@{
    ApplicationRoot = $appRoot
    Python = $pythonExe
    Node = $nodeExe
    Codex = $codexExe
    PythonBase = $pythonBase
    SkillBundles = 7
    AgentDefinitions = 6
    Protected = $true
} | ConvertTo-Json -Compress
