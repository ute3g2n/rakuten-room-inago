[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProbeRoot,
    [Parameter(Mandatory = $true)]
    [string]$TaskName
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function ConvertTo-XmlText([string]$Value) {
    return [Security.SecurityElement]::Escape($Value)
}

function Assert-NoReparsePoint([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'probe_path_reparse_point'
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent
    }
}

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$lc03Root = [IO.Path]::GetFullPath((Join-Path $repoRoot '非公開作業データ\イナゴ実装試験\maintenance-20261017\U18\LC03'))
$root = [IO.Path]::GetFullPath($ProbeRoot)
$relativeRoot = $root.Substring($lc03Root.Length).TrimStart('\')
if (-not $root.StartsWith($lc03Root + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $relativeRoot -notmatch '^attempt-[0-9]+$') {
    throw 'probe_root_out_of_scope'
}
if ($TaskName -notmatch '^InagoMaintenanceProbe_[0-9a-f]{32}$') {
    throw 'probe_task_name_invalid'
}
Assert-NoReparsePoint $lc03Root
Assert-NoReparsePoint $root
if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
}
if (Get-ChildItem -LiteralPath $root -Force | Select-Object -First 1) {
    throw 'probe_root_not_empty'
}

$existing = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($null -ne $existing) { throw 'probe_task_name_collision' }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($null -eq $identity.User) { throw 'probe_user_identity_unavailable' }
$userSid = $identity.User.Value
$powerShellExe = [IO.Path]::GetFullPath((Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'))
if (-not (Test-Path -LiteralPath $powerShellExe -PathType Leaf)) {
    throw 'probe_powershell_unavailable'
}
$workerScript = Join-Path $root 'task-probe.ps1'
$resultFile = Join-Path $root 'task-events.jsonl'
$evidencePath = Join-Path $root 'result.json'
$workerScriptContents = @'
param([Parameter(Mandatory = $true)][string]$EventPath)
$ErrorActionPreference = 'Stop'
@{ event = 'started'; at = [DateTimeOffset]::Now.ToString('o') } |
    ConvertTo-Json -Compress | Add-Content -LiteralPath $EventPath -Encoding UTF8
Start-Sleep -Seconds 4
@{ event = 'completed'; at = [DateTimeOffset]::Now.ToString('o') } |
    ConvertTo-Json -Compress | Add-Content -LiteralPath $EventPath -Encoding UTF8
'@
Set-Content -LiteralPath $workerScript -Value $workerScriptContents -Encoding UTF8
$arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -EventPath "{1}"' -f $workerScript, $resultFile
$taskXml = @"
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Author>RakutenRoomInago U18 LC03</Author><Description>Owned temporary scheduler integration probe.</Description></RegistrationInfo>
  <Triggers />
  <Principals><Principal id="Probe"><UserId>$(ConvertTo-XmlText $userSid)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <WakeToRun>false</WakeToRun>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Probe"><Exec><Command>$(ConvertTo-XmlText $powerShellExe)</Command><Arguments>$(ConvertTo-XmlText $arguments)</Arguments><WorkingDirectory>$(ConvertTo-XmlText $root)</WorkingDirectory></Exec></Actions>
</Task>
"@
Set-Content -LiteralPath (Join-Path $root 'registered-task.xml') -Value $taskXml -Encoding UTF8
$registeredByProbe = $false
$status = 'failed'
$reasonCode = 'probe_failed'
$probeStage = 'register_task'
$errorId = $null
$errorHResult = $null
$errorCategory = $null
$taskObserved = $null
$eventCount = 0
$lastTaskResult = $null

try {
    Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Xml $taskXml -ErrorAction Stop | Out-Null
    $registeredByProbe = $true
    $probeStage = 'verify_definition'
    $task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop
    $action = $task.Actions | Select-Object -First 1
    $limit = [string]$task.Settings.ExecutionTimeLimit
    $registeredUserSid = if ($task.Principal.UserId -match '^S-1-') {
        $task.Principal.UserId
    }
    else {
        (New-Object Security.Principal.NTAccount($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $taskObserved = [ordered]@{
        action = [IO.Path]::GetFullPath($action.Execute)
        arguments = $action.Arguments
        working_directory = [IO.Path]::GetFullPath($action.WorkingDirectory)
        user_id = $task.Principal.UserId
        user_sid = $registeredUserSid
        logon_type = [string]$task.Principal.LogonType
        run_level = [string]$task.Principal.RunLevel
        multiple_instances = [string]$task.Settings.MultipleInstances
        hidden = [bool]$task.Settings.Hidden
        execution_time_limit = $limit
    }
    if ($task.Actions.Count -ne 1 -or
        $taskObserved.action -ine $powerShellExe -or
        $taskObserved.arguments -cne $arguments -or
        $taskObserved.working_directory -ine $root -or
        $registeredUserSid -ne $userSid -or
        $taskObserved.logon_type -notin @('Interactive', 'InteractiveToken') -or
        $taskObserved.run_level -notin @('Limited', 'LeastPrivilege') -or
        $taskObserved.multiple_instances -notin @('IgnoreNew', 'IgnoreNew') -or
        -not $taskObserved.hidden -or
        $limit -notin @('PT0S', '00:00:00')) {
        throw 'probe_task_definition_mismatch'
    }

    $probeStage = 'run_first_instance'
    Start-ScheduledTask -TaskName $TaskName -TaskPath '\'
    $startDeadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
    while ([DateTimeOffset]::UtcNow -lt $startDeadline) {
        if (Test-Path -LiteralPath $resultFile) {
            $events = @(Get-Content -LiteralPath $resultFile -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
            if (@($events | Where-Object event -eq 'started').Count -eq 1) { break }
        }
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $resultFile)) {
        $lastTaskResult = (Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath '\').LastTaskResult
        throw 'probe_task_did_not_start'
    }
    $events = @(Get-Content -LiteralPath $resultFile -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
    if (@($events | Where-Object event -eq 'started').Count -ne 1) { throw 'probe_task_start_not_observed' }
    $probeStage = 'run_overlap_request'
    Start-ScheduledTask -TaskName $TaskName -TaskPath '\'
    $completionDeadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    while ([DateTimeOffset]::UtcNow -lt $completionDeadline) {
        $events = @(Get-Content -LiteralPath $resultFile -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
        if (@($events | Where-Object event -eq 'completed').Count -eq 1) { break }
        Start-Sleep -Milliseconds 100
    }
    $events = @(Get-Content -LiteralPath $resultFile -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
    $eventCount = $events.Count
    if ($eventCount -ne 2 -or
        @($events | Where-Object event -eq 'started').Count -ne 1 -or
        @($events | Where-Object event -eq 'completed').Count -ne 1) {
        throw 'probe_task_concurrency_contract_failed'
    }
    $lastTaskResult = (Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath '\').LastTaskResult
    if ($lastTaskResult -ne 0) { throw 'probe_task_exit_code_failed' }
    $status = 'passed'
    $reasonCode = $null
    $probeStage = 'completed'
}
catch {
    $exceptionType = $_.Exception.GetType().Name
    $reasonCode = if ($exceptionType -match '^[A-Za-z][A-Za-z0-9_]{0,63}$') { "probe_$exceptionType" } else { 'probe_failed' }
    $errorId = [string]$_.FullyQualifiedErrorId
    if ($errorId.Length -gt 160) { $errorId = $errorId.Substring(0, 160) }
    $errorHResult = [int]$_.Exception.HResult
    $errorCategory = [string]$_.CategoryInfo.Category
}
finally {
    $cleanupStatus = 'not_registered'
    if ($registeredByProbe) {
        try {
            $task = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
            if ($null -ne $task -and $task.State -eq 'Running') {
                Stop-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop
            }
            Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
            $remaining = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
            if ($null -ne $remaining) { throw 'probe_task_cleanup_failed' }
            $cleanupStatus = 'removed'
        }
        catch {
            $cleanupStatus = 'failed'
            $status = 'failed'
            $reasonCode = 'probe_task_cleanup_failed'
        }
    }
    $result = [ordered]@{
        schema_version = 'scheduler-probe-v1'
        status = $status
        reason_code = $reasonCode
        failure_stage = if ($status -eq 'failed') { $probeStage } else { $null }
        error_id = $errorId
        error_hresult = $errorHResult
        error_category = $errorCategory
        task_name = $TaskName
        task_count_before = 0
        task_count_after = 0
        task_definition = $taskObserved
        event_count = $eventCount
        last_task_result = $lastTaskResult
        cleanup_status = $cleanupStatus
        task_xml_ref = 'registered-task.xml'
        events_ref = 'task-events.jsonl'
        created_at = [DateTimeOffset]::Now.ToString('o')
    }
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $evidencePath -Encoding UTF8
}

Get-Content -LiteralPath $evidencePath -Raw -Encoding UTF8
if ($status -ne 'passed') { exit 3 }
