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
if (-not $env:USERPROFILE -or -not $env:LOCALAPPDATA) { throw 'The interactive user profile paths are unavailable.' }
$userProfile = [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\')
$localAppData = [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\')
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
$existingDailyTask = Get-ScheduledTask -TaskName 'RakutenRoomInagoDaily' -TaskPath '\' -ErrorAction SilentlyContinue
if ($start -le [DateTimeOffset]::Now -and
    ($null -eq $existingDailyTask -or @('Ready', 'Disabled') -notcontains $existingDailyTask.State.ToString())) {
    throw 'A past daily start boundary can only be retained while the existing task is idle.'
}

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
    if ($Daily) {
        $args = "-B -m automation.inago.maintenance.daily_launcher --config `"$configPath`" --data-root `"$expectedRoot`""
    } else {
        $args = "-B -m automation.inago.cli $Mode --environment production --config `"$configPath`" --data-root `"$expectedRoot`" --user-profile `"$userProfile`" --local-app-data `"$localAppData`""
    }
    $exec.SelectSingleNode('t:Arguments', $manager).InnerText = $args
    $exec.SelectSingleNode('t:WorkingDirectory', $manager).InnerText = $applicationRoot
    $wakeToRun = $document.SelectSingleNode('//t:Settings/t:WakeToRun', $manager)
    if ($null -eq $wakeToRun) { throw 'Task template is missing its wake setting.' }
    $wakeToRun.InnerText = $Daily.ToString().ToLowerInvariant()
    if ($Daily) {
        $boundary = $document.SelectSingleNode('//t:CalendarTrigger/t:StartBoundary', $manager)
        if ($null -eq $boundary) { throw 'Daily task template is missing its calendar boundary.' }
        $boundary.InnerText = $StartBoundary
    } else {
        $logonTrigger = $document.SelectSingleNode('//t:LogonTrigger', $manager)
        if ($null -eq $logonTrigger) { throw 'Human API task template is missing its logon trigger.' }
        $logonUser = $logonTrigger.SelectSingleNode('t:UserId', $manager)
        if ($null -eq $logonUser) {
            $logonUser = $document.CreateElement('UserId', $namespace)
            $logonTrigger.AppendChild($logonUser) | Out-Null
        }
        $logonUser.InnerText = $userId
    }
    $declaration = $document.FirstChild
    if ($declaration -is [System.Xml.XmlDeclaration]) {
        $document.RemoveChild($declaration) | Out-Null
    }
    return $document.OuterXml
}

function Resolve-TaskUserSid([object]$UserId) {
    if ($null -eq $UserId -or [string]::IsNullOrWhiteSpace([string]$UserId)) { return $null }
    $value = [string]$UserId
    try {
        if ($value -match '^S-1-') {
            return ([Security.Principal.SecurityIdentifier]::new($value)).Value
        }
        return ([Security.Principal.NTAccount]::new($value)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        return $null
    }
}

function Register-ExactTask([string]$Name, [string]$Xml, [string]$Mode) {
    $existing = Get-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($existing.Actions.Count -ne 1) { throw "Existing task '$Name' has an unexpected action count." }
        $existingAction = $existing.Actions[0]
        if ($Mode -eq 'daily') {
            $expectedArgs = "-B -m automation.inago.maintenance.daily_launcher --config `"$configPath`" --data-root `"$expectedRoot`""
        } else {
            $expectedArgs = "-B -m automation.inago.cli $Mode --environment production --config `"$configPath`" --data-root `"$expectedRoot`" --user-profile `"$userProfile`" --local-app-data `"$localAppData`""
        }
        $matchesRepository = ([IO.Path]::GetFullPath($existingAction.Execute).ToLowerInvariant() -eq [IO.Path]::GetFullPath($pythonExe).ToLowerInvariant() -and $existingAction.Arguments -ceq $expectedArgs -and $existingAction.WorkingDirectory -eq $applicationRoot)
        $previousRepositoryArgs = @(
            "-B -m automation.inago.cli $Mode --environment production --config `"$configPath`" --data-root `"$expectedRoot`" --user-profile `"$userProfile`" --local-app-data `"$localAppData`"",
            "-B -m automation.inago.cli $Mode --environment production --config `"$configPath`" --data-root `"$expectedRoot`""
        )
        $matchesPreviousRepository = (-not $matchesRepository -and [IO.Path]::GetFullPath($existingAction.Execute).ToLowerInvariant() -eq [IO.Path]::GetFullPath($pythonExe).ToLowerInvariant() -and $previousRepositoryArgs -ccontains $existingAction.Arguments -and $existingAction.WorkingDirectory -eq $applicationRoot)
        $oldRoot = Join-Path $userProfile 'InagoRuntime'
        $oldApp = [IO.Path]::GetFullPath([string]$existingAction.WorkingDirectory).TrimEnd('\')
        $oldPython = Join-Path $oldApp '.venv-inago\Scripts\python.exe'
        $oldConfig = Join-Path $oldRoot 'config\settings.json'
        $oldArgs = "-B -m automation.inago.cli $Mode --environment production --config `"$oldConfig`" --data-root `"$oldRoot`""
        $matchesPrevious = ($oldApp.StartsWith($oldRoot + '\', [StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Parent $oldApp) -eq $oldRoot -and
            (Split-Path -Leaf $oldApp) -match '^app-\d{8}-\d{6}$' -and
            [IO.Path]::GetFullPath($existingAction.Execute).ToLowerInvariant() -eq $oldPython.ToLowerInvariant() -and
            $existingAction.Arguments -ceq $oldArgs)
        if (-not $matchesRepository -and -not $matchesPreviousRepository -and -not $matchesPrevious) {
            throw "Existing task '$Name' differs from the reviewed Inago definition; it was left untouched."
        }
        $principal = $existing.Principal
        $existingSid = Resolve-TaskUserSid $principal.UserId
        if (-not $existingSid) {
            throw "Existing task '$Name' has an unrecognized principal; it was left untouched."
        }
        if ($existingSid -ne $userId -or $principal.LogonType.ToString() -ne 'Interactive' -or $principal.RunLevel.ToString() -ne 'Limited') {
            throw "Existing task '$Name' has an unexpected principal; it was left untouched."
        }
        if ($existing.Settings.MultipleInstances.ToString() -ne 'IgnoreNew' -or -not $existing.Settings.Hidden) {
            throw "Existing task '$Name' has unexpected scheduler settings; it was left untouched."
        }
        $executionTimeLimit = [string]$existing.Settings.ExecutionTimeLimit
        $executionTimeLimitMatches = $executionTimeLimit -in @('PT0S', '00:00:00')
        if ($existing.Triggers.Count -ne 1 -or $existing.Triggers[0].Enabled -ne $true) {
            throw "Existing task '$Name' has unexpected triggers; it was left untouched."
        }
        $trigger = $existing.Triggers[0]
        if ($Mode -eq 'daily') {
            if ($trigger.CimClass.CimClassName -ne 'MSFT_TaskDailyTrigger' -or $trigger.DaysInterval -ne 1 -or
                (($matchesRepository -or $matchesPreviousRepository) -and $trigger.StartBoundary -cne $StartBoundary) -or
                ($matchesPrevious -and $trigger.StartBoundary -notmatch '^\d{4}-\d{2}-\d{2}T13:00:00\+09:00$')) {
                throw "Existing task '$Name' does not match the requested 13:00 daily schedule; it was left untouched."
            }
        } else {
            if ($trigger.CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger') {
                throw "Existing task '$Name' does not use the required logon trigger; it was left untouched."
            }
            $triggerUserSid = Resolve-TaskUserSid $trigger.UserId
            if ($matchesRepository -and $triggerUserSid -ne $userId) {
                throw "Existing task '$Name' is not limited to the current user's logon; it was left untouched."
            }
            if ($matchesPreviousRepository -and $trigger.UserId -and $triggerUserSid -ne $userId) {
                throw "Existing task '$Name' is not limited to the current user's logon; it was left untouched."
            }
        }
        $state = $existing.State.ToString()
        if ($matchesPrevious -or $matchesPreviousRepository) {
            if (@('Ready', 'Disabled') -notcontains $state) {
                throw "Previous Inago task '$Name' is not idle; preserve its run before switching execution paths."
            }
            if ($Mode -eq 'daily' -and $matchesPreviousRepository) {
                Set-ScheduledTask -TaskName $Name -TaskPath '\' -Action (
                    New-ScheduledTaskAction -Execute $pythonExe -Argument $expectedArgs -WorkingDirectory $applicationRoot
                ) -ErrorAction Stop | Out-Null
                $updated = Get-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop
                if ($updated.Actions.Count -ne 1 -or
                    $updated.Actions[0].Arguments -cne $expectedArgs -or
                    $updated.Actions[0].WorkingDirectory -ne $applicationRoot -or
                    [IO.Path]::GetFullPath($updated.Actions[0].Execute).ToLowerInvariant() -ne [IO.Path]::GetFullPath($pythonExe).ToLowerInvariant()) {
                    throw "Daily task '$Name' action did not switch to the daily launcher."
                }
                return
            }
            Register-ScheduledTask -TaskName $Name -TaskPath '\' -Xml $Xml -Force -ErrorAction Stop | Out-Null
            return
        }
        $expectedWakeToRun = ($Mode -eq 'daily')
        $wakeSetting = $existing.Settings.PSObject.Properties['WakeToRun']
        $wakeMatches = $null -ne $wakeSetting -and ([bool]$wakeSetting.Value -eq $expectedWakeToRun)
        if (-not $wakeMatches -or -not $executionTimeLimitMatches) {
            if (@('Ready', 'Disabled') -notcontains $state) {
                throw "Task '$Name' has an unexpected wake/execution time limit and is not idle; it was left untouched."
            }
            Register-ScheduledTask -TaskName $Name -TaskPath '\' -Xml $Xml -Force -ErrorAction Stop | Out-Null
            return
        }
        if ($state -eq 'Disabled') { Enable-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop | Out-Null }
        return
    }
    Register-ScheduledTask -TaskName $Name -TaskPath '\' -Xml $Xml -ErrorAction Stop | Out-Null
    if ($null -eq (Get-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop)) {
        throw "Task '$Name' was not registered."
    }
}

function Test-HumanApiLiveness([int]$Port) {
    $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/health")
    $request.Proxy = $null
    $request.Timeout = 500
    $request.ReadWriteTimeout = 500
    $request.AllowAutoRedirect = $false
    $status = 0
    try {
        $response = $request.GetResponse()
    } catch [System.Net.WebException] {
        $response = $_.Exception.Response
    }
    if ($null -ne $response) {
        $status = [int]$response.StatusCode
        $response.Close()
    }
    return $status -eq 200
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

# Validate the normal scheduler identity and existing runtime state without DB
# writes, browser launch, network access, or task execution.
Push-Location $applicationRoot
try {
    $preflightOutput = & $pythonExe -B -c 'import json,sys; from automation.inago.production_runtime import run_production_startup_preflight; r=run_production_startup_preflight(sys.argv[1], data_root=sys.argv[2], user_profile=sys.argv[3], local_app_data=sys.argv[4]); print(json.dumps(r, ensure_ascii=False, separators=(",",":"))); sys.exit(0 if r["status"] == "ready" else 4)' $configPath $expectedRoot $userProfile $localAppData
    $preflightExitCode = $LASTEXITCODE
    if ($preflightExitCode -ne 0) { throw 'Repository production preflight is blocked; no tasks were registered.' }
    $preflightReport = ($preflightOutput -join "`n") | ConvertFrom-Json
    if ($preflightReport.status -cne 'ready') { throw 'Repository production preflight is blocked; no tasks were registered.' }
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
Enable-ScheduledTask -TaskName 'RakutenRoomInagoDaily' -TaskPath '\' -ErrorAction Stop | Out-Null
Enable-ScheduledTask -TaskName 'RakutenRoomInagoHumanApi' -TaskPath '\' -ErrorAction Stop | Out-Null

if ($StartHumanApi) {
    Start-ScheduledTask -TaskName 'RakutenRoomInagoHumanApi' -TaskPath '\' -ErrorAction Stop
    $healthy = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        $current = Get-ScheduledTask -TaskName 'RakutenRoomInagoHumanApi' -TaskPath '\' -ErrorAction Stop
        if ($current.State -eq 'Running' -and (Test-HumanApiLiveness -Port ([int]$config.human_api.port))) {
            $healthy = $true
            break
        }
    }
    if (-not $healthy) { throw 'Human API task did not remain running and pass the authenticated loopback liveness check.' }
}

[pscustomobject]@{
    DailyTask = (Get-ScheduledTask -TaskName 'RakutenRoomInagoDaily' -TaskPath '\' -ErrorAction Stop).State.ToString()
    HumanApiTask = (Get-ScheduledTask -TaskName 'RakutenRoomInagoHumanApi' -TaskPath '\' -ErrorAction Stop).State.ToString()
    StartBoundary = $StartBoundary
    HumanApiStarted = [bool]$StartHumanApi
    HumanApiHealthy = [bool]$StartHumanApi
} | ConvertTo-Json -Compress
