[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$StartBoundary,
    [switch]$StartHumanApi,
    [switch]$RenderOnly,
    [string]$ProjectRoot = (Join-Path $PSScriptRoot "..\..")
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if (-not $env:USERPROFILE) { throw 'USERPROFILE is unavailable.' }
$projectRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
$runtimeRoot = Join-Path $projectRoot 'rakuten-room-inago\runtime'
$backupRoot = Join-Path $projectRoot 'rakuten-room-inago\backups'
$expectedRoot = [IO.Path]::GetFullPath($runtimeRoot).TrimEnd('\')
$configPath = Join-Path $expectedRoot 'config\settings.json'
$setupPath = Join-Path $expectedRoot 'state\setup-status.json'

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf) -or -not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    throw 'Production configuration and setup state must exist before task registration.'
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$setup = Get-Content -LiteralPath $setupPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.environment -ne 'production' -or $config.start_date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    throw 'Production configuration is missing its valid start date.'
}
$expectedBoundary = "$($config.start_date)T13:00:00+09:00"
if ($StartBoundary -cne $expectedBoundary) {
    throw 'The daily trigger must match the configured first date at 13:00 JST.'
}
if ($config.runtime_root -and [IO.Path]::GetFullPath($config.runtime_root).TrimEnd('\').ToLowerInvariant() -ne $expectedRoot.ToLowerInvariant()) {
    throw 'Production config points outside the repository runtime root.'
}
if ($config.backup_root -and [IO.Path]::GetFullPath($config.backup_root).TrimEnd('\').ToLowerInvariant() -ne [IO.Path]::GetFullPath($backupRoot).TrimEnd('\').ToLowerInvariant()) {
    throw 'Production config points outside the repository backup root.'
}
$applicationRoot = $projectRoot
if (-not $config.application_root -or
    [IO.Path]::GetFullPath([string]$config.application_root).TrimEnd('\').ToLowerInvariant() -ne $applicationRoot.ToLowerInvariant()) {
    throw 'Production config must run directly from this repository.'
}
$browserProfile = Join-Path $env:LOCALAPPDATA 'InagoPlaywrightProfile'
if (-not $config.browser.user_data_dir -or
    [IO.Path]::GetFullPath($config.browser.user_data_dir).TrimEnd('\').ToLowerInvariant() -ne [IO.Path]::GetFullPath($browserProfile).TrimEnd('\').ToLowerInvariant()) {
    throw 'Production config points outside the dedicated Playwright profile.'
}
$pythonExe = Join-Path $applicationRoot '.venv-inago\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { throw 'Repository Inago Python runtime is missing.' }
if ($setup -isnot [PSCustomObject] -or
    ($setup.schema_version -isnot [int] -and $setup.schema_version -isnot [long]) -or
    $setup.schema_version -ne 1) {
    throw 'Production setup state schema is missing or unsupported.'
}
if ($setup.status -ne 'ready' -or $setup.migration_selection_allowed -ne $true) {
    throw 'Migration and settings preconditions are not ready for selection.'
}

$start = [DateTimeOffset]::ParseExact(
    $StartBoundary,
    "yyyy-MM-dd'T'HH:mm:sszzz",
    [System.Globalization.CultureInfo]::InvariantCulture
)
if ($start -le [DateTimeOffset]::Now) { throw 'The daily start boundary must be in the future.' }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userId = $identity.User.Value

$namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'

function New-TaskXml([string]$TemplateName, [string]$Mode, [bool]$Daily) {
    [xml]$document = Get-Content -LiteralPath (Join-Path $PSScriptRoot "task-xml\$TemplateName") -Raw -Encoding UTF8
    $manager = New-Object System.Xml.XmlNamespaceManager($document.NameTable)
    $manager.AddNamespace('t', $namespace)
    $principalNode = $document.SelectSingleNode('//t:Principal/t:UserId', $manager)
    if ($null -eq $principalNode) { throw 'Task template is missing its principal.' }
    $principalNode.InnerText = $userId

    $exec = $document.SelectSingleNode('//t:Actions/t:Exec', $manager)
    if ($null -eq $exec) { throw 'Task template is missing its executable action.' }
    $exec.SelectSingleNode('t:Command', $manager).InnerText = $pythonExe
    $args = "-B -m automation.inago.cli $Mode --environment production --config `"$configPath`" --data-root `"$expectedRoot`""
    $exec.SelectSingleNode('t:Arguments', $manager).InnerText = $args
    $exec.SelectSingleNode('t:WorkingDirectory', $manager).InnerText = $applicationRoot
    if ($Daily) {
        $boundary = $document.SelectSingleNode('//t:CalendarTrigger/t:StartBoundary', $manager)
        if ($null -eq $boundary) { throw 'Daily task template is missing its calendar boundary.' }
        $boundary.InnerText = $StartBoundary
    }
    return $document.OuterXml
}

function Register-ExactTask([string]$Name, [string]$Xml, [string]$Mode) {
    $existing = Get-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($existing.Actions.Count -ne 1) { throw "Existing task '$Name' has an unexpected action count." }
        $existingAction = $existing.Actions[0]
        $expectedArgs = "-B -m automation.inago.cli $Mode --environment production --config `"$configPath`" --data-root `"$expectedRoot`""
        $matchesRepository = ([IO.Path]::GetFullPath($existingAction.Execute).ToLowerInvariant() -eq [IO.Path]::GetFullPath($pythonExe).ToLowerInvariant() -and $existingAction.Arguments -ceq $expectedArgs -and $existingAction.WorkingDirectory -eq $applicationRoot)
        $oldRoot = Join-Path $env:USERPROFILE 'InagoRuntime'
        $oldApp = [IO.Path]::GetFullPath([string]$existingAction.WorkingDirectory).TrimEnd('\')
        $oldPython = Join-Path $oldApp '.venv-inago\Scripts\python.exe'
        $oldConfig = Join-Path $oldRoot 'config\settings.json'
        $oldArgs = "-B -m automation.inago.cli $Mode --environment production --config `"$oldConfig`" --data-root `"$oldRoot`""
        $matchesPrevious = ($oldApp.StartsWith($oldRoot + '\', [StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Parent $oldApp) -eq $oldRoot -and
            (Split-Path -Leaf $oldApp) -match '^app-\d{8}-\d{6}$' -and
            [IO.Path]::GetFullPath($existingAction.Execute).ToLowerInvariant() -eq $oldPython.ToLowerInvariant() -and
            $existingAction.Arguments -ceq $oldArgs)
        if (-not $matchesRepository -and -not $matchesPrevious) {
            throw "Existing task '$Name' differs from the reviewed Inago definition; it was left untouched."
        }
        $principal = $existing.Principal
        try {
            $existingSid = ([Security.Principal.NTAccount]::new($principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
        } catch {
            throw "Existing task '$Name' has an unrecognized principal; it was left untouched."
        }
        if ($existingSid -ne $userId -or $principal.LogonType.ToString() -ne 'Interactive' -or $principal.RunLevel.ToString() -ne 'Limited') {
            throw "Existing task '$Name' has an unexpected principal; it was left untouched."
        }
        if ($existing.Settings.MultipleInstances.ToString() -ne 'IgnoreNew' -or -not $existing.Settings.Hidden) {
            throw "Existing task '$Name' has unexpected scheduler settings; it was left untouched."
        }
        if ($existing.Triggers.Count -ne 1 -or $existing.Triggers[0].Enabled -ne $true) {
            throw "Existing task '$Name' has unexpected triggers; it was left untouched."
        }
        $trigger = $existing.Triggers[0]
        if ($Mode -eq 'daily') {
            if ($trigger.CimClass.CimClassName -ne 'MSFT_TaskDailyTrigger' -or $trigger.DaysInterval -ne 1 -or
                ($matchesRepository -and $trigger.StartBoundary -cne $StartBoundary) -or
                ($matchesPrevious -and $trigger.StartBoundary -notmatch '^\d{4}-\d{2}-\d{2}T13:00:00\+09:00$')) {
                throw "Existing task '$Name' does not match the requested 13:00 daily schedule; it was left untouched."
            }
        } elseif ($trigger.CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger') {
            throw "Existing task '$Name' does not use the required logon trigger; it was left untouched."
        }
        $state = $existing.State.ToString()
        if ($matchesPrevious) {
            if (@('Ready', 'Disabled') -notcontains $state) {
                throw "Previous Inago task '$Name' is not idle; preserve its run before switching execution paths."
            }
            Register-ScheduledTask -TaskName $Name -TaskPath '\' -Xml $Xml -Force | Out-Null
            return
        }
        if ($state -eq 'Disabled') { Enable-ScheduledTask -TaskName $Name -TaskPath '\' | Out-Null }
        return
    }
    Register-ScheduledTask -TaskName $Name -TaskPath '\' -Xml $Xml | Out-Null
}

$dailyXml = New-TaskXml 'RakutenRoomInagoDaily.xml' 'daily' $true
$humanXml = New-TaskXml 'RakutenRoomInagoHumanApi.xml' 'serve' $false
if ($RenderOnly) {
    [pscustomobject]@{
        WorkingDirectory = $applicationRoot
        Python = $pythonExe
        DailyXml = $dailyXml
        HumanApiXml = $humanXml
        Registered = $false
    } | ConvertTo-Json -Depth 5 -Compress
    return
}

# Validate input contracts through the repository Python, without ACL changes,
# source copying, DB migration, browser launch, or installation.
Push-Location $applicationRoot
try {
    & $pythonExe -B -c 'import sys; from automation.inago.production_runtime import load_production_config, require_production_ready; c=load_production_config(sys.argv[1], data_root=sys.argv[2]); require_production_ready(c)' $configPath $expectedRoot
    if ($LASTEXITCODE -ne 0) { throw 'Repository production preflight failed; no tasks were registered.' }
} finally {
    Pop-Location
}
$oldTasks = @(
    'RakutenRoomBakemonoAffiliateDaily',
    'RakutenRoomBakemonoKaiDaily',
    'RakutenRoomBakemonoKaiDailyCandidate',
    'RakutenRoomBakemonoKaiHealthCheck',
    'RakutenRoomBakemonoKaiHealthCheckCandidate'
)
foreach ($oldName in $oldTasks) {
    $oldTask = Get-ScheduledTask -TaskName $oldName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($null -ne $oldTask -and $oldTask.State -ne 'Disabled') {
        throw "Legacy task '$oldName' is not disabled; refusing parallel operation."
    }
}

Register-ExactTask 'RakutenRoomInagoDaily' $dailyXml 'daily'
Register-ExactTask 'RakutenRoomInagoHumanApi' $humanXml 'serve'
Enable-ScheduledTask -TaskName 'RakutenRoomInagoDaily' -TaskPath '\' | Out-Null
Enable-ScheduledTask -TaskName 'RakutenRoomInagoHumanApi' -TaskPath '\' | Out-Null

if ($StartHumanApi) {
    Start-ScheduledTask -TaskName 'RakutenRoomInagoHumanApi' -TaskPath '\'
    $started = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        $current = Get-ScheduledTask -TaskName 'RakutenRoomInagoHumanApi' -TaskPath '\'
        if ($current.State -eq 'Running') { $started = $true; break }
    }
    if (-not $started) { throw 'Human API task did not enter Running state.' }
}

[pscustomobject]@{
    DailyTask = (Get-ScheduledTask -TaskName 'RakutenRoomInagoDaily' -TaskPath '\').State.ToString()
    HumanApiTask = (Get-ScheduledTask -TaskName 'RakutenRoomInagoHumanApi' -TaskPath '\').State.ToString()
    StartBoundary = $StartBoundary
    HumanApiStarted = [bool]$StartHumanApi
} | ConvertTo-Json -Compress
