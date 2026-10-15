[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot "..\..")
)

$ErrorActionPreference = 'Stop'
$taskName = 'RakutenRoomInagoHumanApi'
$taskPath = '\'
$restartCount = 3
$restartInterval = 'PT1M'

if (-not $env:USERPROFILE -or -not $env:LOCALAPPDATA) {
    throw 'The interactive user profile paths are unavailable.'
}
$userProfile = [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\')
$localAppData = [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\')
$projectRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
$runtimeRoot = Join-Path $projectRoot 'rakuten-room-inago\runtime'
$configPath = Join-Path $runtimeRoot 'config\settings.json'
$pythonExe = Join-Path $projectRoot '.venv-inago\Scripts\python.exe'

foreach ($path in @($configPath, $pythonExe)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'The existing Human API task prerequisites are incomplete.'
    }
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$humanApiPort = 0
if ($config.environment -ne 'production' -or
    [IO.Path]::GetFullPath($config.runtime_root).TrimEnd('\') -ne $runtimeRoot -or
    $config.human_api.bind_host -ne '127.0.0.1' -or
    -not [int]::TryParse([string]$config.human_api.port, [ref]$humanApiPort) -or
    $humanApiPort -lt 1 -or $humanApiPort -gt 65535) {
    throw 'The production Human API configuration does not match the approved loopback runtime.'
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userId = $identity.User.Value
$expectedArguments = "-B -m automation.inago.cli serve --environment production --config `"$configPath`" --data-root `"$runtimeRoot`" --user-profile `"$userProfile`" --local-app-data `"$localAppData`""

function Resolve-TaskUserSid([object]$Value) {
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    $user = [string]$Value
    try {
        if ($user -match '^S-1-') {
            return ([Security.Principal.SecurityIdentifier]::new($user)).Value
        }
        return ([Security.Principal.NTAccount]::new($user)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch {
        return $null
    }
}

$task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
if ($task.State -notin @('Ready', 'Running')) {
    throw 'The Human API task is not idle or serving; its registration was left unchanged.'
}
if ($task.Actions.Count -ne 1 -or
    [IO.Path]::GetFullPath($task.Actions[0].Execute).ToLowerInvariant() -ne $pythonExe.ToLowerInvariant() -or
    $task.Actions[0].Arguments -cne $expectedArguments -or
    [IO.Path]::GetFullPath($task.Actions[0].WorkingDirectory).ToLowerInvariant() -ne $projectRoot.ToLowerInvariant()) {
    throw 'The registered Human API action differs from the reviewed repository task; it was left unchanged.'
}
$registeredSid = Resolve-TaskUserSid $task.Principal.UserId
$triggerSid = Resolve-TaskUserSid $task.Triggers[0].UserId
if ($registeredSid -ne $userId -or
    $task.Principal.LogonType.ToString() -ne 'Interactive' -or
    $task.Principal.RunLevel.ToString() -ne 'Limited' -or
    $task.Triggers.Count -ne 1 -or
    $task.Triggers[0].CimClass.CimClassName -ne 'MSFT_TaskLogonTrigger' -or
    $triggerSid -ne $userId -or
    -not $task.Settings.Hidden -or
    $task.Settings.MultipleInstances.ToString() -ne 'IgnoreNew' -or
    [string]$task.Settings.ExecutionTimeLimit -notin @('PT0S', '00:00:00')) {
    throw 'The registered Human API trigger, principal, or settings differ from the reviewed task; it was left unchanged.'
}

$namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
$xmlText = Export-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
[xml]$definition = $xmlText
$manager = New-Object System.Xml.XmlNamespaceManager($definition.NameTable)
$manager.AddNamespace('t', $namespace)
$settings = $definition.SelectSingleNode('/t:Task/t:Settings', $manager)
if ($null -eq $settings) { throw 'The registered Human API definition has no settings node.' }
$restart = $settings.SelectSingleNode('t:RestartOnFailure', $manager)
if ($null -eq $restart) {
    $restart = $definition.CreateElement('RestartOnFailure', $namespace)
    $settings.AppendChild($restart) | Out-Null
}
$intervalNode = $restart.SelectSingleNode('t:Interval', $manager)
if ($null -eq $intervalNode) {
    $intervalNode = $definition.CreateElement('Interval', $namespace)
    $restart.AppendChild($intervalNode) | Out-Null
}
$countNode = $restart.SelectSingleNode('t:Count', $manager)
if ($null -eq $countNode) {
    $countNode = $definition.CreateElement('Count', $namespace)
    $restart.AppendChild($countNode) | Out-Null
}
$intervalNode.InnerText = $restartInterval
$countNode.InnerText = [string]$restartCount

$applyChange = $PSCmdlet.ShouldProcess($taskName, 'Set bounded restart policy on the Human API task only')
if (-not $applyChange) {
    [pscustomobject]@{
        TaskName = $taskName
        RestartCount = $restartCount
        RestartInterval = $restartInterval
        TaskState = $task.State.ToString()
        PlannedOnly = $true
        DailyTaskTouched = $false
        ProcessStopped = $false
    } | ConvertTo-Json -Compress
    return
}

$evidenceRoot = Join-Path $runtimeRoot 'task-definition-backups'
if (-not (Test-Path -LiteralPath $evidenceRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $evidenceRoot -Force -ErrorAction Stop | Out-Null
}
$timestamp = [DateTimeOffset]::Now.ToString('yyyyMMddTHHmmsszzz').Replace(':', '')
$backupPath = Join-Path $evidenceRoot "${taskName}-before-restart-policy-$timestamp.xml"
Set-Content -LiteralPath $backupPath -Value $xmlText -Encoding UTF8 -NoNewline
Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Xml $definition.OuterXml -Force -ErrorAction Stop | Out-Null

$updated = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
$updatedXmlText = Export-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
[xml]$updatedDefinition = $updatedXmlText
$updatedManager = New-Object System.Xml.XmlNamespaceManager($updatedDefinition.NameTable)
$updatedManager.AddNamespace('t', $namespace)
$updatedRestart = $updatedDefinition.SelectSingleNode('/t:Task/t:Settings/t:RestartOnFailure', $updatedManager)
if ($null -eq $updatedRestart -or
    $updatedRestart.SelectSingleNode('t:Interval', $updatedManager).InnerText -ne $restartInterval -or
    $updatedRestart.SelectSingleNode('t:Count', $updatedManager).InnerText -ne [string]$restartCount) {
    throw 'The Task Scheduler did not retain the requested Human API restart policy.'
}

[pscustomobject]@{
    TaskName = $taskName
    RestartCount = $restartCount
    RestartInterval = $restartInterval
    TaskState = $updated.State.ToString()
    PreviousTaskState = $task.State.ToString()
    BackupPath = $backupPath
    DailyTaskTouched = $false
    ProcessStopped = $false
} | ConvertTo-Json -Compress
